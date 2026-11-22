"""鉴权依赖：把 Bearer 令牌换成"当前用户"，并提供角色守卫。

【三个东西】
    get_current_user()   解析 Authorization 头 -> 令牌 -> 用户 id -> 查库 -> 返回用户行
    require_roles(...)   工厂函数，生成"只允许某几个角色"的守卫依赖
    CurrentUser / AgentOrAdmin / AdminOnly   给路由用的类型别名

【为什么解析完令牌还要查库】
    这是防提权的关键：令牌里的 role 可以被伪造（如果密钥泄露）或已过期，
    所以服务端必须以数据库里的 role 为准。查库还能顺带拦住"用户已被删除但令牌还没过期"的情况。

【为什么查不到用户返回 401 而不是 404】
    401 = 身份无效（该重新登录）；403 = 身份有效但没权限。两者语义不能混。
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.security import TokenError, decode_access_token
from app.config import Settings, get_settings
from app.db.deps import get_db

# auto_error=False：没带令牌时不要框架自动抛错，我们要自己控制返回体
_bearer = HTTPBearer(auto_error=False, description="在 Authorization 头中传 Bearer <token>")

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="未登录或登录已过期，请重新登录",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    conn: sqlite3.Connection = Depends(get_db),
) -> sqlite3.Row:
    """从 Bearer 令牌解析出当前用户；任何异常情况统一 401。"""
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORIZED

    try:
        payload = decode_access_token(
            credentials.credentials,
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
    except TokenError:
        raise _UNAUTHORIZED from None

    subject = payload.get("sub")
    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        raise _UNAUTHORIZED from None

    user = conn.execute(
        "SELECT id, username, role, display_name FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if user is None:
        raise _UNAUTHORIZED
    return user


def require_roles(*allowed_roles: str):
    """生成角色守卫依赖，例如 require_roles("agent", "admin")。"""

    def _guard(user: sqlite3.Row = Depends(get_current_user)) -> sqlite3.Row:
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：该操作仅限 {'/'.join(allowed_roles)}",
            )
        return user

    return _guard


# ---- 路由里直接用这些别名，比写 Depends 更好读 ----
CurrentUser = Annotated[sqlite3.Row, Depends(get_current_user)]
AgentOrAdmin = Annotated[sqlite3.Row, Depends(require_roles("agent", "admin"))]
AdminOnly = Annotated[sqlite3.Row, Depends(require_roles("admin"))]
