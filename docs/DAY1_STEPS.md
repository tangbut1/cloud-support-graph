# Day 1 动手顺序（照着做就行）

> 规格细节在 [DAY1_BLUEPRINT.md](DAY1_BLUEPRINT.md)，本文件只管**先写哪个、再写哪个、每步怎么自检**。
> 总时长约 70 分钟，分 5 步。**每步都有检查点，跑通再进下一步**，不要一口气写完再测。

---

## 准备（1 分钟）

打开两个东西：
1. 编辑 `backend/app/db/database.py`（今天的作业本，已给你骨架和 1 张样板表）
2. 终端定在 `D:\软件项目和论文\cloud-support-graph\backend`

如果终端中文乱码，先执行 `chcp 65001`。

---

## 第 1 步 · 认识骨架（2 分钟，不写代码）

先跑一次测试，让报错告诉你还缺什么：

```powershell
.\\.venv\\Scripts\\python.exe -m pytest tests/test_db_schema.py -q
```

你会看到类似 `缺少数据表: ['agent_traces', 'cloud_instances', ...]` —— **这就是你的作业清单**。
现在 `database.py` 里 `connect()` 和 `init_db()` 已写好，只有 3 段 SQL 是空的：

| 段 | 内容 | 现在状态 |
|---|---|---|
| `_SCHEMA_SQL` | 12 张业务表（表 1 users 已给样板） | 缺 11 张 |
| `_INDEX_SQL` | 4 个索引 | 空 |
| `_FTS_SQL` | FTS5 虚拟表 + 3 触发器 | 空 |

> 记住这三个变量名的分工：**建表 / 建索引 / 建 FTS**。别把 FTS5 写进 `_SCHEMA_SQL`，测试按名字找你。

---

## 第 2 步 · 写 12 张表（25 分钟）

在 `_SCHEMA_SQL` 里，把 11 个 `-- TODO(你)` 逐个替换成 `CREATE TABLE`。

**建议节奏：一次写 3 张，跑一次测试**，别 11 张写完再测（出错难定位）。

- 第 1 批：`cloud_instances`、`kb_documents`、`kb_chunks`（注意外键 ON DELETE CASCADE）
- 第 2 批：`conversations`、`messages`、`tickets`（注意 CHECK 枚举、TEXT 主键）
- 第 3 批：`ticket_events`、`agent_traces`、`eval_runs`、`eval_case_results`

**每张表写的时候只盯 5 件事**（这 5 件事就是测试会查的）：
1. `NOT NULL` 有没有漏（漏了测试的 INSERT 会"成功"，但业务上不该允许）
2. `UNIQUE` 有没有（username / title / thread_id / ticket_no）
3. `CHECK (... IN (...))` 枚举有没有（users.role、conversations.status、messages.sender_role、tickets.status）
4. `REFERENCES 父表(列)` 有没有，需要级联的加 `ON DELETE CASCADE`
5. `DEFAULT (datetime('now'))` —— **括号不能少**，少了直接语法报错

**检查点**：
```powershell
.\\.venv\\Scripts\\python.exe -m pytest tests/test_db_schema.py -q -k "table or constraint or unique or foreign"
```
→ 通过后进入第 3 步。如果 `test_all_twelve_tables_created` 还红，看它输出的 `缺少数据表` 列表。

---

## 第 3 步 · 写 4 个索引（5 分钟）

在 `_INDEX_SQL` 里写 4 条 `CREATE INDEX IF NOT EXISTS`（清单见蓝图 §3.5）。

**为什么要索引**（答辩会问）：`messages` 按会话查历史、`cloud_instances` 按 owner 过滤、`tickets` 按客户和状态筛选——都是高频查询，没索引就全表扫。

**检查点**：
```powershell
.\\.venv\\Scripts\\python.exe -m pytest tests/test_db_schema.py -q -k "index"
```

---

## 第 4 步 · 写 FTS5 + 3 个触发器（25 分钟，今天最值钱的一步）

在 `_FTS_SQL` 里写两件事：

**① 虚拟表**（照蓝图 §3.3 抄，注意 `chunk_id UNINDEXED` 和 `tokenize = 'unicode61'`）

**② 三个触发器**，名字固定为 `kb_chunks_ai` / `kb_chunks_au` / `kb_chunks_ad`：
- INSERT 后：把 `new.id, new.doc_title, new.section_title, new.segmented_text` 插入 FTS 表
- UPDATE 后：先 `DELETE FROM kb_chunks_fts WHERE chunk_id = old.id`，再插 `new.*`（FTS5 不支持原地更新）
- DELETE 后：`DELETE FROM kb_chunks_fts WHERE chunk_id = old.id`

**检查点**：
```powershell
.\\.venv\\Scripts\\python.exe -m pytest tests/test_db_schema.py -q -k "fts"
```
→ 三个 fts 用例全绿后，跑全量：

```powershell
.\\.venv\\Scripts\\python.exe -m pytest tests/test_db_schema.py -v
```
**目标：12 passed。** 到这里 database.py 就完成了 ✅

---

## 第 5 步 · 写 seed.py 的 seed()（20 分钟）

打开 `backend/app/db/seed.py`，只实现 `seed()` 一个函数（`main()` 已给你写好）。
数据在 `backend/app/db/seed_data.py`，不要改。

按文件里注释的 6 步写：
1. 解析 `db_path`（默认 `get_settings().app_db`）
2. `init_db(path)` 先建表
3. `connect(path)` 拿连接（try/finally 关）
4. 循环 `USERS` → `INSERT OR IGNORE`，用 `cursor.rowcount` 累加新增数
5. 循环 `INSTANCES` → 先查 `owner_id`，再查「该 owner 下是否已有同名实例」，没有才 INSERT
6. `commit()` + 返回 `{"users": n, "instances": m}`

**唯一容易错的地方**：幂等。第二次运行必须「新增 0」，所以 users 靠 `INSERT OR IGNORE`（有 UNIQUE），
instances 没有唯一键，必须**先 SELECT 判断再插**。

**检查点**：
```powershell
.\\.venv\\Scripts\\python.exe -m app.db.seed   # 第一次：新增用户 4 个，新增云实例 5 台
.\\.venv\\Scripts\\python.exe -m app.db.seed   # 第二次：新增用户 0 个，新增云实例 0 台 ← 关键
```

---

## 第 6 步 · 端到端确认（5 分钟）

```powershell
# 1) 全量测试
.\\.venv\\Scripts\\python.exe -m pytest -q

# 2) 起服务，浏览器打开 http://127.0.0.1:8000/api/health
.\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8000 --reload

# 3) 看看数据（可选，用 sqlite3 或 DB 工具打开 data/app.db）
```
`/api/health` 返回 `{"status":"ok","db_exists":true,...}` 就说明整条链路通了。

---

## 常见报错对照表

| 报错 | 原因 | 改法 |
|---|---|---|
| `near "(": syntax error` | `DEFAULT datetime('now')` 少了括号 | 写成 `DEFAULT (datetime('now'))` |
| `no such table: kb_chunks_fts` | FTS 表没建，或写错位置 | 必须在 `_FTS_SQL` 里 |
| `CHECK constraint failed` | 枚举值拼错 / 大小写不符 | 对照蓝图 §3.2 逐个字符核 |
| `foreign key mismatch` | 父表还没建，或 REFERENCES 的列不是主键 | 按 users → ... → eval_case_results 顺序建 |
| `cannot change into wal mode from within a transaction` | 在事务里设 WAL | `connect()` 里紧跟 connect 就设，别包事务 |
| `database is locked` | 上一个进程没释放 | 关掉 uvicorn / 其他连接再跑 |
| `test_row_factory_is_sqlite_row` 红 | `row["role"]` 报 TypeError | `connect()` 里的 `row_factory` 那段别删 |
| 第二次 seed 又新增了 | 没用 `INSERT OR IGNORE` / 没先 SELECT | 见第 5 步 |

---

## 你交回来的判断标准（我按这个验收）

1. `pytest tests/test_db_schema.py -v` → **12 passed**
2. `python -m app.db.seed` 连续两次 → 第二次全 0
3. `/api/health` → 200 且 `db_exists: true`
4. `data/app.db` 里 12 张表 + 4 用户 + 5 实例

任何一步卡超过 15 分钟，把**完整报错**贴给我，别硬扛。
