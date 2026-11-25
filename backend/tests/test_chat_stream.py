"""Day 6 SSE 流式问答、挂起、图片上传校验与人工接管分流测试。"""

from __future__ import annotations

import json

import pytest

from app.services.image_intake import MAX_IMAGE_BYTES
from app.services.kb_indexer import index_all
from tests.conftest import login_headers, make_user

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _parse_sse_lines(lines: list[str]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    cur_event: str | None = None
    for raw in lines:
        line = raw.strip()
        if line.startswith("event:"):
            cur_event = line[6:].strip()
        elif line.startswith("data:") and cur_event is not None:
            events.append((cur_event, json.loads(line[5:].strip())))
            cur_event = None
    return events


@pytest.fixture()
def stream_env(client, conn, settings, monkeypatch):
    """准备带知识库、故障实例与客户账号的离线测试环境。"""
    monkeypatch.setattr("app.agent.graph.get_settings", lambda: settings)
    monkeypatch.setattr("app.mcp.server.get_settings", lambda: settings)

    zs_id = make_user(conn, "zhangsan", "customer", "客户张三")
    conn.execute(
        """
        INSERT INTO cloud_instances (owner_id, instance_name, public_ip, region, spec, os_image,
                                     status, firewall_ports_json, service_status_json, icp_filed,
                                     swap_mb, updated_at)
        VALUES (?, 'ubuntu-web-prod', '47.94.101.23', '华北2（北京）', '2C2G', 'Ubuntu 22.04 LTS',
                'running', '[22, 80, 443]', '{"nginx": "running", "backend-8080": "stopped"}',
                1, 0, datetime('now'))
        """,
        (zs_id,),
    )
    conn.commit()
    index_all(conn)

    headers = login_headers(client, "zhangsan")
    return {"client": client, "conn": conn, "headers": headers, "user_id": zs_id}


def test_sse_kb_answer_event_order_and_persistence(stream_env):
    client = stream_env["client"]
    headers = stream_env["headers"]
    conv_id = client.post("/api/conversations", json={}, headers=headers).json()["id"]

    with client.stream(
        "POST",
        f"/api/conversations/{conv_id}/chat/stream",
        data={"message": "Nginx 502 怎么排查"},
        headers=headers,
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        events = _parse_sse_lines(list(resp.iter_lines()))

    names = [name for name, _ in events]
    assert names[0] == "status"
    assert "citations" in names
    assert "token" in names
    assert names[-1] == "done"
    assert names.index("citations") < names.index("token") < names.index("done")

    citations_data = dict(events)["citations"]
    assert citations_data["citations"]
    assert citations_data["rag_top_score"] >= 0.35

    done_data = dict(events)["done"]
    assert done_data["decision_type"] == "kb_answer"
    assert done_data["conversation_status"] == "ai_handling"
    assert done_data["message_id"] is not None
    assert done_data["trace_id"] is not None

    # 历史消息落库完整（含引用与原文展开）
    msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=headers).json()
    assert len(msgs) == 2
    assert msgs[0]["sender_role"] == "customer"
    assert msgs[1]["sender_role"] == "assistant"
    assert msgs[1]["citations"]
    assert msgs[1]["citation_details"]
    assert msgs[1]["trace_id"] == done_data["trace_id"]


def test_sse_kb_refuse_scenario(stream_env):
    client = stream_env["client"]
    headers = stream_env["headers"]
    conv_id = client.post("/api/conversations", json={}, headers=headers).json()["id"]

    with client.stream(
        "POST",
        f"/api/conversations/{conv_id}/chat/stream",
        data={"message": "帮我申请一张阿里云代金券"},
        headers=headers,
    ) as resp:
        assert resp.status_code == 200
        events = _parse_sse_lines(list(resp.iter_lines()))

    done_data = [d for n, d in events if n == "done"][-1]
    assert done_data["decision_type"] == "kb_refuse"
    token_text = "".join(d["text"] for n, d in events if n == "token")
    assert "未覆盖" in token_text


def test_sse_tool_critical_fault_interrupts_and_persists_summary(stream_env):
    client = stream_env["client"]
    conn = stream_env["conn"]
    headers = stream_env["headers"]
    conv_id = client.post("/api/conversations", json={}, headers=headers).json()["id"]

    with client.stream(
        "POST",
        f"/api/conversations/{conv_id}/chat/stream",
        data={"message": "我的实例 ubuntu-web-prod 诊断一下"},
        headers=headers,
    ) as resp:
        assert resp.status_code == 200
        events = _parse_sse_lines(list(resp.iter_lines()))

    names = [n for n, _ in events]
    assert "interrupt" in names
    assert names[-1] == "done"

    intr_data = [d for n, d in events if n == "interrupt"][0]
    assert intr_data["type"] == "human_approval"
    assert intr_data["handoff_summary"]["instance"] == "ubuntu-web-prod"
    assert intr_data["draft_ticket"] is not None

    done_data = [d for n, d in events if n == "done"][-1]
    assert done_data["conversation_status"] == "pending_human"

    row = conn.execute(
        "SELECT status, ai_handoff_summary_json FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    assert row["status"] == "pending_human"
    assert row["ai_handoff_summary_json"]
    saved_summary = json.loads(row["ai_handoff_summary_json"])
    assert saved_summary["instance"] == "ubuntu-web-prod"


def test_sse_human_handling_does_not_invoke_ai(stream_env, monkeypatch):
    client = stream_env["client"]
    conn = stream_env["conn"]
    headers = stream_env["headers"]
    conv_id = client.post("/api/conversations", json={}, headers=headers).json()["id"]

    conn.execute("UPDATE conversations SET status = 'human_handling' WHERE id = ?", (conv_id,))
    conn.commit()

    def _forbid_graph(*args, **kwargs):
        raise AssertionError("human_handling 状态下不应调用 LangGraph")

    monkeypatch.setattr("app.routers.conversations.build_graph", _forbid_graph)

    with client.stream(
        "POST",
        f"/api/conversations/{conv_id}/chat/stream",
        data={"message": "坐席你好，我已经重启服务了"},
        headers=headers,
    ) as resp:
        assert resp.status_code == 200
        events = _parse_sse_lines(list(resp.iter_lines()))

    assert [n for n, _ in events] == ["done"]
    assert events[0][1]["conversation_status"] == "human_handling"

    msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=headers).json()
    assert len(msgs) == 1
    assert msgs[0]["sender_role"] == "customer"
    assert msgs[0]["content"] == "坐席你好，我已经重启服务了"


def test_sse_image_upload_validation_and_success(stream_env):
    client = stream_env["client"]
    headers = stream_env["headers"]
    conv_id = client.post("/api/conversations", json={}, headers=headers).json()["id"]

    # 假扩展名图片 -> 400
    bad_resp = client.post(
        f"/api/conversations/{conv_id}/chat/stream",
        data={"message": "看图"},
        files={"image": ("fake.png", b"not-a-real-png", "image/png")},
        headers=headers,
    )
    assert bad_resp.status_code == 400

    # 超 2MB -> 413
    big_resp = client.post(
        f"/api/conversations/{conv_id}/chat/stream",
        data={"message": "看图"},
        files={"image": ("big.png", PNG_BYTES + b"\x00" * MAX_IMAGE_BYTES, "image/png")},
        headers=headers,
    )
    assert big_resp.status_code == 413

    # 合法 PNG 正常通过并标记 has_image=True
    with client.stream(
        "POST",
        f"/api/conversations/{conv_id}/chat/stream",
        data={"message": "Nginx 502 怎么排查"},
        files={"image": ("err.png", PNG_BYTES, "image/png")},
        headers=headers,
    ) as ok_resp:
        assert ok_resp.status_code == 200
        events = _parse_sse_lines(list(ok_resp.iter_lines()))

    assert any(n == "status" and d.get("node") == "vision" for n, d in events)
    msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=headers).json()
    assert msgs[0]["has_image"] is True


def test_sse_noisy_ocr_screenshot_reaches_kb_answer_with_citations(stream_env, monkeypatch):
    client = stream_env["client"]
    headers = stream_env["headers"]
    conv_id = client.post("/api/conversations", json={}, headers=headers).json()["id"]

    async def _fake_vision(*args, **kwargs) -> str:
        return (
            "502 Bad Gateway nginx/1.24.0 (Ubuntu 22.04.3 LTS) "
            "2026/11/20 14:32:09 [error] 24518#0: *109 connect() failed (111: Connection refused) "
            "while connecting to upstream http://47.94.101.23:8080/api/orders"
        )

    monkeypatch.setattr("app.routers.conversations.describe_image_safe", _fake_vision)

    with client.stream(
        "POST",
        f"/api/conversations/{conv_id}/chat/stream",
        data={"message": "这张报错截图是什么原因，怎么排查"},
        files={"image": ("nginx502.png", PNG_BYTES, "image/png")},
        headers=headers,
    ) as resp:
        assert resp.status_code == 200
        events = _parse_sse_lines(list(resp.iter_lines()))

    citations_events = [d for n, d in events if n == "citations"]
    assert citations_events, f"应命中引用而非被瞬态 IP/PID 噪点压到 0.30 拒答: {events}"
    assert citations_events[0]["rag_top_score"] >= 0.35

    done_data = [d for n, d in events if n == "done"][-1]
    assert done_data["decision_type"] == "kb_answer"
