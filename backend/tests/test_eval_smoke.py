"""Day 8 黄金评测集格式、离线评测执行器与管理端 Trace/Eval 接口测试。"""

from __future__ import annotations

from collections import Counter

import pytest

from app.agent.trace_logger import record_trace
from app.db.seed import seed
from app.eval.runner import load_dataset, run_eval
from tests.conftest import login_headers, make_user


def test_dataset_jsonl_format_and_distribution():
    cases = load_dataset()
    assert len(cases) == 32

    ids = [c["case_id"] for c in cases]
    assert len(set(ids)) == 32, "case_id 不能重复"

    counts = Counter(c["category"] for c in cases)
    assert counts == {
        "kb_answerable": 10,
        "kb_unanswerable": 8,
        "security_adversarial": 6,
        "tool_and_hitl": 8,
    }
    for case in cases:
        assert case.get("query") and isinstance(case["query"], str)
        assert isinstance(case.get("expected"), dict)


async def test_offline_eval_runner_full_32_cases(conn, settings, monkeypatch):
    """离线（无真实模型）完整跑完 32 题，校验指标与落库。"""
    monkeypatch.setattr("app.agent.graph.get_settings", lambda: settings)
    monkeypatch.setattr("app.mcp.server.get_settings", lambda: settings)

    seed(settings.app_db)
    zs = conn.execute("SELECT id, role FROM users WHERE username = 'zhangsan'").fetchone()

    summary = await run_eval(
        conn,
        checkpoint_db=settings.checkpoint_db,
        user_id=int(zs["id"]),
        role=str(zs["role"]),
        run_name="离线冒烟32题",
        settings=settings,
        db_path=settings.app_db,
    )

    assert summary["total_cases"] == 32
    assert summary["intent_acc"] == 1.0
    assert summary["refusal_acc"] == 1.0
    assert summary["security_block_rate"] == 1.0
    assert summary["tool_acc"] == 1.0
    assert summary["pass_rate"] == 1.0

    # 评测收尾用 takeover_only，不应污染 tickets 表
    assert conn.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()["n"] == 0

    run_row = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (summary["run_id"],)).fetchone()
    assert run_row is not None
    assert run_row["total_cases"] == 32
    case_cnt = conn.execute(
        "SELECT COUNT(*) AS n FROM eval_case_results WHERE run_id = ?", (summary["run_id"],)
    ).fetchone()["n"]
    assert case_cnt == 32


def test_admin_traces_and_eval_endpoints_rbac_and_data(client, conn, settings, monkeypatch):
    monkeypatch.setattr("app.agent.graph.get_settings", lambda: settings)
    monkeypatch.setattr("app.mcp.server.get_settings", lambda: settings)

    seed(settings.app_db)
    h_zs = login_headers(client, "zhangsan")
    h_agent = login_headers(client, "wanggong")
    h_admin = login_headers(client, "admin")

    # 非管理员访问全部 403
    for h in (h_zs, h_agent):
        assert client.get("/api/admin/traces", headers=h).status_code == 403
        assert client.get("/api/admin/eval/runs", headers=h).status_code == 403
        assert client.post("/api/admin/eval/runs", json={"limit": 2}, headers=h).status_code == 403

    # 写入两条 trace 测试筛选与聚合
    record_trace(
        conn,
        conversation_id="c1",
        user_id=1,
        intent="kb_qa",
        rag_hit_count=3,
        rag_top_score=0.78,
        tool_calls=[],
        decision="kb_answer",
        latency_ms=120,
        prompt_tokens=300,
        completion_tokens=200,
    )
    record_trace(
        conn,
        conversation_id="c2",
        user_id=1,
        intent="reject_unsafe",
        rag_hit_count=0,
        rag_top_score=0.0,
        tool_calls=[],
        decision="reject_unsafe",
        latency_ms=10,
        prompt_tokens=0,
        completion_tokens=0,
    )

    tr = client.get("/api/admin/traces?limit=10&offset=0", headers=h_admin)
    assert tr.status_code == 200
    body = tr.json()
    assert body["total"] == 2
    assert body["aggregate"]["total_calls"] == 2
    assert body["aggregate"]["total_tokens"] == 500
    assert body["aggregate"]["decision_distribution"]["kb_answer"] == 1
    assert body["aggregate"]["decision_distribution"]["reject_unsafe"] == 1

    # 按意图筛选
    tr_filtered = client.get("/api/admin/traces?intent=reject_unsafe", headers=h_admin).json()
    assert tr_filtered["total"] == 1
    assert tr_filtered["items"][0]["decision"] == "reject_unsafe"

    # 触发后台小样本评测（TestClient 同步等待 BackgroundTasks 完成）
    trig = client.post(
        "/api/admin/eval/runs",
        json={"run_name": "接口冒烟", "limit": 3},
        headers=h_admin,
    )
    assert trig.status_code == 202
    run_id = trig.json()["run_id"]

    runs = client.get("/api/admin/eval/runs", headers=h_admin).json()
    assert any(r["id"] == run_id for r in runs)

    detail = client.get(f"/api/admin/eval/runs/{run_id}", headers=h_admin)
    assert detail.status_code == 200
    dbody = detail.json()
    assert dbody["total_cases"] == 3
    assert len(dbody["cases"]) == 3
    assert dbody["status"] == "completed"

    # 不存在的评测 id -> 404
    assert client.get("/api/admin/eval/runs/99999", headers=h_admin).status_code == 404


def test_admin_trace_source_breakdown_preserved_under_user_filter(client, conn, settings):
    seed(settings.app_db)
    h_admin = login_headers(client, "admin")

    record_trace(
        conn,
        conversation_id="user-conv-1",
        user_id=1,
        intent="kb_qa",
        decision="kb_answer",
        latency_ms=100,
        prompt_tokens=50,
        completion_tokens=60,
    )
    record_trace(
        conn,
        conversation_id="eval-batch-01",
        user_id=1,
        intent="kb_qa",
        decision="kb_refuse",
        latency_ms=900,
        prompt_tokens=400,
        completion_tokens=500,
    )

    # 默认切到 source=user 时：耗时与 Token 仅计真实流量，但跨源统计仍提示存在 eval_calls=1
    user_view = client.get("/api/admin/traces?source=user", headers=h_admin).json()
    assert user_view["total"] == 1
    assert user_view["items"][0]["conversation_id"] == "user-conv-1"
    assert user_view["aggregate"]["total_calls"] == 1
    assert user_view["aggregate"]["avg_latency_ms"] == 100.0
    assert user_view["aggregate"]["total_tokens"] == 110
    assert user_view["aggregate"]["user_calls"] == 1
    assert user_view["aggregate"]["eval_calls"] == 1


def test_admin_trace_cleanup_rbac_ttl_dry_run_and_active_conv_protection(client, conn, settings):
    seed(settings.app_db)
    h_zs = login_headers(client, "zhangsan")
    h_agent = login_headers(client, "wanggong")
    h_admin = login_headers(client, "admin")

    # 1. RBAC：客户与坐席均禁止调用清理接口
    for h in (h_zs, h_agent):
        resp = client.post("/api/admin/traces/cleanup", json={"source": "eval"}, headers=h)
        assert resp.status_code == 403

    # 2. 准备活跃会话、已关闭会话及新旧评测跑批 Trace
    zs_id = int(conn.execute("SELECT id FROM users WHERE username = 'zhangsan'").fetchone()["id"])
    conn.execute(
        "INSERT INTO conversations (id, customer_id, thread_id, status, created_at, updated_at) "
        "VALUES ('conv-active', ?, 'th-active', 'pending_human', '2020-01-01 00:00:00', '2020-01-01 00:00:00'), "
        "       ('conv-closed', ?, 'th-closed', 'closed', '2020-01-01 00:00:00', '2020-01-01 00:00:00')",
        (zs_id, zs_id),
    )
    t_active = record_trace(conn, conversation_id="conv-active", user_id=zs_id, intent="tool_ops", decision="hitl_takeover")
    t_closed = record_trace(conn, conversation_id="conv-closed", user_id=zs_id, intent="kb_qa", decision="kb_answer")
    t_eval_old = record_trace(conn, conversation_id="eval-old-01", user_id=zs_id, intent="kb_qa", decision="kb_answer")
    t_eval_new = record_trace(conn, conversation_id="eval-new-01", user_id=zs_id, intent="kb_qa", decision="kb_refuse")

    conn.execute(
        "UPDATE agent_traces SET created_at = '2020-01-01 00:00:00' WHERE id IN (?, ?, ?)",
        (t_active, t_closed, t_eval_old),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_role, content, trace_id, created_at) "
        "VALUES ('conv-closed', 'assistant', '历史回答', ?, '2020-01-01 00:00:05')",
        (t_closed,),
    )
    conn.commit()

    # 3. dry_run=True + older_than_days=7：仅统计过期评测 Trace，不删库
    dry = client.post(
        "/api/admin/traces/cleanup",
        json={"source": "eval", "older_than_days": 7, "dry_run": True},
        headers=h_admin,
    ).json()
    assert dry["dry_run"] is True
    assert dry["deleted_count"] == 1
    assert dry["remaining_total"] == 4

    # 4. 正式执行 TTL=7 天评测清理：仅删 t_eval_old，保留近期 t_eval_new
    res_ttl = client.post(
        "/api/admin/traces/cleanup",
        json={"source": "eval", "older_than_days": 7, "dry_run": False},
        headers=h_admin,
    ).json()
    assert res_ttl["deleted_count"] == 1
    assert res_ttl["remaining_eval_calls"] == 1
    assert res_ttl["remaining_user_calls"] == 2

    # 5. source=all 且 older_than_days=0：清理剩余评测与已关闭会话 Trace（同步清空 messages.trace_id），但严格保留活跃会话 Trace
    res_all = client.post(
        "/api/admin/traces/cleanup",
        json={"source": "all", "older_than_days": 0},
        headers=h_admin,
    ).json()
    assert res_all["deleted_count"] == 2  # t_eval_new + t_closed
    assert res_all["remaining_total"] == 1
    assert res_all["remaining_user_calls"] == 1

    leftover = conn.execute("SELECT id, conversation_id FROM agent_traces").fetchall()
    assert len(leftover) == 1
    assert leftover[0]["conversation_id"] == "conv-active"

    msg_row = conn.execute("SELECT trace_id FROM messages WHERE conversation_id = 'conv-closed'").fetchone()
    assert msg_row["trace_id"] is None
