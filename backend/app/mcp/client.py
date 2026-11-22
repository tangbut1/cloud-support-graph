"""MCP 客户端：通过内存传输在进程内调用工具。

【为什么用 create_connected_server_and_client_session】
    它建立一个完整的 MCP 会话（initialize / tools/list / tools/call 都真实发生），
    只是底层不走 socket。好处：
      * 仍然是标准 MCP 调用链路，换成 stdio/SSE 时业务代码零改动；
      * 2C2G 机器上不用为内部工具层额外常驻一个进程，省内存。

【返回体解析】
    MCP 的 CallToolResult 同时带 content（文本块）与 structuredContent（结构化）。
    我们优先取 structuredContent；没有则回退到解析第一个文本块里的 JSON。
"""

from __future__ import annotations

import json
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session

from app.mcp.server import mcp_server


def _extract_payload(result) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        # FastMCP 对"返回 dict 的工具"通常原样放入 structuredContent；
        # 兼容个别版本包一层 {"result": {...}} 的情况。
        if set(structured.keys()) == {"result"} and isinstance(structured["result"], dict):
            return structured["result"]
        return structured

    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        return parsed if isinstance(parsed, dict) else {"result": parsed}

    return {}


async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """调用一个 MCP 工具并返回解析后的 dict。"""
    async with create_connected_server_and_client_session(mcp_server) as session:
        result = await session.call_tool(name, arguments)
        payload = _extract_payload(result)
        if getattr(result, "isError", False):
            if isinstance(payload, dict) and "error" in payload:
                return payload
            return {"error": "TOOL_ERROR", "message": str(payload)}
        return payload


async def list_tool_names() -> list[str]:
    """列出服务端注册的工具名（调试与测试用）。"""
    async with create_connected_server_and_client_session(mcp_server) as session:
        response = await session.list_tools()
        return [tool.name for tool in response.tools]
