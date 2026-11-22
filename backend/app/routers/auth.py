"""认证路由：登录、演示登录、获取当前用户。

三个接口的安全差异值得注意：
    /api/auth/login       账号密码登录，失败时**不区分**"用户不存在"和"密码错误"
                          （否则攻击者可以通过报错差异枚举出哪些用户名存在）
    /api/auth/demo-login  仅演示环境开放，由 .env 的 ENABLE_DEMO_LOGIN 控制，
                          生产环境应关闭，否则等于留了个免密后门
    /api/auth/me          必须带有效令牌，用于前端刷新页面后恢复登录态
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import CurrentUser
from app.auth.security import create_access_token, verify_password
from app.config import Settings, get_settings
from app.db.deps import get_db
from app.schemas.auth import DemoLoginRequest, LoginRequest, TokenResponse, UserOut

router = APIRouter(prefix="/api/auth", tags=["认证"])

_USER_COLUMNS = "id, username, role, display_name"


def _user_out(row: sqlite3.Row) -> UserOut:
    return UserOut(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        display_name=row["display_name"],
    )


def _issue_token(row: sqlite3.Row, settings: Settings) -> TokenResponse:
    """把数据库里的用户行换成一张令牌 + 用户信息。"""
    token = create_access_token(
        user_id=row["id"],
        username=row["username"],
        role=row["role"],
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.jwt_expire_minutes,
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        user=_user_out(row),
    )


@router.post("/login", response_model=TokenResponse, summary="账号密码登录")
def login(
    payload: LoginRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    row = conn.execute(
        f"SELECT {_USER_COLUMNS}, password_hash FROM users WHERE username = ?",
        (payload.username,),
    ).fetchone()

    # 故意不区分"用户不存在"与"密码错误"
    if row is None or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _issue_token(row, settings)


@router.post("/demo-login", response_model=TokenResponse, summary="演示账号一键登录（仅演示环境）")
def demo_login(
    payload: DemoLoginRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    if not settings.enable_demo_login:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="演示登录已关闭（ENABLE_DEMO_LOGIN=false）",
        )

    row = conn.execute(
        f"SELECT {_USER_COLUMNS} FROM users WHERE username = ?", (payload.username,)
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="演示账号不存在，请先执行 python -m app.db.seed",
        )
    return _issue_token(row, settings)


@router.get("/config", summary="获取前端认证配置（是否开启演示登录）")
def auth_config(settings: Settings = Depends(get_settings)) -> dict[str, bool]:
    return {"enable_demo_login": bool(settings.enable_demo_login)}


@router.get("/me", response_model=UserOut, summary="获取当前登录用户")
def me(user: CurrentUser) -> UserOut:
    return _user_out(user)
