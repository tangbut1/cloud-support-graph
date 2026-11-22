"""MCP 工具的**业务实现**（纯函数，不依赖 MCP 协议与 FastAPI）。

为什么把业务逻辑从 server.py 里拆出来：
    1. 可以直接用普通单元测试覆盖（不用起 MCP 会话，快且稳定）；
    2. Day 5 的 LangGraph 既能通过 MCP 协议调用，也能在需要时降级直调；
    3. 安全校验只有一份实现，不会出现"协议层校验了、直调路径没校验"的漏洞。

【统一返回约定】
    成功：{"...": 业务字段}
    失败：{"error": <错误码>, "message": "人话原因"}
    错误码：
        PERMISSION_DENIED   该资源不存在或不属于当前用户（防 IDOR 的唯一出口）
        AUTH_INVALID        身份上下文无效（由 server.py 在验签阶段拦截）
        INVALID_ARGUMENT    参数不合法

    为什么没有 INSTANCE_NOT_FOUND：
        如果"不存在"和"不是你的"返回不同错误，就等于给攻击者一个 id 枚举预言机。
        统一成 PERMISSION_DENIED 后，调用方只能知道"这次访问不被允许"。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from app.mcp.auth_context import AuthContext
from app.services.instance_diagnostics import diagnose_instance
from app.services.ticket_sm import TICKET_STATUSES

_PRIORITY_RE = re.compile(r"^P[0-4]$")


def _permission_denied(message: str = "无权访问该资源：它不存在，或不属于当前用户") -> dict[str, Any]:
    # 统一用一句话描述，不区分"不存在"与"不是你的"，避免通过报错差异探测资源是否存在
    return {"error": "PERMISSION_DENIED", "message": message}


# ---------------------------------------------------------------------------
# 工具 1：列出我的云实例
# ---------------------------------------------------------------------------
def list_my_instances(ctx: AuthContext, conn: sqlite3.Connection) -> dict[str, Any]:
    """只返回 owner_id 等于当前身份的一行行数据。"""
    rows = conn.execute(
        """
        SELECT id, instance_name, public_ip, region, spec, os_image, status,
               icp_filed, swap_mb, updated_at
        FROM cloud_instances
        WHERE owner_id = ?
        ORDER BY id
        """,
        (ctx.user_id,),
    ).fetchall()

    return {
        "count": len(rows),
        "instances": [
            {
                "id": int(row["id"]),
                "instance_name": row["instance_name"],
                "public_ip": row["public_ip"],
                "region": row["region"],
                "spec": row["spec"],
                "os_image": row["os_image"],
                "status": row["status"],
                "icp_filed": bool(row["icp_filed"]),
                "swap_mb": int(row["swap_mb"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# 工具 2：诊断实例网络（防 IDOR 的核心用例）
# ---------------------------------------------------------------------------
def diagnose_instance_network(
    ctx: AuthContext, conn: sqlite3.Connection, instance_id: int
) -> dict[str, Any]:
    """诊断指定实例；不是自己的实例一律 PERMISSION_DENIED。"""
    if instance_id <= 0:
        return {"error": "INVALID_ARGUMENT", "message": "instance_id 必须是正整数"}

    row = conn.execute("SELECT * FROM cloud_instances WHERE id = ?", (instance_id,)).fetchone()

    # 第二道防线：即使拿到了别人的 instance_id，也必须在这里被拦下。
    # 注意：**"不存在" 与 "不属于自己" 返回完全相同的错误**。
    # 否则攻击者可以按 id 逐一试探：返回 PERMISSION_DENIED 说明该实例存在，
    # 返回 NOT_FOUND 说明不存在 —— 这就成了一个实例 id 枚举预言机。
    if row is None or int(row["owner_id"]) != ctx.user_id:
        return _permission_denied()

    diagnosis = diagnose_instance(row)
    result = diagnosis.as_dict()
    result["owner_verified"] = True
    return result


# ---------------------------------------------------------------------------
# 工具 3：列出我的工单
# ---------------------------------------------------------------------------
def list_my_tickets(
    ctx: AuthContext, conn: sqlite3.Connection, status: str | None = None
) -> dict[str, Any]:
    """只返回当前用户名下的工单摘要；不接受任何"别人"的参数。"""
    if status is not None and status not in TICKET_STATUSES:
        return {
            "error": "INVALID_ARGUMENT",
            "message": f"status 必须是 {list(TICKET_STATUSES)} 之一",
        }

    sql = (
        "SELECT id, ticket_no, title, status, priority, created_at "
        "FROM tickets WHERE customer_id = ?"
    )
    params: list[Any] = [ctx.user_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT 50"

    rows = conn.execute(sql, params).fetchall()
    return {
        "count": len(rows),
        "tickets": [
            {
                "id": int(row["id"]),
                "ticket_no": row["ticket_no"],
                "title": row["title"],
                "status": row["status"],
                "priority": row["priority"],
                "created_at": row["created_at"],
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# 工具 4：生成工单草稿（只生成，不入库）
# ---------------------------------------------------------------------------
def build_ticket_draft(
    ctx: AuthContext,
    conn: sqlite3.Connection,
    *,
    title: str,
    summary: str,
    category: str | None = None,
    priority: str = "P3",
    instance_id: int | None = None,
) -> dict[str, Any]:
    """生成结构化草稿。真正的入库要等人工坐席审批（Day 5 的 interrupt/resume）。"""
    title = (title or "").strip()
    if not title:
        return {"error": "INVALID_ARGUMENT", "message": "title 不能为空"}
    if len(title) > 200:
        return {"error": "INVALID_ARGUMENT", "message": "title 长度不能超过 200"}
    if not _PRIORITY_RE.match(priority or ""):
        return {"error": "INVALID_ARGUMENT", "message": "priority 必须是 P0~P4"}

    if instance_id is not None:
        row = conn.execute(
            "SELECT owner_id, instance_name FROM cloud_instances WHERE id = ?", (instance_id,)
        ).fetchone()
        # 与诊断工具一致：不存在与不属于自己返回同样的错误，避免枚举探针
        if row is None or int(row["owner_id"]) != ctx.user_id:
            return _permission_denied()
        instance_hint = row["instance_name"]
    else:
        instance_hint = None

    return {
        "draft": {
            "title": title,
            "summary": (summary or "").strip(),
            "category": (category or "未分类").strip(),
            "priority": priority,
            "instance_id": instance_id,
            "instance_name": instance_hint,
            "customer_id": ctx.user_id,
        },
        "requires_human_approval": True,
        "note": "草稿未入库，需坐席通过 interrupt/resume 审批后才创建正式工单",
    }
