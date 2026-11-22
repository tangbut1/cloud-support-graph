"""MCP 工具业务逻辑与防越权测试（Day 4 核心验收）。

覆盖三类风险：
    1. 越权（IDOR）：拿别人的 instance_id / 关联别人的实例建草稿，必须 PERMISSION_DENIED；
    2. 身份伪造：签名篡改、过期、格式错误的上下文令牌必须拒绝；
    3. 数据隔离：list_my_instances / list_my_tickets 只能返回自己的数据。
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from app.mcp.auth_context import (
    AuthContext,
    AuthContextError,
    create_auth_context,
    decode_auth_context,
)
from app.mcp.tools import (
    build_ticket_draft,
    diagnose_instance_network,
    list_my_instances,
    list_my_tickets,
)

SECRET = "mcp-context-secret-for-pytest-0123456789"


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------
def _insert_instance(
    conn: sqlite3.Connection,
    owner_id: int,
    name: str,
    *,
    status: str = "running",
    ports: str = "[22, 80, 443]",
    services: str = '{"nginx": "running", "backend-8080": "stopped"}',
    swap_mb: int = 0,
    icp_filed: int = 1,
    spec: str = "2C2G",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO cloud_instances (owner_id, instance_name, public_ip, region, spec, os_image,
                                     status, firewall_ports_json, service_status_json,
                                     icp_filed, swap_mb, updated_at)
        VALUES (?, ?, '47.94.101.23', '华北2（北京）', ?, 'Ubuntu 22.04 LTS',
                ?, ?, ?, ?, ?, datetime('now'))
        """,
        (owner_id, name, spec, status, ports, services, icp_filed, swap_mb),
    )
    conn.commit()
    return int(cur.lastrowid)


@pytest.fixture()
def zhangsan(conn) -> int:
    from tests.conftest import make_user
    return make_user(conn, "zhangsan", "customer", "客户张三")


@pytest.fixture()
def lisi(conn) -> int:
    from tests.conftest import make_user
    return make_user(conn, "lisi", "customer", "客户李四")


@pytest.fixture()
def web_instance(conn, zhangsan) -> int:
    return _insert_instance(conn, zhangsan, "ubuntu-web-prod")


@pytest.fixture()
def yang_ctx() -> AuthContext:
    return AuthContext(user_id=1, role="customer")


# ===========================================================================
# 一、身份上下文令牌
# ===========================================================================
def test_auth_context_roundtrip():
    token = create_auth_context(user_id=7, role="agent", secret=SECRET, ttl_seconds=60)
    ctx = decode_auth_context(token, secret=SECRET)
    assert ctx.user_id == 7 and ctx.role == "agent"


def test_auth_context_rejects_tampered_signature():
    token = create_auth_context(user_id=1, role="customer", secret=SECRET, ttl_seconds=60)
    body, signature = token.split(".")
    forged = body + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(AuthContextError):
        decode_auth_context(forged, secret=SECRET)


def test_auth_context_rejects_other_secret():
    token = create_auth_context(user_id=1, role="customer", secret=SECRET, ttl_seconds=60)
    with pytest.raises(AuthContextError):
        decode_auth_context(token, secret="another-secret-0123456789abcdef")


def test_auth_context_rejects_expired():
    token = create_auth_context(user_id=1, role="customer", secret=SECRET, ttl_seconds=-1)
    time.sleep(0.01)
    with pytest.raises(AuthContextError):
        decode_auth_context(token, secret=SECRET)


def test_auth_context_rejects_malformed():
    for bad in ("", "no-dot", "a.b.c", "a.b"):
        with pytest.raises(AuthContextError):
            decode_auth_context(bad, secret=SECRET)


# ===========================================================================
# 二、数据隔离
# ===========================================================================
def test_list_my_instances_returns_only_own(conn, zhangsan, lisi):
    _insert_instance(conn, zhangsan, "zhang-instance")
    _insert_instance(conn, lisi, "lisi-instance")

    zhang_result = list_my_instances(AuthContext(zhangsan, "customer"), conn)
    lisi_result = list_my_instances(AuthContext(lisi, "customer"), conn)

    assert zhang_result["count"] == 1
    assert zhang_result["instances"][0]["instance_name"] == "zhang-instance"
    assert lisi_result["count"] == 1
    assert lisi_result["instances"][0]["instance_name"] == "lisi-instance"


def test_list_my_tickets_returns_only_own(conn, zhangsan, lisi):
    from app.services.ticket_sm import create_ticket

    create_ticket(conn, customer_id=zhangsan, title="张三的问题")
    create_ticket(conn, customer_id=lisi, title="李四的问题")

    zhang = list_my_tickets(AuthContext(zhangsan, "customer"), conn)
    assert zhang["count"] == 1
    assert zhang["tickets"][0]["title"] == "张三的问题"


def test_list_my_tickets_rejects_invalid_status(conn, zhangsan):
    result = list_my_tickets(AuthContext(zhangsan, "customer"), conn, status="DONE")
    assert result["error"] == "INVALID_ARGUMENT"


# ===========================================================================
# 三、诊断：正常结论项
# ===========================================================================
def test_diagnose_reports_typical_502_causes(conn, zhangsan, web_instance):
    result = diagnose_instance_network(AuthContext(zhangsan, "customer"), conn, web_instance)

    assert result["owner_verified"] is True
    assert result["risk_level"] == "critical"
    codes = {check["code"] for check in result["checks"]}
    assert "SERVICE_STOPPED" in codes          # backend-8080 stopped
    assert "PORT_NOT_ALLOWED" in codes         # 8080 不在 [22,80,443]
    assert "SWAP_NOT_CONFIGURED" in codes      # 2C2G 且 swap=0
    assert "502" in result["conclusion"]
    assert result["suggested_actions"]


def test_diagnose_healthy_instance_is_ok(conn, zhangsan):
    instance_id = _insert_instance(
        conn,
        zhangsan,
        "healthy-node",
        ports="[22, 8080]",
        services='{"app-8080": "running"}',
        swap_mb=2048,
        icp_filed=1,
    )
    result = diagnose_instance_network(AuthContext(zhangsan, "customer"), conn, instance_id)
    assert result["risk_level"] == "ok"
    assert "未发现异常" in result["conclusion"]


def test_diagnose_stopped_instance(conn, zhangsan):
    instance_id = _insert_instance(conn, zhangsan, "stopped-node", status="stopped")
    result = diagnose_instance_network(AuthContext(zhangsan, "customer"), conn, instance_id)
    codes = {check["code"] for check in result["checks"]}
    assert "INSTANCE_NOT_RUNNING" in codes


# ===========================================================================
# 四、防越权（IDOR）——本日最重要的测试
# ===========================================================================
def test_diagnose_other_users_instance_is_denied(conn, zhangsan, lisi):
    lisi_instance = _insert_instance(conn, lisi, "finance-api-node")
    result = diagnose_instance_network(AuthContext(zhangsan, "customer"), conn, lisi_instance)
    assert result["error"] == "PERMISSION_DENIED"
    # 不得泄露任何实例细节
    assert "instance_name" not in result
    assert "checks" not in result


def test_diagnose_missing_instance_looks_same_as_denied(conn, zhangsan):
    """不存在的实例与别人的实例必须返回同样的错误，否则可用于枚举 id。"""
    missing = diagnose_instance_network(AuthContext(zhangsan, "customer"), conn, 99999)
    assert missing["error"] == "PERMISSION_DENIED"


def test_diagnose_invalid_instance_id(conn, zhangsan):
    result = diagnose_instance_network(AuthContext(zhangsan, "customer"), conn, 0)
    assert result["error"] == "INVALID_ARGUMENT"


def test_build_ticket_draft_rejects_foreign_instance(conn, zhangsan, lisi):
    lisi_instance = _insert_instance(conn, lisi, "finance-api-node")
    result = build_ticket_draft(
        AuthContext(zhangsan, "customer"),
        conn,
        title="越权建单",
        summary="试图关联他人实例",
        instance_id=lisi_instance,
    )
    assert result["error"] == "PERMISSION_DENIED"


# ===========================================================================
# 五、工单草稿
# ===========================================================================
def test_build_ticket_draft_ok(conn, zhangsan, web_instance):
    result = build_ticket_draft(
        AuthContext(zhangsan, "customer"),
        conn,
        title="网站打不开，返回 502",
        summary="后端 8080 进程停止且端口未放行",
        category="应用服务",
        priority="P2",
        instance_id=web_instance,
    )
    assert "error" not in result
    draft = result["draft"]
    assert draft["title"].startswith("网站打不开")
    assert draft["instance_name"] == "ubuntu-web-prod"
    assert draft["customer_id"] == zhangsan
    assert result["requires_human_approval"] is True        # 必须人工审批后才入库

    # 草稿不落库
    count = conn.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()["n"]
    assert count == 0


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"title": "", "summary": "x"}, "INVALID_ARGUMENT"),
        ({"title": "x" * 201, "summary": "y"}, "INVALID_ARGUMENT"),
        ({"title": "正常标题", "summary": "y", "priority": "P9"}, "INVALID_ARGUMENT"),
    ],
)
def test_build_ticket_draft_validates_input(conn, zhangsan, kwargs, expected):
    result = build_ticket_draft(AuthContext(zhangsan, "customer"), conn, **kwargs)
    assert result["error"] == expected
