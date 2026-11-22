"""LangGraph 图状态定义（SupportState）。

【为什么用 TypedDict 而不是 dataclass】
    LangGraph 的节点之间靠"部分更新"传递状态：每个节点只返回自己改动的字段，
    框架把它合并进全局状态。TypedDict + total=False 正好表达"字段可能不存在"，
    也天然是 JSON 可序列化的——检查点（checkpoints.db）需要把状态存盘再恢复。

【设计原则：状态里只放"事实"，不放"过程"】
    例如不放 "检索耗时" 这种中间量（放 trace），只放 rag_top_score 这种
    后续节点/前端要用来做决策与展示的结果。

【按阶段分组】
    身份上下文 / 意图 / 检索 / 工具观测 / 人工审批 / 最终输出
"""

from __future__ import annotations

from typing_extensions import TypedDict


class SupportState(TypedDict, total=False):
    # ---- 身份与上下文（由 API 层注入，LLM 不可修改） ----
    conversation_id: str
    thread_id: str
    auth_user_id: int
    auth_role: str
    user_query: str
    image_summary: str          # 多模态前置识别的结果（Day 6 接入）
    started_at: float           # 用于计算端到端耗时

    # ---- 意图识别 ----
    intent: str                 # kb_qa / tool_ops / escalate_hitl / reject_unsafe
    intent_reason: str

    # ---- 知识库检索 ----
    rag_hits: list[dict]
    rag_top_score: float
    rag_accepted: bool          # 是否达到拒答阈值
    citations: list[str]        # ["[文档名 · 章节名]", ...]

    # ---- MCP 工具观测 ----
    mcp_observations: list[dict]
    tool_call_names: list[str]
    needs_escalation: bool
    draft_ticket: dict
    handoff_summary: dict

    # ---- 人工审批（interrupt / resume） ----
    human_decision: dict
    created_ticket_no: str

    # ---- 最终输出 ----
    final_answer: str
    decision_type: str          # kb_answer / kb_refuse / reject_unsafe /
                                # tool_answer / hitl_ticket_created / hitl_takeover
    prompt_tokens: int
    completion_tokens: int
    error: str
