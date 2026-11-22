"""坐席工作台路由：待接管队列、HITL 审批恢复、人工回复。

权限：全部仅限 agent / admin（由 AgentOrAdmin 依赖统一拦截，客户访问必返 403）。
并发与容错保障：
    1. 按 conversation_id 加进程内异步互斥锁，防止并发双击审批重复建单；
    2. 恢复前严格校验检查点 `hitl` 挂起状态：
       - 若检查点失效，批准建单返回 409（提示改用仅接管）；
       - 仅接管（takeover_only）降级为强制接管，保证队列绝不永久卡死；
    3. `resume_turn` 与后续落库均受异常兜底与孤儿工单补偿清理保护。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import weakref
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.agent.graph import build_graph, make_config, open_checkpointer, resume_turn
from app.auth.deps import AgentOrAdmin
from app.config import Settings, get_settings
from app.db.deps import get_db
from app.mcp.auth_context import create_auth_context
from app.routers.conversations import parse_handoff_blob
from app.schemas.conversation import (
    AgentQueueItemOut,
    AgentReplyRequest,
    AgentReplyResponse,
    AgentResumeRequest,
    AgentResumeResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["坐席工作台"])

# 按 conversation_id 串行化审批恢复，防止并发重复建单（WeakValueDictionary 自动回收空闲锁，避免内存增长）
_RESUME_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _get_resume_lock(conversation_id: str) -> asyncio.Lock:
    lock = _RESUME_LOCKS.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _RESUME_LOCKS[conversation_id] = lock
    return lock


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _cleanup_orphan_tickets(
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    after_id: int | None = None,
    ticket_no: str | None = None,
) -> int:
    """补偿删除本次失败审批中新产生的孤儿工单（级联清理 ticket_events）。"""
    deleted = 0
    try:
        if ticket_no:
            cur = conn.execute("DELETE FROM tickets WHERE ticket_no = ?", (ticket_no,))
            deleted += cur.rowcount
        if after_id is not None:
            cur = conn.execute(
                "DELETE FROM tickets WHERE conversation_id = ? AND id > ?",
                (conversation_id, after_id),
            )
            deleted += cur.rowcount
        if deleted:
            conn.commit()
    except Exception:
        logger.exception(
            "补偿清理孤儿工单失败 conversation_id=%s ticket_no=%s",
            conversation_id,
            ticket_no,
        )
    return deleted


@router.get(
    "/queue",
    response_model=list[AgentQueueItemOut],
    summary="获取待接管会话队列（按 updated_at 升序）",
)
def get_queue(
    operator: AgentOrAdmin,
    conn: sqlite3.Connection = Depends(get_db),
) -> list[AgentQueueItemOut]:
    _ = operator
    rows = conn.execute(
        """
        SELECT c.id AS conversation_id, u.display_name AS customer_name,
               c.updated_at, c.ai_handoff_summary_json
        FROM conversations AS c
        JOIN users AS u ON u.id = c.customer_id
        WHERE c.status = 'pending_human'
        ORDER BY c.updated_at ASC, c.rowid ASC
        """
    ).fetchall()

    items: list[AgentQueueItemOut] = []
    for row in rows:
        summary, draft = parse_handoff_blob(row["ai_handoff_summary_json"])
        items.append(
            AgentQueueItemOut(
                conversation_id=row["conversation_id"],
                customer_name=row["customer_name"],
                updated_at=row["updated_at"],
                handoff_summary=summary or {},
                draft_ticket=draft,
            )
        )
    return items


@router.post(
    "/conversations/{conversation_id}/resume",
    response_model=AgentResumeResponse,
    summary="坐席审批并从 LangGraph 断点恢复执行（支持检查点失效时的强制接管）",
)
async def resume_conversation(
    conversation_id: str,
    payload: AgentResumeRequest,
    operator: AgentOrAdmin,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AgentResumeResponse:
    async with _get_resume_lock(conversation_id):
        conv = conn.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if conv is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        if conv["status"] != "pending_human":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"当前会话状态为 {conv['status']}，仅 pending_human 状态允许审批恢复",
            )

        pre_row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS max_id FROM agent_traces WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        prev_trace_id = int(pre_row["max_id"]) if pre_row else 0

        pre_tk_row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS max_id FROM tickets WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        prev_ticket_id = int(pre_tk_row["max_id"]) if pre_tk_row else 0

        auth_token = create_auth_context(
            user_id=operator["id"],
            role=operator["role"],
            secret=settings.jwt_secret,
            ttl_seconds=300,
        )
        cfg = make_config(
            thread_id=conv["thread_id"],
            db_path=settings.app_db,
            auth_context_token=auth_token,
        )

        decision_payload = {"action": payload.action, "comment": payload.comment or ""}
        async with open_checkpointer(settings.checkpoint_db) as saver:
            graph = build_graph(saver)
            # 恢复前严格校验检查点确实挂起在 hitl 节点
            snapshot = await graph.aget_state(cfg)
            next_nodes = set(snapshot.next or ()) if snapshot is not None else set()
            has_hitl_pending = "hitl" in next_nodes

            if not has_hitl_pending:
                # 兜底出口：若检查点丢失，takeover_only 降级为强制接管，防止队列永久卡死
                if payload.action == "takeover_only":
                    now = _now_text()
                    note = (
                        f"原审批检查点已失效，坐席已强制接管本会话转入人工处理。"
                        f"{('备注：' + payload.comment.strip()) if payload.comment and payload.comment.strip() else ''}"
                    )
                    conn.execute(
                        """
                        INSERT INTO messages (
                            conversation_id, sender_role, content, has_image,
                            citations_json, trace_id, created_at
                        ) VALUES (?, 'system', ?, 0, '[]', NULL, ?)
                        """,
                        (conversation_id, note, now),
                    )
                    conn.execute(
                        """
                        UPDATE conversations
                        SET status = 'human_handling',
                            assigned_agent_id = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (operator["id"], now, conversation_id),
                    )
                    conn.commit()
                    return AgentResumeResponse(
                        conversation_status="human_handling",
                        ticket_no=None,
                        message=note,
                        forced_takeover=True,
                    )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="该会话缺少待审批的 HITL 检查点（可能已恢复或检查点已失效），无法自动建单；如需人工跟进请点击「仅接管对话」强制接管",
                )

            try:
                out = await resume_turn(graph, decision_payload, cfg)
            except Exception as exc:
                # 若图内已建单但后续节点（如 trace）抛出异常，立即补偿清理本次孤儿工单
                cleaned = _cleanup_orphan_tickets(
                    conn, conversation_id, after_id=prev_ticket_id
                )
                logger.exception(
                    "坐席审批 resume_turn 执行异常 conversation_id=%s cleaned_orphan_tickets=%d",
                    conversation_id,
                    cleaned,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="审批恢复执行失败，已自动清理未完成工单，会话状态未变更，请稍后重试或选择仅接管对话",
                ) from exc

        ticket_no: str | None = out.get("created_ticket_no")
        final_answer = str(out.get("final_answer") or "")

        if payload.action == "approve_ticket_and_reply" and not ticket_no:
            _cleanup_orphan_tickets(conn, conversation_id, after_id=prev_ticket_id)
            logger.error(
                "approve_ticket_and_reply 恢复后未产出工单号 conversation_id=%s out_keys=%s",
                conversation_id,
                list(out.keys()),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="审批恢复未能创建工单，会话状态未变更，请稍后重试",
            )

        now = _now_text()
        # 原子落库 + 数据库级状态 CAS（WHERE status = 'pending_human'），
        # 即使多进程/多 Worker 并发审批也不会重复写入消息或误删已被先手认领的工单
        try:
            cas_cur = conn.execute(
                """
                UPDATE conversations
                SET status = 'human_handling',
                    assigned_agent_id = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'pending_human'
                """,
                (operator["id"], now, conversation_id),
            )
            if cas_cur.rowcount == 0:
                conn.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="该会话已被其他坐席处理，请刷新队列",
                )

            if payload.action == "approve_ticket_and_reply":
                trace_row = conn.execute(
                    """
                    SELECT id FROM agent_traces
                    WHERE conversation_id = ? AND id > ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (conversation_id, prev_trace_id),
                ).fetchone()
                trace_id = int(trace_row["id"]) if trace_row else None

                if ticket_no:
                    summary, _ = parse_handoff_blob(conv["ai_handoff_summary_json"])
                    diagnosis_text = (summary or {}).get("diagnosis")
                    conn.execute(
                        """
                        UPDATE tickets
                        SET assignee_id = ?,
                            conversation_id = COALESCE(conversation_id, ?),
                            ai_diagnosis = COALESCE(ai_diagnosis, ?)
                        WHERE ticket_no = ?
                        """,
                        (operator["id"], conversation_id, diagnosis_text, ticket_no),
                    )

                conn.execute(
                    """
                    INSERT INTO messages (
                        conversation_id, sender_role, content, has_image,
                        citations_json, trace_id, created_at
                    ) VALUES (?, 'assistant', ?, 0, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        final_answer or f"已完成审批，工单 {ticket_no or ''} 已创建。",
                        json.dumps([], ensure_ascii=False),
                        trace_id,
                        now,
                    ),
                )

            conn.commit()
        except HTTPException:
            raise
        except Exception as exc:
            conn.rollback()
            if payload.action == "approve_ticket_and_reply":
                _cleanup_orphan_tickets(
                    conn,
                    conversation_id,
                    after_id=prev_ticket_id,
                    ticket_no=ticket_no,
                )
            logger.exception("坐席审批后落库失败 conversation_id=%s", conversation_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="审批结果落库失败，已自动回滚并清理孤儿工单以保持状态一致",
            ) from exc

        return AgentResumeResponse(
            conversation_status="human_handling",
            ticket_no=ticket_no if payload.action == "approve_ticket_and_reply" else None,
            message=final_answer or "已接管该会话",
            forced_takeover=False,
        )


@router.post(
    "/conversations/{conversation_id}/reply",
    response_model=AgentReplyResponse,
    summary="坐席人工回复（可选同时关闭会话）",
)
def reply_conversation(
    conversation_id: str,
    payload: AgentReplyRequest,
    operator: AgentOrAdmin,
    conn: sqlite3.Connection = Depends(get_db),
) -> AgentReplyResponse:
    conv = conn.execute(
        "SELECT id, status FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    if conv["status"] != "human_handling":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"当前会话状态为 {conv['status']}，仅 human_handling 状态允许坐席回复",
        )

    now = _now_text()
    cur = conn.execute(
        """
        INSERT INTO messages (conversation_id, sender_role, content, has_image, created_at)
        VALUES (?, 'agent', ?, 0, ?)
        """,
        (conversation_id, payload.content, now),
    )
    message_id = int(cur.lastrowid)

    new_status = "closed" if payload.close else "human_handling"
    conn.execute(
        """
        UPDATE conversations
        SET status = ?,
            assigned_agent_id = COALESCE(assigned_agent_id, ?),
            updated_at = ?
        WHERE id = ?
        """,
        (new_status, operator["id"], now, conversation_id),
    )
    conn.commit()

    return AgentReplyResponse(
        message_id=message_id,
        conversation_status=new_status,
        created_at=now,
    )
