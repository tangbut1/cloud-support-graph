r"""工单状态机：合法跃迁校验 + 审计事件写入。

【什么是状态机】
    工单不是"想改成什么状态就改成什么"。它有一条固定的生命周期：
        OPEN -> TRIAGED -> IN_PROGRESS -> RESOLVED -> CLOSED
    把"允许的改变"写成一张表（ALLOWED_TRANSITIONS），任何不在表里的改动一律拒绝。
    这样"OPEN 直接跳 CLOSED"这种跳过排查过程的脏流转就不可能发生。

【为什么用代码而不是数据库 CHECK 约束】
    数据库 CHECK 只能校验"当前值是否合法"，无法表达"A 能不能变成 B"（它看不到旧值）。
    所以状态合法性在数据库层守，跃迁合法性在服务层守，两层配合。

【为什么每次流转都要写 ticket_events】
    工单在真实业务里是要追责的：谁在什么时候把它从哪个状态改到哪个状态、为什么改。
    ticket_events 是只追加、不修改的审计流水，第 4 章"工单状态时间轴"直接读它。

【本模块不依赖 FastAPI】
    纯业务逻辑 + 数据库，方便单测；HTTP 状态码/权限判定放在路由层。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 状态定义与允许的跃迁
# ---------------------------------------------------------------------------
TICKET_STATUSES: tuple[str, ...] = ("OPEN", "TRIAGED", "IN_PROGRESS", "RESOLVED", "CLOSED")

# 每个状态能去往哪些状态。frozenset = 不可变集合，防止运行时被意外修改。
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    # 刚创建，只能先被分诊
    "OPEN": frozenset({"TRIAGED"}),
    # 分诊后要么开始处理，要么判定为误报直接关闭
    "TRIAGED": frozenset({"IN_PROGRESS", "CLOSED"}),
    # 处理中：要么解决，要么关闭（客户放弃/无法复现）
    "IN_PROGRESS": frozenset({"RESOLVED", "CLOSED"}),
    # 已解决：可以关闭，也可以因为客户反馈未解决而重新打开
    "RESOLVED": frozenset({"CLOSED", "IN_PROGRESS"}),
    # 终态：不可再变
    "CLOSED": frozenset(),
}


class TicketError(Exception):
    """工单业务错误基类。"""


class TicketNotFoundError(TicketError):
    """工单不存在。"""


class InvalidTransitionError(TicketError):
    """非法状态跃迁。"""

    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        allowed = sorted(ALLOWED_TRANSITIONS.get(from_status, frozenset()))
        super().__init__(
            f"工单状态不能从 {from_status} 直接变为 {to_status}；"
            f"当前允许的下一状态: {', '.join(allowed) if allowed else '无（终态）'}"
        )


class TicketValidationError(TicketError):
    """参数校验失败（例如关联的实例不属于当前用户）。"""


# ---------------------------------------------------------------------------
# 校验函数
# ---------------------------------------------------------------------------
def next_statuses(current: str) -> list[str]:
    """返回当前状态的合法下一状态列表（前端按钮/文档展示用）。"""
    return sorted(ALLOWED_TRANSITIONS.get(current, frozenset()))


def is_valid_transition(from_status: str, to_status: str) -> bool:
    """判断跃迁是否合法；非法状态名一律视为非法。"""
    return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())


def validate_transition(from_status: str, to_status: str) -> None:
    """校验跃迁，非法则抛 InvalidTransitionError。"""
    if to_status not in TICKET_STATUSES:
        raise InvalidTransitionError(from_status, to_status)
    if not is_valid_transition(from_status, to_status):
        raise InvalidTransitionError(from_status, to_status)


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------
def _now_text() -> str:
    """统一的时间文本（UTC），与数据库 DEFAULT datetime('now') 保持一致。

    为什么必须是 UTC：SQLite 的 datetime('now') 返回 UTC。本文件如果写本地时间，
    tickets / ticket_events 里的时间就会和 users / cloud_instances 相差 8 小时（东八区），
    跨表统计（平均处理时长、按天分桶、工单号日期前缀）会全部错位。
    约定：全库统一存 UTC，展示层再转本地时区。
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 写操作
# ---------------------------------------------------------------------------
def create_ticket(
    conn: sqlite3.Connection,
    *,
    customer_id: int,
    title: str,
    description: str | None = None,
    instance_id: int | None = None,
    category: str | None = None,
    priority: str = "P3",
    conversation_id: str | None = None,
) -> sqlite3.Row:
    """创建工单（初始状态 OPEN），并写入第一条审计事件。

    安全与幂等约束：
        1. instance_id 如果传了，必须属于当前客户 —— 防止"关联别人家服务器"的越权写入；
        2. conversation_id 非空时具备数据库级唯一索引 (idx_tickets_conv_unique) 幂等保障：
           若该会话已存在工单（如崩溃后重放或并发冲突），直接复用并返回已有工单，绝不重复建单。
    """
    if instance_id is not None:
        owned = conn.execute(
            "SELECT 1 FROM cloud_instances WHERE id = ? AND owner_id = ?",
            (instance_id, customer_id),
        ).fetchone()
        if owned is None:
            raise TicketValidationError("关联的云实例不存在，或不属于当前用户")

    if conversation_id is not None:
        existing = conn.execute(
            "SELECT * FROM tickets WHERE conversation_id = ? AND status != 'CLOSED' LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if existing is not None:
            if int(existing["customer_id"]) != int(customer_id):
                raise TicketValidationError("该会话关联的工单归属与当前用户不一致")
            return existing

    now = _now_text()
    ticket_no = _generate_ticket_no(conn, now)
    try:
        cur = conn.execute(
            """
            INSERT INTO tickets (
                ticket_no, customer_id, instance_id, conversation_id, category,
                priority, status, title, description, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)
            """,
            (ticket_no, customer_id, instance_id, conversation_id, category,
             priority, title, description, now, now),
        )
        ticket_id = int(cur.lastrowid)

        # 首条审计事件：从"无"到 OPEN，operator_id 记为创建者
        conn.execute(
            """
            INSERT INTO ticket_events (ticket_id, operator_id, from_status, to_status, comment, created_at)
            VALUES (?, ?, NULL, 'OPEN', '创建工单', ?)
            """,
            (ticket_id, customer_id, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        if conversation_id is not None:
            existing = conn.execute(
                "SELECT * FROM tickets WHERE conversation_id = ? AND status != 'CLOSED' LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if existing is not None:
                if int(existing["customer_id"]) != int(customer_id):
                    raise TicketValidationError("该会话关联的工单归属与当前用户不一致")
                return existing
        raise
    return _fetch_ticket(conn, ticket_id)


def transition_ticket(
    conn: sqlite3.Connection,
    *,
    ticket_id: int,
    to_status: str,
    operator_id: int,
    comment: str | None = None,
) -> sqlite3.Row:
    """执行状态跃迁：校验 -> 更新工单 -> 写审计事件（两步必须同时成功）。

    注意：这里用同一个连接连续执行两条写语句后 commit，
    保证"状态改了但审计没写"这种半截情况不会出现。
    """
    row = _fetch_ticket(conn, ticket_id)
    from_status = row["status"]
    validate_transition(from_status, to_status)

    now = _now_text()
    conn.execute(
        "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
        (to_status, now, ticket_id),
    )
    conn.execute(
        """
        INSERT INTO ticket_events (ticket_id, operator_id, from_status, to_status, comment, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (ticket_id, operator_id, from_status, to_status, comment, now),
    )
    conn.commit()
    return _fetch_ticket(conn, ticket_id)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _fetch_ticket(conn: sqlite3.Connection, ticket_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise TicketNotFoundError(f"工单 {ticket_id} 不存在")
    return row


def _generate_ticket_no(conn: sqlite3.Connection, now_text: str) -> str:
    """生成形如 CSG-20261119-0001 的工单号（按天流水，日期前缀取 UTC）。

    为什么取"当天最大序号 + 1"而不是 COUNT(*) + 1：
        COUNT 在删除过工单后会回退。例如当天已有 0001~0003，删掉 0002 后
        COUNT 变成 2，下一条会生成 0003，直接撞上 UNIQUE 约束报 500。
        取 MAX 永远基于"已用过的最大号"，删除不会造成重号。

    并发说明：两个请求仍可能读到同一个 MAX（竞态未消除）。
        当前"SQLite 单写者 + 单实例部署"下不会发生；
        将来上多进程 / 多机时，应改为序列表，或"唯一约束 + 冲突重试"。
    """
    day = now_text[:10].replace("-", "")
    prefix = f"CSG-{day}-"
    row = conn.execute(
        "SELECT ticket_no FROM tickets WHERE ticket_no LIKE ? ORDER BY ticket_no DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    next_seq = int(row["ticket_no"].rsplit("-", 1)[1]) + 1 if row is not None else 1
    return f"{prefix}{next_seq:04d}"
