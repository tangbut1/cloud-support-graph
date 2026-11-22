# 六轮质量审计与定稿收口说明（Day 6–9 代码与本地完整体）

> 更新日期：2026-11-21
> 适用范围：本地完整体定稿（Day 1–8 全功能 + Day 9 部署脚本与安全闸门就绪；Day 9 阿里云公网实机部署按计划暂缓；Day 10 报告另行编写）

---

## 一、最终验证基线

| 维度 | 实测结果 | 说明 |
|---|---|---|
| 后端离线单元测试 | **182 passed / 0 failed / 0 skip** | 含 [test_audit_fixes.py](../backend/tests/test_audit_fixes.py) 20 项六轮审计专项回归 |
| 前端生产构建 | **0 错误** (`tsc -b && vite build`) | 产物与源码同步，登录页无明文口令/无默认免密按钮 |
| Playwright E2E（默认安全态） | **3/3 passed** (20.50 s) | `ENABLE_DEMO_LOGIN=false` 下走标准账号密码登录 |
| 真实大模型 32 题黄金集 | **30/32 (93.8%)**（`run_id=#2`） | 零幻觉安全路由 / 规则安全拦截 / MCP 工具 / 纯 HITL / 启发式引用命中率均 100%；`python -m app.eval.report --run-id 2` 与仓内 `Day8-01-*.txt` 逐字节一致 |
| 部署脚本离线干跑校验 | **通过** (`bash -n` 零错误) | HTTP 与 `-EnableTls -PublicDomain` 双模式渲染为纯 LF 脚本，通过 `scp` 落盘 `/tmp/csg-remote-deploy.sh` 远端执行并 `trap` 自动清理，避开 Windows PowerShell 5.1 传参剥离双引号缺陷 |
| 仓库卫生 | **clean** | 无 `.env` / `*.db` / API Key / TLS 证书私钥入库 |

---

## 二、六轮审计核心闭环摘要

1. **安全与部署闸门**：
   - 默认 `ENABLE_DEMO_LOGIN=false`、移除登录页硬编码口令、生产启动 fail-fast 校验 `JWT_SECRET` 与免密开关、开发环境占位密钥自动降级生成临时高熵密钥；
   - `seed.py` 生产模式禁止 `Passw0rd!` 并全表自动轮换残留弱口令；
   - `deploy_aliyun.ps1` 采用单引号 here-string + `scp` 文件传输执行（解决转义与 PowerShell 5.1 argv 剥离双引号两个 P1），含本地/远程双闸门、TLS 预检与弱口令 401 复核；`deploy/docker-compose.tls.yml` 接入 HTTPS 443，Nginx 静态资源显式继承安全响应头。
2. **人机协同（HITL）与数据一致性**：
   - 坐席审批按会话加 `WeakValueDictionary` 互斥锁 + DB 级状态 CAS（`WHERE status='pending_human'`）+ `idx_tickets_conv_active_unique`（`WHERE conversation_id IS NOT NULL AND status != 'CLOSED'`），防止重复建单同时允许工单关闭后重建；
   - `init_db()` 建索引前执行无损去重迁移并写 `ticket_events` 审计备注；
   - 检查点丢失时 `takeover_only` 降级为强制接管防止队列死锁；
   - SSE 客户端断连取消采用纯同步 `SqliteSaver` 补偿，免疫 ASGI/anyio 取消作用域二次中断。
3. **多模态、限流与指标透明化**：
   - 补齐会话 `title`、报错截图鉴权回看（`/attachments` 防 IDOR 与路径穿越）、JSON + multipart 双入参及 OpenAPI 契约、流式硬限流（文本 ≤4000 字符 / JSON ≤64 KB / 图片 ≤2 MB / 表单 ≤3 MB）、`<untrusted_screenshot_ocr>` 提示词层注入隔离；
   - `eval_runs` 贯通 `safe_routing_rate`、`tool_ops_acc`、`hitl_escalate_acc`、`citation_hit_rate`、`judge_mode`、`error_cases`、`status` 专用列至 API 与前端，Trace 看板支持 `source=all|user|eval` 分源。

---

## 三、已知遗留与边界说明（答辩/报告如实披露）

| 级别 | 遗留项 | 现状与处理建议 |
|---|---|---|
| **未执行** | 阿里云实机部署与真实域名 HTTPS 验收 | 脚本、Nginx/TLS 模板与 `verify_public.py` 均已就绪并通过 `bash -n` 干跑，但尚未在 `<YOUR_SERVER_IP>` 实际部署与签发 CA 证书；答辩或报告第 4/5 章需明确区分“容器化方案与验收脚本已就绪”与“实机公网实测”。 |
| **P2** | Trace 保留策略与默认视图 | 当前 `agent_traces` 无自动 TTL 清理策略，前端默认展示 `source=all`（含历史 64 条评测 trace 与 15 条早期测试链路），管理员可通过顶部来源下拉框切换 `仅非评测流量` / `仅评测跑批`。 |
| **P2** | 多 Worker 下 CAS 409 的图侧成本与孤儿边界 | 当前 2C2G 部署采用单 Worker（`--workers 1`），进程内锁即可串行化审批；若未来扩展多 Worker，需将状态锁前置至图执行之前（如引入 `resuming` 中间态或外部分布式锁）。 |
| **P2** | 流式限流复用 Starlette `request._body` | 为兼顾 chunked 限流与 FastAPI `request.form()`/`request.json()` 复用，写入了 Starlette 内部 `_body` 缓存；升级 Starlette 主版本时需以现有 chunked 413 回归测试复核。 |
| **P3** | `app.eval.report` 依赖本地 `data/app.db` | 全新克隆环境无 `data/app.db`（已 gitignore），需先运行 `python -m app.eval.runner` 生成批次后再执行 `python -m app.eval.report`；检索层 A/B 脚本 `app.eval.ab_threshold` 不依赖评测历史，可在 `seed` 后直接复现。 |
| **P3** | 表数量表述 | `data/app.db` 共 11 张业务关系表 + 1 张 `kb_chunks_fts` FTS5 虚拟表（文档统称 12 张表）。 |
