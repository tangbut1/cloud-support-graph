r"""种子数据脚本：把演示数据写入数据库（幂等，可重复执行；生产自动轮换公开弱口令）。

【这是什么】
    开发/演示/生产环境的初始化数据脚本，预置 4 个账号（2 客户 + 1 坐席 + 1 管理员）、
    5 台仿真云实例及 15 篇运维知识库索引。

【安全机制】
    - 开发环境（APP_ENV=development/dev/local/test）：未传 SEED_DEFAULT_PASSWORD 时沿用本地默认口令；
    - 生产环境（APP_ENV=production）：
      1. 严禁使用仓库公开默认口令 Passw0rd! 或长度 < 10 位的弱口令；
      2. 对已存在的存量用户，**自动检测并轮换**仍使用 Passw0rd! 的旧哈希，彻底堵住"存量库保留公开口令"缺口；
      3. 支持命令行参数 `--rotate-weak`（默认在生产强制开启）与 `--rotate-all`（全量轮换 4 个预设账号口令）。

【怎么运行】
    cd backend
    .\.venv\Scripts\python.exe -m app.db.seed
    .\.venv\Scripts\python.exe -m app.db.seed --rotate-weak
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.auth.security import hash_password, verify_password
from app.config import get_settings
from app.db.database import connect, init_db
from app.db.seed_data import DEMO_PASSWORD, INSTANCES, USERS
from app.services.kb_indexer import index_all


def _resolve_seed_password(password: str | None = None, env: str | None = None) -> tuple[str, bool]:
    """解析初始化口令并返回 (effective_password, is_production)。

    生产环境（APP_ENV=production）严禁使用仓库公开的 DEMO_PASSWORD (Passw0rd!)，
    必须通过参数或环境变量 SEED_DEFAULT_PASSWORD 提供 >=10 位自定义强口令。
    """
    settings = get_settings()
    app_env = (env or os.environ.get("APP_ENV") or settings.app_env or "development").strip().lower()
    is_prod = app_env not in ("development", "dev", "local", "test")
    candidate = (
        password
        if password is not None
        else (os.environ.get("SEED_DEFAULT_PASSWORD") or settings.seed_default_password or "")
    ).strip()

    if is_prod:
        if not candidate or candidate == DEMO_PASSWORD or len(candidate) < 10:
            raise RuntimeError(
                "安全校验失败：生产环境严禁使用仓库公开默认口令初始化账号。"
                "请先在 .env 中设置 SEED_DEFAULT_PASSWORD（长度 >= 10 且不得为 Passw0rd!）后再运行 seed。"
            )
        return candidate, True
    return candidate or DEMO_PASSWORD, False


def seed(
    db_path: str | Path | None = None,
    *,
    password: str | None = None,
    env: str | None = None,
    rotate_weak_passwords: bool = False,
    rotate_all_passwords: bool = False,
) -> dict[str, int]:
    """写入种子数据，返回 {"users", "passwords_rotated", "instances", "kb_documents", "kb_chunks"}。

    实现要点：
        1. 先 init_db()，保证表结构与最新增量列/唯一索引就绪；
        2. users 有 username UNIQUE 约束，先查后插；在生产环境或显式开启 rotate 时，
           对已存在但仍使用仓库公开口令 Passw0rd! 的存量账号自动轮换为新口令；
        3. cloud_instances 先查 (owner_id, instance_name) 保证幂等；
        4. JSON 字段序列化后入库，并重建/校验 FTS5 知识库索引。
    """
    path = Path(db_path) if db_path is not None else get_settings().app_db
    effective_password, is_prod = _resolve_seed_password(password=password, env=env)
    should_rotate_weak = is_prod or rotate_weak_passwords
    init_db(path)

    conn = connect(path)
    try:
        users_added = 0
        passwords_rotated = 0
        for user in USERS:
            existing = conn.execute(
                "SELECT id, password_hash FROM users WHERE username = ?", (user["username"],)
            ).fetchone()
            if existing:
                if rotate_all_passwords and not verify_password(
                    effective_password, existing["password_hash"]
                ):
                    conn.execute(
                        "UPDATE users SET password_hash = ? WHERE id = ?",
                        (hash_password(effective_password), existing["id"]),
                    )
                    passwords_rotated += 1
                continue
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, role, display_name) "
                "VALUES (?, ?, ?, ?)",
                (user["username"], hash_password(effective_password), user["role"], user["display_name"]),
            )
            users_added += 1

        # 全表扫描弱口令轮换（覆盖全部存量账号，容错损坏/非 bcrypt 哈希）
        if should_rotate_weak and effective_password != DEMO_PASSWORD:
            all_users = conn.execute("SELECT id, username, password_hash FROM users").fetchall()
            for row in all_users:
                try:
                    is_weak = verify_password(DEMO_PASSWORD, row["password_hash"])
                except ValueError:
                    continue
                if is_weak:
                    conn.execute(
                        "UPDATE users SET password_hash = ? WHERE id = ?",
                        (hash_password(effective_password), row["id"]),
                    )
                    passwords_rotated += 1

        instances_added = 0
        for inst in INSTANCES:
            owner = conn.execute(
                "SELECT id FROM users WHERE username = ?", (inst["owner_username"],)
            ).fetchone()
            if owner is None:
                raise RuntimeError(f"种子数据引用了不存在的用户: {inst['owner_username']}")
            owner_id = int(owner["id"])

            exists = conn.execute(
                "SELECT 1 FROM cloud_instances WHERE owner_id = ? AND instance_name = ?",
                (owner_id, inst["instance_name"]),
            ).fetchone()
            if exists:
                continue

            conn.execute(
                """
                INSERT INTO cloud_instances (
                    owner_id, instance_name, public_ip, region, spec, os_image, status,
                    firewall_ports_json, service_status_json, icp_filed, swap_mb, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    owner_id,
                    inst["instance_name"],
                    inst["public_ip"],
                    inst["region"],
                    inst["spec"],
                    inst["os_image"],
                    inst["status"],
                    json.dumps(inst["firewall_ports"], ensure_ascii=False),
                    json.dumps(inst["service_status"], ensure_ascii=False),
                    inst["icp_filed"],
                    inst["swap_mb"],
                ),
            )
            instances_added += 1

        conn.commit()

        kb = index_all(conn)

        return {
            "users": users_added,
            "passwords_rotated": passwords_rotated,
            "instances": instances_added,
            "kb_documents": kb["documents"],
            "kb_chunks": kb["chunks"],
        }
    finally:
        conn.close()


def main() -> None:
    """命令行入口：打印新增与轮换统计（生产环境不回显口令明文）。"""
    parser = argparse.ArgumentParser(description="初始化种子数据（生产模式自动轮换公开弱口令）")
    parser.add_argument(
        "--rotate-weak",
        action="store_true",
        help="将存量仍使用 Passw0rd! 的预设账号口令轮换为 SEED_DEFAULT_PASSWORD（生产模式默认启用）",
    )
    parser.add_argument(
        "--rotate-all",
        action="store_true",
        help="强制将全部 4 个预设账号口令轮换为 SEED_DEFAULT_PASSWORD",
    )
    args = parser.parse_args()

    db_path = get_settings().app_db
    effective_password, is_prod = _resolve_seed_password()
    result = seed(
        db_path,
        password=effective_password,
        rotate_weak_passwords=args.rotate_weak or is_prod,
        rotate_all_passwords=args.rotate_all,
    )

    print(f"[seed] 数据库文件: {db_path}")
    print(
        f"[seed] 新增用户 {result['users']} 个，轮换弱口令 {result['passwords_rotated']} 个，"
        f"新增云实例 {result['instances']} 台"
    )
    print(f"[seed] 知识库索引 {result['kb_documents']} 篇文档 / {result['kb_chunks']} 个切片")
    if is_prod:
        print("[seed] 生产环境账号已按 SEED_DEFAULT_PASSWORD 初始化/轮换（口令不回显）：")
    else:
        print(f"[seed] 本地开发演示账号（统一密码 {effective_password}）：")
    for user in USERS:
        print(f"        - {user['username']:<10} {user['role']:<9} {user['display_name']}")


if __name__ == "__main__":
    main()
