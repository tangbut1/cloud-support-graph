"""Day 6 坐席工作台（队列、审批恢复、人工回复与关单）测试。"""

from __future__ import annotations

import pytest

from app.services.kb_indexer import index_all
from tests.conftest import login_headers, make_user


@pytest.fixture()
def desk_env(client, conn, settings, monkeypatch):
    monkeypatch.setattr("app.agent.graph.get_settings", lambda: settings)
    monkeypatch.setattr("app.mcp.server.get_settings", lambda: settings)

    zs_id = make_user(conn, "zhangsan", "customer", "客户张三")
    agent_id = make_user(conn, "wanggong", "agent", "坐席王工")
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

    h_zs = login_headers(client, "zhangsan")
    h_agent = login_headers(client, "wanggong")
    return {
        "client": client,
        "conn": conn,
        "h_zs": h_zs,
        "h_agent": h_agent,
        "zs_id": zs_id,
        "agent_id": agent_id,
    }


def _trigger_pending_conversation(client, headers) -> str:
    conv_id = client.post("/api/conversations", json={}, headers=headers).json()["id"]
    with client.stream(
        "POST",
        f"/api/conversations/{conv_id}/chat/stream",
        data={"message": "我的实例 ubuntu-web-prod 诊断一下"},
        headers=headers,
    ) as resp:
        assert resp.status_code == 200
        list(resp.iter_lines())
    return conv_id


def test_customer_cannot_access_agent_endpoints(desk_env):
    client = desk_env["client"]
    h_zs = desk_env["h_zs"]

    assert client.get("/api/agent/queue", headers=h_zs).status_code == 403
    assert (
        client.post(
            "/api/agent/conversations/fake/resume",
            json={"action": "takeover_only"},
            headers=h_zs,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/agent/conversations/fake/reply",
            json={"content": "hi"},
            headers=h_zs,
        ).status_code
        == 403
    )


def test_queue_only_contains_pending_and_non_pending_resume_returns_409(desk_env):
    client = desk_env["client"]
    h_zs = desk_env["h_zs"]
    h_agent = desk_env["h_agent"]

    # 一个普通 ai_handling 会话，不应出现在队列
    ai_conv_id = client.post("/api/conversations", json={}, headers=h_zs).json()["id"]
    # 一个挂起的 pending_human 会话，应出现在队列
    pending_id = _trigger_pending_conversation(client, h_zs)

    queue = client.get("/api/agent/queue", headers=h_agent)
    assert queue.status_code == 200
    items = queue.json()
    assert [i["conversation_id"] for i in items] == [pending_id]
    assert items[0]["customer_name"] == "客户张三"
    assert items[0]["handoff_summary"]["instance"] == "ubuntu-web-prod"
    assert items[0]["draft_ticket"] is not None

    # 对非 pending_human 会话 resume -> 409
    r_conflict = client.post(
        f"/api/agent/conversations/{ai_conv_id}/resume",
        json={"action": "takeover_only"},
        headers=h_agent,
    )
    assert r_conflict.status_code == 409


def test_approve_resume_creates_ticket_and_updates_conversation(desk_env):
    client = desk_env["client"]
    conn = desk_env["conn"]
    h_zs = desk_env["h_zs"]
    h_agent = desk_env["h_agent"]

    conv_id = _trigger_pending_conversation(client, h_zs)

    resp = client.post(
        f"/api/agent/conversations/{conv_id}/resume",
        json={"action": "approve_ticket_and_reply", "comment": "请先启动 backend-8080"},
        headers=h_agent,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_status"] == "human_handling"
    assert body["ticket_no"] and body["ticket_no"].startswith("CSG-")
    assert body["ticket_no"] in body["message"]

    # 校验数据库：会话已指派给王工，工单已创建，assistant 回复已落库
    conv = conn.execute("SELECT status, assigned_agent_id FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    assert conv["status"] == "human_handling"
    assert conv["assigned_agent_id"] == desk_env["agent_id"]

    tickets = client.get("/api/tickets", headers=h_zs).json()
    assert len(tickets) == 1
    assert tickets[0]["ticket_no"] == body["ticket_no"]

    msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=h_zs).json()
    assert msgs[-1]["sender_role"] == "assistant"
    assert body["ticket_no"] in msgs[-1]["content"]

    # 队列已清空
    assert client.get("/api/agent/queue", headers=h_agent).json() == []


def test_takeover_only_does_not_create_ticket_and_reply_closes(desk_env):
    client = desk_env["client"]
    conn = desk_env["conn"]
    h_zs = desk_env["h_zs"]
    h_agent = desk_env["h_agent"]

    conv_id = _trigger_pending_conversation(client, h_zs)

    # pending_human 状态直接 reply -> 409
    assert (
        client.post(
            f"/api/agent/conversations/{conv_id}/reply",
            json={"content": "先回复"},
            headers=h_agent,
        ).status_code
        == 409
    )

    # 仅接管不建单
    r_take = client.post(
        f"/api/agent/conversations/{conv_id}/resume",
        json={"action": "takeover_only"},
        headers=h_agent,
    )
    assert r_take.status_code == 200
    assert r_take.json()["conversation_status"] == "human_handling"
    assert r_take.json()["ticket_no"] is None
    assert conn.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()["n"] == 0

    # 人工回复（不关闭）
    r_reply1 = client.post(
        f"/api/agent/conversations/{conv_id}/reply",
        json={"content": "您好，我是王工，正在为您排查", "close": False},
        headers=h_agent,
    )
    assert r_reply1.status_code == 200
    assert r_reply1.json()["conversation_status"] == "human_handling"

    # 人工回复并关闭
    r_reply2 = client.post(
        f"/api/agent/conversations/{conv_id}/reply",
        json={"content": "问题已解决，本次服务结束", "close": True},
        headers=h_agent,
    )
    assert r_reply2.status_code == 200
    assert r_reply2.json()["conversation_status"] == "closed"

    msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=h_zs).json()
    agent_msgs = [m for m in msgs if m["sender_role"] == "agent"]
    assert len(agent_msgs) == 2

    # 已关闭会话再次发消息 -> 409
    r_closed = client.post(
        f"/api/conversations/{conv_id}/chat/stream",
        data={"message": "还有个问题"},
        headers=h_zs,
    )
    assert r_closed.status_code == 409
