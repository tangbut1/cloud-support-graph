"""链路追踪写入：每一轮对话写一条 agent_traces。

用途：
    1. 管理端观测看板（耗时/Token/决策分布）；
    2. 第 5 章实验数据（端到端延迟、拒答率、工具调用率）；
    3. 出问题时的排查依据（哪一步错了、模型说了什么）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def record_trace(
    conn: sqlite3.Connection,
    *,
    conversation_id: str | None,
    user_id: int | None,
    intent: str | None,
    rag_hit_count: int = 0,
    rag_top_score: float = 0.0,
    tool_calls: list | None = None,
    decision: str | None = None,
    latency_ms: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    error_msg: str | None = None,
) -> int:
    """写入一条追踪记录，返回 trace_id。"""
    cur = conn.execute(
        """
        INSERT INTO agent_traces (
            conversation_id, user_id, intent, rag_hit_count, rag_top_score,
            tool_calls_json, decision, latency_ms, prompt_tokens, completion_tokens,
            error_msg, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            user_id,
            intent,
            rag_hit_count,
            float(rag_top_score or 0.0),
            json.dumps(tool_calls or [], ensure_ascii=False),
            decision,
            int(latency_ms),
            int(prompt_tokens),
            int(completion_tokens),
            error_msg,
            _now_text(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)
