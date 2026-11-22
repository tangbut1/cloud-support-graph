"""FastAPI 应用入口。

【启动时做什么】
    1. init_db()：保证 12 张表存在（幂等，容器每次重启都跑一遍也没问题）；
    2. 挂载各组路由（认证 / 工单）。

【为什么用 create_app() 工厂函数】
    测试需要"一份全新的 app 实例 + 覆盖依赖（换测试数据库）"。
    如果模块级直接写 app = FastAPI(...)，测试只能复用同一个全局对象，
    多个测试互相污染。工厂模式让每个测试都能拿到干净的应用。

【接口分组】
    /api/health                健康检查（系统级）
    /api/auth/*                认证（Day 2）
    /api/tickets/*             工单（Day 2）
    后续：/api/instances、/api/kb、/api/conversations、/api/agent、/api/admin
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db.database import connect, init_db, mark_stale_eval_runs_failed
from app.routers import admin_eval, agent_desk, auth, conversations, instances, kb, tickets


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.validate_runtime_security()
    init_db(settings.app_db)
    conn = connect(settings.app_db)
    try:
        mark_stale_eval_runs_failed(conn)
    finally:
        conn.close()
    app.state.settings = settings
    yield


def create_app() -> FastAPI:
    """构建应用实例（测试可反复调用，互不干扰）。"""
    application = FastAPI(
        title="cloud-support-graph",
        description="面向云服务的智能分诊与工单协作系统",
        version="0.2.0",
        lifespan=_lifespan,
    )
    application.include_router(auth.router)
    application.include_router(tickets.router)
    application.include_router(kb.router)
    application.include_router(conversations.router)
    application.include_router(agent_desk.router)
    application.include_router(instances.router)
    application.include_router(admin_eval.router)

    @application.get("/api/health", tags=["系统"], summary="健康检查")
    def health() -> dict:
        settings = get_settings()
        return {
            "status": "ok",
            "app": settings.app_name,
            "env": settings.app_env,
            "db_path": str(settings.app_db),
            "db_exists": settings.app_db.exists(),
        }

    return application


app = create_app()
