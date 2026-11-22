"""会话、消息与坐席工作台 Pydantic 模型。"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# 零宽/不可见格式化字符（防纯零宽字符绕过空白内容校验）
_INVISIBLE_CHARS = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\u00ad]")


class ConversationCreateRequest(BaseModel):
    """创建会话请求（标题为可选展示信息）。"""

    title: str | None = Field(default=None, max_length=120)


class ConversationCreateOut(BaseModel):
    """创建会话响应。"""

    id: str
    thread_id: str
    title: str | None = None
    status: str
    created_at: str


class ConversationOut(BaseModel):
    """会话列表/详情条目。"""

    id: str
    customer_id: int
    customer_name: str | None = None
    assigned_agent_id: int | None = None
    thread_id: str
    title: str | None = None
    status: str
    handoff_summary: dict[str, Any] | None = None
    draft_ticket: dict[str, Any] | None = None
    last_message: str | None = None
    created_at: str
    updated_at: str


class CitationDetailOut(BaseModel):
    """引用原文片段（供前端引用徽章点击展开）。"""

    citation: str
    doc_title: str
    section_title: str | None = None
    content: str


class MessageOut(BaseModel):
    """会话消息条目。"""

    id: int
    sender_role: str
    content: str
    has_image: bool
    image_url: str | None = None
    citations: list[str] = Field(default_factory=list)
    citation_details: list[CitationDetailOut] = Field(default_factory=list)
    trace_id: int | None = None
    created_at: str


class AgentQueueItemOut(BaseModel):
    """坐席待接管队列条目。"""

    conversation_id: str
    customer_name: str
    updated_at: str
    handoff_summary: dict[str, Any] = Field(default_factory=dict)
    draft_ticket: dict[str, Any] | None = None


class AgentResumeRequest(BaseModel):
    """坐席审批恢复请求。"""

    action: Literal["approve_ticket_and_reply", "takeover_only"]
    comment: str | None = Field(default=None, max_length=1000)


class AgentResumeResponse(BaseModel):
    """坐席审批恢复响应。"""

    conversation_status: str
    ticket_no: str | None = None
    message: str
    forced_takeover: bool = False


class AgentReplyRequest(BaseModel):
    """人工接管状态下坐席回复请求。"""

    content: str = Field(min_length=1, max_length=4000)
    close: bool = False

    @field_validator("content")
    @classmethod
    def _reject_blank_content(cls, value: str) -> str:
        cleaned = _INVISIBLE_CHARS.sub("", value).strip()
        if not cleaned:
            raise ValueError("回复内容不能为空白或不可见字符")
        return cleaned


class AgentReplyResponse(BaseModel):
    """坐席回复响应。"""

    message_id: int
    conversation_status: str
    created_at: str
