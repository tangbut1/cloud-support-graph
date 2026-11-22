# 零基础建表速成（15 分钟，只为今天的任务）

> 你不需要学完 SQL，只需要学会今天这 12 张表用到的那一小部分。
> 遇到不确定就回来翻这份；动手顺序见 [DAY1_STEPS.md](DAY1_STEPS.md)。

---

## 0. 先建立 5 个概念

1. **一个 `.db` 文件 = 一个 Excel 工作簿**，里面有很多张"表"（sheet）。我们项目是 `data/app.db` 里放 12 张表。
2. **建表 = 定义表头**：先声明每列叫什么、存什么类型、守什么规矩；数据是之后才往里塞的。
3. **你只会用到 3 种类型**：

   | 类型 | 存什么 | 本项目例子 |
   |---|---|---|
   | `INTEGER` | 整数 | id、端口号、token 数、0/1 真假 |
   | `TEXT` | 字符串（**时间、JSON 也存这里**） | 用户名、IP、`2026-11-18 14:00:00`、`'[22, 80]'` |
   | `REAL` | 小数 | 检索分数 0.35、准确率 0.92 |

4. **约束 = 护栏**，防止写进去脏数据。今天只用 5 种（见第 3 节）。
5. **一张表 = 一条 CREATE TABLE 语句**，最后以分号结束。

---

## 1. 一张表长什么样（骨架）

```sql
CREATE TABLE IF NOT EXISTS 表名 (
    列名1   类型   [约束],
    列名2   类型   [约束],     -- 每列结尾是逗号
    列名3   类型   [约束]      -- 最后一列没有逗号
);                             -- 整条语句以分号结束
```

- `IF NOT EXISTS`：表已存在就跳过、不报错 —— 这是 `init_db()` 能反复执行（幂等）的关键
- **括号、逗号、分号** 这三个符号占新手报错的 80%，写完先数一遍

---

## 2. 逐行解剖 users 表（你文件里已有的样板）

```sql
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('customer', 'agent', 'admin')),
    display_name  TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

逐行翻译成中文：

| 这一行 | 意思 |
|---|---|
| `id INTEGER PRIMARY KEY AUTOINCREMENT` | 自增编号，系统从 1 开始自动发，你不用填 |
| `username TEXT NOT NULL UNIQUE` | 文本、必填、全表不许有两个一样的 |
| `password_hash TEXT NOT NULL` | 存 bcrypt 哈希后的字符串（**绝不能存明文密码**） |
| `role TEXT NOT NULL CHECK (role IN (...))` | 必填，且只能是 customer / agent / admin 三个值之一 |
| `display_name TEXT NOT NULL` | 显示名，必填 |
| `created_at TEXT NOT NULL DEFAULT (datetime('now'))` | 不传就自动填当前时间 |

---

## 3. 五种约束速查

| 约束 | 作用 | 写法示例 | 本项目用在哪 |
|---|---|---|---|
| `PRIMARY KEY` | 一行的唯一身份证 | `id INTEGER PRIMARY KEY AUTOINCREMENT` | 每张表 |
| `NOT NULL` | 必填 | `username TEXT NOT NULL` | 大部分列 |
| `UNIQUE` | 整张表里不许重复 | `ticket_no TEXT NOT NULL UNIQUE` | username / title / thread_id / ticket_no |
| `CHECK` | 只能取枚举里的值 | `CHECK (status IN ('OPEN', 'CLOSED'))` | 4 个状态/角色字段 |
| `DEFAULT` | 不填时的默认值 | `DEFAULT 0`、`DEFAULT '[]'`、`DEFAULT (datetime('now'))` | swap_mb、icp_filed、created_at |
| `REFERENCES` | 外键：这列的值必须存在于另一张表 | `owner_id INTEGER NOT NULL REFERENCES users(id)` | 归属关系 |
| `ON DELETE CASCADE` | 附在外键后：父行删了，子行跟着删 | `REFERENCES kb_documents(id) ON DELETE CASCADE` | kb_chunks、messages、ticket_events |

> 为什么需要外键？因为 `cloud_instances.owner_id = 99999` 这种"一台不属于任何人的实例"必须被数据库直接拦住 —— 这就是测试 `test_foreign_key_is_enforced` 在验证的东西。

---

## 4. 六条语法铁律（新手 90% 的报错都在这）

1. 字符串用**单引号** `'running'`，不要双引号
2. 每列后面是**逗号**，**最后一列不加逗号**
3. 整条语句必须以**分号**结尾
4. `DEFAULT (datetime('now'))` **括号不能少**（它是函数调用，不是字面量）
5. **被引用的表要先建**：users 必须在 cloud_instances 之前
6. 关键字大小写不敏感，但统一大写更好读

---

## 5. 练习 A：你写 cloud_instances

我把它拆成「一句需求 → 一行 SQL」的对照表，你负责**按顺序拼装**：

| # | 需求（来自蓝图 §3.2-2） | 你要写的这一行 |
|---|---|---|
| 1 | 自增编号主键 | `id INTEGER PRIMARY KEY AUTOINCREMENT` |
| 2 | 归属用户，必填，且必须是 users 里存在的 id | `owner_id INTEGER NOT NULL REFERENCES users(id)` |
| 3 | 实例名，必填 | `instance_name TEXT NOT NULL` |
| 4 | 公网 IP，可不填 | `public_ip TEXT` |
| 5 | 地域，可不填 | `region TEXT` |
| 6 | 配置规格，可不填 | `spec TEXT` |
| 7 | 系统镜像，可不填 | `os_image TEXT` |
| 8 | 运行状态，必填，只能三选一，默认 running | `status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'stopped', 'error'))` |
| 9 | 防火墙端口 JSON 文本，必填，默认空数组 | `firewall_ports_json TEXT NOT NULL DEFAULT '[]'` |
| 10 | 服务监听状态 JSON 文本，必填，默认空对象 | `service_status_json TEXT NOT NULL DEFAULT '{}'` |
| 11 | 是否备案（0/1），必填，默认 0 | `icp_filed INTEGER NOT NULL DEFAULT 0` |
| 12 | Swap 大小（MB），必填，默认 0 | `swap_mb INTEGER NOT NULL DEFAULT 0` |
| 13 | 更新时间，必填 | `updated_at TEXT NOT NULL` |

拼装位置：打开 `backend/app/db/database.py`，找到 `-- TODO(你): cloud_instances` 那一行，替换成：

```sql
-- ---------- 表 2：仿真云实例 ----------
CREATE TABLE IF NOT EXISTS cloud_instances (
    ← 把上面 13 行按顺序放进来，每行结尾加逗号，最后一行不加
);
```

**拼完立刻跑这条命令**：

```powershell
.\\.venv\\Scripts\\python.exe -m pytest tests/test_db_schema.py -q -k "foreign"
```

→ `test_foreign_key_is_enforced` 从红变绿 = 外键写对了。

---

## 6. 练习 A 的参考实现（先自己写，写完再对照）

```sql
CREATE TABLE IF NOT EXISTS cloud_instances (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id            INTEGER NOT NULL REFERENCES users(id),
    instance_name       TEXT    NOT NULL,
    public_ip           TEXT,
    region              TEXT,
    spec                TEXT,
    os_image            TEXT,
    status              TEXT    NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'stopped', 'error')),
    firewall_ports_json TEXT    NOT NULL DEFAULT '[]',
    service_status_json TEXT    NOT NULL DEFAULT '{}',
    icp_filed           INTEGER NOT NULL DEFAULT 0,
    swap_mb             INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT    NOT NULL
);
```

---

## 7. 剩下 10 张表怎么"翻译"（以 kb_documents 为例）

蓝图给你的是字段清单，你要做的是**机械翻译**。蓝图原文：

```text
3) kb_documents
   id           INTEGER PRIMARY KEY AUTOINCREMENT
   title        TEXT NOT NULL UNIQUE
   category     TEXT
   raw_markdown TEXT NOT NULL
   version      INTEGER NOT NULL DEFAULT 1
   updated_at   TEXT NOT NULL
```

翻译规则只有 4 条：
1. 没有任何 NOT NULL 的列（如 `category`）= 可空，行末什么都不加
2. 有 `NOT NULL` / `UNIQUE` / `DEFAULT x` 的，**照抄**
3. 有 `REFERENCES 表(列)` 的照抄，带 `ON DELETE CASCADE` 的也照抄
4. 唯一要你自己动脑的：**逗号在哪、分号在哪**

结果：

```sql
CREATE TABLE IF NOT EXISTS kb_documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL UNIQUE,
    category     TEXT,
    raw_markdown TEXT    NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    updated_at   TEXT    NOT NULL
);
```

**就这么简单。剩下 9 张全部同理**，只是列多了点。

---

## 8. 每张表写完问自己 4 句

1. 这张表有主键吗？（`id INTEGER PRIMARY KEY AUTOINCREMENT`）
2. 哪些列是必填的？漏了 `NOT NULL` 测试会红
3. 哪一列只允许几个值？→ 用 `CHECK`
4. 哪一列指向别的表？→ 用 `REFERENCES`，必要时加 `ON DELETE CASCADE`

---

## 9. FTS5 和触发器什么时候学

那是**第 4 步**的事（最后 25 分钟），等 11 张表都跑通了我再单独给你讲 10 分钟——
它跟普通建表不是一个东西，现在不用提前焦虑。
