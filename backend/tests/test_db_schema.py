"""Day 1 数据库层验收测试（AI 编写，用来验收你手写的 database.py）。

你需要在 backend/app/db/database.py 中提供且仅要求这两个公开函数：

    connect(db_path: str | Path) -> sqlite3.Connection
        返回连接，并已完成以下设置：
        - PRAGMA journal_mode = WAL
        - PRAGMA foreign_keys = ON
        - PRAGMA busy_timeout = 5000
        - conn.row_factory = sqlite3.Row

    init_db(db_path: str | Path) -> None
        幂等建库：父目录不存在要自动创建；重复调用不报错、不重复建表；
        必须建出 12 张表（含 FTS5 虚拟表）与 3 个 FTS 同步触发器、4 个索引。

完整字段规格见 docs/DAY1_BLUEPRINT.md。
"""

from __future__ import annotations

import sqlite3

import pytest

from app.db.database import connect, init_db

EXPECTED_TABLES = {
    "users",
    "cloud_instances",
    "kb_documents",
    "kb_chunks",
    "kb_chunks_fts",
    "conversations",
    "messages",
    "tickets",
    "ticket_events",
    "agent_traces",
    "eval_runs",
    "eval_case_results",
}

EXPECTED_INDEXES = {
    "idx_instances_owner",
    "idx_msg_conv",
    "idx_ticket_customer",
    "idx_ticket_status",
}

EXPECTED_TRIGGERS = {"kb_chunks_ai", "kb_chunks_au", "kb_chunks_ad"}


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "app.db"
    init_db(path)
    return path


@pytest.fixture()
def conn(db_path):
    connection = connect(db_path)
    yield connection
    connection.close()


def _objects(conn: sqlite3.Connection, kind: str) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,)).fetchall()
    return {r[0] for r in rows}


def _add_user(conn: sqlite3.Connection, username: str = "customer_a", role: str = "customer") -> int:
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
        (username, "fake-bcrypt-hash", role, "测试用户"),
    )
    return int(cur.lastrowid)


# --------------------------------------------------------------------------- #
# 1. 建库与幂等
# --------------------------------------------------------------------------- #
def test_init_db_creates_nested_dir_and_is_idempotent(tmp_path):
    path = tmp_path / "nested" / "data" / "app.db"
    init_db(path)
    init_db(path)  # 第二次必须不报错
    assert path.exists(), "init_db 没有创建数据库文件"
    conn = connect(path)
    try:
        assert EXPECTED_TABLES <= _objects(conn, "table")
    finally:
        conn.close()


def test_all_twelve_tables_created(conn):
    missing = EXPECTED_TABLES - _objects(conn, "table")
    assert not missing, f"缺少数据表: {sorted(missing)}"


# --------------------------------------------------------------------------- #
# 2. FTS5 与同步触发器
# --------------------------------------------------------------------------- #
def test_kb_chunks_fts_is_fts5_virtual_table(conn):
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'kb_chunks_fts'").fetchone()
    assert row is not None, "kb_chunks_fts 不存在"
    sql = (row[0] or "").lower()
    assert "virtual table" in sql and "fts5" in sql, f"kb_chunks_fts 必须是 FTS5 虚拟表，实际: {row[0]}"


def test_fts_sync_triggers_exist(conn):
    missing = EXPECTED_TRIGGERS - _objects(conn, "trigger")
    assert not missing, f"缺少 FTS 同步触发器: {sorted(missing)}"


def test_fts_stays_in_sync_on_insert_update_delete(conn):
    doc_id = int(
        conn.execute(
            "INSERT INTO kb_documents (title, category, raw_markdown, updated_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            ("Nginx 502 排查", "云服务器", "# 现象\n访问站点返回 502"),
        ).lastrowid
    )
    chunk_id = int(
        conn.execute(
            "INSERT INTO kb_chunks (doc_id, doc_title, section_title, content, segmented_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (doc_id, "Nginx 502 排查", "一、现象", "访问站点返回 502", "访问 站点 返回 502"),
        ).lastrowid
    )

    def matched(term: str) -> set[int]:
        rows = conn.execute(
            "SELECT chunk_id FROM kb_chunks_fts WHERE kb_chunks_fts MATCH ?", (term,)
        ).fetchall()
        return {int(r[0]) for r in rows}

    assert chunk_id in matched("502"), "INSERT 后 FTS 表没有同步到切片"

    conn.execute(
        "UPDATE kb_chunks SET segmented_text = ? WHERE id = ?",
        ("upstream 后端 进程 挂了", chunk_id),
    )
    assert chunk_id in matched("upstream"), "UPDATE 后 FTS 表没有同步"

    conn.execute("DELETE FROM kb_chunks WHERE id = ?", (chunk_id,))
    assert chunk_id not in matched("upstream"), "DELETE 后 FTS 表残留了旧切片"


# --------------------------------------------------------------------------- #
# 3. 索引、PRAGMA、约束
# --------------------------------------------------------------------------- #
def test_required_indexes_created(conn):
    missing = EXPECTED_INDEXES - _objects(conn, "index")
    assert not missing, f"缺少索引: {sorted(missing)}"


def test_pragmas_applied_on_every_connection(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal", "必须开启 WAL"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "必须开启外键约束"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000, "busy_timeout 应为 5000ms"


def test_row_factory_is_sqlite_row(conn):
    _add_user(conn, "row_factory_probe")
    row = conn.execute("SELECT * FROM users WHERE username = ?", ("row_factory_probe",)).fetchone()
    assert isinstance(row, sqlite3.Row), "connect() 必须设置 row_factory = sqlite3.Row"
    assert row["role"] == "customer"


def test_foreign_key_is_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cloud_instances (owner_id, instance_name, updated_at) "
            "VALUES (99999, 'orphan-instance', datetime('now'))"
        )


def test_username_is_unique(conn):
    _add_user(conn, "duplicate_user")
    with pytest.raises(sqlite3.IntegrityError):
        _add_user(conn, "duplicate_user")


def test_conversation_status_check_constraint(conn):
    customer_id = _add_user(conn, "status_probe")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO conversations (id, customer_id, thread_id, status, created_at, updated_at) "
            "VALUES ('c-bad', ?, 't-bad', 'illegal_status', datetime('now'), datetime('now'))",
            (customer_id,),
        )


def test_ticket_status_check_constraint(conn):
    customer_id = _add_user(conn, "ticket_probe")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tickets (ticket_no, customer_id, status, title, created_at, updated_at) "
            "VALUES ('T-BAD', ?, 'DONE', '非法状态', datetime('now'), datetime('now'))",
            (customer_id,),
        )
