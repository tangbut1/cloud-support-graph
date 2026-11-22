"""测试共享夹具。

设计要点：
    1. 每个测试用独立的临时数据库（tmp_path），测试之间零污染；
    2. 用 FastAPI 的 dependency_overrides 把 get_db / get_settings 换成测试版本，
       被测代码一行不改；
    3. 不进入 TestClient 的 lifespan（不用 with），避免启动真实 data/app.db。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.config import Settings, get_settings
from app.db.database import connect, init_db
from app.db.deps import get_db
from app.main import create_app

TEST_PASSWORD = "Passw0rd!"


# ---------------------------------------------------------------------------
# 配置与数据库
# ---------------------------------------------------------------------------
@pytest.fixture()
def settings(tmp_path) -> Settings:
    """测试用配置：独立数据库 + 固定的 JWT 密钥（不受 .env 影响）。"""
    return Settings(
        app_db_path=str(tmp_path / "app.db"),
        checkpoint_db_path=str(tmp_path / "checkpoints.db"),
        jwt_secret="test-secret-for-pytest-only-0123456789abcdef",
        jwt_algorithm="HS256",
        jwt_expire_minutes=60,
        enable_demo_login=True,
        # 测试绝不能打到真实大模型：显式关掉（Settings 会从 .env 读取未指定字段）
        llm_base_url="",
        llm_model_name="",
        llm_api_key="",
    )


@pytest.fixture()
def conn(settings: Settings) -> sqlite3.Connection:
    """建好 12 张表的测试连接（模拟应用已启动过）。"""
    init_db(settings.app_db)
    connection = connect(settings.app_db)
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------
def build_client(connection: sqlite3.Connection, settings: Settings) -> TestClient:
    application = create_app()
    application.dependency_overrides[get_db] = lambda: connection
    application.dependency_overrides[get_settings] = lambda: settings
    return TestClient(application)


@pytest.fixture()
def client(conn: sqlite3.Connection, settings: Settings) -> TestClient:
    return build_client(conn, settings)


# ---------------------------------------------------------------------------
# 数据准备工具
# ---------------------------------------------------------------------------
def make_user(
    connection: sqlite3.Connection,
    username: str,
    role: str,
    display_name: str | None = None,
    password: str = TEST_PASSWORD,
) -> int:
    """插入一个用户并返回 id。"""
    cur = connection.execute(
        "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
        (username, hash_password(password), role, display_name or username),
    )
    connection.commit()
    return int(cur.lastrowid)


def make_instance(connection: sqlite3.Connection, owner_id: int, name: str = "ubuntu-web-prod") -> int:
    """插入一台云实例并返回 id。"""
    cur = connection.execute(
        """
        INSERT INTO cloud_instances (owner_id, instance_name, public_ip, region, spec, os_image,
                                     status, firewall_ports_json, service_status_json, icp_filed,
                                     swap_mb, updated_at)
        VALUES (?, ?, '47.94.101.23', '华北2（北京）', '2C2G', 'Ubuntu 22.04 LTS',
                'running', '[22, 80, 443]', '{"nginx": "running"}', 1, 0, datetime('now'))
        """,
        (owner_id, name),
    )
    connection.commit()
    return int(cur.lastrowid)


@pytest.fixture()
def mcp_settings(tmp_path, monkeypatch) -> Settings:
    """MCP 专用：独立数据库 + 固定密钥，并替换服务端读取配置的函数。

    为什么必须替换：MCP 服务端每次调用自己 open 连接（读 settings.app_db），
    测试要让这些连接指向临时库，否则会写到真实的 data/app.db。
    """
    settings = Settings(
        app_db_path=str(tmp_path / "mcp.db"),
        checkpoint_db_path=str(tmp_path / "mcp_checkpoints.db"),
        jwt_secret="mcp-test-secret-0123456789abcdef",
        jwt_algorithm="HS256",
        llm_base_url="",
        llm_model_name="",
        llm_api_key="",
    )
    init_db(settings.app_db)
    monkeypatch.setattr("app.mcp.server.get_settings", lambda: settings)
    return settings


def login_headers(client: TestClient, username: str, password: str = TEST_PASSWORD) -> dict[str, str]:
    """登录并把令牌包成请求头。"""
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
