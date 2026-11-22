"""数据库依赖：每个请求一个 SQLite 连接，请求结束后自动关闭。

为什么不用全局单例连接？
    FastAPI 的同步端点跑在线程池里，不同请求可能落在不同线程；
    每个请求独立一个连接（connect() 里已开 check_same_thread=False 且 WAL 模式），
    既不会串数据，也避免长事务把写锁一直占着。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Depends

from app.config import Settings, get_settings
from app.db.database import connect


def get_db(settings: Settings = Depends(get_settings)) -> Iterator[sqlite3.Connection]:
    """FastAPI 依赖：yield 出的连接在请求结束时由 finally 关闭。"""
    conn = connect(settings.app_db)
    try:
        yield conn
    finally:
        conn.close()
