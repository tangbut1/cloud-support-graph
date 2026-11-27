"""Day 8 黄金评测集格式、离线评测执行器与管理端 Trace/Eval 接口测试。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from app.agent.trace_logger import record_trace
from app.db.seed import seed
from app.eval.report import build_eval_report_payload, format_eval_report, run_report_cli
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


def test_eval_report_cli_graceful_missing_db_without_disk_pollution(tmp_path):
    missing_db = tmp_path / "fresh_clone_no_app.db"
    assert not missing_db.exists()

    # 1. 默认无 --run-id：优雅降级输出 18 题检索 A/B 基线与指引，且不在磁盘创建空 app.db
    code, text_out = run_report_cli(db_path=missing_db)
    assert code == 0
    assert not missing_db.exists(), "只读报告命令不应在全新克隆环境残留空白数据库文件"
    assert "尚未生成端到端评测批次记录" in text_out
    assert "0.35" in text_out
    assert "100.0%" in text_out

    # 2. JSON 模式同样可降级输出 ab_only 结构
    code_json, json_out = run_report_cli(db_path=missing_db, as_json=True)
    assert code_json == 0
    parsed = __import__("json").loads(json_out)
    assert parsed["status"] == "ab_only"
    assert parsed["eval_run"] is None
    assert any(r["threshold"] == 0.35 and r["overall_acc"] == 1.0 for r in parsed["ab_thresholds"])

    # 3. 显式指定不存在的 --run-id 或开启 --strict：必须返回非零退出码防静默吞错
    code_missing_id, err_msg = run_report_cli(db_path=missing_db, run_id=999)
    assert code_missing_id == 1
    assert "run_id=999" in err_msg

    code_strict, _ = run_report_cli(db_path=missing_db, strict=True)
    assert code_strict == 1


def test_eval_report_json_export_includes_badcases_and_auto_indexes_kb(conn, tmp_path):
    import json

    # 故意不预先调用 index_all(conn)，验证报告工具在空知识库表上会自动补齐索引
    cur = conn.execute(
        """
        INSERT INTO eval_runs (
            run_name, config_json, status, total_cases, error_cases,
            intent_acc, refusal_acc, safe_routing_rate, security_block_rate,
            tool_acc, tool_ops_acc, hitl_escalate_acc, avg_faithfulness,
            citation_hit_rate, judge_mode, avg_latency_ms, created_at
        ) VALUES (
            'JSON导出与Badcase测试',
            '{"llm_model_name":"deepseek-v4.1-flash","rag_min_score":0.35,"total_prompt_tokens":300,"total_completion_tokens":450}',
            'completed', 2, 0, 0.5, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
            'citation_heuristic', 210.5, '2026-11-27 10:00:00'
        )
        """
    )
    rid = int(cur.lastrowid)
    conn.execute(
        """
        INSERT INTO eval_case_results (run_id, case_id, category, passed, expected_json, actual_json, judge_score, reason)
        VALUES
            (?, 'kb-01', 'kb_answerable', 1, '{"intent":"kb_qa","decision":"kb_answer"}', '{"intent":"kb_qa","decision":"kb_answer","latency_ms":180.0}', 1.0, '通过'),
            (?, 'ref-01', 'kb_unanswerable', 0, '{"intent":"kb_qa","decision":"kb_refuse"}', '{"intent":"escalate_hitl","decision":"hitl_takeover","latency_ms":241.0}', 0.0, '意图不符(期望 kb_qa, 实际 escalate_hitl)')
        """,
        (rid, rid),
    )
    conn.commit()

    # 文本报告自动补齐索引后 0.35 档应为 100.0% 而非 0.0%
    txt = format_eval_report(conn, run_id=rid)
    assert "10/10 (100.0%)" in txt
    assert "ref-01: 意图不符" in txt

    # 结构化 JSON 报告与文件落盘
    payload = build_eval_report_payload(conn, run_id=rid)
    assert payload["status"] == "full"
    assert payload["eval_run"]["passed_cases"] == 1
    assert len(payload["eval_run"]["badcases"]) == 1
    assert payload["eval_run"]["badcases"][0]["case_id"] == "ref-01"

    out_file = tmp_path / "artifacts" / "eval_report.json"
    db_file = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    code, _ = run_report_cli(db_path=db_file, run_id=rid, as_json=True, output_path=out_file)
    assert code == 0
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert saved["eval_run"]["run_id"] == rid
    assert saved["eval_run"]["badcases"][0]["actual"]["decision"] == "hitl_takeover"
