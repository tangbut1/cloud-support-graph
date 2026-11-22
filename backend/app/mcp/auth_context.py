"""内部认证上下文令牌（Auth Context Token）。

【要解决什么问题】
    MCP 工具必须知道"当前是谁在调用"，否则无法判断"这台实例是不是你的"。
    但工具是给大模型调用的，如果把 user_id 做成工具参数：
        diagnose_instance_network(instance_id=1, user_id=2)
    模型（或被注入的提示词）完全可以自己填 user_id=2 去查别人的服务器 —— 这就是 IDOR
    （Insecure Direct Object Reference，越权访问）。

【方案：把身份做成"服务端签发、模型看不见"的凭证】
    1. FastAPI 层从已验证的 JWT 里取出 user_id / role；
    2. 用 HMAC-SHA256 签一个短时效（默认 120 秒）的上下文令牌；
    3. 这个令牌只出现在 tools_bridge 的闭包里，**不暴露在工具 schema 给模型**；
    4. MCP 服务端收到令牌后验签 + 验过期，得到可信的 user_id；
    5. 工具内部再校验 instance.owner_id == 该 user_id（纵深防御第二道）。

【为什么还要 120 秒过期】
    即使令牌意外进了日志或被回放，窗口也极短；它只服务于"一轮图执行"。
    签名密钥复用 JWT_SECRET（可用 MCP_CONTEXT_SECRET 单独配置，见 config）。

【与 JWT 的区别】
    JWT 是发给**前端**的长期凭证（12 小时）；
    AuthContext 是后端**内部**短凭证，永不出服务端。两者用途不同，不要混用。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


class AuthContextError(Exception):
    """上下文令牌无效：签名不对、格式错误或已过期。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"身份上下文无效：{reason}")


@dataclass(frozen=True)
class AuthContext:
    """验签后的可信身份。"""

    user_id: int
    role: str


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(body: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return _b64encode(digest)


def create_auth_context(
    *, user_id: int, role: str, secret: str, ttl_seconds: int = 120
) -> str:
    """签发一个短时效的身份上下文令牌。"""
    payload = {
        "uid": int(user_id),
        "role": role,
        "exp": int(time.time()) + int(ttl_seconds),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body, secret)}"


def decode_auth_context(token: str, *, secret: str) -> AuthContext:
    """验签并解析令牌；任何异常情况一律抛 AuthContextError。"""
    if not token or token.count(".") != 1:
        raise AuthContextError("格式错误")

    body, signature = token.split(".", 1)
    expected = _sign(body, secret)
    # 必须用 constant-time 比较，避免通过响应时间侧信道猜签名
    if not hmac.compare_digest(signature, expected):
        raise AuthContextError("签名不匹配")

    try:
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuthContextError("内容无法解析") from exc

    if not isinstance(payload, dict):
        raise AuthContextError("内容格式错误")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise AuthContextError("已过期")

    user_id = payload.get("uid")
    role = payload.get("role")
    if not isinstance(user_id, int) or not isinstance(role, str):
        raise AuthContextError("缺少必要字段")
    return AuthContext(user_id=user_id, role=role)
