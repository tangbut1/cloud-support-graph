"""认证与权限隔离测试（对应 DEV_SPEC §8.1 的 test_auth_rbac.py）。

覆盖四类风险：
    1. 密码存储：绝不能明文，bcrypt 校验要能区分对错；
    2. 令牌安全：篡改、过期、伪造都要被拒；
    3. 登录接口：不给攻击者"用户名是否存在"的信息；
    4. 越权隔离：客户不能改工单状态、不能读他人工单、不能查别人实例。
"""

from __future__ import annotations

import pytest

from app.auth.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from tests.conftest import TEST_PASSWORD, build_client, login_headers, make_instance, make_user

# RFC 7518 建议 HS256 密钥不少于 32 字节，测试里也用够长的串，避免库的告警噪音
SECRET = "unit-test-secret-key-0123456789abcdef"
WRONG_SECRET = "another-wrong-secret-0123456789abcdef"


# ===========================================================================
# 一、密码哈希
# ===========================================================================
def test_password_hash_is_not_plaintext_and_verifies():
    hashed = hash_password(TEST_PASSWORD)
    assert TEST_PASSWORD not in hashed          # 不存明文
    assert hashed.startswith("$2b$")            # bcrypt 标识
    assert verify_password(TEST_PASSWORD, hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_same_password_gets_different_hashes():
    """bcrypt 每次加盐，因此同一密码两次哈希结果不同——这能防彩虹表。"""
    assert hash_password(TEST_PASSWORD) != hash_password(TEST_PASSWORD)


def test_verify_password_tolerates_broken_hash():
    assert verify_password("any", "this-is-not-a-bcrypt-hash") is False


# ===========================================================================
# 二、JWT 令牌
# ===========================================================================
def test_jwt_roundtrip_keeps_identity():
    token = create_access_token(user_id=7, username="zhangsan", role="customer", secret=SECRET)
    payload = decode_access_token(token, secret=SECRET)
    assert payload["sub"] == "7"                # 标准声明 sub 是字符串
    assert payload["username"] == "zhangsan"
    assert payload["role"] == "customer"
    assert payload["exp"] > payload["iat"]


def test_jwt_with_wrong_secret_is_rejected():
    token = create_access_token(user_id=1, username="a", role="customer", secret=SECRET)
    with pytest.raises(TokenError):
        decode_access_token(token, secret=WRONG_SECRET)


def test_jwt_tampered_payload_is_rejected():
    token = create_access_token(user_id=1, username="zhangsan", role="customer", secret=SECRET)
    head, payload, signature = token.split(".")
    # 把 role 从 customer 改成 admin（想提权）——签名对不上就会被拒
    tampered = ".".join([head, payload[:-2] + ("AA" if payload[-2:] != "AA" else "BB"), signature])
    with pytest.raises(TokenError):
        decode_access_token(tampered, secret=SECRET)


def test_expired_token_is_rejected():
    token = create_access_token(
        user_id=1, username="a", role="customer", secret=SECRET, expires_minutes=-1
    )
    with pytest.raises(TokenError):
        decode_access_token(token, secret=SECRET)


# ===========================================================================
# 三、登录接口
# ===========================================================================
def test_login_success_returns_token_and_user(conn, client):
    make_user(conn, "zhangsan", "customer", "客户张三")
    resp = client.post("/api/auth/login", json={"username": "zhangsan", "password": TEST_PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "customer"
    assert "password_hash" not in body["user"]      # 绝不回传哈希


def test_login_wrong_password_returns_401(conn, client):
    make_user(conn, "zhangsan", "customer")
    resp = client.post("/api/auth/login", json={"username": "zhangsan", "password": "bad"})
    assert resp.status_code == 401


def test_login_unknown_user_looks_identical_to_wrong_password(conn, client):
    """两种失败必须返回同样的报错，避免用户名枚举。"""
    make_user(conn, "zhangsan", "customer")
    wrong_pwd = client.post("/api/auth/login", json={"username": "zhangsan", "password": "bad"})
    no_user = client.post("/api/auth/login", json={"username": "nobody", "password": "bad"})
    assert wrong_pwd.status_code == no_user.status_code == 401
    assert wrong_pwd.json()["detail"] == no_user.json()["detail"]


def test_me_requires_valid_token(conn, client):
    make_user(conn, "zhangsan", "customer")
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_me_returns_current_user(conn, client):
    make_user(conn, "zhangsan", "customer", "客户张三")
    resp = client.get("/api/auth/me", headers=login_headers(client, "zhangsan"))
    assert resp.status_code == 200
    assert resp.json()["username"] == "zhangsan"


def test_demo_login_works_when_enabled(conn, client):
    make_user(conn, "admin", "admin", "系统管理员")
    resp = client.post("/api/auth/demo-login", json={"username": "admin"})
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "admin"


def test_demo_login_disabled_returns_403(conn, settings):
    """生产环境关掉演示登录后，这个"后门"必须真的关死。"""
    settings.enable_demo_login = False
    hardened = build_client(conn, settings)
    make_user(conn, "admin", "admin")
    assert hardened.post("/api/auth/demo-login", json={"username": "admin"}).status_code == 403


# ===========================================================================
# 四、RBAC 与越权隔离
# ===========================================================================
def test_customer_cannot_transition_ticket(conn, client):
    customer_id = make_user(conn, "zhangsan", "customer")
    make_user(conn, "wanggong", "agent")
    instance_id = make_instance(conn, customer_id)

    created = client.post(
        "/api/tickets",
        headers=login_headers(client, "zhangsan"),
        json={"title": "网站 502", "instance_id": instance_id},
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]

    # 客户自己不能把工单推进到 TRIAGED
    resp = client.patch(
        f"/api/tickets/{ticket_id}/transition",
        headers=login_headers(client, "zhangsan"),
        json={"to_status": "TRIAGED"},
    )
    assert resp.status_code == 403

    # 坐席可以
    ok = client.patch(
        f"/api/tickets/{ticket_id}/transition",
        headers=login_headers(client, "wanggong"),
        json={"to_status": "TRIAGED", "comment": "已分诊"},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "TRIAGED"


def test_customer_cannot_read_other_customers_ticket(conn, client):
    zhangsan = make_user(conn, "zhangsan", "customer")
    lisi = make_user(conn, "lisi", "customer")
    make_user(conn, "wanggong", "agent")
    lisi_instance = make_instance(conn, lisi, "finance-api-node")

    created = client.post(
        "/api/tickets",
        headers=login_headers(client, "lisi"),
        json={"title": "数据库连接超时", "instance_id": lisi_instance},
    )
    ticket_id = created.json()["id"]

    # 张三去看李四的工单 -> 404（不泄露存在性）
    resp = client.get(f"/api/tickets/{ticket_id}", headers=login_headers(client, "zhangsan"))
    assert resp.status_code == 404

    # 坐席可以看
    assert client.get(f"/api/tickets/{ticket_id}", headers=login_headers(client, "wanggong")).status_code == 200


def test_customer_cannot_attach_other_customers_instance(conn, client):
    make_user(conn, "zhangsan", "customer")
    lisi = make_user(conn, "lisi", "customer")
    lisi_instance = make_instance(conn, lisi, "finance-api-node")

    resp = client.post(
        "/api/tickets",
        headers=login_headers(client, "zhangsan"),
        json={"title": "越权关联实例", "instance_id": lisi_instance},
    )
    assert resp.status_code == 400


def test_ticket_list_is_scoped_by_role(conn, client):
    zhangsan = make_user(conn, "zhangsan", "customer")
    lisi = make_user(conn, "lisi", "customer")
    make_user(conn, "wanggong", "agent")
    make_instance(conn, zhangsan)

    client.post("/api/tickets", headers=login_headers(client, "zhangsan"), json={"title": "张三的问题"})
    client.post("/api/tickets", headers=login_headers(client, "lisi"), json={"title": "李四的问题"})

    zhang_list = client.get("/api/tickets", headers=login_headers(client, "zhangsan")).json()
    assert [t["title"] for t in zhang_list] == ["张三的问题"]

    agent_list = client.get("/api/tickets", headers=login_headers(client, "wanggong")).json()
    assert len(agent_list) == 2
