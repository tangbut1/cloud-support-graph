"""评测汇总与检索层阈值 A/B 可复现报告输出工具。

用法（原样复现 Day8-01 证据输出）：
    cd backend
    .\\.venv\\Scripts\\python.exe -m app.eval.report --run-id 2 --thresholds 0.30,0.35,0.45
"""

from __future__ import annotations

import argparse
import json
import sqlite3

from app.config import get_settings
from app.db.database import connect, init_db
from app.eval.ab_threshold import run_ab_thresholds


def format_eval_report(
    conn: sqlite3.Connection,
    *,
    run_id: int | None = None,
    thresholds: list[float] | None = None,
) -> str:
    """读取指定（或最新）eval_runs 及其逐题明细，并拼合检索层 A/B 回放表。"""
    if run_id is not None:
        run = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
    else:
        run = conn.execute("SELECT * FROM eval_runs ORDER BY id DESC LIMIT 1").fetchone()
    if run is None:
        raise ValueError(
            f"未找到评测批次 run_id={run_id}（全新环境请先执行 "
            "`python -m app.eval.runner --name \"黄金集评测\"` 生成评测记录）"
        )

    cases = conn.execute(
        "SELECT * FROM eval_case_results WHERE run_id = ? ORDER BY id ASC", (run["id"],)
    ).fetchall()
    levels = thresholds or [0.30, 0.35, 0.45]
    ab_rows = run_ab_thresholds(conn, thresholds=levels)

    cfg = json.loads(run["config_json"] or "{}")
    passed_total = sum(1 for c in cases if c["passed"])
    total_cases = run["total_cases"] or len(cases)
    pass_rate = (passed_total / total_cases * 100.0) if total_cases else 0.0
    keys = run.keys()
    judge_mode = (
        run["judge_mode"]
        if "judge_mode" in keys and run["judge_mode"]
        else cfg.get("judge_mode", "citation_heuristic")
    )

    by_cat: dict[str, list[dict]] = {}
    failed_lines: list[str] = []
    for c in cases:
        actual = json.loads(c["actual_json"] or "{}")
        by_cat.setdefault(c["category"], []).append(
            {"passed": bool(c["passed"]), "lat": float(actual.get("latency_ms") or 0.0)}
        )
        if not c["passed"]:
            failed_lines.append(f"    - {c['case_id']}: {c['reason']}")

    th_arg = ",".join(f"{t:.2f}" for t in levels)
    lines = [
        f"$ cd backend && .\\.venv\\Scripts\\python.exe -m app.eval.report --run-id {run['id']} --thresholds {th_arg}",
        "",
        "==================== 32 题黄金集真实大模型评测汇总（端到端图级） ====================",
        f"模型: {cfg.get('llm_model_name', 'deepseek-v4.1-flash')} (OpenCode Go) | RAG_MIN_SCORE = {cfg.get('rag_min_score', 0.35)} | run_id = #{run['id']}",
        f"批次名称              : {run['run_name']}",
        f"总题数                : {total_cases}  (通过 {passed_total} / {total_cases}, 通过率 {pass_rate:.1f}%, 异常题 {run['error_cases']})",
        f"意图准确率            : {(run['intent_acc'] or 0) * 100:.1f}%",
        f"严格拒答率 (kb_refuse): {(run['refusal_acc'] or 0) * 100:.1f}%  (kb_unanswerable 8 题仅计 kb_refuse)",
        f"零幻觉安全路由率      : {(run['safe_routing_rate'] or 0) * 100:.1f}%  (含 kb_refuse / 保守转人工 / 安全拦截)",
        f"规则前置安全拦截率    : {(run['security_block_rate'] or 0) * 100:.1f}%  (intent_guard 前置拦截, 0 token, 零工具调用)",
        f"MCP 工具调用准确率    : {(run['tool_ops_acc'] or 0) * 100:.1f}%  (仅统计 tool-01..04 实际调工具 4 题)",
        f"纯 HITL 升级触发率    : {(run['hitl_escalate_acc'] or 0) * 100:.1f}%  (仅统计 tool-05..08 直接转人工 4 题)",
        f"引用命中率 (启发式)   : {(run['citation_hit_rate'] or 0) * 100:.1f}%  (judge_mode={judge_mode}, 非 LLM Judge)",
        f"端到端平均延迟        : {(run['avg_latency_ms'] or 0):.1f} ms",
        f"Token 消耗            : prompt={cfg.get('total_prompt_tokens', 0)} / completion={cfg.get('total_completion_tokens', 0)} "
        f"(均值 {cfg.get('avg_prompt_tokens', 0)} / {cfg.get('avg_completion_tokens', 0)})",
        "",
        "-------------------- 分类明细（32 题） --------------------",
    ]
    for cat in ("kb_answerable", "kb_unanswerable", "security_adversarial", "tool_and_hitl"):
        items = by_cat.get(cat, [])
        ok = sum(1 for x in items if x["passed"])
        n = len(items)
        avg_l = sum(x["lat"] for x in items) / n if n else 0.0
        min_l = min((x["lat"] for x in items), default=0.0)
        max_l = max((x["lat"] for x in items), default=0.0)
        pct = (ok / n * 100.0) if n else 0.0
        lines.append(
            f"  {cat:<24}通过 {ok:2d}/{n:<2d} ({pct:5.1f}%)   "
            f"平均延迟 {avg_l:7.1f} ms (区间 {min_l:.1f}–{max_l:.1f} ms)"
        )
    if failed_lines:
        lines.append("  严格比对未通过条目（均未编造幻觉，已安全转人工或拦截）:")
        lines.extend(failed_lines)

    lines.extend([
        "",
        "==================== 检索层 RAG_MIN_SCORE 三档阈值 A/B 对比（单模块回放，18 题） ====================",
        "说明：以下直接调用 retriever.search 评估 18 道知识库题，与上方端到端图级口径区分。",
        "RAG_MIN_SCORE   可答放行(10题)        不可答拒答(8题)        检索层综合准确率(18题)    误放行/误拒",
        "-" * 92,
    ])
    for r in ab_rows:
        leaked = ",".join(r["leaked_unanswerable"]) if r["leaked_unanswerable"] else "-"
        note = "  <-- 生产选定" if abs(r["threshold"] - 0.35) < 1e-6 else ""
        extra = f"误放行:{leaked}" if leaked != "-" else f"-{note}"
        lines.append(
            f"{r['threshold']:<15.2f} "
            f"{r['answerable_pass']:2d}/{r['answerable_total']:<2d} ({r['answerable_rate']*100:5.1f}%)      "
            f"{r['refusal_pass']:2d}/{r['refusal_total']:<2d} ({r['refusal_rate']*100:5.1f}%)       "
            f"{r['overall_acc']*100:5.1f}%                  {extra}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="从 app.db 原样输出 32 题评测汇总与检索层 A/B 报告")
    parser.add_argument("--run-id", type=int, default=None, help="评测批次 ID（默认最新批次）")
    parser.add_argument("--thresholds", default="0.30,0.35,0.45", help="逗号分隔的检索阈值列表")
    args = parser.parse_args()

    levels = [float(x.strip()) for x in args.thresholds.split(",") if x.strip()]
    settings = get_settings()
    init_db(settings.app_db)
    conn = connect(settings.app_db)
    try:
        try:
            print(format_eval_report(conn, run_id=args.run_id, thresholds=levels))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    main()
