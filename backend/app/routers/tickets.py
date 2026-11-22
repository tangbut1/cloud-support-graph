"""工单路由：列表、详情、创建、状态流转。

权限设计（这是本项目防越权的第二道防线，第一道在 MCP 工具层）：
    * 客户只能看到 customer_id = 自己的工单 —— 越权访问他人工单返回 404 而不是 403，
      避免"工单存在与否"这种信息泄露；
    * 只有坐席/管理员能改状态（业务上客户不能自己把自己的工单标成"已解决"）；
    * 交接给状态机之前，路由层不判断"A 能不能变成 B"，只判断"这个人有没有资格改"
      —— 职责分离：权限归路由，规则归 services/ticket_sm.py。
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.deps import AgentOrAdmin, CurrentUser
from app.db.deps import get_db
from app.schemas.ticket import (
    TicketCreateRequest,
    TicketDetailOut,
    TicketEventOut,
    TicketOut,
    TicketTransitionRequest,
)
from app.services.ticket_sm import (
    InvalidTransitionError,
    TicketNotFoundError,
    TicketValidationError,
    create_ticket,
    next_statuses,
    transition_ticket,
)

router = APIRouter(prefix="/api/tickets", tags=["工单"])


# ---------------------------------------------------------------------------
# 行 -> 响应模型
# ---------------------------------------------------------------------------
def _ticket_out(row: sqlite3.Row) -> TicketOut:
    return TicketOut(
        id=row["id"],
        ticket_no=row["ticket_no"],
        customer_id=row["customer_id"],
        instance_id=row["instance_id"],
        assignee_id=row["assignee_id"],
        category=row["category"],
        priority=row["priority"],
        status=row["status"],
        title=row["title"],
        description=row["description"],
        ai_diagnosis=row["ai_diagnosis"],
        resolution_note=row["resolution_note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event_out(row: sqlite3.Row) -> TicketEventOut:
    return TicketEventOut(
        id=row["id"],
        operator_id=row["operator_id"],
        from_status=row["from_status"],
        to_status=row["to_status"],
        comment=row["comment"],
        created_at=row["created_at"],
    )


def _ticket_detail(conn: sqlite3.Connection, ticket_id: int) -> TicketDetailOut:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    events = conn.execute(
        "SELECT * FROM ticket_events WHERE ticket_id = ? ORDER BY id", (ticket_id,)
    ).fetchall()
    return TicketDetailOut(**_ticket_out(row).model_dump(), events=[_event_out(e) for e in events])


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
@router.get("", response_model=list[TicketOut], summary="工单列表（客户只能看到自己的）")
def list_tickets(
    user: CurrentUser,
    status_filter: str | None = Query(default=None, alias="status", description="按状态筛选"),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[TicketOut]:
    sql = "SELECT * FROM tickets"
    conditions: list[str] = []
    params: list[object] = []

    if user["role"] == "customer":
        conditions.append("customer_id = ?")
        params.append(user["id"])
    if status_filter:
        conditions.append("status = ?")
        params.append(status_filter)

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY id DESC"

    return [_ticket_out(row) for row in conn.execute(sql, params).fetchall()]


@router.get("/{ticket_id}", response_model=TicketDetailOut, summary="工单详情（含状态时间轴）")
def get_ticket(
    ticket_id: int,
    user: CurrentUser,
    conn: sqlite3.Connection = Depends(get_db),
) -> TicketDetailOut:
    row = conn.execute("SELECT customer_id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    # 客户越权访问他人工单：按"不存在"处理，不泄露存在性
    if user["role"] == "customer" and row["customer_id"] != user["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    return _ticket_detail(conn, ticket_id)


@router.post("", response_model=TicketOut, status_code=status.HTTP_201_CREATED, summary="创建工单")
def create(
    payload: TicketCreateRequest,
    user: CurrentUser,
    conn: sqlite3.Connection = Depends(get_db),
) -> TicketOut:
    try:
        row = create_ticket(
            conn,
            customer_id=user["id"],
            title=payload.title,
            description=payload.description,
            instance_id=payload.instance_id,
            category=payload.category,
            priority=payload.priority,
        )
    except TicketValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _ticket_out(row)


@router.patch(
    "/{ticket_id}/transition",
    response_model=TicketDetailOut,
    summary="工单状态流转（仅坐席/管理员）",
)
def transition(
    ticket_id: int,
    payload: TicketTransitionRequest,
    operator: AgentOrAdmin,
    conn: sqlite3.Connection = Depends(get_db),
) -> TicketDetailOut:
    try:
        transition_ticket(
            conn,
            ticket_id=ticket_id,
            to_status=payload.to_status,
            operator_id=operator["id"],
            comment=payload.comment,
        )
    except TicketNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        # 409 Conflict：请求本身合法，但与当前资源状态冲突
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _ticket_detail(conn, ticket_id)


@router.get(
    "/{ticket_id}/next-statuses",
    response_model=list[str],
    summary="查看当前状态的合法下一状态",
)
def get_next_statuses(
    ticket_id: int,
    user: CurrentUser,
    conn: sqlite3.Connection = Depends(get_db),
) -> list[str]:
    row = conn.execute("SELECT customer_id, status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    if user["role"] == "customer" and row["customer_id"] != user["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    return next_statuses(row["status"])
