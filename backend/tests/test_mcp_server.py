"""MCP 协议层冒烟测试：真实走一遍 initialize / tools/list / tools/call。

这里不测业务规则（那在 test_mcp_tools.py），只验证：
    * 4 个工具确实注册到了 MCP 服务端；
    * 通过 MCP 协议调用能拿到结构化结果；
    * 伪造令牌在协议入口就被拦下；
    * 越权调用通过协议链路仍然返回 PERMISSION_DENIED（纵深防御没有被协议层绕过）。
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.db.database import connect
from app.mcp import client as mcp_client
from app.mcp.auth_context import create_auth_context

EXPECTED_TOOLS = {
    "list_my_instances",
    "diagnose_instance_network",
    "list_my_tickets",
    "build_ticket_draft",
}


def _seed(settings: Settings) -> dict[str, int]:
    """在 MCP 测试库里造两个用户各一台实例。"""
    conn = connect(settings.app_db)
    try:
        zhang = int(
            conn.execute(
                "INSERT INTO users (username, password_hash, role, display_name) "
                "VALUES ('zhangsan', 'x', 'customer', '客户张三')"
            ).lastrowid
        )
        lisi = int(
            conn.execute(
                "INSERT INTO users (username, password_hash, role, display_name) "
                "VALUES ('lisi', 'x', 'customer', '客户李四')"
            ).lastrowid
        )
        for owner_id, name in ((zhang, "ubuntu-web-prod"), (lisi, "finance-api-node")):
            conn.execute(
                """
                INSERT INTO cloud_instances (owner_id, instance_name, public_ip, region, spec,
                                             os_image, status, firewall_ports_json,
                                             service_status_json, icp_filed, swap_mb, updated_at)
                VALUES (?, ?, '47.94.101.23', '华北2（北京）', '2C2G', 'Ubuntu 22.04 LTS',
                        'running', '[22, 80, 443]',
                        '{"nginx": "running", "backend-8080": "stopped"}', 1, 0, datetime('now'))
                """,
                (owner_id, name),
            )
        conn.commit()
        return {"zhang": zhang, "lisi": lisi}
    finally:
        conn.close()


def _token(settings: Settings, user_id: int, role: str = "customer") -> str:
    return create_auth_context(
        user_id=user_id, role=role, secret=settings.jwt_secret, ttl_seconds=120
    )


async def test_mcp_exposes_four_tools(mcp_settings):
    names = await mcp_client.list_tool_names()
    assert set(names) == EXPECTED_TOOLS


async def test_mcp_call_returns_structured_result(mcp_settings):
    ids = _seed(mcp_settings)
    payload = await mcp_client.call_tool(
        "list_my_instances", {"auth_context_token": _token(mcp_settings, ids["zhang"])}
    )
    assert payload["count"] == 1
    assert payload["instances"][0]["instance_name"] == "ubuntu-web-prod"


async def test_mcp_rejects_forged_token(mcp_settings):
    _seed(mcp_settings)
    payload = await mcp_client.call_tool(
        "list_my_instances", {"auth_context_token": "forged.signature"}
    )
    assert payload["error"] == "AUTH_INVALID"


async def test_mcp_blocks_cross_user_instance(mcp_settings):
    ids = _seed(mcp_settings)

    conn = connect(mcp_settings.app_db)
    try:
        lisi_instance = int(
            conn.execute(
                "SELECT id FROM cloud_instances WHERE owner_id = ?", (ids["lisi"],)
            ).fetchone()["id"]
        )
    finally:
        conn.close()

    payload = await mcp_client.call_tool(
        "diagnose_instance_network",
        {"auth_context_token": _token(mcp_settings, ids["zhang"]), "instance_id": lisi_instance},
    )
    assert payload["error"] == "PERMISSION_DENIED"


async def test_mcp_diagnose_own_instance_through_protocol(mcp_settings):
    ids = _seed(mcp_settings)
    conn = connect(mcp_settings.app_db)
    try:
        own_instance = int(
            conn.execute(
                "SELECT id FROM cloud_instances WHERE owner_id = ?", (ids["zhang"],)
            ).fetchone()["id"]
        )
    finally:
        conn.close()

    payload = await mcp_client.call_tool(
        "diagnose_instance_network",
        {"auth_context_token": _token(mcp_settings, ids["zhang"]), "instance_id": own_instance},
    )
    assert payload["owner_verified"] is True
    assert payload["risk_level"] == "critical"
    assert any(check["code"] == "PORT_NOT_ALLOWED" for check in payload["checks"])
