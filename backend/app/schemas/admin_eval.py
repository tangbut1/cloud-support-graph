"""管理员可观测性 Trace 与黄金集评测 Pydantic 模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TraceItemOut(BaseModel):
    id: int
    conversation_id: str | None = None
    user_id: int | None = None
    intent: str | None = None
    rag_hit_count: int | None = None
    rag_top_score: float | None = None
    tool_calls: list[str] = Field(default_factory=list)
    decision: str | None = None
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error_msg: str | None = None
    created_at: str


class TraceAggregateOut(BaseModel):
    total_calls: int
    user_calls: int = 0
    eval_calls: int = 0
    avg_latency_ms: float
    total_tokens: int
    decision_distribution: dict[str, int] = Field(default_factory=dict)


class TraceListResponse(BaseModel):
    items: list[TraceItemOut]
    total: int
    aggregate: TraceAggregateOut


class EvalRunTriggerRequest(BaseModel):
    run_name: str | None = Field(default="黄金集评测", max_length=120)
    limit: int | None = Field(default=None, ge=1, le=100)


class EvalRunTriggerResponse(BaseModel):
    run_id: int
    status: str


class EvalRunSummaryOut(BaseModel):
    id: int
    run_name: str
    config_json: dict[str, Any] | None = None
    total_cases: int | None = None
    passed_cases: int | None = None
    error_cases: int = 0
    intent_acc: float | None = None
    refusal_acc: float | None = None
    safe_routing_rate: float | None = None
    security_block_rate: float | None = None
    tool_acc: float | None = None
    tool_ops_acc: float | None = None
    hitl_escalate_acc: float | None = None
    avg_faithfulness: float | None = None
    citation_hit_rate: float | None = None
    judge_mode: str = "citation_heuristic"
    avg_latency_ms: float | None = None
    status: str = "completed"
    created_at: str


class EvalCaseResultOut(BaseModel):
    id: int
    case_id: str
    category: str | None = None
    passed: bool
    expected: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    judge_score: float | None = None
    reason: str | None = None


class EvalRunDetailOut(EvalRunSummaryOut):
    cases: list[EvalCaseResultOut] = Field(default_factory=list)
