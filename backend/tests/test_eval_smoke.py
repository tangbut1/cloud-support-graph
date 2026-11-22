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
