"""认证相关请求/响应模型。

Pydantic 模型在这里承担双重职责：
    1. 请求体校验（哪怕前端不校验，后端也不会收到脏数据）；
    2. 直接生成 OpenAPI 文档（Swagger 里能看到字段说明）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """账号密码登录请求。"""

    username: str = Field(min_length=1, max_length=64, description="用户名")
    password: str = Field(min_length=1, max_length=128, description="明文密码（HTTPS 传输）")


class DemoLoginRequest(BaseModel):
    """演示账号一键登录请求（仅 ENABLE_DEMO_LOGIN=true 时可用）。"""

    username: str = Field(min_length=1, max_length=64, description="演示账号用户名，如 zhangsan / wanggong / admin")


class UserOut(BaseModel):
    """对外暴露的用户信息（绝不包含 password_hash）。"""

    id: int
    username: str
    role: str = Field(description="customer / agent / admin")
    display_name: str


class TokenResponse(BaseModel):
    """登录成功后的响应。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="有效期秒数")
    user: UserOut
