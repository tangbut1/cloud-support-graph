"""工单状态机测试（对应 DEV_SPEC §8.1 的 test_ticket_sm.py）。

这组测试是第 2 天最核心的验收：
    * 每条合法跃迁必须能走通，且写一条审计事件；
    * 每条非法跃迁必须被拒，且**数据库不留任何痕迹**（状态没改、事件没写）；
    * 终态不可再变；
    * 工单号按天递增、格式固定。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from app.services.ticket_sm import (
    ALLOWED_TRANSITIONS,
    TICKET_STATUSES,
    InvalidTransitionError,
    TicketNotFoundError,
    TicketValidationError,
    create_ticket,
    is_valid_transition,
    next_statuses,
    transition_ticket,
    validate_transition,
)
from tests.conftest import make_instance, make_user


@pytest.fixture()
def customer_id(conn) -> int:
    return make_user(conn, "zhangsan", "customer", "客户张三")


@pytest.fixture()
def agent_id(conn) -> int:
    return make_user(conn, "wanggong", "agent", "技术支持坐席王工")


@pytest.fixture()
def ticket_id(conn, customer_id) -> int:
    row = create_ticket(conn, customer_id=customer_id, title="网站打不开，报 502")
    return int(row["id"])


# ===========================================================================
# 一、状态跃迁规则本身（纯函数，不碰数据库）
# ===========================================================================
def test_all_declared_transitions_are_valid():
    """ALLOWED_TRANSITIONS 里写明的每条跃迁，校验函数都必须放行。"""
    for from_status, targets in ALLOWED_TRANSITIONS.items():
        for to_status in targets:
            assert is_valid_transition(from_status, to_status) is True


def test_open_cannot_jump_to_closed():
    """OPEN 直接跳 CLOSED 跳过了排查全过程，必须拒绝。"""
    assert is_valid_transition("OPEN", "CLOSED") is False
    with pytest.raises(InvalidTransitionError) as excinfo:
        validate_transition("OPEN", "CLOSED")
    message = str(excinfo.value)
    assert "OPEN" in message and "CLOSED" in message
    assert "TRIAGED" in message          # 报错要告诉调用方合法选项是什么


def test_unknown_status_is_rejected():
    with pytest.raises(InvalidTransitionError):
        validate_transition("OPEN", "不存在的状态")


def test_closed_is_terminal():
    assert next_statuses("CLOSED") == []
    for target in TICKET_STATUSES:
        assert is_valid_transition("CLOSED", target) is False


def test_resolved_can_be_reopened():
    """客户反馈没解决时，要能把工单从 RESOLVED 拉回 IN_PROGRESS。"""
    assert is_valid_transition("RESOLVED", "IN_PROGRESS") is True


# ===========================================================================
# 二、创建工单
# ===========================================================================
def test_create_ticket_starts_as_open_with_first_event(conn, customer_id):
    row = create_ticket(conn, customer_id=customer_id, title="Nginx 502")
    assert row["status"] == "OPEN"
    assert row["ticket_no"].startswith("CSG-")

    events = conn.execute(
        "SELECT * FROM ticket_events WHERE ticket_id = ? ORDER BY id", (row["id"],)
    ).fetchall()
    assert len(events) == 1
    assert events[0]["from_status"] is None       # 从"无"到 OPEN
    assert events[0]["to_status"] == "OPEN"
    assert events[0]["operator_id"] == customer_id


def test_ticket_no_is_daily_sequential(conn, customer_id):
    first = create_ticket(conn, customer_id=customer_id, title="问题一")
    second = create_ticket(conn, customer_id=customer_id, title="问题二")

    assert re.fullmatch(r"CSG-\d{8}-\d{4}", first["ticket_no"])
    assert second["ticket_no"].endswith("-0002")


def test_ticket_no_does_not_reuse_numbers_after_deletion(conn, customer_id):
    """删除中间某张工单后，新单号必须接着"已用过的最大号"，不能回退重号。

    回归测试：旧实现用 COUNT(*)+1，删掉 0001 后新单号会变回 0002，
    与已存在的 0002 撞 UNIQUE 约束，创建工单直接 500。
    """
    first = create_ticket(conn, customer_id=customer_id, title="问题一")
    second = create_ticket(conn, customer_id=customer_id, title="问题二")
    assert second["ticket_no"].endswith("-0002")

    conn.execute("DELETE FROM tickets WHERE id = ?", (first["id"],))
    conn.commit()

    third = create_ticket(conn, customer_id=customer_id, title="问题三")
    assert third["ticket_no"].endswith("-0003")


def test_timestamps_are_stored_in_utc(conn, customer_id):
    """创建时间必须是 UTC，与 database.py 中 datetime('now') 的基准一致。

    回归测试：旧实现用 datetime.now()（本地时间），在东八区会比 UTC 快 8 小时，
    导致 tickets 与 users/cloud_instances 的时间无法直接比较。
    """
    row = create_ticket(conn, customer_id=customer_id, title="时间基准检查")
    stored = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    delta = abs((datetime.now(timezone.utc) - stored).total_seconds())
    assert delta < 60, f"created_at 与 UTC 当前时间相差 {delta:.0f} 秒，疑似写入了本地时间"


def test_create_ticket_rejects_foreign_instance(conn, customer_id):
    """关联别人家的服务器 -> 拒绝（否则就成了通过工单写越权关联）。"""
    other = make_user(conn, "lisi", "customer")
    foreign_instance = make_instance(conn, other, "finance-api-node")
    with pytest.raises(TicketValidationError):
        create_ticket(
            conn, customer_id=customer_id, title="越权", instance_id=foreign_instance
        )


# ===========================================================================
# 三、合法跃迁：状态改变 + 审计事件
# ===========================================================================
def test_valid_transition_updates_status_and_writes_event(conn, ticket_id, agent_id):
    row = transition_ticket(
        conn, ticket_id=ticket_id, to_status="TRIAGED", operator_id=agent_id, comment="已分诊"
    )
    assert row["status"] == "TRIAGED"

    events = conn.execute(
        "SELECT * FROM ticket_events WHERE ticket_id = ? ORDER BY id", (ticket_id,)
    ).fetchall()
    assert [e["to_status"] for e in events] == ["OPEN", "TRIAGED"]
    # 第一次流转必然从 OPEN 出发；这里必须是严格断言，不能用 or 放宽成恒真条件
    assert events[-1]["from_status"] == "OPEN"
    assert events[-1]["comment"] == "已分诊"
    assert events[-1]["operator_id"] == agent_id


def test_full_lifecycle_open_to_closed(conn, ticket_id, agent_id):
    """完整走一遍生命周期，每一步都合法，审计轴应有 5 条记录。"""
    for target in ("TRIAGED", "IN_PROGRESS", "RESOLVED", "CLOSED"):
        transition_ticket(conn, ticket_id=ticket_id, to_status=target, operator_id=agent_id)

    row = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert row["status"] == "CLOSED"

    events = conn.execute(
        "SELECT * FROM ticket_events WHERE ticket_id = ? ORDER BY id", (ticket_id,)
    ).fetchall()
    assert len(events) == 5                                  # 创建 + 4 次流转
    assert events[-1]["from_status"] == "RESOLVED"
    assert events[-1]["to_status"] == "CLOSED"


# ===========================================================================
# 四、非法跃迁：必须"什么都没发生"
# ===========================================================================
def test_illegal_transition_leaves_database_untouched(conn, ticket_id, agent_id):
    before_ticket = conn.execute("SELECT status, updated_at FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    before_events = conn.execute(
        "SELECT COUNT(*) AS n FROM ticket_events WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()["n"]

    with pytest.raises(InvalidTransitionError):
        transition_ticket(conn, ticket_id=ticket_id, to_status="CLOSED", operator_id=agent_id)

    after_ticket = conn.execute("SELECT status, updated_at FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    after_events = conn.execute(
        "SELECT COUNT(*) AS n FROM ticket_events WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()["n"]

    assert before_ticket["status"] == after_ticket["status"] == "OPEN"
    assert before_ticket["updated_at"] == after_ticket["updated_at"]   # 连时间戳都没动
    assert before_events == after_events                                # 没有脏审计


def test_transition_on_missing_ticket_raises_not_found(conn, agent_id):
    with pytest.raises(TicketNotFoundError):
        transition_ticket(conn, ticket_id=99999, to_status="TRIAGED", operator_id=agent_id)


def test_closed_ticket_rejects_further_transitions(conn, ticket_id, agent_id):
    for target in ("TRIAGED", "IN_PROGRESS", "RESOLVED", "CLOSED"):
        transition_ticket(conn, ticket_id=ticket_id, to_status=target, operator_id=agent_id)

    with pytest.raises(InvalidTransitionError):
        transition_ticket(conn, ticket_id=ticket_id, to_status="IN_PROGRESS", operator_id=agent_id)
