# Day 1 开发蓝图 · 数据库层（你写核心，我写周边）

> 日期：2026-11-18　|　对应计划：Day 1「仓库骨架与数据库层」
> 交付日期 2026-11-27 不变，因此本周按「今天补 Day 1、明天起顺推」压缩执行，报告素材要边做边攒。
> **本文件是「规格」，动手顺序请看 [DAY1_STEPS.md](DAY1_STEPS.md)。**

---

## 0. 今日验收标准（做完这 4 条就算过）

1. `backend/app/db/database.py` 写完，`pytest tests/test_db_schema.py` **全绿**；
2. `backend/app/db/seed.py` 写完，`python -m app.db.seed` 跑通且**可重复执行**；
3. `data/app.db` 落地，用任意 SQLite 工具能看到 12 张表 + 种子数据；
4. 完成仓库**首次 Git 提交**（之前 0 commit）。

预计投入：**60–90 分钟**（DDL 是机械劳动，FTS5 与触发器是今天的真正学习点）。

---

## 1. 今日分工

### 我已经写好（不用你动，但建议你逐个看一遍，都是你后面要改的地方）

| 文件 | 作用 |
|---|---|
| `backend/app/config.py` | Pydantic Settings 配置中心，把 `.env` 相对路径解析成绝对路径 |
| `backend/app/auth/security.py` | bcrypt 密码哈希（seed 要用）；JWT 部分留给 Day 2 |
| `backend/app/main.py` | FastAPI 最小入口 + 启动时 `init_db()` + `GET /api/health` |
| `backend/tests/test_db_schema.py` | **验收测试**：12 张表、FTS5、触发器、索引、PRAGMA、约束，12 个用例 |
| `backend/Dockerfile` `backend/.dockerignore` `docker-compose.yml` `deploy/nginx.conf` | 本地 Docker 化 + 后续部署用（本机暂无 docker，配置文件先备好） |
| `.env` | 本地环境变量（已 gitignore，JWT_SECRET 已随机生成） |
| `backend/pyproject.toml` | 修了一个坑：`readme = "../README.md"` 指向仓库外会让 `pip install -e .` 失败，改为内联文本 |

### 你要写（今天的主体工作）

| 文件 | 内容 | 为什么必须你写 |
|---|---|---|
| `backend/app/db/database.py` | **已给骨架**（`connect()` + `init_db()` 已写好）；你只需填 3 段 SQL：`_SCHEMA_SQL` 11 张表、`_INDEX_SQL` 4 索引、`_FTS_SQL` FTS5+3 触发器 | 数据库约束与 FTS5 是整个项目的"确定性底座"，第 5 章实验与面试必问 |
| `backend/app/db/seed.py` | **已给骨架**（`main()` 已写好）；你只需实现 `seed()` 的 6 步幂等写入；数据在 `seed_data.py`（我写好，不用改） | 你亲手配的数据，后面演示与答辩才讲得清"AI 到底在查什么系统" |

---

## 2. 两个先说清的技术决定

**决定一：Day 1 用标准库 `sqlite3`（同步），不用 aiosqlite。**
理由：单 worker + 2C2G 场景下 SQLite 是读多写少，FastAPI 会把同步依赖放到线程池执行；同步 `sqlite3` 少一层 async 调试成本，而 WAL/busy_timeout 的并发行为是一样的。`aiosqlite` 仍留在依赖里，后续真需要异步再换，**DDL 不用改一行**。

**决定二：DDL 直接写在 `database.py` 里，不单独拆 `.sql` 文件。**
理由：按 DEV_SPEC 的约定，建表语句与初始化逻辑同源，你调试时不用在两处跳转。

---

## 3. 作业 A：`backend/app/db/database.py`（骨架已给，你填 3 段 SQL）

### 3.1 对外契约（必须严格一致，测试按此验收）

```python
def connect(db_path: str | Path) -> sqlite3.Connection:
    """建立连接并完成所有 PRAGMA 设置。"""

def init_db(db_path: str | Path) -> None:
    """幂等建库：自动创建父目录，建 12 张表 + 4 索引 + 3 触发器。"""
```

**`connect()` 每次连上都必须做这 4 件事**：
1. `sqlite3.connect(str(db_path), timeout=5.0)`
2. `conn.row_factory = sqlite3.Row`
3. `PRAGMA foreign_keys = ON`（SQLite 默认是 OFF，不开外键约束形同虚设）
4. `PRAGMA busy_timeout = 5000`；`PRAGMA journal_mode = WAL`

**`init_db()` 要求**：
- `Path(db_path).parent.mkdir(parents=True, exist_ok=True)` —— 测试会传一个多层不存在的路径；
- 内部复用 `connect()`；
- 所有建表语句用 `IF NOT EXISTS`，**重复调用不报错**；
- 用 `executescript()` 批量执行 DDL 更省事（注意它会隐式提交）。

### 3.2 12 张表字段规格（逐字照抄类型与约束即可）

```text
1) users
   id                 INTEGER PRIMARY KEY AUTOINCREMENT
   username           TEXT NOT NULL UNIQUE
   password_hash      TEXT NOT NULL
   role               TEXT NOT NULL CHECK (role IN ('customer','agent','admin'))
   display_name       TEXT NOT NULL
   created_at         TEXT NOT NULL DEFAULT (datetime('now'))

2) cloud_instances
   id                 INTEGER PRIMARY KEY AUTOINCREMENT
   owner_id           INTEGER NOT NULL REFERENCES users(id)
   instance_name      TEXT NOT NULL
   public_ip          TEXT
   region             TEXT
   spec               TEXT
   os_image           TEXT
   status             TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','stopped','error'))
   firewall_ports_json TEXT NOT NULL DEFAULT '[]'
   service_status_json TEXT NOT NULL DEFAULT '{}'
   icp_filed          INTEGER NOT NULL DEFAULT 0
   swap_mb            INTEGER NOT NULL DEFAULT 0        -- 见下方说明
   updated_at         TEXT NOT NULL

3) kb_documents
   id                 INTEGER PRIMARY KEY AUTOINCREMENT
   title              TEXT NOT NULL UNIQUE
   category           TEXT
   raw_markdown       TEXT NOT NULL
   version            INTEGER NOT NULL DEFAULT 1
   updated_at         TEXT NOT NULL

4) kb_chunks
   id                 INTEGER PRIMARY KEY AUTOINCREMENT
   doc_id             INTEGER NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE
   doc_title          TEXT NOT NULL
   section_title      TEXT
   content            TEXT NOT NULL
   segmented_text     TEXT NOT NULL

5) kb_chunks_fts      （FTS5 虚拟表，见 3.3）

6) conversations
   id                 TEXT PRIMARY KEY                 -- UUID
   customer_id        INTEGER NOT NULL REFERENCES users(id)
   assigned_agent_id  INTEGER REFERENCES users(id)
   thread_id          TEXT NOT NULL UNIQUE             -- 对应 LangGraph thread
   status             TEXT NOT NULL DEFAULT 'ai_handling'
                      CHECK (status IN ('ai_handling','pending_human','human_handling','closed'))
   ai_handoff_summary_json TEXT
   created_at         TEXT NOT NULL
   updated_at         TEXT NOT NULL

7) messages
   id                 INTEGER PRIMARY KEY AUTOINCREMENT
   conversation_id    TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE
   sender_role        TEXT NOT NULL CHECK (sender_role IN ('customer','assistant','agent','system'))
   content            TEXT NOT NULL
   has_image          INTEGER NOT NULL DEFAULT 0
   citations_json     TEXT
   trace_id           INTEGER
   created_at         TEXT NOT NULL

8) tickets
   id                 INTEGER PRIMARY KEY AUTOINCREMENT
   ticket_no          TEXT NOT NULL UNIQUE             -- 形如 CSG-20261118-0001
   customer_id        INTEGER NOT NULL REFERENCES users(id)
   instance_id        INTEGER REFERENCES cloud_instances(id)
   assignee_id        INTEGER REFERENCES users(id)
   conversation_id    TEXT
   category           TEXT
   priority           TEXT NOT NULL DEFAULT 'P3'
   status             TEXT NOT NULL DEFAULT 'OPEN'
                      CHECK (status IN ('OPEN','TRIAGED','IN_PROGRESS','RESOLVED','CLOSED'))
   title              TEXT NOT NULL
   description        TEXT
   ai_diagnosis       TEXT
   resolution_note    TEXT
   created_at         TEXT NOT NULL
   updated_at         TEXT NOT NULL

9) ticket_events
   id                 INTEGER PRIMARY KEY AUTOINCREMENT
   ticket_id          INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE
   operator_id        INTEGER REFERENCES users(id)
   from_status        TEXT
   to_status          TEXT NOT NULL
   comment            TEXT
   created_at         TEXT NOT NULL

10) agent_traces
   id                 INTEGER PRIMARY KEY AUTOINCREMENT
   conversation_id    TEXT
   user_id            INTEGER
   intent             TEXT
   rag_hit_count      INTEGER
   rag_top_score      REAL
   tool_calls_json    TEXT
   decision           TEXT
   latency_ms         INTEGER
   prompt_tokens      INTEGER
   completion_tokens  INTEGER
   error_msg          TEXT
   created_at         TEXT NOT NULL

11) eval_runs
   id                 INTEGER PRIMARY KEY AUTOINCREMENT
   run_name           TEXT NOT NULL
   config_json        TEXT
   total_cases        INTEGER
   intent_acc         REAL
   refusal_acc        REAL
   security_block_rate REAL
   tool_acc           REAL
   avg_faithfulness   REAL
   avg_latency_ms     REAL
   created_at         TEXT NOT NULL

12) eval_case_results
   id                 INTEGER PRIMARY KEY AUTOINCREMENT
   run_id             INTEGER NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE
   case_id            TEXT NOT NULL
   category           TEXT
   passed             INTEGER NOT NULL                 -- 0/1
   expected_json      TEXT
   actual_json        TEXT
   judge_score        REAL
   reason             TEXT
```

> **`swap_mb` 这一列是我补的，请照抄**：DEV_SPEC 模块 2 要求仿真实例带「Swap 配置状态」（2C2G 机器没挂 Swap 是老生常谈的踩坑点），但 §5 的表结构漏了这列。加上它，Day 3/6 才能做"Swap 未配置导致 OOM"的诊断演示。
> `updated_at` 等时间字段统一存 `TEXT`（`datetime('now')` 产出 `'YYYY-MM-DD HH:MM:SS'`，按字符串比较即可排序）。

### 3.3 FTS5 虚拟表（今天最容易卡住的地方）

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks_fts USING fts5(
    chunk_id    UNINDEXED,
    doc_title,
    section_title,
    segmented_text,
    tokenize = 'unicode61'
);
```

**为什么是 `UNINDEXED`**：`chunk_id` 只用来回表关联 `kb_chunks.id`，不需要被检索；不建倒排索引能省空间。
**为什么 `tokenize = 'unicode61'` 够用**：SQLite 默认分词器切不动连续中文（"数据库连接超时"会变成一个 token）。我们的做法是**入库前用 jieba 切好词、空格连起来存进 `segmented_text`**（Day 3 实现），查询时同样先切词再 MATCH，所以 unicode61 只负责按空格切分就行。

### 3.4 三个同步触发器（FTS 表不会自己跟着主表变）

必须建 3 个触发器，命名就是测试里写死的：
`kb_chunks_ai`（AFTER INSERT）、`kb_chunks_au`（AFTER UPDATE）、`kb_chunks_ad`（AFTER DELETE）。

- INSERT：把 `new.id, new.doc_title, new.section_title, new.segmented_text` 插进 `kb_chunks_fts`；
- UPDATE：先 `DELETE FROM kb_chunks_fts WHERE chunk_id = old.id`，再按 INSERT 的方式写入 new 值（FTS5 不支持原地更新，只能删了重插）；
- DELETE：`DELETE FROM kb_chunks_fts WHERE chunk_id = old.id`。

> 踩坑提示：FTS5 表没有 `id` 主键列，删记录**只能按 `chunk_id` 列过滤**，不要写成 `WHERE rowid = old.id`（fts 的 rowid 与 kb_chunks.id 不是一回事）。

### 3.5 4 个索引

```text
idx_instances_owner   ON cloud_instances(owner_id)
idx_msg_conv          ON messages(conversation_id, created_at)
idx_ticket_customer   ON tickets(customer_id)
idx_ticket_status     ON tickets(status)
```

### 3.6 建议的实现顺序

1. 模块头 docstring + `import sqlite3` / `from pathlib import Path`；
2. 写 `connect()`：连接 → row_factory → 三条 PRAGMA；
3. 把 12 张表的 `CREATE TABLE IF NOT EXISTS` 按上面顺序抄成字符串列表 `_DDL`；
4. 追加索引语句、FTS5 虚拟表、3 个触发器；
5. 写 `init_db()`：mkdir → connect → `executescript(";".join(...))` → commit → close；
6. 跑测试，按报错逐个补。

---

## 4. 作业 B：`backend/app/db/seed.py`（骨架已给，你实现 `seed()`）

### 4.1 契约

```python
def seed(db_path: str | Path | None = None) -> dict[str, int]:
    """幂等写入种子数据，返回 {"users": 新增数, "instances": 新增数}。"""

if __name__ == "__main__":
    ...  # 打印结果与演示账号表
```

- 运行方式：`cd backend` 后 `python -m app.db.seed`；
- 入口先 `init_db(db_path)`（可传 `get_settings().app_db`）；
- **幂等**：`users` 用 `INSERT OR IGNORE`（靠 username UNIQUE）；`cloud_instances` 没有唯一键，先用 `SELECT 1 FROM cloud_instances WHERE owner_id=? AND instance_name=?` 判断再插；
- 密码哈希用 `from app.auth.security import hash_password`，统一明文密码：`Passw0rd!`。

### 4.2 种子数据（照此录入）

**用户（4 个）**

| username | role | display_name |
|---|---|---|
| `zhangsan` | customer | 客户张三 |
| `lisi` | customer | 客户李四 |
| `wanggong` | agent | 技术支持坐席王工 |
| `admin` | admin | 系统管理员 |

**仿真云实例（5 台）**

| owner | instance_name | public_ip | region | spec | os_image | status | firewall_ports_json | service_status_json | icp_filed | swap_mb |
|---|---|---|---|---|---|---|---|---|---|---|
| zhangsan | ubuntu-web-prod | 47.94.101.23 | 华北2（北京） | 2C2G | Ubuntu 22.04 LTS | running | `[22,80,443]` | `{"nginx":"running","backend-8080":"stopped"}` | 1 | 0 |
| zhangsan | app-test-node | 47.94.101.88 | 华北2（北京） | 2C4G | Ubuntu 22.04 LTS | running | `[22,8080]` | `{"app-8080":"running"}` | 0 | 2048 |
| zhangsan | win-rdp-jump | 47.94.102.15 | 华东1（杭州） | 2C2G | Windows Server 2022 | running | `[3389]` | `{"rdp":"running"}` | 1 | 0 |
| lisi | finance-api-node | 8.140.55.201 | 华北2（北京） | 2C2G | Ubuntu 22.04 LTS | running | `[22,443]` | `{"api-8443":"running"}` | 1 | 1024 |
| lisi | crm-backend | 8.140.55.77 | 华东1（杭州） | 2C4G | CentOS 7.9 | stopped | `[22,80]` | `{"tomcat-8080":"running","mysql-3306":"running"}` | 0 | 0 |

> 这些数据是给 Day 3/4 的 MCP 诊断和故障靶场用的：`ubuntu-web-prod` 的 `backend-8080=stopped` + `firewall_ports_json` 不含 8080 + `swap_mb=0`，正好对应"Nginx 502 / 端口未放行 / 小内存 OOM"三类演示场景。
> JSON 字段用 `json.dumps(value, ensure_ascii=False)` 存字符串；`updated_at` 用 `datetime('now')`。

---

## 5. 九个易错点（按踩坑概率排序）

1. **`PRAGMA foreign_keys` 是连接级开关**，每次新连接都要重开，写在 `init_db` 里只对那一次连接有效。
2. **`journal_mode = WAL` 是数据库级持久属性**，设一次就记住；但每次连接都设一遍也无害，测试就是按"连接后查得到 wal"来验的。
3. **FTS5 外部内容表 vs 独立表**：本方案用独立表（自己存数据），所以不需要 `content=` 参数；写了 `content='kb_chunks'` 反而要额外维护 rowid 对齐，别加。
4. **`CREATE VIRTUAL TABLE IF NOT EXISTS`** 支持 IF NOT EXISTS，别漏，否则第二次执行就报错，幂等性挂掉。
5. **`executescript` 会先隐式 COMMIT**，所以不要在事务中间调用它。
6. **外键 ON DELETE CASCADE 只有在 `foreign_keys=ON` 时才生效**，两者必须配套。
7. **`created_at`/`updated_at` 要给 `DEFAULT (datetime('now'))`**，否则测试里不传时间的 INSERT 会因 NOT NULL 失败（括号不能少：`DEFAULT (datetime('now'))` 是表达式，`DEFAULT datetime('now')` 会语法错误）。
8. **CHECK 约束**：`conversations.status`、`tickets.status`、`messages.sender_role`、`users.role` 四个枚举必须有，测试会故意插非法值验证。
9. **`sqlite3.Row`** 才有 `row["role"]` 的下标访问；忘了设 `row_factory` 就只能用下标数字。

---

## 6. 验收命令（写完后自己先跑一遍）

```powershell
cd D:\软件项目和论文\cloud-support-graph\backend

# 1) 数据库层验收：目标 12 passed
.\.venv\Scripts\python.exe -m pytest tests\test_db_schema.py -v

# 2) 种子数据（可重复执行，第二次应显示新增 0）
.\.venv\Scripts\python.exe -m app.db.seed
.\.venv\Scripts\python.exe -m app.db.seed

# 3) 起服务看健康检查
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload
#    浏览器打开 http://127.0.0.1:8000/docs 或 http://127.0.0.1:8000/api/health
```

## 7. 你写完回来告诉我，我接着做

我会：跑全量验收 → 逐行 review 你的 DDL（重点看外键/CHECK/触发器）→ 记录第一条 Git 提交 → 一起过一遍 Day 2（JWT + RBAC + 工单状态机）的切片边界。
如果中途卡住，直接把报错贴给我，别自己硬扛超过 15 分钟。
