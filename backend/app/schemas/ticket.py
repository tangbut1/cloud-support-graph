"""工单相关请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TicketCreateRequest(BaseModel):
    """客户手工提单。"""

    title: str = Field(min_length=1, max_length=200, description="工单标题")
    description: str | None = Field(default=None, description="问题描述")
    instance_id: int | None = Field(default=None, description="关联的云实例 id（必须属于本人）")
    category: str | None = Field(default=None, max_length=32, description="分类，如 网络/备案/应用")
    priority: str = Field(default="P3", pattern=r"^P[0-4]$", description="优先级 P0(最高)~P4(最低)")


class TicketTransitionRequest(BaseModel):
    """工单状态流转请求。"""

    to_status: str = Field(description="目标状态，必须是状态机允许的下一个状态")
    comment: str | None = Field(default=None, max_length=500, description="流转备注，写入审计流水")


class TicketEventOut(BaseModel):
    """一条工单状态变更审计记录。"""

    id: int
    operator_id: int | None
    from_status: str | None
    to_status: str
    comment: str | None
    created_at: str


class TicketOut(BaseModel):
    """工单基本信息。"""

    id: int
    ticket_no: str
    customer_id: int
    instance_id: int | None
    assignee_id: int | None
    category: str | None
    priority: str
    status: str
    title: str
    description: str | None
    ai_diagnosis: str | None
    resolution_note: str | None
    created_at: str
    updated_at: str


class TicketDetailOut(TicketOut):
    """工单详情 = 基本信息 + 状态流转时间轴。"""

    events: list[TicketEventOut] = Field(default_factory=list)
