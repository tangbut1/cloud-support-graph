r"""SQLite 数据库层：连接管理 + 建表 DDL + FTS5 全文索引。

【这是什么】
    系统的第 1 层（数据层）。上层的鉴权、知识库检索、工单流转、智能体编排
    全部依赖这里定义的表结构。

【文件结构】（从上到下就是代码里的出现顺序）
    1. 常量            —— 锁等待时间
    2. connect()       —— 建连接，并打开 WAL / 外键 / 锁等待
    3. 建表 SQL 常量   —— 按业务域分成 5 组，另加索引、FTS5
         _DDL_ACCOUNT        账号与云资产      users / cloud_instances
         _DDL_KNOWLEDGE      知识库            kb_documents / kb_chunks
         _DDL_CONVERSATION   会话与消息        conversations / messages
         _DDL_TICKET         工单              tickets / ticket_events
         _DDL_OBSERVABILITY  观测与评测        agent_traces / eval_runs / eval_case_results
         _DDL_INDEX          索引
         _DDL_FTS            FTS5 虚拟表 + 3 个同步触发器
    4. init_db()       —— 按顺序执行上面的 SQL，可重复调用（幂等）

【怎么用】
    from app.db.database import connect, init_db

    init_db(settings.app_db)           # 应用启动时建库（main.py 里已调用）
    conn = connect(settings.app_db)    # 需要读写时拿连接，用完 conn.close()
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# SQLite 遇到写锁时最多等 5 秒，而不是立刻抛 "database is locked"
_BUSY_TIMEOUT_MS = 5_000


# ============================================================================
# 一、连接管理
# ============================================================================
def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开数据库连接，并设置三个必须的 PRAGMA。

    为什么这三条不能省：
        foreign_keys  —— SQLite 默认关闭外键约束！不显式打开，表里写的
                         REFERENCES 等于注释，脏数据随便插。
        busy_timeout  —— 并发写时先等待而不是立刻报错。
        journal_mode  —— WAL 模式让"读"和"写"互不阻塞，小内存机器上更稳；
                         它是数据库级持久属性，设一次就记住，每次设置无害。

    row_factory = sqlite3.Row 让查询结果支持 row["列名"] 取值，比数字下标好读。

    check_same_thread=False：FastAPI 的同步端点跑在线程池，依赖和端点可能不在同一线程，
    默认 True 会直接抛 ProgrammingError。每个请求各持一个连接，不存在并发共用的问题。
    """
    conn = sqlite3.connect(str(db_path), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# ============================================================================
# 二、建表 DDL（按业务域分组，共 12 张表）
# ============================================================================
# 通用约定：
#   * 所有表都写 IF NOT EXISTS，保证 init_db() 可以反复执行；
#   * 主键统一 INTEGER PRIMARY KEY AUTOINCREMENT（conversations 例外，见下）；
#   * 时间统一存 TEXT，格式 'YYYY-MM-DD HH:MM:SS'（datetime('now') 的产出），
#     并且**统一用 UTC**（datetime('now') 返回 UTC）：字符串比较即可排序，
#     跨表统计不会因时区错位；展示层再转本地时区；
#   * JSON 数据（端口列表、服务状态等）存 TEXT，程序里用 json.loads/dumps 转换
#     —— SQLite 没有原生 JSON 类型。

# ---------------------------------------------------------------------------
# 2.1 账号与云资产
# ---------------------------------------------------------------------------
_DDL_ACCOUNT = """
-- 用户表：客户 / 坐席 / 管理员共用一张，靠 role 区分
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('customer', 'agent', 'admin')),
    display_name  TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- 仿真云实例台账：演示用的云服务器资产
-- 安全要点：owner_id 是外键，客户只能看到 owner_id = 自己 的实例（防越权的基础）
-- 说明：swap_mb 是 DEV_SPEC 表结构里漏掉、但业务需要的一列（小内存机器 OOM 排查）
CREATE TABLE IF NOT EXISTS cloud_instances (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id            INTEGER NOT NULL REFERENCES users(id),
    instance_name       TEXT    NOT NULL,
    public_ip           TEXT,
    region              TEXT,
    spec                TEXT,
    os_image            TEXT,
    status              TEXT    NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'stopped', 'error')),
    firewall_ports_json TEXT    NOT NULL DEFAULT '[]',
    service_status_json TEXT    NOT NULL DEFAULT '{}',
    icp_filed           INTEGER NOT NULL DEFAULT 0,
    swap_mb             INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT    NOT NULL
);
"""

# ---------------------------------------------------------------------------
# 2.2 知识库（RAG 的资料源）
# ---------------------------------------------------------------------------
_DDL_KNOWLEDGE = """
-- 知识库文档：一篇运维文章
CREATE TABLE IF NOT EXISTS kb_documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL UNIQUE,
    category     TEXT,
    raw_markdown TEXT    NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    updated_at   TEXT    NOT NULL
);

-- 知识库切片：文档按章节切开后的小块，检索的最小单位
-- segmented_text 存 jieba 分词后的结果（空格分隔），供 FTS5 建倒排索引
-- ON DELETE CASCADE：删文档时，它的切片自动跟着删，不留孤儿数据
CREATE TABLE IF NOT EXISTS kb_chunks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id         INTEGER NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    doc_title      TEXT    NOT NULL,
    section_title  TEXT,
    content        TEXT    NOT NULL,
    segmented_text TEXT    NOT NULL
);
"""

# ---------------------------------------------------------------------------
# 2.3 会话与消息（客户与 AI / 坐席的对话）
# ---------------------------------------------------------------------------
_DDL_CONVERSATION = """
-- 会话：一次咨询 = 一个会话，thread_id 与 LangGraph 的检查点线程一一对应
-- status 四个状态构成会话生命周期：
--   ai_handling     AI 正在处理
--   pending_human   已挂起，等待坐席接管
--   human_handling  坐席人工处理中
--   closed          已关闭
-- 注意：id 用 TEXT（UUID）而不是自增整数，因为它同时作为 LangGraph 的 thread 标识，
--       跨进程/重建图后仍要能对上号
CREATE TABLE IF NOT EXISTS conversations (
    id                      TEXT    PRIMARY KEY,
    customer_id             INTEGER NOT NULL REFERENCES users(id),
    assigned_agent_id       INTEGER REFERENCES users(id),
    thread_id               TEXT    NOT NULL UNIQUE,
    title                   TEXT,
    status                  TEXT    NOT NULL DEFAULT 'ai_handling'
                            CHECK (status IN ('ai_handling', 'pending_human', 'human_handling', 'closed')),
    ai_handoff_summary_json TEXT,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL
);

-- 消息：会话里的每一条发言
-- sender_role 区分是谁说的；citations_json 存回答引用的知识库切片；
-- trace_id 关联 agent_traces，用于从一条回答回溯到那次调用的耗时和 Token
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT    NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_role     TEXT    NOT NULL CHECK (sender_role IN ('customer', 'assistant', 'agent', 'system')),
    content         TEXT    NOT NULL,
    has_image       INTEGER NOT NULL DEFAULT 0,
    image_url       TEXT,
    citations_json  TEXT,
    trace_id        INTEGER,
    created_at      TEXT    NOT NULL
);
"""

# ---------------------------------------------------------------------------
# 2.4 工单与状态审计
# ---------------------------------------------------------------------------
_DDL_TICKET = """
-- 工单：客户问题的正式记录，状态机是 OPEN -> TRIAGED -> IN_PROGRESS -> RESOLVED -> CLOSED
-- ai_diagnosis 存 AI 的诊断结论，让坐席不用重查一遍
CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_no       TEXT    NOT NULL UNIQUE,
    customer_id     INTEGER NOT NULL REFERENCES users(id),
    instance_id     INTEGER REFERENCES cloud_instances(id),
    assignee_id     INTEGER REFERENCES users(id),
    conversation_id TEXT,
    category        TEXT,
    priority        TEXT    NOT NULL DEFAULT 'P3',
    status          TEXT    NOT NULL DEFAULT 'OPEN'
                    CHECK (status IN ('OPEN', 'TRIAGED', 'IN_PROGRESS', 'RESOLVED', 'CLOSED')),
    title           TEXT    NOT NULL,
    description     TEXT,
    ai_diagnosis    TEXT,
    resolution_note TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

-- 工单状态审计：每次状态变更写一条，不可修改，用于追责与时间轴展示
CREATE TABLE IF NOT EXISTS ticket_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    operator_id INTEGER REFERENCES users(id),
    from_status TEXT,
    to_status   TEXT    NOT NULL,
    comment     TEXT,
    created_at  TEXT    NOT NULL
);
"""

# ---------------------------------------------------------------------------
# 2.5 可观测性与评测（第 5 章实验数据来源）
# ---------------------------------------------------------------------------
_DDL_OBSERVABILITY = """
-- Agent 链路追踪：每轮对话写一条，支撑观测看板与"成本/延迟"分析
CREATE TABLE IF NOT EXISTS agent_traces (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id   TEXT,
    user_id           INTEGER,
    intent            TEXT,
    rag_hit_count     INTEGER,
    rag_top_score     REAL,
    tool_calls_json   TEXT,
    decision          TEXT,
    latency_ms        INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    error_msg         TEXT,
    created_at        TEXT    NOT NULL
);

-- 评测跑批：一次 32 题黄金集跑批的汇总指标
-- 这些字段就是报告第 5 章要画对比表的列：意图准确率、拒答率、越权拦截率、忠实度、延迟
CREATE TABLE IF NOT EXISTS eval_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_name            TEXT    NOT NULL,
    config_json         TEXT,
    status              TEXT    NOT NULL DEFAULT 'completed',
    total_cases         INTEGER,
    error_cases         INTEGER NOT NULL DEFAULT 0,
    intent_acc          REAL,
    refusal_acc         REAL,
    safe_routing_rate   REAL,
    security_block_rate REAL,
    tool_acc            REAL,
    tool_ops_acc        REAL,
    hitl_escalate_acc   REAL,
    avg_faithfulness    REAL,
    citation_hit_rate   REAL,
    judge_mode          TEXT    NOT NULL DEFAULT 'citation_heuristic',
    avg_latency_ms      REAL,
    created_at          TEXT    NOT NULL
);

-- 单题评测结果：一次跑批里每一道题的判定明细
CREATE TABLE IF NOT EXISTS eval_case_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    case_id       TEXT    NOT NULL,
    category      TEXT,
    passed        INTEGER NOT NULL,
    expected_json TEXT,
    actual_json   TEXT,
    judge_score   REAL,
    reason        TEXT
);
"""

# ---------------------------------------------------------------------------
# 2.6 索引（不加索引，高频查询会全表扫描）
# ---------------------------------------------------------------------------
_DDL_INDEX = """
-- 按 owner 查实例：客户登录后列自己的实例
CREATE INDEX IF NOT EXISTS idx_instances_owner  ON cloud_instances(owner_id);

-- 按会话查历史消息，并让结果天然按时间有序
CREATE INDEX IF NOT EXISTS idx_msg_conv         ON messages(conversation_id, created_at);

-- 按客户查工单 / 按状态筛工单（坐席工作台的待办队列）
CREATE INDEX IF NOT EXISTS idx_ticket_customer  ON tickets(customer_id);
CREATE INDEX IF NOT EXISTS idx_ticket_status    ON tickets(status);
-- 会话级活跃工单幂等唯一索引：防止 HITL 审批崩溃重放或跨进程并发重复建单，同时允许历史已关闭工单后重建新单
CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_conv_active_unique
    ON tickets(conversation_id)
    WHERE conversation_id IS NOT NULL AND status != 'CLOSED';
"""

# ---------------------------------------------------------------------------
# 2.7 FTS5 全文索引（中文检索的核心）
# ---------------------------------------------------------------------------
# 为什么需要它：SQLite 自带的 LIKE 查询不走索引，几万条切片就慢；FTS5 建倒排索引，
#              并支持 BM25 相关性打分（分数用于"低于阈值就拒答"）。
# 为什么 tokenize 用 unicode61：它按空格切词。我们不指望它切中文，而是在入库前
#              用 jieba 把中文切成空格分隔的词存进 segmented_text，查询时同样先切词。
# chunk_id 为什么 UNINDEXED：它只用来回表关联 kb_chunks.id，不需要被搜索，省索引空间。
_DDL_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks_fts USING fts5(
    chunk_id       UNINDEXED,
    doc_title,
    section_title,
    segmented_text,
    tokenize = 'unicode61'
);

-- 三个触发器让 FTS 索引与主表自动保持一致（FTS5 不会自己同步）
-- 新增切片 -> 插入索引
CREATE TRIGGER IF NOT EXISTS kb_chunks_ai
AFTER INSERT ON kb_chunks
BEGIN
    INSERT INTO kb_chunks_fts (chunk_id, doc_title, section_title, segmented_text)
    VALUES (new.id, new.doc_title, new.section_title, new.segmented_text);
END;

-- 修改切片 -> 先删旧索引记录再插新的（FTS5 不支持原地更新）
CREATE TRIGGER IF NOT EXISTS kb_chunks_au
AFTER UPDATE ON kb_chunks
BEGIN
    DELETE FROM kb_chunks_fts WHERE chunk_id = old.id;
    INSERT INTO kb_chunks_fts (chunk_id, doc_title, section_title, segmented_text)
    VALUES (new.id, new.doc_title, new.section_title, new.segmented_text);
END;

-- 删除切片 -> 同步删除索引记录
CREATE TRIGGER IF NOT EXISTS kb_chunks_ad
AFTER DELETE ON kb_chunks
BEGIN
    DELETE FROM kb_chunks_fts WHERE chunk_id = old.id;
END;
"""


def _deduplicate_conversation_tickets(conn: sqlite3.Connection) -> int:
    """存量库升级去重迁移：在创建 idx_tickets_conv_unique 之前处理历史同会话多工单。

    策略（无损审计）：
    - 对同一 conversation_id 存在多条工单的脏数据，保留最早创建的主工单（id 最小）；
    - 其余重复行将 conversation_id 置为 NULL（保留其工单正文与 ticket_events 审计流水，
      不做破坏性删除），满足部分唯一索引 `WHERE conversation_id IS NOT NULL`；
    - 记录可诊断告警日志，防止存量库升级启动时因 IntegrityError 崩溃。
    """
    dup_rows = conn.execute(
        """
        SELECT conversation_id, COUNT(*) AS cnt
        FROM tickets
        WHERE conversation_id IS NOT NULL AND status != 'CLOSED'
        GROUP BY conversation_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    detached = 0
    for dup in dup_rows:
        conv_id = dup["conversation_id"]
        rows = conn.execute(
            "SELECT id, ticket_no, customer_id, status FROM tickets WHERE conversation_id = ? AND status != 'CLOSED' ORDER BY id ASC",
            (conv_id,),
        ).fetchall()
        keep = rows[0]
        extras = rows[1:]
        extra_ids = [int(r["id"]) for r in extras]
        extra_nos = [str(r["ticket_no"]) for r in extras]
        distinct_owners = {int(r["customer_id"]) for r in rows}
        if len(distinct_owners) > 1:
            logger.error(
                "存量库去重迁移告警：会话 %s 存在跨客户重复工单 customer_ids=%s",
                conv_id,
                sorted(distinct_owners),
            )
        placeholders = ",".join("?" for _ in extra_ids)
        conn.execute(
            f"UPDATE tickets SET conversation_id = NULL WHERE id IN ({placeholders})",
            extra_ids,
        )
        # 写入不可变审计备注，保证历史解绑记录可回溯
        for extra in extras:
            conn.execute(
                """
                INSERT INTO ticket_events (ticket_id, operator_id, from_status, to_status, comment, created_at)
                VALUES (?, NULL, ?, ?, ?, datetime('now'))
                """,
                (
                    int(extra["id"]),
                    extra["status"],
                    extra["status"],
                    f"[系统迁移] 解绑重复关联会话 {conv_id}，主工单为 {keep['ticket_no']}",
                ),
            )
        detached += len(extra_ids)
        logger.warning(
            "存量库去重迁移：会话 %s 存在多张历史工单，保留主工单 %s，解绑重复工单 %s 的 conversation_id",
            conv_id,
            keep["ticket_no"],
            extra_nos,
        )
    return detached


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, col_def: str
) -> None:
    """幂等列迁移：已有数据库缺少新列时自动 ALTER TABLE 补齐，不影响存量数据。"""
    existing = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


def mark_stale_eval_runs_failed(conn: sqlite3.Connection) -> int:
    """服务启动自愈：将上次进程异常中断遗留的 running 评测批次标记为 failed，避免前端无限轮询。"""
    import json

    rows = conn.execute(
        "SELECT id, status, config_json FROM eval_runs WHERE status = 'running' OR config_json LIKE '%\"running\"%'"
    ).fetchall()
    updated = 0
    for r in rows:
        cfg: dict = {}
        if r["config_json"]:
            try:
                loaded = json.loads(r["config_json"])
                if isinstance(loaded, dict):
                    cfg = loaded
            except json.JSONDecodeError:
                cfg = {}
        if r["status"] != "running" and cfg.get("status") != "running":
            continue
        cfg["status"] = "failed"
        cfg["error"] = cfg.get("error") or "服务重启或进程中断，已自动标记为失败"
        conn.execute(
            "UPDATE eval_runs SET status = 'failed', config_json = ? WHERE id = ?",
            (json.dumps(cfg, ensure_ascii=False), r["id"]),
        )
        updated += 1
    if updated:
        conn.commit()
    return updated


# ============================================================================
# 三、初始化入口
# ============================================================================
def init_db(db_path: str | Path) -> None:
    """建库：创建 12 张表 + 4 个索引 + FTS5 索引与触发器，并执行幂等增量列迁移。

    - 父目录不存在会自动创建（容器里首次启动时会用到）；
    - 全程使用 IF NOT EXISTS，所以重复调用不会报错，也不会丢数据。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect(path)
    try:
        # 顺序很重要：被引用的表必须先建
        #   users -> cloud_instances -> kb_documents -> kb_chunks -> ...
        for ddl in (_DDL_ACCOUNT, _DDL_KNOWLEDGE, _DDL_CONVERSATION, _DDL_TICKET, _DDL_OBSERVABILITY):
            conn.executescript(ddl)
        # 存量库升级：移除旧版无 status 过滤的唯一索引，去重活跃重复工单后重建活跃唯一索引
        conn.execute("DROP INDEX IF EXISTS idx_tickets_conv_unique")
        _deduplicate_conversation_tickets(conn)
        conn.executescript(_DDL_INDEX)
        conn.executescript(_DDL_FTS)

        # 存量库幂等增量列迁移（保证已有 data/app.db 平滑升级）
        _ensure_column(conn, "conversations", "title", "TEXT")
        _ensure_column(conn, "messages", "image_url", "TEXT")
        _ensure_column(conn, "eval_runs", "status", "TEXT NOT NULL DEFAULT 'completed'")
        _ensure_column(conn, "eval_runs", "error_cases", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "eval_runs", "safe_routing_rate", "REAL")
        _ensure_column(conn, "eval_runs", "tool_ops_acc", "REAL")
        _ensure_column(conn, "eval_runs", "hitl_escalate_acc", "REAL")
        _ensure_column(conn, "eval_runs", "citation_hit_rate", "REAL")
        _ensure_column(conn, "eval_runs", "judge_mode", "TEXT NOT NULL DEFAULT 'citation_heuristic'")

        conn.commit()
    finally:
        conn.close()
