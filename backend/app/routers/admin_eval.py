"""管理员观测看板与 32 题黄金集评测路由（全部仅限 admin）。"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.auth.deps import AdminOnly
from app.config import Settings, get_settings
from app.db.database import connect
from app.db.deps import get_db
from app.eval.runner import load_dataset, run_eval
from app.schemas.admin_eval import (
    EvalCaseResultOut,
    EvalRunDetailOut,
    EvalRunSummaryOut,
    EvalRunTriggerRequest,
    EvalRunTriggerResponse,
    TraceAggregateOut,
    TraceCleanupRequest,
    TraceCleanupResponse,
    TraceItemOut,
    TraceListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["管理观测与评测"])


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _safe_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _eval_summary_out(row: sqlite3.Row) -> EvalRunSummaryOut:
    cfg = _safe_json(row["config_json"], {})
    cfg_dict = cfg if isinstance(cfg, dict) else {}
    keys = row.keys()
    col_status = row["status"] if "status" in keys and row["status"] else None
    st = col_status or cfg_dict.get("status", "completed")
    # 兼容存量行：优先读专用列，缺失时回退 config_json
    safe_routing = (
        row["safe_routing_rate"]
        if "safe_routing_rate" in keys and row["safe_routing_rate"] is not None
        else cfg_dict.get("safe_routing_rate")
    )
    tool_ops = (
        row["tool_ops_acc"]
        if "tool_ops_acc" in keys and row["tool_ops_acc"] is not None
        else cfg_dict.get("tool_ops_acc")
    )
    hitl_acc = (
        row["hitl_escalate_acc"]
        if "hitl_escalate_acc" in keys and row["hitl_escalate_acc"] is not None
        else cfg_dict.get("hitl_escalate_acc")
    )
    # 不再把历史 avg_faithfulness 静默伪装成新列 citation_hit_rate，便于前端准确标识旧批次
    citation_rate = (
        row["citation_hit_rate"]
        if "citation_hit_rate" in keys and row["citation_hit_rate"] is not None
        else cfg_dict.get("citation_hit_rate")
    )
    err_cases = (
        int(row["error_cases"])
        if "error_cases" in keys and row["error_cases"] is not None
        else int(cfg_dict.get("error_cases") or 0)
    )
    col_judge_mode = (
        row["judge_mode"]
        if "judge_mode" in keys and row["judge_mode"]
        else cfg_dict.get("judge_mode", "citation_heuristic")
    )
    return EvalRunSummaryOut(
        id=row["id"],
        run_name=row["run_name"],
        config_json=cfg_dict or None,
        total_cases=row["total_cases"],
        passed_cases=cfg_dict.get("passed_cases"),
        error_cases=err_cases,
        intent_acc=row["intent_acc"],
        refusal_acc=row["refusal_acc"],
        safe_routing_rate=safe_routing,
        security_block_rate=row["security_block_rate"],
        tool_acc=row["tool_acc"],
        tool_ops_acc=tool_ops,
        hitl_escalate_acc=hitl_acc,
        avg_faithfulness=row["avg_faithfulness"],
        citation_hit_rate=citation_rate,
        judge_mode=str(col_judge_mode),
        avg_latency_ms=row["avg_latency_ms"],
        status=str(st),
        created_at=row["created_at"],
    )


@router.get("/traces", response_model=TraceListResponse, summary="全链路 Trace 分页查询与聚合统计（仅管理员）")
def list_traces(
    admin: AdminOnly,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    intent: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    source: Literal["all", "user", "eval"] = Query(
        default="all",
        description="按来源过滤：all=全部，user=仅非评测流量，eval=仅评测跑批(eval-*)",
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> TraceListResponse:
    _ = admin
    base_conditions: list[str] = []
    base_params: list[object] = []
    if intent:
        base_conditions.append("intent = ?")
        base_params.append(intent)
    if decision:
        base_conditions.append("decision = ?")
        base_params.append(decision)

    conditions = list(base_conditions)
    params = list(base_params)
    if source == "eval":
        conditions.append("conversation_id LIKE 'eval-%'")
    elif source == "user":
        conditions.append("(conversation_id IS NULL OR conversation_id NOT LIKE 'eval-%')")

    base_where_sql = (" WHERE " + " AND ".join(base_conditions)) if base_conditions else ""
    where_sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = int(
        conn.execute(f"SELECT COUNT(*) AS n FROM agent_traces{where_sql}", params).fetchone()["n"]
    )
    rows = conn.execute(
        f"""
        SELECT * FROM agent_traces{where_sql}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()

    agg_row = conn.execute(
        f"""
        SELECT COUNT(*) AS total_calls,
               COALESCE(AVG(latency_ms), 0.0) AS avg_latency_ms,
               COALESCE(SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)), 0) AS total_tokens
        FROM agent_traces{where_sql}
        """,
        params,
    ).fetchone()

    # 跨源流量计数基于 intent/decision 基线统计，使前端默认切到 source=user 时仍能感知评测跑批积压量
    source_split_row = conn.execute(
        f"""
        SELECT COALESCE(SUM(CASE WHEN conversation_id LIKE 'eval-%' THEN 1 ELSE 0 END), 0) AS eval_calls,
               COALESCE(SUM(CASE WHEN conversation_id IS NULL OR conversation_id NOT LIKE 'eval-%' THEN 1 ELSE 0 END), 0) AS user_calls
        FROM agent_traces{base_where_sql}
        """,
        base_params,
    ).fetchone()

    dist_rows = conn.execute(
        f"""
        SELECT COALESCE(decision, 'unknown') AS dec, COUNT(*) AS cnt
        FROM agent_traces{where_sql}
        GROUP BY dec
        """,
        params,
    ).fetchall()

    items = [
        TraceItemOut(
            id=r["id"],
            conversation_id=r["conversation_id"],
            user_id=r["user_id"],
            intent=r["intent"],
            rag_hit_count=r["rag_hit_count"],
            rag_top_score=r["rag_top_score"],
            tool_calls=_safe_json(r["tool_calls_json"], []),
            decision=r["decision"],
            latency_ms=r["latency_ms"],
            prompt_tokens=r["prompt_tokens"],
            completion_tokens=r["completion_tokens"],
            error_msg=r["error_msg"],
            created_at=r["created_at"],
        )
        for r in rows
    ]

    return TraceListResponse(
        items=items,
        total=total,
        aggregate=TraceAggregateOut(
            total_calls=int(agg_row["total_calls"]),
            user_calls=int(source_split_row["user_calls"]),
            eval_calls=int(source_split_row["eval_calls"]),
            avg_latency_ms=round(float(agg_row["avg_latency_ms"]), 2),
            total_tokens=int(agg_row["total_tokens"]),
            decision_distribution={r["dec"]: int(r["cnt"]) for r in dist_rows},
        ),
    )


@router.post(
    "/traces/cleanup",
    response_model=TraceCleanupResponse,
    summary="按来源与保留天数清理历史 Trace（保护未关闭活跃会话，仅管理员）",
)
def cleanup_traces(
    payload: TraceCleanupRequest,
    admin: AdminOnly,
    conn: sqlite3.Connection = Depends(get_db),
) -> TraceCleanupResponse:
    _ = admin
    conditions: list[str] = []
    params: list[object] = []

    if payload.source == "eval":
        conditions.append("conversation_id LIKE 'eval-%'")
    elif payload.source == "user":
        conditions.append("(conversation_id IS NULL OR conversation_id NOT LIKE 'eval-%')")

    # 安全保护：当范围涉及非评测流量时，绝不删除仍处于处理中/排队中的活跃会话关联 Trace
    if payload.source in ("user", "all"):
        conditions.append(
            "(conversation_id IS NULL OR conversation_id NOT IN "
            "(SELECT id FROM conversations WHERE status != 'closed'))"
        )

    cutoff_utc: str | None = None
    if payload.older_than_days > 0:
        cutoff_utc = (
            datetime.now(timezone.utc) - timedelta(days=payload.older_than_days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        conditions.append("created_at < ?")
        params.append(cutoff_utc)

    where_sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    id_rows = conn.execute(f"SELECT id FROM agent_traces{where_sql}", params).fetchall()
    target_ids = [int(r["id"]) for r in id_rows]

    if target_ids and not payload.dry_run:
        placeholders = ",".join("?" for _ in target_ids)
        conn.execute(
            f"UPDATE messages SET trace_id = NULL WHERE trace_id IN ({placeholders})",
            target_ids,
        )
        conn.execute(
            f"DELETE FROM agent_traces WHERE id IN ({placeholders})",
            target_ids,
        )
        conn.commit()

    rem = conn.execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN conversation_id LIKE 'eval-%' THEN 1 ELSE 0 END), 0) AS eval_calls,
               COALESCE(SUM(CASE WHEN conversation_id IS NULL OR conversation_id NOT LIKE 'eval-%' THEN 1 ELSE 0 END), 0) AS user_calls
        FROM agent_traces
        """
    ).fetchone()

    return TraceCleanupResponse(
        deleted_count=len(target_ids),
        dry_run=payload.dry_run,
        cutoff_utc=cutoff_utc,
        remaining_total=int(rem["total"]),
        remaining_user_calls=int(rem["user_calls"]),
        remaining_eval_calls=int(rem["eval_calls"]),
    )


async def _run_eval_background(
    *,
    run_id: int,
    run_name: str,
    limit: int | None,
    user_id: int,
    role: str,
    settings: Settings,
    overall_timeout_seconds: float = 4200.0,
) -> None:
    conn = connect(settings.app_db)
    try:
        await asyncio.wait_for(
            run_eval(
                conn,
                checkpoint_db=settings.checkpoint_db,
                user_id=user_id,
                role=role,
                run_name=run_name,
                limit=limit,
                settings=settings,
                run_id=run_id,
                db_path=settings.app_db,
            ),
            timeout=overall_timeout_seconds,
        )
    except BaseException as exc:  # 捕获包括 TimeoutError / CancelledError 在内的异常，保证状态不滞留 running
        logger.exception("后台评测任务 #%s 执行失败", run_id)
        try:
            conn.execute(
                "UPDATE eval_runs SET status = 'failed', config_json = ? WHERE id = ?",
                (
                    json.dumps(
                        {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
                        ensure_ascii=False,
                    ),
                    run_id,
                ),
            )
            conn.commit()
        except Exception:
            logger.exception("标记评测任务 #%s 失败状态时异常", run_id)
        if not isinstance(exc, Exception):
            raise
    finally:
        conn.close()


@router.post(
    "/eval/runs",
    response_model=EvalRunTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="后台启动 32 题黄金集评测（仅管理员）",
)
async def trigger_eval_run(
    payload: EvalRunTriggerRequest,
    background_tasks: BackgroundTasks,
    admin: AdminOnly,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EvalRunTriggerResponse:
    customer = conn.execute(
        "SELECT id, role FROM users WHERE username = 'zhangsan'"
    ).fetchone()
    eval_user_id = int(customer["id"]) if customer else int(admin["id"])
    eval_role = str(customer["role"]) if customer else str(admin["role"])

    run_name = (payload.run_name or "黄金集评测").strip()
    planned = len(load_dataset(limit=payload.limit))
    now = _now_text()
    cur = conn.execute(
        """
        INSERT INTO eval_runs (run_name, config_json, status, total_cases, created_at)
        VALUES (?, ?, 'running', ?, ?)
        """,
        (run_name, json.dumps({"status": "running"}, ensure_ascii=False), planned, now),
    )
    conn.commit()
    run_id = int(cur.lastrowid)

    # 按计划题数动态计算整批超时预算（每题最多 run + resume 两阶段 * 120s + 300s 缓冲）
    overall_budget = max(1800.0, planned * 240.0 + 300.0)
    background_tasks.add_task(
        _run_eval_background,
        run_id=run_id,
        run_name=run_name,
        limit=payload.limit,
        user_id=eval_user_id,
        role=eval_role,
        settings=settings,
        overall_timeout_seconds=overall_budget,
    )
    return EvalRunTriggerResponse(run_id=run_id, status="running")


@router.get("/eval/runs", response_model=list[EvalRunSummaryOut], summary="评测历史列表（仅管理员）")
def list_eval_runs(
    admin: AdminOnly,
    conn: sqlite3.Connection = Depends(get_db),
) -> list[EvalRunSummaryOut]:
    _ = admin
    rows = conn.execute("SELECT * FROM eval_runs ORDER BY id DESC").fetchall()
    return [_eval_summary_out(r) for r in rows]


@router.get("/eval/runs/{run_id}", response_model=EvalRunDetailOut, summary="单次评测详情与逐题明细（仅管理员）")
def get_eval_run(
    run_id: int,
    admin: AdminOnly,
    conn: sqlite3.Connection = Depends(get_db),
) -> EvalRunDetailOut:
    _ = admin
    row = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="评测记录不存在")

    case_rows = conn.execute(
        "SELECT * FROM eval_case_results WHERE run_id = ? ORDER BY id ASC", (run_id,)
    ).fetchall()
    cases = [
        EvalCaseResultOut(
            id=c["id"],
            case_id=c["case_id"],
            category=c["category"],
            passed=bool(c["passed"]),
            expected=_safe_json(c["expected_json"], {}),
            actual=_safe_json(c["actual_json"], {}),
            judge_score=c["judge_score"],
            reason=c["reason"],
        )
        for c in case_rows
    ]
    return EvalRunDetailOut(**_eval_summary_out(row).model_dump(), cases=cases)
