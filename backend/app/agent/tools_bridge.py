"""工具桥接层：把 MCP 工具包装成 LangChain 工具，并把身份"焊死"在闭包里。

【这是整个防越权设计的最后一环】
    链路：
        FastAPI(JWT) -> 取出 user_id -> create_auth_context() 签发短令牌
        -> build_tools_for_user(令牌) -> 生成 LangChain 工具（闭包持有令牌）
        -> 大模型只会看到业务参数（instance_id / title ...）
        -> 调用时才由闭包把令牌塞进 MCP 参数
        -> MCP 服务端验签 -> 业务层校验资源归属

    关键点：给模型看的工具 schema 里**没有** auth_context_token，也没有 user_id。
    模型既看不到目标用户，也无从伪造——它唯一能做的就是描述"要做什么"。

【为什么工具做成 async】
    Day 5 的 LangGraph 用异步节点执行，MCP 会话本身也是 async；
    工具保持 async，图里 await 即可，不用在线程池里绕。

【返回值里如果有 error 字段】
    不要抛异常。让模型看到"PERMISSION_DENIED"这种结构化结果，
    它才能据此回复用户"这个实例不属于您"；抛异常反而会让图直接中断。
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.mcp import client as mcp_client


# ---------------------------------------------------------------------------
# 工具参数 schema（给大模型看的，刻意只包含业务参数）
# ---------------------------------------------------------------------------
class ListInstancesArgs(BaseModel):
    """列出当前位置名下的云实例（无需参数）。"""


class DiagnoseArgs(BaseModel):
    instance_id: int = Field(description="要诊断的云服务器实例 id，需先从 list_my_instances 获取")


class ListTicketsArgs(BaseModel):
    status: str | None = Field(
        default=None,
        description="可选：按状态过滤，OPEN/TRIAGED/IN_PROGRESS/RESOLVED/CLOSED",
    )


class BuildDraftArgs(BaseModel):
    title: str = Field(description="工单标题，简明描述故障现象")
    summary: str = Field(description="诊断摘要：现象 + 已排查结论 + 建议动作")
    category: str | None = Field(default=None, description="分类，如 网络安全 / 应用服务 / 合规备案")
    priority: str = Field(default="P3", description="优先级 P0(最高)~P4(最低)")
    instance_id: int | None = Field(default=None, description="关联的云实例 id（可选）")


def build_tools_for_user(auth_context_token: str) -> list[StructuredTool]:
    """为某个已登录用户构建一组工具；令牌被闭包固定，不进入 schema。"""

    async def _list_my_instances() -> dict:
        return await mcp_client.call_tool(
            "list_my_instances", {"auth_context_token": auth_context_token}
        )

    async def _diagnose_instance_network(instance_id: int) -> dict:
        return await mcp_client.call_tool(
            "diagnose_instance_network",
            {"auth_context_token": auth_context_token, "instance_id": instance_id},
        )

    async def _list_my_tickets(status: str | None = None) -> dict:
        return await mcp_client.call_tool(
            "list_my_tickets",
            {"auth_context_token": auth_context_token, "status": status},
        )

    async def _build_ticket_draft(
        title: str,
        summary: str,
        category: str | None = None,
        priority: str = "P3",
        instance_id: int | None = None,
    ) -> dict:
        return await mcp_client.call_tool(
            "build_ticket_draft",
            {
                "auth_context_token": auth_context_token,
                "title": title,
                "summary": summary,
                "category": category,
                "priority": priority,
                "instance_id": instance_id,
            },
        )

    return [
        StructuredTool.from_function(
            coroutine=_list_my_instances,
            name="list_my_instances",
            description="列出当前登录用户名下的云服务器实例。排查故障时第一步先调它拿到实例 id。",
            args_schema=ListInstancesArgs,
        ),
        StructuredTool.from_function(
            coroutine=_diagnose_instance_network,
            name="diagnose_instance_network",
            description=(
                "诊断指定云服务器的网络与服务状态，返回端口放行、进程状态、Swap、备案等检查项与结论。"
                "只能诊断当前用户自己的实例。"
            ),
            args_schema=DiagnoseArgs,
        ),
        StructuredTool.from_function(
            coroutine=_list_my_tickets,
            name="list_my_tickets",
            description="列出当前登录用户名下的工单，可按状态过滤。",
            args_schema=ListTicketsArgs,
        ),
        StructuredTool.from_function(
            coroutine=_build_ticket_draft,
            name="build_ticket_draft",
            description=(
                "生成结构化工单草稿（不入库）。需要人工坐席审批后才创建正式工单，"
                "适用于需要改动配置或需要人工介入的故障。"
            ),
            args_schema=BuildDraftArgs,
        ),
    ]
