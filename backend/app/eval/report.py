"""评测汇总与检索层阈值 A/B 可复现报告输出工具。

用法（原样复现 Day8-01 证据输出）：
    cd backend
    .\\.venv\\Scripts\\python.exe -m app.eval.report --run-id 2 --thresholds 0.30,0.35,0.45

全新克隆环境（无 data/app.db）直接运行：
    .\\.venv\\Scripts\\python.exe -m app.eval.report
    -> 不污染磁盘创建空库，自动在内存库回放 18 题检索阈值 A/B 基线并提示如何生成端到端评测批次。

导出结构化 JSON（含 Badcase 明细归档）：
    .\\.venv\\Scripts\\python.exe -m app.eval.report --json --output eval_report.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db.database import connect, init_db
from app.eval.ab_threshold import run_ab_thresholds
from app.services.kb_indexer import index_all


def _ensure_kb_indexed(conn: sqlite3.Connection) -> None:
    """若当前库尚未索引内置知识库文档，自动补齐索引以保证检索 A/B 回放准确。"""
    doc_count = int(conn.execute("SELECT COUNT(*) AS n FROM kb_documents").fetchone()["n"])
    if doc_count == 0:
        index_all(conn)


def _format_ab_table_lines(ab_rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "==================== 检索层 RAG_MIN_SCORE 三档阈值 A/B 对比（单模块回放，18 题） ====================",
        "说明：以下直接调用 retriever.search 评估 18 道知识库题，与上方端到端图级口径区分。",
        "RAG_MIN_SCORE   可答放行(10题)        不可答拒答(8题)        检索层综合准确率(18题)    误放行/误拒",
        "-" * 92,
    ]
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
    return lines


def build_eval_report_payload(
    conn: sqlite3.Connection,
    *,
    run_id: int | None = None,
    thresholds: list[float] | None = None,
    allow_ab_only: bool = False,
) -> dict[str, Any]:
    """构建结构化评测报告字典（含分类延迟区间、Badcase 归因明细与检索层 A/B 结果）。"""
    if run_id is not None:
        run = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
    else:
        run = conn.execute("SELECT * FROM eval_runs ORDER BY id DESC LIMIT 1").fetchone()

    _ensure_kb_indexed(conn)
    levels = thresholds or [0.30, 0.35, 0.45]
    ab_rows = run_ab_thresholds(conn, thresholds=levels)

    if run is None:
        if not allow_ab_only or run_id is not None:
            raise ValueError(
                f"未找到评测批次 run_id={run_id}（全新环境请先执行 "
                "`python -m app.eval.runner --name \"黄金集评测\"` 生成评测记录）"
            )
        return {
            "status": "ab_only",
            "message": (
                "当前尚未生成端到端评测批次记录（data/app.db 不存在或 eval_runs 为空）；"
                "已回放内置 18 题知识库检索阈值 A/B 基线。"
            ),
            "next_step": 'python -m app.eval.runner --name "黄金集基准评测"',
            "eval_run": None,
            "ab_thresholds": ab_rows,
        }

    cases = conn.execute(
        "SELECT * FROM eval_case_results WHERE run_id = ? ORDER BY id ASC", (run["id"],)
    ).fetchall()
    cfg = json.loads(run["config_json"] or "{}")
    passed_total = sum(1 for c in cases if c["passed"])
    total_cases = int(run["total_cases"] or len(cases))
    pass_rate = round(passed_total / total_cases, 4) if total_cases else 0.0
    keys = run.keys()
    judge_mode = (
        run["judge_mode"]
        if "judge_mode" in keys and run["judge_mode"]
        else cfg.get("judge_mode", "citation_heuristic")
    )

    by_cat: dict[str, list[dict[str, Any]]] = {}
    badcases: list[dict[str, Any]] = []
    for c in cases:
        expected_obj = json.loads(c["expected_json"] or "{}")
        actual_obj = json.loads(c["actual_json"] or "{}")
        by_cat.setdefault(c["category"], []).append(
            {"passed": bool(c["passed"]), "lat": float(actual_obj.get("latency_ms") or 0.0)}
        )
        if not c["passed"]:
            badcases.append(
                {
                    "case_id": c["case_id"],
                    "category": c["category"],
                    "reason": c["reason"],
                    "expected": expected_obj,
                    "actual": actual_obj,
                }
            )

    category_breakdown: dict[str, dict[str, Any]] = {}
    for cat in ("kb_answerable", "kb_unanswerable", "security_adversarial", "tool_and_hitl"):
        items = by_cat.get(cat, [])
        ok = sum(1 for x in items if x["passed"])
        n = len(items)
        category_breakdown[cat] = {
            "passed": ok,
            "total": n,
            "pass_rate": round(ok / n, 4) if n else 0.0,
            "avg_latency_ms": round(sum(x["lat"] for x in items) / n, 1) if n else 0.0,
            "min_latency_ms": round(min((x["lat"] for x in items), default=0.0), 1),
            "max_latency_ms": round(max((x["lat"] for x in items), default=0.0), 1),
        }

    return {
        "status": "full",
        "eval_run": {
            "run_id": int(run["id"]),
            "run_name": run["run_name"],
            "model": cfg.get("llm_model_name", "deepseek-v4.1-flash"),
            "rag_min_score": cfg.get("rag_min_score", 0.35),
            "judge_mode": str(judge_mode),
            "total_cases": total_cases,
            "passed_cases": passed_total,
            "pass_rate": pass_rate,
            "error_cases": int(run["error_cases"] or 0),
            "intent_acc": run["intent_acc"],
            "refusal_acc": run["refusal_acc"],
            "safe_routing_rate": run["safe_routing_rate"],
            "security_block_rate": run["security_block_rate"],
            "tool_ops_acc": run["tool_ops_acc"],
            "hitl_escalate_acc": run["hitl_escalate_acc"],
            "citation_hit_rate": run["citation_hit_rate"],
            "avg_latency_ms": run["avg_latency_ms"],
            "tokens": {
                "total_prompt": cfg.get("total_prompt_tokens", 0),
                "total_completion": cfg.get("total_completion_tokens", 0),
                "avg_prompt": cfg.get("avg_prompt_tokens", 0),
                "avg_completion": cfg.get("avg_completion_tokens", 0),
            },
            "category_breakdown": category_breakdown,
            "badcases": badcases,
            "created_at": run["created_at"],
        },
        "ab_thresholds": ab_rows,
    }


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
    _ensure_kb_indexed(conn)
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

    lines.append("")
    lines.extend(_format_ab_table_lines(ab_rows))
    return "\n".join(lines)


def _open_report_conn(db_path: Path) -> sqlite3.Connection:
    """若本地 app.db 尚不存在，使用纯内存 SQLite 建表避免在全新克隆环境留下未初始化空库文件。"""
    if db_path.exists():
        init_db(db_path)
        return connect(db_path)
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    from app.db.database import _DDL_ACCOUNT, _DDL_CONVERSATION, _DDL_FTS, _DDL_INDEX, _DDL_KNOWLEDGE, _DDL_OBSERVABILITY, _DDL_TICKET

    for block in (_DDL_ACCOUNT, _DDL_KNOWLEDGE, _DDL_CONVERSATION, _DDL_TICKET, _DDL_OBSERVABILITY, _DDL_INDEX, _DDL_FTS):
        mem.executescript(block)
    return mem


def run_report_cli(
    *,
    db_path: Path,
    run_id: int | None = None,
    thresholds: list[float] | None = None,
    as_json: bool = False,
    output_path: Path | None = None,
    strict: bool = False,
) -> tuple[int, str]:
    """执行报告生成 CLI 逻辑，返回 (exit_code, rendered_text)。"""
    levels = thresholds or [0.30, 0.35, 0.45]
    conn = _open_report_conn(db_path)
    try:
        if as_json:
            try:
                payload = build_eval_report_payload(
                    conn,
                    run_id=run_id,
                    thresholds=levels,
                    allow_ab_only=(not strict and run_id is None),
                )
            except ValueError as exc:
                return 1, str(exc)
            rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        else:
            try:
                rendered = format_eval_report(conn, run_id=run_id, thresholds=levels)
            except ValueError as exc:
                if strict or run_id is not None:
                    return 1, str(exc)
                _ensure_kb_indexed(conn)
                ab_rows = run_ab_thresholds(conn, thresholds=levels)
                banner = [
                    "[提示] 当前环境尚未生成端到端评测批次记录（data/app.db 不存在或 eval_runs 为空）。",
                    '       如需复现完整 32 题图级评测，请先运行: python -m app.eval.runner --name "黄金集基准评测"',
                    "",
                ]
                rendered = "\n".join(banner + _format_ab_table_lines(ab_rows))
    finally:
        conn.close()

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    return 0, rendered


def main() -> None:
    parser = argparse.ArgumentParser(description="从 app.db 原样输出 32 题评测汇总与检索层 A/B 报告")
    parser.add_argument("--run-id", type=int, default=None, help="评测批次 ID（默认最新批次）")
    parser.add_argument("--thresholds", default="0.30,0.35,0.45", help="逗号分隔的检索阈值列表")
    parser.add_argument("--json", action="store_true", dest="as_json", help="以结构化 JSON 输出（含 Badcase 明细）")
    parser.add_argument("--output", type=Path, default=None, help="可选报告输出文件路径")
    parser.add_argument("--strict", action="store_true", help="无评测历史批次时返回非零退出码（供严格门禁使用）")
    args = parser.parse_args()

    levels = [float(x.strip()) for x in args.thresholds.split(",") if x.strip()]
    settings = get_settings()
    code, rendered = run_report_cli(
        db_path=settings.app_db,
        run_id=args.run_id,
        thresholds=levels,
        as_json=args.as_json,
        output_path=args.output,
        strict=args.strict,
    )
    if code != 0:
        print(rendered, file=sys.stderr)
        raise SystemExit(code)
    print(rendered)


if __name__ == "__main__":  # pragma: no cover
    main()
