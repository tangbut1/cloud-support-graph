"""FastMCP 工具服务端：把 4 个受限工具暴露为标准 MCP 工具。

【传输方式】
    默认使用 MCP 的**内存传输**（in-process 会话），见 app/mcp/client.py。
    理由：2C2G 服务器上没必要为一个内部工具层再开一个常驻进程；
    内存传输仍然是完整的 MCP 协议（同样的 initialize/list_tools/call_tool 流程），
    只是不经过 socket。若将来要独立部署工具服务，只需把 client.py 换成 stdio_client。

【安全边界（本文件只做第一道）】
    every tool 的第一个参数都是 auth_context_token：
      1. 验签 + 验过期（防伪造、防过期回放）；
      2. 解析出可信的 user_id / role；
      3. 交给 tools.py 里的业务函数，由它再校验资源归属（第二道）。

    重要：这个参数**不会出现在给大模型的工具 schema 里** ——
    见 app/agent/tools_bridge.py，它用闭包把令牌固定住，模型只看到业务参数。

【独立运行】
    python -m app.mcp.server     # 以 stdio 方式启动，便于外部 MCP 客户端调试
"""

from __future__ import annotations

import logging
import sqlite3

from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from app.db.database import connect
from app.mcp import tools
from app.mcp.auth_context import AuthContext, AuthContextError, decode_auth_context

logger = logging.getLogger(__name__)
# MCP SDK 默认在 INFO 级别打印每个请求，测试与本地日志会很吵，这里压到 WARNING
logging.getLogger("mcp").setLevel(logging.WARNING)

mcp_server = FastMCP(
    "CloudSupportTools",
    instructions=(
        "云服务技术支持工具集。所有工具都需要内部身份上下文令牌，"
        "该令牌由服务端注入，调用方无法伪造，也不需要（也不应该）提供用户 id。"
    ),
)


def _open_conn() -> sqlite3.Connection:
    return connect(get_settings().app_db)


def _resolve_context(token: str) -> AuthContext | None:
    """验签；失败返回 None 由各工具统一转成 AUTH_INVALID。"""
    try:
        return decode_auth_context(token, secret=get_settings().jwt_secret)
    except AuthContextError as exc:
        logger.warning("MCP 身份上下文校验失败: %s", exc.reason)
        return None


def _auth_invalid(reason: str) -> dict:
    return {"error": "AUTH_INVALID", "message": f"身份上下文无效：{reason}"}


def _with_context(token: str, handler):
    """统一的"验签 -> 开连接 -> 执行 -> 关闭"模板。"""
    ctx = _resolve_context(token)
    if ctx is None:
        return _auth_invalid("签名不匹配或已过期")
    conn = _open_conn()
    try:
        return handler(ctx, conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 工具定义
# ---------------------------------------------------------------------------
@mcp_server.tool()
def list_my_instances(auth_context_token: str) -> dict:
    """列出当前登录用户名下的云服务器实例（不含他人实例）。"""
    return _with_context(auth_context_token, lambda ctx, conn: tools.list_my_instances(ctx, conn))


@mcp_server.tool()
def diagnose_instance_network(auth_context_token: str, instance_id: int) -> dict:
    """诊断指定云服务器的网络与服务状态（端口放行、进程状态、Swap、备案）。

    只能诊断当前用户名下的实例；传入他人实例 id 会返回 PERMISSION_DENIED。
    """
    return _with_context(
        auth_context_token,
        lambda ctx, conn: tools.diagnose_instance_network(ctx, conn, instance_id=instance_id),
    )


@mcp_server.tool()
def list_my_tickets(auth_context_token: str, status: str | None = None) -> dict:
    """列出当前登录用户名下的工单（可按状态过滤）。"""
    return _with_context(
        auth_context_token,
        lambda ctx, conn: tools.list_my_tickets(ctx, conn, status=status),
    )


@mcp_server.tool()
def build_ticket_draft(
    auth_context_token: str,
    title: str,
    summary: str,
    category: str | None = None,
    priority: str = "P3",
    instance_id: int | None = None,
) -> dict:
    """生成结构化工单草稿（不直接入库，需人工坐席审批后创建）。"""
    return _with_context(
        auth_context_token,
        lambda ctx, conn: tools.build_ticket_draft(
            ctx,
            conn,
            title=title,
            summary=summary,
            category=category,
            priority=priority,
            instance_id=instance_id,
        ),
    )


if __name__ == "__main__":   # pragma: no cover - 手工调试入口
    mcp_server.run()
