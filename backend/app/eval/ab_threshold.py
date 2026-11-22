"""可复现的 RAG_MIN_SCORE 检索层阈值 A/B 对比脚本（对应第 5 章阈值实验）。

口径说明：
    本脚本直接在检索层（`retriever.search`）对黄金集中的 18 道知识库题（10 可答 + 8 不可答）
    对比不同 `RAG_MIN_SCORE` 阈值下的可答放行率与不可答拒答率，不涉及端到端大模型意图分类。
    端到端图级评测请运行 `python -m app.eval.runner`。

用法：
    cd backend
    .\\.venv\\Scripts\\python.exe -m app.eval.ab_threshold --thresholds 0.30,0.35,0.45
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db.database import connect, init_db
from app.eval.runner import load_dataset
from app.services.kb_indexer import index_all
from app.services.retriever import search


def run_ab_thresholds(
    conn: sqlite3.Connection,
    thresholds: list[float] | None = None,
    dataset_path: Path | None = None,
) -> list[dict[str, Any]]:
    """对黄金集 18 道知识库题执行多阈值检索 A/B，返回结构化对比结果。"""
    levels = thresholds or [0.30, 0.35, 0.45]
    cases = load_dataset(dataset_path)
    kb_ans = [c for c in cases if c["category"] == "kb_answerable"]
    kb_ref = [c for c in cases if c["category"] == "kb_unanswerable"]

    results: list[dict[str, Any]] = []
    for th in levels:
        ans_pass = 0
        ref_pass = 0
        leaked_ids: list[str] = []
        missed_ids: list[str] = []

        for c in kb_ans:
            res = search(conn, c["query"], min_score=th)
            if res.accepted:
                ans_pass += 1
            else:
                missed_ids.append(c["case_id"])

        for c in kb_ref:
            res = search(conn, c["query"], min_score=th)
            if not res.accepted:
                ref_pass += 1
            else:
                leaked_ids.append(c["case_id"])

        total = len(kb_ans) + len(kb_ref)
        results.append(
            {
                "threshold": th,
                "answerable_pass": ans_pass,
                "answerable_total": len(kb_ans),
                "answerable_rate": round(ans_pass / len(kb_ans), 4) if kb_ans else 0.0,
                "refusal_pass": ref_pass,
                "refusal_total": len(kb_ref),
                "refusal_rate": round(ref_pass / len(kb_ref), 4) if kb_ref else 0.0,
                "overall_acc": round((ans_pass + ref_pass) / total, 4) if total else 0.0,
                "leaked_unanswerable": leaked_ids,
                "missed_answerable": missed_ids,
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="检索层 RAG_MIN_SCORE 多阈值 A/B 实验")
    parser.add_argument("--thresholds", default="0.30,0.35,0.45", help="逗号分隔的阈值列表")
    parser.add_argument("--json", action="store_true", dest="as_json", help="以 JSON 格式输出")
    args = parser.parse_args()

    levels = [float(x.strip()) for x in args.thresholds.split(",") if x.strip()]
    settings = get_settings()
    init_db(settings.app_db)
    conn = connect(settings.app_db)
    try:
        doc_count = conn.execute("SELECT COUNT(*) AS n FROM kb_documents").fetchone()["n"]
        if doc_count == 0:
            index_all(conn)
        rows = run_ab_thresholds(conn, thresholds=levels)
    finally:
        conn.close()

    if args.as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    print(f"{'RAG_MIN_SCORE':<15s} {'可答放行(10题)':<18s} {'不可答拒答(8题)':<18s} {'综合准确率(18题)':<18s} 误放行/误拒")
    print("-" * 86)
    for r in rows:
        notes = []
        if r["leaked_unanswerable"]:
            notes.append("误放行:" + ",".join(r["leaked_unanswerable"]))
        if r["missed_answerable"]:
            notes.append("误拒:" + ",".join(r["missed_answerable"]))
        print(
            f"{r['threshold']:<15.2f} "
            f"{r['answerable_pass']}/{r['answerable_total']} ({r['answerable_rate']*100:5.1f}%)       "
            f"{r['refusal_pass']}/{r['refusal_total']} ({r['refusal_rate']*100:5.1f}%)       "
            f"{r['overall_acc']*100:5.1f}%             "
            f"{'; '.join(notes) or '-'}"
        )


if __name__ == "__main__":  # pragma: no cover
    main()
