r"""认证基础能力：bcrypt 密码哈希 + JWT 令牌签发/解析。

【这是什么】
    安全层最底层的两个工具函数，不依赖数据库、不依赖 FastAPI，纯函数，方便单测。

【两部分】
    1. 密码：hash_password / verify_password
       - 用 bcrypt 加盐哈希，数据库里永远不存明文；
       - bcrypt 只处理前 72 字节，超长密码先截断（否则新版本直接抛异常）。
    2. 令牌：create_access_token / decode_access_token
       - 登录成功后签发一个 JWT，前端之后每次请求放在 Authorization: Bearer <token> 头里；
       - JWT 是无状态的：服务端不存会话，靠签名 + 过期时间校验，适合小内存服务器；
       - 令牌里带 sub（用户 id）、username、role、iat（签发时间）、exp（过期时间）。

【安全要点】
    - JWT 只做"防篡改"，不加密内容：令牌里的 role 只是方便前端展示，
      真正的权限判定必须在服务端重新查库（见 app/auth/deps.py），否则伪造 role 就能提权。
    - 解析失败统一抛 TokenError，由上层转成 401，不把底层异常细节暴露给客户端。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

# bcrypt 只处理前 72 字节
_MAX_PASSWORD_BYTES = 72


class TokenError(Exception):
    """JWT 无效：签名不对、已过期、格式错误。统一成一个异常，便于上层转 401。"""


# ============================================================================
# 一、密码哈希
# ============================================================================
def _normalize(password: str) -> bytes:
    return password.encode("utf-8")[:_MAX_PASSWORD_BYTES]


def hash_password(password: str) -> str:
    """生成 bcrypt 哈希（每次加盐，同一密码两次结果不同，但都能校验）。"""
    return bcrypt.hashpw(_normalize(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文与哈希是否匹配。哈希格式非法时返回 False，不抛异常。"""
    try:
        return bcrypt.checkpw(_normalize(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ============================================================================
# 二、JWT 令牌
# ============================================================================
def create_access_token(
    *,
    user_id: int,
    username: str,
    role: str,
    secret: str,
    algorithm: str = "HS256",
    expires_minutes: int = 720,
) -> str:
    """签发访问令牌。

    参数全部用关键字传入，避免调用时把 user_id 和 role 位置搞反这类低级错误。
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),          # 标准声明：主体（这里放用户 id），JWT 规范要求字符串
        "username": username,
        "role": role,
        "iat": int(now.timestamp()),  # 签发时间
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),  # 过期时间
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(token: str, *, secret: str, algorithm: str = "HS256") -> dict[str, Any]:
    """解析并校验令牌（签名 + 过期时间）。失败抛 TokenError。"""
    try:
        return jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
