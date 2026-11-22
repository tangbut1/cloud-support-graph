r"""LangGraph 编排：意图路由 / 检索 / 工具 / 人工审批挂起恢复。

【这张图长什么样】

    START
      |
    guard  意图识别 + 安全前置守卫（规则优先）
      |
      +-- reject_unsafe --> reject ------+
      +-- escalate_hitl --> escalate --> hitl --> apply_decision --+
      +-- tool_ops --> tools --(需要人工?)--> hitl                 |
      |                  \--> (不需要) --> trace                   |
      +-- kb_qa --> rag --(达到阈值?)--> answer_kb --> ...         |
                    \--(未达阈值)--> refuse --> ...                |
                                                                  v
                                                                trace --> END

【为什么用 LangGraph 而不是 if/else 或手写 while】
    1. 转人工这一步要"挂起后能恢复"：interrupt() 会把整个状态存进 checkpoints.db，
       坐席几小时后批准，用 Command(resume=...) 从断点继续，而不是从头重跑。
    2. 条件分支（四条链路）用图表达比嵌套 if 清楚，也方便前端可视化展示节点进度。
    3. 步数上限（recursion_limit）天然防死循环。

【状态存哪里】
    业务数据 -> data/app.db（sqlite3）
    图快照  -> data/checkpoints.db（AsyncSqliteSaver）
    两个库分离，避免长程图快照与业务写入抢锁（Day 1 的设计）。

【无大模型时的降级】
    LlmClient.enabled == False 时：
        意图分类 -> 纯规则（intent_guard.classify_by_rules）
        回答生成 -> 抽取式（把命中的切片整理成结论 + 引用）
    保证没有 API Key 也能端到端跑通与测试；拿到 Key 后自动切换模型路径。
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent.intent_guard import (
    ESCALATE,
    KB_QA,
    TOOL_OPS,
    UNSAFE,
    IntentDecision,
    classify_by_rules,
)
from app.agent.llm import LlmClient
from app.agent.state import SupportState
from app.agent.trace_logger import record_trace
from app.config import get_settings
from app.db.database import connect
from app.mcp.auth_context import AuthContext
from app.services.retriever import search
from app.services.ticket_sm import create_ticket

logger = logging.getLogger(__name__)


# ===========================================================================
# 基础设施
# ===========================================================================
def _conf(config: RunnableConfig | None, key: str, default: Any = None) -> Any:
    """从 config["configurable"] 取运行时参数（数据库路径、身份令牌等）。"""
    if not config:
        return default
    return (config.get("configurable") or {}).get(key, default)


def _db_path(config: RunnableConfig | None):
    return _conf(config, "db_path") or get_settings().app_db


def _open_conn(config: RunnableConfig | None):
    return connect(_db_path(config))


def _llm() -> LlmClient:
    return LlmClient(get_settings())


def _diagnosis_answer(observations: list[dict]) -> str:
    """把诊断结果整理成客户能看懂的回答（无模型时的降级实现）。"""
    diagnosis = None
    for item in observations:
        if item.get("tool") == "diagnose_instance_network" and item.get("ok"):
            diagnosis = item.get("result")
    if not diagnosis:
        return "已查询您的云实例，但未发现明确异常。"

    lines = [f"【{diagnosis['instance_name']}】风险等级：{diagnosis['risk_level']}"]
    for check in diagnosis.get("checks", []):
        if check["level"] == "ok":
            continue
        mark = "严重" if check["level"] == "critical" else "警告"
        lines.append(f"- [{mark}] {check['message']}")
    if diagnosis.get("suggested_actions"):
        lines.append("建议动作：")
        lines.extend(f"  {index + 1}. {action}" for index, action in enumerate(diagnosis["suggested_actions"]))
    return "\n".join(lines)


def _extractive_answer(hits: list[dict], citations: list[str]) -> str:
    """无模型时的"抽取式回答"：不做推理，只把命中原文整理成要点并标来源。"""
    if not hits:
        return "当前官方文档未覆盖该问题，建议转人工或提交工单。"
    lines = ["根据官方文档，相关信息如下："]
    for hit, citation in zip(hits[:3], citations[:3]):
        snippet = str(hit.get("content", "")).strip().replace("\n", " ")
        if len(snippet) > 120:
            snippet = snippet[:120] + "…"
        lines.append(f"- {snippet} {citation}")
    lines.append("如需进一步定位，请提供具体实例与报错信息。")
    return "\n".join(lines)


# ===========================================================================
# 节点：意图识别 + 安全守卫
# ===========================================================================
async def guard_node(state: SupportState, config: RunnableConfig) -> dict:
    """规则优先、模型补充；安全类规则不可被模型覆盖。"""
    query = state.get("user_query", "")
    rule_decision = classify_by_rules(query)
    decision: IntentDecision = rule_decision
    source = "rules"

    # 规则已经判为不安全 -> 直接终判，不给模型"改判"的机会
    # 规则命中且明确（rule_id 非空）时也直接采用：
    #   一次意图分类要 4-5 秒，规则已经足够确定的情况下没必要花这个时间。
    #   只有规则兜底（rule_id is None，即没认出特征）时才请模型判断。
    prompt_tokens = 0
    completion_tokens = 0
    if rule_decision.intent != UNSAFE and rule_decision.rule_id is None:
        llm = _llm()
        model_result = await llm.classify_intent(query)
        prompt_tokens += llm.last_usage.get("prompt_tokens", 0)
        completion_tokens += llm.last_usage.get("completion_tokens", 0)
        if model_result is not None:
            intent, reason = model_result
            decision = IntentDecision(intent=intent, reason=f"模型分类：{reason}", rule_id=None)
            source = "llm"

    logger.info("意图识别 query=%r intent=%s source=%s", query[:40], decision.intent, source)
    return {
        "intent": decision.intent,
        "intent_reason": decision.reason,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        # 每次进入图都刷新：复用同一个 thread_id 继续对话时，
        # 不能沿用上一次的起点，否则 trace 里的耗时会包含两轮之间的间隔。
        "started_at": time.time(),
    }


def route_after_guard(state: SupportState) -> str:
    """按意图分流（四选一）。"""
    intent = state.get("intent")
    if intent == UNSAFE:
        return "reject"
    if intent == ESCALATE:
        return "escalate"
    if intent == TOOL_OPS:
        return "tools"
    if intent == KB_QA:
        return "rag"
    return "rag"


# ===========================================================================
# 节点：知识库问答链路
# ===========================================================================
async def rag_node(state: SupportState, config: RunnableConfig) -> dict:
    conn = _open_conn(config)
    try:
        settings = get_settings()
        result = search(
            conn,
            state.get("user_query", ""),
            top_k=settings.rag_top_k,
            min_score=settings.rag_min_score,
        )
    finally:
        conn.close()

    hits = [
        {
            "chunk_id": hit.chunk_id,
            "doc_title": hit.doc_title,
            "section_title": hit.section_title,
            "content": hit.content,
            "score": hit.score,
        }
        for hit in result.hits
    ]
    return {
        "rag_hits": hits,
        "rag_top_score": result.top_score,
        "rag_accepted": result.accepted,
        "citations": result.citations,
    }


def route_after_rag(state: SupportState) -> str:
    return "answer_kb" if state.get("rag_accepted") else "refuse"


async def answer_kb_node(state: SupportState, config: RunnableConfig) -> dict:
    hits = state.get("rag_hits") or []
    citations = state.get("citations") or []
    context = "\n\n".join(
        f"{citation}\n{hit['content']}" for hit, citation in zip(hits, citations)
    )

    llm = _llm()
    answer = await llm.answer_with_citations(state.get("user_query", ""), context)
    if not answer:
        answer = _extractive_answer(hits, citations)

    prompt_tokens = int(state.get("prompt_tokens") or 0) + llm.last_usage.get("prompt_tokens", 0)
    completion_tokens = int(state.get("completion_tokens") or 0) + llm.last_usage.get("completion_tokens", 0)
    return {
        "final_answer": answer,
        "decision_type": "kb_answer",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


async def refuse_node(state: SupportState, config: RunnableConfig) -> dict:
    """检索分未达阈值：明确拒答，不自由发挥。"""
    score = float(state.get("rag_top_score") or 0.0)
    threshold = get_settings().rag_min_score
    return {
        "final_answer": (
            f"当前官方文档库未覆盖该问题（最高相关度 {score:.2f}，低于阈值 {threshold:.2f}）。"
            "为避免给出不准确的信息，建议转人工坐席或提交工单进一步核查。"
        ),
        "decision_type": "kb_refuse",
    }


# ===========================================================================
# 节点：安全拒答
# ===========================================================================
async def reject_node(state: SupportState, config: RunnableConfig) -> dict:
    return {
        "final_answer": (
            "抱歉，该请求涉及安全策略限制（例如要求越权访问他人资源、索取赔付承诺或试图覆盖系统规则），"
            "无法执行。如需查询本人资源，请直接描述您的实例与故障现象。"
        ),
        "decision_type": "reject_unsafe",
    }


# ===========================================================================
# 节点：转人工（无需工具，直接挂起）
# ===========================================================================
async def escalate_node(state: SupportState, config: RunnableConfig) -> dict:
    return {
        "needs_escalation": True,
        "handoff_summary": {
            "customer_intent": state.get("user_query", ""),
            "intent_reason": state.get("intent_reason"),
            "diagnosis": None,
            "suggested_action": "客户要求人工处理或涉及高危操作，请坐席接管",
        },
        "draft_ticket": None,
    }


# ===========================================================================
# 节点：工具链路（确定性计划，无模型时也能跑）
# ===========================================================================
def _pick_instance(payload: dict, query: str) -> dict | None:
    """从实例列表里挑一台要诊断的：优先按名字匹配，否则取第一台。"""
    instances = payload.get("instances") or []
    if not instances:
        return None
    for instance in instances:
        name = str(instance.get("instance_name", ""))
        if name and name in query:
            return instance
    return instances[0]


async def tools_node(state: SupportState, config: RunnableConfig) -> dict:
    """调用 MCP 工具做诊断；无模型时按确定性计划执行。

    确定性计划（等价于模型会做的合理步骤）：
        1. list_my_instances 拿到自己的实例
        2. 选中一台 diagnose_instance_network
        3. 若为 critical -> 生成工单草稿，进入人工审批
           否则直接把诊断结论作为回答
    """
    from app.agent.tools_bridge import build_tools_for_user

    token = _conf(config, "auth_context_token")
    if not token:
        return {
            "final_answer": "内部身份上下文缺失，无法查询实例。请重新登录后再试。",
            "decision_type": "error",
            "error": "missing auth_context_token",
        }

    tools = {tool.name: tool for tool in build_tools_for_user(token)}
    observations: list[dict] = []
    tool_names: list[str] = []

    listing = await tools["list_my_instances"].ainvoke({})
    tool_names.append("list_my_instances")
    observations.append({"tool": "list_my_instances", "ok": "error" not in listing,
                         "result": listing})
    if listing.get("error"):
        return {
            "mcp_observations": observations,
            "tool_call_names": tool_names,
            "final_answer": "查询实例失败：" + str(listing.get("message")),
            "decision_type": "error",
        }

    target = _pick_instance(listing, state.get("user_query", ""))
    if target is None:
        return {
            "mcp_observations": observations,
            "tool_call_names": tool_names,
            "final_answer": "您的账号下暂无可诊断的云实例。",
            "decision_type": "tool_answer",
        }

    diagnosis = await tools["diagnose_instance_network"].ainvoke({"instance_id": target["id"]})
    tool_names.append("diagnose_instance_network")
    observations.append({"tool": "diagnose_instance_network", "ok": "error" not in diagnosis,
                         "result": diagnosis, "instance_id": target["id"]})

    if diagnosis.get("error"):
        return {
            "mcp_observations": observations,
            "tool_call_names": tool_names,
            "final_answer": "诊断失败：" + str(diagnosis.get("message")),
            "decision_type": "error",
        }

    if diagnosis.get("risk_level") == "critical":
        abnormal = [
            check for check in (diagnosis.get("checks") or []) if check.get("level") != "ok"
        ]
        primary_code = abnormal[0]["code"] if abnormal else "UNKNOWN"
        draft = await tools["build_ticket_draft"].ainvoke(
            {
                "title": f"{target['instance_name']} 故障诊断：{primary_code}",
                "summary": diagnosis.get("conclusion", ""),
                "category": "应用服务",
                "priority": "P2",
                "instance_id": target["id"],
            }
        )
        tool_names.append("build_ticket_draft")
        observations.append({"tool": "build_ticket_draft", "ok": "error" not in draft,
                             "result": draft})

        handoff = {
            "customer_intent": state.get("user_query", ""),
            "instance": target["instance_name"],
            "diagnosis": diagnosis.get("conclusion", ""),
            "checks": diagnosis.get("checks", []),
            "suggested_actions": diagnosis.get("suggested_actions", []),
            "suggested_action": "诊断发现严重问题，建议批准创建工单并回复客户",
        }
        return {
            "mcp_observations": observations,
            "tool_call_names": tool_names,
            "needs_escalation": True,
            "draft_ticket": draft.get("draft"),
            "handoff_summary": handoff,
        }

    # 非严重问题：直接给诊断结论
    return {
        "mcp_observations": observations,
        "tool_call_names": tool_names,
        "needs_escalation": False,
        "final_answer": _diagnosis_answer(observations),
        "decision_type": "tool_answer",
    }


def route_after_tools(state: SupportState) -> str:
    if state.get("needs_escalation"):
        return "hitl"
    if state.get("decision_type") == "error":
        return "trace"
    return "trace"


# ===========================================================================
# 节点：人工审批挂起与恢复（本图的核心）
# ===========================================================================
async def hitl_node(state: SupportState, config: RunnableConfig) -> dict:
    """调用 interrupt() 挂起整张图，把状态写入 checkpoints.db。

    interrupt() 的返回值就是"恢复时传进来的 Command(resume=...) 的载荷"。
    注意：节点在挂起时会从头重新执行一次 —— 所以 interrupt() 之前的代码
    必须是幂等的（这里只构造 payload，没有副作用，安全）。
    """
    payload = {
        "type": "human_approval",
        "question": "是否批准创建工单并回复客户？",
        "handoff_summary": state.get("handoff_summary") or {},
        "draft_ticket": state.get("draft_ticket"),
    }
    decision = interrupt(payload)
    return {"human_decision": decision or {}}


async def apply_decision_node(state: SupportState, config: RunnableConfig) -> dict:
    """坐席批准后：原子创建工单并给出最终回复。"""
    decision = state.get("human_decision") or {}
    action = decision.get("action", "takeover_only")
    comment = decision.get("comment") or ""

    if action == "approve_ticket_and_reply":
        draft = state.get("draft_ticket") or {}
        conn = _open_conn(config)
        try:
            row = create_ticket(
                conn,
                customer_id=int(state["auth_user_id"]),
                title=draft.get("title") or "客户反馈故障",
                description=draft.get("summary"),
                instance_id=draft.get("instance_id"),
                category=draft.get("category"),
                priority=draft.get("priority") or "P3",
                conversation_id=state.get("conversation_id"),
            )
        finally:
            conn.close()
        return {
            "created_ticket_no": row["ticket_no"],
            "final_answer": (
                f"已完成处理：工单 {row['ticket_no']} 已创建，坐席备注：{comment or '无'}。"
                "请按建议动作处理后回复本工单。"
            ),
            "decision_type": "hitl_ticket_created",
        }

    return {
        "final_answer": "已为您转接人工坐席，坐席将直接与您沟通后续处理。",
        "decision_type": "hitl_takeover",
    }


# ===========================================================================
# 节点：追踪落库
# ===========================================================================
async def trace_node(state: SupportState, config: RunnableConfig) -> dict:
    # 优先用"本次调用"的起点：HITL 恢复时人工审批可能等了几小时，
    # 把它算进端到端耗时会严重失真，所以 run_turn/resume_turn 每次重新打戳。
    started_at = float(_conf(config, "run_started_at") or state.get("started_at") or time.time())
    latency_ms = int((time.time() - started_at) * 1000)
    conn = _open_conn(config)
    try:
        record_trace(
            conn,
            conversation_id=state.get("conversation_id"),
            user_id=state.get("auth_user_id"),
            intent=state.get("intent"),
            rag_hit_count=len(state.get("rag_hits") or []),
            rag_top_score=float(state.get("rag_top_score") or 0.0),
            tool_calls=state.get("tool_call_names") or [],
            decision=state.get("decision_type"),
            latency_ms=latency_ms,
            prompt_tokens=int(state.get("prompt_tokens") or 0),
            completion_tokens=int(state.get("completion_tokens") or 0),
            error_msg=state.get("error"),
        )
    finally:
        conn.close()
    return {}


# ===========================================================================
# 组装
# ===========================================================================
def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """构建并编译状态图。传入同步或异步 checkpointer 均可使用 interrupt/resume。"""
    builder = StateGraph(SupportState)

    builder.add_node("guard", guard_node)
    builder.add_node("rag", rag_node)
    builder.add_node("answer_kb", answer_kb_node)
    builder.add_node("refuse", refuse_node)
    builder.add_node("reject", reject_node)
    builder.add_node("escalate", escalate_node)
    builder.add_node("tools", tools_node)
    builder.add_node("hitl", hitl_node)
    builder.add_node("apply_decision", apply_decision_node)
    builder.add_node("trace", trace_node)

    builder.add_edge(START, "guard")
    builder.add_conditional_edges(
        "guard",
        route_after_guard,
        {"reject": "reject", "escalate": "escalate", "tools": "tools", "rag": "rag"},
    )
    builder.add_conditional_edges("rag", route_after_rag, {"answer_kb": "answer_kb", "refuse": "refuse"})
    builder.add_conditional_edges("tools", route_after_tools, {"hitl": "hitl", "trace": "trace"})

    builder.add_edge("escalate", "hitl")
    builder.add_edge("hitl", "apply_decision")
    builder.add_edge("apply_decision", "trace")

    for terminal in ("answer_kb", "refuse", "reject"):
        builder.add_edge(terminal, "trace")
    builder.add_edge("trace", END)

    return builder.compile(checkpointer=checkpointer)


# ===========================================================================
# 便捷入口（供 API 层与测试使用）
# ===========================================================================
@asynccontextmanager
async def open_checkpointer(checkpoint_db_path):
    """打开检查点数据库，显式启用 WAL 模式与 busy_timeout=5000。"""
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_db_path)) as saver:
        await saver.conn.execute("PRAGMA journal_mode = WAL")
        await saver.conn.execute("PRAGMA busy_timeout = 5000")
        yield saver


def make_config(
    *,
    thread_id: str,
    db_path,
    auth_context_token: str,
    recursion_limit: int = 15,
) -> dict:
    """构造运行配置。身份令牌走 configurable，不进入图状态（避免渗入 LLM 上下文）。"""
    return {
        "configurable": {
            "thread_id": thread_id,
            "db_path": str(db_path),
            "auth_context_token": auth_context_token,
        },
        "recursion_limit": recursion_limit,
    }


def _stamp_run(config: dict) -> dict:
    """复制一份 config 并写入本次调用开始时间（不污染调用方持有的配置）。"""
    stamped = dict(config)
    stamped["configurable"] = dict(config.get("configurable") or {})
    stamped["configurable"]["run_started_at"] = time.time()
    return stamped


async def run_turn(graph, state_input: dict, config: dict) -> dict:
    """执行一轮对话；若在中途挂起，返回值里会带 __interrupt__。"""
    return await graph.ainvoke(state_input, _stamp_run(config))


async def resume_turn(graph, decision: dict, config: dict) -> dict:
    """坐席审批后从断点恢复（耗时从恢复这一刻开始算，不含人工等待）。"""
    return await graph.ainvoke(Command(resume=decision), _stamp_run(config))
