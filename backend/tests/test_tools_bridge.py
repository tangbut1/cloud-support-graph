"""工具桥接层测试：验证"身份对模型不可见、也不可伪造"。

这是本项目安全设计里最容易被忽略的一环：
    即使 MCP 服务端校验很严，如果桥接层把 auth_context_token 暴露成工具参数，
    模型（或被注入的提示词）就能换个令牌去查别人 —— 防线等于没有。
"""

from __future__ import annotations

from app.agent.tools_bridge import build_tools_for_user
from app.config import Settings
from app.db.database import connect
from app.mcp.auth_context import create_auth_context

EXPECTED_TOOLS = {
    "list_my_instances",
    "diagnose_instance_network",
    "list_my_tickets",
    "build_ticket_draft",
}


def _seed(settings: Settings) -> dict[str, int]:
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


def _token(settings: Settings, user_id: int) -> str:
    return create_auth_context(
        user_id=user_id, role="customer", secret=settings.jwt_secret, ttl_seconds=120
    )


# ===========================================================================
# 一、schema 里绝不能出现身份参数
# ===========================================================================
def test_bridge_exposes_expected_tools(mcp_settings):
    tools = build_tools_for_user(_token(mcp_settings, 1))
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


def test_bridge_tool_schemas_hide_identity(mcp_settings):
    """模型看到的工具参数里，不能有 auth_context_token / user_id / customer_id。"""
    tools = build_tools_for_user(_token(mcp_settings, 1))
    for tool in tools:
        arg_names = set(tool.args)
        assert "auth_context_token" not in arg_names
        assert "user_id" not in arg_names
        assert "customer_id" not in arg_names


def test_bridge_uses_business_argument_names(mcp_settings):
    tools = {tool.name: tool for tool in build_tools_for_user(_token(mcp_settings, 1))}
    assert set(tools["diagnose_instance_network"].args) == {"instance_id"}
    assert set(tools["build_ticket_draft"].args) == {
        "title",
        "summary",
        "category",
        "priority",
        "instance_id",
    }


# ===========================================================================
# 二、通过桥接层真实调用（走 MCP 协议）
# ===========================================================================
async def test_bridge_tool_calls_mcp_with_injected_identity(mcp_settings):
    ids = _seed(mcp_settings)
    tools = {tool.name: tool for tool in build_tools_for_user(_token(mcp_settings, ids["zhang"]))}

    payload = await tools["list_my_instances"].ainvoke({})
    assert payload["count"] == 1
    assert payload["instances"][0]["instance_name"] == "ubuntu-web-prod"


async def test_bridge_tool_denies_other_users_instance(mcp_settings):
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

    tools = {tool.name: tool for tool in build_tools_for_user(_token(mcp_settings, ids["zhang"]))}
    payload = await tools["diagnose_instance_network"].ainvoke({"instance_id": lisi_instance})
    assert payload["error"] == "PERMISSION_DENIED"
