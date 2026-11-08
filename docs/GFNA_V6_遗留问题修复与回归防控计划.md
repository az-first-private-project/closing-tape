# GFNA V6 遗留问题修复与回归防控计划

> 版本：2026-11-07  
> 基线：11-01 遗留清单 + P0-1~P0-35 期间新增项，全部经 2026-11-07 现场核对  
> 执行方式：一次一项、测试先行、单独提交、默认不 push；数据库订正先预览后授权  
> 执行者：Qoder；授权与优先级调整：用户

## 1. 目的与原则

本计划解决两件事：

1. 把剩余 P0/P1/P2/P3 问题按依赖关系排成可执行序列，明确验收口径与回滚方式；
2. 在修业务问题之前先补齐“能自动发现回归”的基础设施，避免继续出现修一处、坏一处。

原则：

- **P0 优先**：数据正确性 > 数据完整性 > 可靠性 > 性能 > 体验。
- **测试先行**：每个缺陷先写一个当前会失败的测试，再改代码。
- **真实路径验收**：不用模拟数据、不覆写环境；数据库修复必须能对账。
- **单一语义**：状态、新鲜度、SCD2 规则只允许一个入口，禁止跨层手写。
- **改动预算**：超过 5 个文件或 200 行必须拆分；本次不顺手重构无关代码。
- **可回滚**：数据库订正先备份/预览；代码修复保持单提交可回退。
- **不伪造数据**：缺样本就显示“低样本/待成熟”，缺名单就显示“未收录”，禁止用 0 或演示值凑数。

## 2. 当前基线（2026-11-07 实测）

### 2.1 P0 状态

| 项目 | 结果 |
|---|---|
| P0 编号任务 | 35/35 已关闭（P0-1 ~ P0-35） |
| 最新提交 | `d999ad2`（P0-35），仅此 1 个提交未 push |
| 行情最新日 | 2026-11-06，5573 只 |
| `/health` | `data_ready=true`，15/15 数据集新鲜 |
| 待授权数据订正 | 11-05 两融未来函数（1 项） |

### 2.2 遗留台账

| 优先级 | 数量 | 主要内容 |
|---|---:|---|
| P0 | 1 | 11-05 两融数据订正（待授权） |
| P1 | 5 | 板块名单、信号样本、自选隔离、任务状态机、NO_PROXY |
| P2 | 10 | partial 提示、矩阵复用、纸面性能、回测超时、N+1、移动入口、历史 run 元数据、数据卫生、回测默认结束日、无效资金流代码 |
| P3 | 4 | 主包体积、评分榜 sparkline、`/ladder` 路由、视口/生产验收 |

### 2.3 关键量化基线

| 指标 | 当前值 |
|---|---|
| 后端测试 | 479 passed / 33 skipped；API 与数据库测试默认跳过 |
| 前端测试 | 仅 treemap / sparkline / palette 三个纯函数测试 |
| CI / pre-commit | 均不存在 |
| 概念板块成员 | 347 个有名单；38 个无名单；6 个 partial（各 100 只样本） |
| 非回测信号 | 20 个信号日、902 条 perf、187 条 20 日成熟 |
| 历史 run 元数据 | 101 个 run 缺 `metrics.triggered` |
| 测试残留 | 10 条 failed 回测；15 条 failed + 12 条僵尸 running 任务 |
| 前端构建 | 主包 1013 kB（未分包） |
| watchdog | 计划任务已注册，状态 Ready |

## 3. 目标与完成定义

### 3.1 程序级目标

- 5 周内关闭全部 P1/P2/P3，P0 数据订正在授权后 1 个工作日内完成；
- 建立 CI、集成测试、数据不变量脚本三道防线；
- 修复后 7 天内回归逃逸数为 0；任何逃逸必须补测试并复盘。

### 3.2 单项完成定义（DoD）

1. 有复现证据与验收标准；
2. 有先失败、后通过的自动化测试，或说明无法自动化的原因；
3. 走真实路径验证（API/页面/数据库/调度实际运行）；
4. 有回滚方案；
5. 单独 commit，提交信息带问题编号；
6. 三段汇报（改了什么 / 自测结果 / 遗留风险）；
7. 不 push（除非用户明确要求）。

## 4. 总体路线

| 阶段 | 周期 | 目标 |
|---|---|---|
| Phase 0 安全护栏 | 第 1 周 | CI、测试库、集成测试骨架、不变量脚本、状态枚举、清理残留 |
| Phase 1 P0 订正 | 第 1 周 | 11-05 两融未来函数修正（需授权） |
| Phase 2 P1 | 第 1–3 周 | 关闭 5 项 P1 |
| Phase 3 P2 | 第 3–4 周 | 关闭 10 项 P2 |
| Phase 4 P3 | 第 5 周 | 关闭 4 项 P3，完成最终回归与复盘 |

## 5. 工作分解（WBS）

### Phase 0：安全护栏（第 1 周，约 3 天）

#### W0-1 建立 CI

- 文件：`.github/workflows/ci.yml`
- 内容：
  - backend job：Python 3.11，跑单元测试（不设 `GFNA_TEST_DB`，不连库）；
  - backend-integration job：PostgreSQL 16 service，`alembic upgrade head` 后跑集成测试，设置 `GFNA_TEST_DB=1`；
  - frontend job：`npm ci` + `npm run build` + 三个 `node --test`；
  - 失败即阻断。
- 验收：CI 在干净环境一次通过；故意改坏一个断言能变红。
- 注意：集成测试库名必须以 `_test` 结尾，禁止连生产库。

#### W0-2 集成测试骨架

- 目录：`backend/tests/integration/`
- 机制：
  - 使用独立 `gfna_test` 数据库；
  - `conftest.py` 断言库名以 `_test` 结尾，否则直接退出；
  - 每个用例事务回滚，测试结束不留数据；
  - 测试数据用最小 fixture，不复用生产演示数据。
- 最少 6 组不变量：
  1. SCD2 区间不重叠；完整名单取代 partial；空名单不得关闭 full 区间；
  2. scoped 回补不改数据集新鲜度，全市场回补才推进；
  3. `failed`/`quality_failed` 不得标记 success；`partial` 必须向上传递；
  4. catch-up 的 `margin` 必须排在评分/日报之后；
  5. checkpoint 只在提交后推进；失败码重跑能被重试并清除；
  6. 同一交易日、同一参数重复运行结果一致。
- 完成情况（11-07）：`tests/integration/` 9 个用例覆盖上述 6 组不变量；本地 `GFNA_TEST_DB=1 pytest tests/integration -q` 9 passed；CI 新增 `backend-integration` job（PostgreSQL 16 + 迁移 + 集成测试）。

#### W0-3 数据不变量脚本

- 文件：`backend/scripts/check_invariants.py`
- 只读，输出 JSON，违规退出码 1；管线结束后调用，也可由 watchdog 定时调用。
- 首批检查：
  - SCD2 重叠；
  - open partial 板块数；
  - 最新行情日与 `meta_data_quality` 是否一致；
  - 行情/特征/评分/资金流覆盖是否断层；
  - 超过 2 小时的 running 任务；
  - failed 回测/任务是否被误计入成功统计。
- 验收：在已知 12 条僵尸任务上能报出违规；清理后归零。
- 完成情况（11-07）：
  - `backend/app/quality/invariants.py` 实现 17 项只读检查；生产实测违规 1 项（`stale_running_jobs`=12），警告 2 项（`scd2_zero_length`=351、`open_partial_sectors`=6），退出码 1，与已知基线一致；
  - `check_invariants.py` 支持 `--compact`，事务只读，异常不阻断调用方；
  - `run_daily_pipeline` 结束与 catch-up `signal_perf` 之后各调用一次；watchdog 健康/拉起成功后每小时最多执行一次（60s 超时，失败只记日志）；
  - 集成测试新增 3 个用例（干净库无违规 / 重叠+僵尸+失败产物可报出 / 空库仍报错），`tests/integration` 12 passed，全量 479 passed。

#### W0-4 状态枚举与单一入口

- 文件：`backend/app/core/status.py`
- 内容：
  - `TaskStatus`: running / success / partial / failed / skipped；
  - `QualityStatus`: ok / stale / quality_failed / error / unknown；
  - 允许的状态迁移表（任务终态不得回到 running；新鲜度不得写回 unknown）；
  - 新鲜度写入只保留一个函数入口 `services/freshness.py::write_freshness`。
- 替换 `pipeline.py`、`catch_up.py`、`bulk_backfill.py` 中的魔法字符串；`record_quality`
  与 `mark_success`/`mark_failure` 全部改为调用单一写入口。
- 验收：相关模块不再有裸状态字符串；现有 45 个相关测试全绿。
- 完成情况（11-07）：
  - `TaskStatus` 按运行时真实取值补入 `running/failed`（原计划写的 `quality_failed/error`
    属于质量域，已归入 `QualityStatus`）；
  - 所有 `meta_data_quality` 写入统一走 `write_freshness`（success / attempt / failure
    三种模式），`record_quality`、`mark_success`、`mark_failure` 均为薄封装；新增静态
    守卫测试确保其他模块不得直接 upsert 该表；
  - `pipeline`/`catch_up`/`bulk_backfill`/`freshness`/`ingest service`/`valuation`/`calendar`
    的状态字面量已替换为枚举；
  - 新增 `tests/test_status.py` 4 个用例与 1 个集成用例；全量 483 passed / 46 skipped，
    集成 13 passed。

#### W0-5 清理测试残留（需授权）

- 已完成（11-07，用户授权"全部归档"）：
  - 10 条 failed 回测 → `backtest_run.status='archived'`；
  - 15 条 failed 任务 → `ingest_job.status='archived'`；
  - 12 条僵尸 running 任务（无 `finished_at`）→ `archived`，补 `finished_at=now()`
    与 `stale running > 2h; archived (W0-5)` 说明；
  - 单事务执行，更新语句均带 `AND status=...` 守卫并严格校验 rowcount 为
    10/12/15，不符即回滚；审计记录全部保留、未删除。
- `/api/v1/meta/jobs` 默认排除 `archived`，显式传 `status=archived` 仍可审计
  查询；新增集成测试覆盖（`tests/integration/test_jobs_archive_filter.py`）。
- 验收：`check_invariants.py` 退出码 0、`stale_running_jobs=0`；`ingest_job`
  仅剩 success 66 / partial 20 / skipped 3 / archived 27；`backtest_run` 仅剩
  success 88 / archived 10；单测 483 passed、集成 14 passed。

### Phase 1：P0 数据订正（需授权，预计 1–2 小时）

#### P0-1 11-05 两融未来函数

背景：11-05 两融行在 11-07 12:51 写入，随后 11-05 评分/日报/策略重算，使用了 T+1 才披露的数据。

执行步骤：

1. 只读确认：`margin_trading` 是否真的被 11-05 的 `sector_scores/report/algos` 消费（检查字段与任务依赖）；若未消费，仅记录，不重算。
2. 预览 SQL：11-05 两融行、11-05 下游 run/report 的时间戳与数量。
3. 备份：将 11-05 两融行导出到临时表或 JSON（不放仓库）。
4. 删除 11-05 两融行。
5. 按 `DAY_STEPS` 顺序重跑 11-05 下游，**不跑 margin**：`sector_scores → temperature → report → algos`，必要时补 `signal_perf` 与 paper。
6. 回插 11-05 两融行。
7. 对账：
   - 两融行恢复且只有 1 条；
   - 11-05 评分/日报时间戳晚于重算开始时间；
   - 策略 run 的 `data_version` 与当前 11-05 指纹一致；
   - `/health` 15/15；
   - `check_invariants.py` 无违规。
8. 回滚：恢复备份行，重跑同一序列，核对快照。

## 6. P1 详细方案（5 项）

### P1-1 板块名单完整性

- 原现状：38 个概念无成员、6 个 partial（各 100 只），共 44 个板块受影响。
- 已完成（11-07）：
  - 38 个无成员板块全部补齐：30 个通过浏览器同源抓取 THS；6 个复用既有缓存；
    算力租赁为 THS partial（19 页）；腾讯概念无可用 THS 节点，按新浪精确同名
    `chgn_700623` 导入 327 只（`source=sina`）；
  - `import_sina_members.py` 改为同名节点候选列表（`gn_` 优先、`chgn_` 回退），
    避免首选节点为空时误判“无成分”；
  - `import_ths_members.py` 新增 `--name` 定向过滤，避免触碰未列入目标的缓存板块；
  - 新增 `tests/test_import_ths_members.py` 5 个用例，锁定完整性下限与 partial 判定；
  - 导入后重算 2026-10-27~11-06 聚合：板块日线 4046 行、partial 龙头 108 行、
    `dim_stock.industry_l1` 4585 行；同步订正 38 个回补板块的 `member_count`
    （32 个完整名单写实际家数，6 个 partial 置 NULL）。
- 验收（2026-11-06）：
  - 447 个板块日线全部有成分（missing=0），板块评分 447/447 匹配；
  - 435 个板块有涨跌家数，12 个 partial 按设计不计家数、仅保留龙头；
  - `check_invariants.py` 17 项检查 0 违规；2 个 warning 为既有 351 条零长 SCD2
    与 12 个 open partial；
  - API `/market/dashboard`、`/market/sectors`、`/market/sector/THS_金属铜` 均 200，
    新增板块 `member_count/up_count/down_count/leading_stock` 正常。
- 源不可得清单（12 个，均保持 partial，不计家数）：
  - 新增 pages>10：粤港澳大湾区（51 页）、智能物流（26 页）、IP经济(谷子经济)（20 页）、
    文化传媒概念（20 页）、新股与次新股（16 页）、算力租赁（19 页）；
  - 既有 partial：三胎概念、仿制药一致性评价、共同富裕示范区、共封装光学(CPO)、
    海峡两岸、高股息精选；
  - 原因：THS 在 pages>10 时只暴露前后各 5 页（100 只），新浪无精确同名节点；
    不伪造名单，treemap 中这些板块仍显式显示“成分 —”。
- 依赖：P2-1 的 UI 标识可并行。
- 代价：0.5–1 天 + WAF 等待；风险是源封禁，禁止伪造。

### P1-2 信号样本不足

- 原现状：20 个信号日、902 条 perf、187 条 20 日成熟；b2/b3/b4 仍很薄。
- 已完成（11-08）：
  - 不伪造历史信号，沿用每日 17:10 管线自然累积；
  - 共识榜展开后复用 `/algo/ladder?window=60&horizon=20&trade_date=<共识日>`，
    每个命中策略卡显示样本状态，与天梯同一口径；
  - 新增 `frontend/src/lib/sample.ts`：有效样本 = 已成熟且 T+1 可成交；
    已成熟 = 总信号 − 未成熟；有效样本 < 30 标“低样本”；
  - 新增 `sample.test.mjs` 5 个用例（无统计、无信号、待成熟、不可成交、低样本/充足）；
  - 共识榜顶部标明“近 60 日信号 · 20 日成熟窗口（截至日期）”。
- 验收：
  - 浏览器 `/algo-center` 展开共识榜：B5 显示“低样本 · 有效样本 5 · 已成熟 5/88”，
    A3 显示“待成熟 · 0 条有效样本 · 211 条未成熟”；
  - 天梯 7 行均显示 `n=`、低样本/待成熟标识；共识榜无 NaN/undefined；
  - Console 仅 Vite/React Router 基线提示；`npm run test:sample` 5 passed；
    `npm run build` 通过（主包 1.02MB，沿用既有 >500kB 警告）。
- 说明：样本数仍会很少，界面只做解释与弱化，不改变排序与收益口径。
- 代价：UI 0.5 天；数据需数周。

### P1-3 自选名单隔离与鉴权

- 现状：`app_watchlist_state` 只有 id=1 一行，所有浏览器共享。
- 方案（二选一）：
  - 单机自用：部署说明 + 反向代理 Basic Auth（1 小时）；
  - 公网/多用户：增加 `user_id`/租户列 + 登录态，迁移与接口改造（1–2 天）。
- 已确认方案（11-08）：**单机自用收口**，不引入账号体系，也不做伪多租户隔离。
- 已完成（11-08）：
  - `docker-compose.yml` 的 postgres/backend/frontend 发布端口全部绑定 `127.0.0.1`
    （`5433/8000/5173` 均不再对局域网开放）；`start-local.ps1` 原本已绑定 `127.0.0.1`，
    `start.sh` 默认监听从 `0.0.0.0` 改为 `127.0.0.1`（仍可用 `SERVER_HOST` 显式覆盖）。
  - `app_watchlist_state(id=1)` 保持单行单用户，不迁移数据、不改读写契约；
    同一后端的所有浏览器共享一份名单，属该方案预期。
  - README 增加“访问边界（单机自用）”说明：跨设备优先 SSH 隧道/Tailscale，
    确有公网需求再加反向代理 Basic Auth，禁止直接改成 `0.0.0.0`。
  - 新增 `tests/test_deployment_bindings.py`，防止后续把 compose 或一键启动改回全网卡监听。
- 验收：实际监听为 `TCP 127.0.0.1:8000`、`127.0.0.1:5173`；从本机局域网地址
  （10.2.121.44 / 192.168.182.1 / 172.24.240.1）连接 8000 与 5173 均超时、未建立连接；
  `docker compose config` 三处 `host_ip=127.0.0.1`；端口绑定回归测试 3 passed；
  `GFNA_TEST_DB=1` 下自选读写测试 2 passed；现有自选行不迁移。
- 说明：该方案没有账号体系，因此没有 401 语义；以“非本机请求不可达”替代
  “未授权请求 401”。若未来需要远程或多用户，再升级 Basic Auth 或多用户登录。
- 代价：约 1 小时。

### P1-4 任务状态机

- 现状：`grid` 使用进程内 `BackgroundTasks`；`ingest_job` 有 12 条 10-21~11-04 的僵尸 running。
- 方案：
  1. 启动时把超过 2 小时的 running/queued 标为 failed，错误写明“进程重启遗留”；
  2. T10 任务失败后允许重试；
  3. 中期改为 DB 队列 + `FOR UPDATE SKIP LOCKED` + heartbeat。
- 已完成（11-08）：
  - 新增 `app/services/task_recovery.py`：启动时扫描 `ingest_job`/`backtest_run` 的
    `running|queued` 与兼容的 `stg_run.running`，把开始时间超过 2 小时的行改为 `failed`，
    `error` 固定为“进程重启遗留：服务启动时任务仍处于 running/queued 状态，已标记 failed”，
    并写 `finished_at`/`elapsed_ms`；返回各表清理计数并记 warning。
  - `app/main.py` lifespan 在建表后、调度器前调用恢复；异常只记录不阻断启动。
  - T10 新增 `POST /api/v1/backtest/grid/{grid_id}/retry`：只把该矩阵 `failed` 格重置为
    `queued` 并清空旧指标/净值/成交/错误，后台按每格参数快照（含 `strategy`、起止日）重跑；
    成功格与运行中的格不动；未知 `grid_id` 返回 404。
  - `execute_grid` 支持每格 `strategy`/`start_date`/`end_date` 覆盖，避免用单格参数放大
    其他格子的选股上限。
  - 前端敏感性矩阵在 `progress.failed > 0` 时显示“重试失败格”，成功后失效矩阵查询并继续轮询。
- 验收：新增 18 个单测 + 2 个真实 SQL 集成测试通过；完整后端套件 499 passed / 49 skipped；
  真实重启 PID 73968 → 80396，启动日志 `启动任务状态检查完成：ingest_job=0 stg_run=0
  backtest_run=0 已标记 failed`，`/health` 200，`/api/v1/meta/jobs` 无 running/queued；
  成功矩阵重试返回 `retried=0`，未知矩阵 404；前端 `npm run build` 与 `npm run test:sample`
  通过（主包 1.03MB，沿用既有 >500kB 警告）。
- 遗留：任务仍由进程内 `BackgroundTasks` 执行，进程被杀时正在跑的任务要等 2 小时阈值才会被
  下次启动清理；彻底方案为 DB 队列 + `FOR UPDATE SKIP LOCKED` + heartbeat（中期）。
- 代价：短期 0.5 天，完整 2–3 天。

### P1-5 NO_PROXY 固化

- 现状：`NO_PROXY` 含 `[::1]` 会让 httpx/openai 初始化报 `Invalid port`，AI 日报静默退化为模板。
- 已完成（11-07）：
  - `llm_service.sanitize_proxy_env()` 同时清洗 `NO_PROXY`/`no_proxy`：去掉
    空项、重复项与 IPv6 方括号（`[::1]` → `::1`），保留其余例外项，变化时记 warning；
  - `get_client()` 构造 OpenAI 客户端前调用；FastAPI `lifespan` 启动时也调用一次，
    服务启动即暴露异常，不再等到首次日报；
  - 新增 `tests/test_llm_proxy.py` 3 个用例（清洗、幂等、脏环境下客户端可构造）。
- 验收：脏环境下复现 `InvalidURL: Invalid port: ':1]'`；修复后真实
  `chat_text` 返回正常，重启日志含清洗 warning，`/health` 15/15 数据集新鲜；
  单测 486 passed / 33 skipped。
- 代价：0.5 天。

## 7. P2 详细方案（10 项）

| 编号 | 问题 | 方案 | 验收 | 代价 |
|---|---|---|---|---|
| P2-1 | partial 板块无“名单不完整”提示 | 用 `is_partial` 加徽章；家数/市值显示“不适用” | 抽屉与 tooltip 明确标注样本数 | 1h |
| P2-2 | 矩阵复用改挂旧 `grid_id`，旧链接 404 | 增 `backtest_grid` 主表，复用只新增关联 | 旧链接可打开；复用不改变历史记录 | 0.5–1d |
| P2-3 | 纸面组合全区间重算约 50s/策略 | 水位 + 60 日重叠窗口，每周全量校准 | 单策略 < 10s；与全量结果一致 | 1–2d |
| P2-4 | 单次回测 19–21s 紧贴 30s 超时 | 异步任务 + 轮询；短期单独放宽超时 | 长回测不被截断；接口不阻塞 | 1–2d |
| P2-5 | 共识榜 N+1 kline | 接口批量返回 30 日序列或新增批量端点 | 单页请求数从 N 降为 1 | 0.5d |
| P2-6 | 移动端无命令面板入口 | 移动导航加搜索按钮，复用 `PALETTE_OPEN_EVENT` | 375px 可打开面板 | 1h |
| P2-7 | 101 个历史 run 缺 `metrics.triggered` | 加 schema 版本，只读标注；可复现的按 run 重算 | 旧 run 明确显示“旧版本快照” | 0.5–1d |
| P2-8 | 测试残留污染真实库 | 清理 + API/集成测试固定测试库/事务回滚 | 无测试写入残留；CI 不连生产库 | 0.5d |
| P2-9 | 单次回测默认结束日取 today | 改为 `min(today, /health.latest_trade_date)` | 周末/数据滞后不直接失败 | 1h |
| P2-10 | 资金流含无效代码 810011/810013/810014 | 预览后定向删除，并加代码合法性校验 | 三码清零；新数据不再写入 | 15min |

### P2-1 partial 板块提示（已完成 11-08）

- 后端：`/market/sectors`、`/market/dashboard.sectors`、`/market/sector/{code}` 增列
  `is_partial`（整份名单是否只有 partial 样本）与 `partial_member_count`（样本成员数）；
  判定基于 `dim_sector_member` 当前有效区间，完整名单与 partial 混合时仍按完整名单处理。
- 前端：热力图 tooltip/抽屉显示“名单不完整 · 样本 N 只”，家数与面积/市值显示“不适用”；
  格子 `aria-label` 与头部统计补充 partial 数量，抽屉标题带“名单不完整”徽章。
- 验收：单测 3 passed、测试库集成 2 passed；真实 API 2026-11-06 概念板块 357 个中
  12 个 partial（各 100 只），详情返回 `is_partial=true / partial_member_count=100`；
  前端 build、tooltip/抽屉浏览器验收与禁用文案扫描通过。
- 代价：约 1 小时。

### P2-2 矩阵复用与旧链接（已完成 11-08）

- 新增 `backtest_grid` 主表（`grid_id / algo_code / start_date / end_date / axes /
  strategy_params / created_at`）与 `backtest_grid_cell` 关联表（`grid_id + bt_id` 主键、
  两轴坐标、`reused`）；同一回测可同时属于多个矩阵。
- `create_grid` 复用只在关联表新增行，不再改写回测行；`get_grid` 与 `requeue_failed_cells`
  改为读关联表，响应结构与前端不变。
- 迁移 0009 把存量矩阵回填进新表后删除 `backtest_run.grid_id / grid_cell`；
  历史上已被复用改挂丢格的旧矩阵无法还原，但迁移时仍存在的链接全部保持可查。
- 验收：单测 16 passed（含新增的关联读取用例）；测试库迁移 + 关联集成测试 2 passed；
  矩阵 API 4 passed；真实库迁移得 23 个矩阵 / 46 条关联、0 孤儿；抽样旧链接 200、
  未知矩阵 404；真实复用后旧矩阵 4 格、elapsed 与复用标记完全不变。
- 遗留：2026-11-01 之前被复用改挂的矩阵丢格无法恢复（旧列已被覆盖，没有历史映射）。
- 代价：约 3 小时。

### P2-3 纸面组合水位增量重算（已完成 11-08）

- 引擎新增检查点续算：`simulate_portfolio` 可从「现金 / 后复权份额 / 成本 / 基准基数」
  内部状态继续模拟，并可在指定交易日收盘落状态；`run_backtest` 新增
  `initial_state / phase_anchor / signal_from / capture_state_on / state_out`。
- 调仓相位以组合建仓日为绝对锚点（`phase_signal_dates`），窗口首日不再重排调仓节奏；
  检查点日的信号在窗口首日成交，保证与全量逐日一致。
- 纸面同步默认走「检查点 + 60 个交易日重叠窗口」：每日只重算水位之后至多 60 个交易日，
  每周第一个同步日（按结束日 ISO 周判断）全量校准；增量异常时回滚并自动回退一次全量。
- 指标改由完整 `paper_nav` 序列计算（`_series_metrics`），回撤峰值从窗口前净值续算，
  避免增量窗口截断累计收益/回撤。检查点与参数快照存于 `paper_portfolio.params.incremental`
  （JSONB，无新表、无迁移）；参数或策略快照不一致时强制全量。
- 验收：真实库同日两次同步——a3 全量 86.4s / 572 行、增量 2.16s / 60 行窗口；b4
  全量 1.39s / 10 行、增量 0.03s / 9 行窗口；两次结果在 nav/benchmark/drawdown/
  cash_ratio/holdings/pending_orders/vs_backtest 上逐行零差异。单测 20 passed（含检查点
  下标 25/26/30 的续算 = 全量、周切换/参数变更/无效状态回退全量）；非 DB 套件
  515 passed / 1 skipped，集成 18 passed。
- 遗留：每周全量校准仍是一次整段重算（a3 约 86s）；历史信号随数据版本重算时，全量校准会
  改写既有历史净值（b4 近期 5/5 行不变，a3 的 2024 年以来信号在 11-01/11-08 被重算，
  故净值整体漂移），跨数据版本不承诺历史净值不变；检查点 JSONB 体积随持仓数增长。
- 代价：约 1.5 天。

### P2-4 回测异步化（已完成 11-08）

- `POST /backtest/{algo_code}` 改为「校验 + 入队」：同步阶段只做未知策略、区间、数据起点
  裁剪等可快速判定的校验，落 `backtest_run.status=queued` 并 commit 后，通过 FastAPI
  `BackgroundTasks` 调度 `execute_backtest`，立即返回任务信封
  （`bt_id/status=queued/start_date/end_date/requested_start/effective_start/start_trimmed/poll`）。
- 后台执行复用原 `run_backtest` 引擎（`db=None` 自建会话，`bt_id` 已存在），成功/失败都由
  引擎落库；执行器只记日志，不把异常抛回事件循环。HTTP 请求不再承担 19–21s 的回测时长。
- `GET /backtest/{bt_id}` 成为唯一结果入口：始终返回 `status/error`，`queued/running` 时
  metrics/曲线/成交为 null、派生视图为空，`success` 时与旧同步响应完全一致。
- 前端 `useBacktest` 改为「POST 入队 + 每 1.5s 轮询 + 10 分钟上限」，mutation 接口不变，
  `BacktestPanel` 无需改动；失败时把后端 `error` 转成 `BACKTEST_FAILED` 展示。
- 启动清理已覆盖 `queued/running` 的 `BacktestRun`：进程重启后超过 2 小时的遗留行标 failed，
  不会永久占着「运行中」。
- 验收：新增 3 个单测（入队不内联执行、非法区间不入队、执行器透传并吞掉异常）；全量单测
  518 passed / 51 skipped；回测 API 集成 2 passed（21.7s，含矩阵复用）；`npm run build`
  通过（主包 1026.67 kB / gzip 301.28 kB）。
- 真实路径：POST a3 一年区间 **47ms** 返回 queued，紧接着 GET 为 queued；轮询 9 次
  （1.5s 间隔）12.7s 后 success，metrics/243 点净值/164 笔成交/extras 齐全，CSV 673 行、
  4 个 block 正常。浏览器点击「运行回测」400ms 内显示「回测中…」，后台完成后按钮恢复、
  结果与导出按钮出现，Console 无新增 error（仅既有 React Router warning）；网络记录为
  1 次 POST + 7 次 GET 轮询。
- 遗留：POST 响应是 breaking change（已同步前端与测试，无其他消费方）；后台任务在 API
  进程内执行，无持久队列，进程重启后 2 小时内不会自动判失败、也不会自动续跑；前端轮询
  最长 10 分钟，超时后后台任务仍继续，页面需重新刷新查看；多标签页会各自轮询，无并发上限。
- 代价：约 0.5 天。

### P2-5 共识榜批量走势（已完成 11-08）

- 新增 `GET /api/v1/stocks/sparkline/batch?codes=&days=30&end=`：一次 SQL 取多只股票的
  近 N 个交易日收盘价，返回 `{end, days, adjust_basis:"raw", items:[{code, closes}]}`。
  路由注册在 `/{code}` 之前，避免 `sparkline` 被当成股票代码吞掉。
- 参数约束：`codes` 逗号分隔、最多 200 个、允许省略前导零（`1` → `000001`）、自动去重
  且保持首次出现顺序；空串/非数字/超 6 位/超 200 个统一 `422 INVALID_PARAM`。
  `days` 5–120 默认 30（按交易日，不是自然日），`end` 默认库内最新行情日。
- 查询窗口固定为 `end − (days×2+10)` 自然日，避免无界扫描；窗口内不足 N 条就返回实际
  条数，不向前补齐、不插值。无数据的代码返回空数组，前端渲染 `—`。
- 口径与 4.2 单只 K 线严格一致：同一 `fact_stock_daily.close` 未复权原始价，
  集成测试逐值比对批量与单只 `kline` 的 `close` 序列。
- 前端 `ConsensusBoard` 从「每行一个 `useKline`」改为「整页一次 `useSparklineBatch`」：
  query key 含排序去重后的代码集合 + days + trade_date，`staleTime` 5 分钟，
  `placeholderData` 保留上一次数据避免展开/切换时闪烁；面板未展开时不发请求。
- 验收：新增 6 个纯函数单测（补零/去重/非法输入/上限）与 3 个集成测试
  （批量 = 单只逐值一致、顺序一致、参数校验 422）；全量单测 524 passed / 53 skipped；
  `npm run build` 通过（主包 1027.18 kB / gzip 约 301 kB）。
- 真实路径：15 只代码批量接口 **30ms / 2.9 KB**，全部序列 30 点；同 10 只逐只 `kline`
  合计 263ms。浏览器共识榜 22 行 / 15 个信号代码，网络记录 `batchCalls=1`、`klineCalls=0`，
  15 个 sparkline 全部 30 点、`missing=0`，tooltip 的末值/区间/点数与序列一致，
  Console 无新增 error（仅既有 React Router warning）。
- 遗留：批量接口只返回收盘价，不含 OHLC/均线，需要完整 K 线的场景仍走 4.2；单次最多
  200 只，超出需前端分批；前端按「代码集合」缓存，信号列表变化会触发一次新请求；
  无数据代码返回空数组属预期，不区分「停牌」与「代码不存在」。
- 代价：约 0.5 天。

### P2-6 移动端命令面板入口（已完成 11-08）

- 窄屏（`<768px`）桌面搜索框整块 `hidden md:block`，命令面板只剩 Ctrl/⌘+K 键盘快捷键，
  手机上没有任何入口。
- `Navbar` 右侧操作区新增 `md:hidden` 搜索按钮（36×36，`aria-label="打开命令面板"`），
  点击派发 `PALETTE_OPEN_EVENT`，与桌面 ⌘K 按钮共用同一事件；`CommandPalette` 的
  打开/关闭、焦点管理和结果渲染零改动。`≥768px` 按钮隐藏，桌面仍用搜索框内 ⌘K。
- 验收（Playwright headless Chromium，真实 375×812 视口，非 MCP 固定视口）：
  - 375px 下 `innerWidth=375`、`scrollWidth=375`（无横向溢出）；移动按钮可见
    （36×36，x=268，y=14），桌面搜索框 `display:none`。
  - 点击按钮后命令面板出现（343×537，x=16/y=97，完整落在视口内），14 个可选项，
    `pages` / `algos` 分组正常，输入框自动聚焦。
  - 输入 `600519` 返回「贵州茅台」1 条且默认选中；Esc 与点击遮罩均能关闭。
  - 1280px 下移动按钮 `display:none`，⌘K 按钮可见并能打开面板（桌面无回归）。
  - Console 仅既有 React Router 两条 warning，无 error。
- 回归：`npm run build` 通过（主包 1027.69 kB / gzip 301.51 kB，较 P2-5 +0.51 kB）。
- 遗留：移动端无物理键盘，面板底部仍显示 ↑↓/Tab/↵ 键盘提示；320px 以下未验；
  该按钮只打开命令面板，不替代桌面新闻搜索框（窄屏仍无新闻关键词搜索入口）。
- 代价：约 1 小时。

### P2-7 历史 run 信号快照版本（已完成 11-08）

- 问题：重构前的 run 没有 `metrics.triggered` / `score_breakdown`。前端只凭字段缺失
  判断“旧版本”，无法区分“旧快照”与“当前版本但该信号缺依据”；统计侧也无法按版本筛选。
- 方案：`stg_run` 新增 `signal_schema SMALLINT NOT NULL`（1=旧版无结构化依据，2=当前）。
  迁移 `0010_stg_run_signal_schema` 先按默认 1 加列，仅用已落库 JSONB 证据回填：
  `metrics ? 'triggered' OR metrics ? 'score_breakdown'` 的 run 标为 2，再把默认值改为 2；
  不重算、不推断、不改写任何信号数据。
- 后端：`runner.SIGNAL_SCHEMA_VERSION = 2`，新 run 写入该值；`_run_meta` 与 `list_runs`
  返回 `signal_schema`（未落库对象兜底 1）。**不批量重跑历史 run**：重跑会生成新 run 并把
  同参数旧 success run 置为 `superseded`，改变历史与统计口径。
- 前端：`AlgoRun.signal_schema` + `isLegacySignalSnapshot()`；`RunMetaPanel` 仅对
  a3/b1–b5 且 `signal_schema<2`、`signal_count>0` 的 run 显示「旧版本快照」徽章（title 带
  版本号）；`SignalExplanation` 对旧快照显示“无拆解/无逐条依据”，对当前版本缺字段显示
  “当前版本未记录该信号…”，两种提示不再混用。a1/a2/a4 本身无可解释依据，不显示徽章。
- 数据对账（gfna_db）：迁移前 head `0009`，1019 个 run；迁移后 `signal_schema=2` 366 个、
  `=1` 653 个；`signal_schema=1` 且存在结构化证据的 run = 0，结构化证据 run 全部为 2；
  列类型 `smallint NOT NULL DEFAULT 2`。迁移同时应用到 `gfna_test`。
- 验收（Playwright 1440×900，真实前后端）：
  - 当前 a3 run（schema 2）：无徽章，展开首行显示评分拆解（“条形长度 = 分项得分 / 10”）。
  - 历史 a3 run `989c8220`（schema 1，20 信号）：徽章可见且 title 含“信号快照 schema 1”，
    展开显示“该 run 为旧版本快照，无评分拆解”，无拆解条。
  - a4 历史 run（schema 1、50 信号）：无徽章、无展开列（策略本身无可解释依据，不误报）。
  - Console 仅既有 React Router 两条 warning，无 error。
- 回归：`tests/test_run_signal_schema.py`（4 例）、`tests/integration/test_run_signal_schema_sql.py`
  （1 例，gfna_test 默认值）；相关 run/strategy 单测 43 例通过。`npm run build` 通过
  （主包 1028.32 kB / gzip 301.75 kB，较 P2-6 +0.63 kB）。
- 遗留：历史 run 的“可复现重算”未执行（批量重算会污染历史与统计），仅保留只读标注；
  确需最新依据需用户显式执行或强制刷新。历史 run 下拉仅列最近 10 条，更早的旧 run 需
  通过 run_id 直达（本次验收用真实 onChange 注入，不改接口/数据）；a1/a2/a4 存量 run 仍为
  schema 1，仅表示写入版本，不产生依据缺失提示。
- 代价：约 0.5 天。

### P2-9 回测默认结束日（已完成 11-08）

- 问题：`POST /api/v1/backtest/{algo_code}` 省略 `end` 时取 `date.today()`，与契约
  “默认最新交易日”不符。2026-11-08（周日）实测：返回并落库 `end_date=2026-11-08`，
  但净值曲线最后一天是 `2026-11-06`；遇到行情未落库的工作日还会把无数据日算进区间。
- 方案：新增 `_default_end_date()`，取
  `min(date.today(), max(fact_stock_daily.trade_date))`，与 `/health.latest_trade_date`
  同源；库内无行情时抛 `DataUnavailableError`（不静默用 today）。显式传入 `end` 原样使用。
  前端无需改动：单次回测只传 `start`，由后端解析；参数敏感性矩阵已用
  `/health.latest_trade_date` 作为默认结束日。
- 验收（真实前后端）：
  - 修复前：`POST /backtest/a3?start=2026-09-01`（不传 end）→ `end_date=2026-11-08`，
    DB 同上，曲线止于 `2026-11-06`。
  - 修复后同一请求 → `end_date=2026-11-06`，曲线末点 `2026-11-06`，`status=success`，
    DB 落库一致；显式 `end=2026-09-30` 的记录仍为 `2026-09-30`。
  - 浏览器 1440×900 真实点击「运行回测」：界面显示
    「回测区间：2026-09-01 ~ 2026-11-06」，不含 11-08；Console 仅两条既有
    React Router warning，无 error。
  - `tests/test_backtest_job.py` 5 例通过（含默认结束日、未来日期夹紧、无行情报错、显式 end 不覆盖）；
    `test_backtest_view.py` + `test_grid_unit.py` 24 例通过。
- 遗留：`latest_trade_date` 只代表行情最新日，不等于各策略指标/评分的最新就绪日；
  若指标或评分晚于行情，信号层仍会报“数据未计算”，属于新鲜度/补跑问题。显式传入未来
  `end` 仍原样接受，不在本次范围。
- 代价：约 1 小时。

### P3-3 /ladder 独立路由（已完成 11-08）

- 问题：外部链接或书签直接访问 `/ladder` 命中 404；策略天梯只能从选股中心手动下滚。
- 方案：`/ladder` 用 `<Navigate to="/algo-center#ladder" replace />` 重定向；
  `StrategyLadder` 根节点增加 `id="ladder"` 与 `scroll-mt-20`；`AlgoCenter` 增加
  hash 定位 effect，等开场幕布交还滚动（`.app-shell.is-intro-ready`，5s 兜底超时）
  后再 `scrollIntoView`。纯前端改动，不涉及接口与数据。
- 验收（真实浏览器，Playwright Chromium 1440×900）：
  - 直接访问 `/ladder` → 地址栏 `/algo-center#ladder`，页面无 404 文案；
  - `#ladder` 存在且可见，`scrollY=108`，区块顶边 `top=86`（避开固定导航），
    首屏即「策略天梯」；截图 `backend/var/p3_3_ladder_route.png`；
  - Console 仅既有两条 React Router warning，无 error。
  - `npm run build` 通过（主包 1029.20 kB / gzip 302.01 kB，较 P2-9 +0.88 kB）。
- 遗留：hash 定位依赖天梯区块已渲染；若未来改为按需加载/条件渲染，需要把定位逻辑
  挂到区块挂载后。开场幕布禁用（`?intro=0`、reduced-motion）时直接定位，路径一致。
- 代价：约 20 分钟。

### P3-2 评分榜 sparkline（已完成 11-08）

- 问题：`ScoreRanking` 只有当日综合分，源码留有 `TODO(T14)`「评分历史未接入」；无法从
  评分榜看出板块评分是在走强还是走弱。
- 数据现状：`sector_score` 目前仅有 **13 个评分日**（2026-10-21 ~ 11-06，每日 447–480 行），
  不是 30 个自然日；因此接口以“实际存在的评分日”为共同横轴，而不是固定 30 天日历。
- 方案：
  - 新增 `GET /api/v1/market/sector-scores/sparkline?codes=&days=30&end=`（契约 3.6）：
    `codes` 去重保序、最多 100 个；`days` 5–120；`end` 默认库内最新评分日；
    响应 `{end, days, dates[], items[{sector_code, scores[]}]}`，`scores` 与 `dates`
    等长，某日无评分填 `null`，不补 0、不前值填充；未知代码返回全 `null` 而非报错。
  - 前端 `ScoreRanking` 只对 TOP 10 请求 30 日序列（`useSectorScoreSparkline`，缓存键含
    排序去重后的代码 + days + end），复用 `Sparkline`（固定 `domain=[0,10]`）；0/1 个
    有效点显示 `—`，不画灰色假线；`sm` 以下隐藏走势列；日期切换时 `end` 跟随所选交易日。
- 自测：
  - `backend/tests/test_sector_score_sparkline.py`：无 DB 时 8 通过 1 跳过；
    `GFNA_TEST_DB=1` 时 9 通过（含与 dashboard top-10 的末值/日期/区间一致性）。
  - 真实浏览器（Playwright Chromium，1440×900，`?intro=0`）：
    - 最新日 11-06：10 行、10 个可见 sparkline，`data-points=13`、`data-missing=0`，
      tooltip `光伏设备 · 近 13 个评分日综合评分 · 末值 7.1 · 区间 3.6 – 7.1 · 13 点`，
      单次批量请求（`apiRequests=1`）；
    - 切换 2026-10-21（当日仅一天评分历史）：10 行全部 `—`、0 个 sparkline SVG；
    - 切换 2026-10-23：10 个 sparkline、3 个点；
    - 375px 视口：10 个 SVG 在 DOM 中但可见数为 0，`scrollWidth=375=innerWidth` 无横向溢出；
    - Console 仅两条既有 React Router warning，无 error。
  - `npm run build` 通过（主包 1030.70 kB / gzip 302.55 kB，较 P3-3 +1.50 kB）。
- 遗留：只有 13 个评分日，30/60 日走势在补齐评分前无法呈现；2026-10-21 的评分行可能
  含演示期数据（清理未授权，与评分榜现状一致，未在本项处理）；移动端隐藏走势列；
  接口按评分日取点而非自然日，跨长假时相邻点间隔会变大。
- 代价：约 1.5 小时。

## 8. P3 详细方案（4 项）

| 编号 | 问题 | 方案 | 验收 | 代价 |
|---|---|---|---|---|
| P3-1 | 主包 1013 kB，未分包 | 路由 `React.lazy` + `manualChunks` | 主包显著下降；生产构建无新增报错 | 0.5–1d |
| P3-2 | 评分榜无 sparkline | 增加板块评分序列接口，复用 Sparkline | 30 日走势可见；空值显示 `—` | 0.5d |
| P3-3 | 无 `/ladder` 独立路由 | redirect 到 `/algo-center#ladder` | 直接访问不 404 | 15min |
| P3-4 | 1440/1920 与生产构建验收不足 | Playwright 四档视口 × 明暗 × 核心页面 | CI 产出截图与断言结果 | 1d |

## 9. 修复前检查清单（每项必填）

| # | 检查项 | 必填内容 |
|---|---|---|
| 1 | 复现 | 日志/SQL/接口证据，修复前行为 |
| 2 | 验收标准 | 可断言的业务不变量，不写“页面正常” |
| 3 | 影响面 | 表、字段、任务、API、前端消费方 |
| 4 | 共享状态 | session/engine、checkpoint、新鲜度、状态字段 |
| 5 | 回归测试 | 先失败后通过的测试文件与用例名 |
| 6 | 真实路径 | 实际任务/页面/数据库验证步骤 |
| 7 | 回滚 | 代码回退或数据库恢复步骤 |
| 8 | 可观测 | 日志、quality_status、监控项 |
| 9 | 预算 | 文件数、净增行数；超限拆分 |

## 10. 变更影响分析模板

| 维度 | 检查动作 | 结果 |
|---|---|---|
| 代码引用 | 全仓搜索函数/字段/状态字符串 | 列出所有调用方 |
| 数据血缘 | 源表 → 引擎 → 任务 → API → 前端 | 标出上下游 |
| 任务顺序 | 是否影响 `DAY_STEPS` / `RANGE_STEPS` | 标出前后依赖 |
| API 契约 | 响应字段、状态值、前端类型 | 是否 breaking |
| 数据库 | 迁移、索引、行数、回滚 | 预览 SQL 与影响行数 |
| 共享状态 | checkpoint、新鲜度、全局会话 | 是否需要迁移/清理 |
| 验收 | 真实路径与 canary 时间点 | 下次调度后复核 |

## 11. 回归验证矩阵

| 层级 | 工具/命令 | 覆盖 | 何时跑 | 闸门 |
|---|---|---|---|---|
| 单元 | `pytest tests -q` | 纯函数、状态、重试 | 每次提交 | 全绿 |
| 集成 | `GFNA_TEST_DB=1 pytest tests/integration -q` | SCD2、新鲜度、状态机、checkpoint | 每次提交/CI | 全绿 |
| 契约 | OpenAPI/响应 schema 快照 | 字段与状态值 | 改 API 时 | 无 breaking |
| 前端 | `npm run build` + 三个 `node --test` | 类型、构建、纯函数 | 每次前端提交 | 全绿 |
| E2E | Playwright | 375/768/1440/1920 × 明暗 × 核心页 | 里程碑/发布前 | 无溢出、无禁用文案、Console 无新增 error |
| 数据不变量 | `python scripts/check_invariants.py` | SCD2、新鲜度、僵尸任务、失败状态 | 每次管线后 | 无违规 |
| Canary | 下一次真实日更 | 修复项是否被覆盖 | 数据修复后 | `/health` + 对账通过 |

测试库约定：

- 专用库名 `gfna_test`，以 `_test` 结尾；
- CI 使用 PostgreSQL service，禁止连接 `gfna_db`；
- 每个用例事务回滚，测试后无残留；
- 现有 `test_api_v5.py` 等演示数据用例逐步迁到独立 fixture。

## 12. 排期与里程碑

| 周次 | 日期 | 任务 | 里程碑 |
|---|---|---|---|
| 第 1 周 | 11-08 ~ 11-14 | P0 预览与订正（待授权）；W0-1 CI；W0-2 测试骨架；W0-3 不变量脚本；W0-4 状态枚举；W0-5 清理；P1-5 NO_PROXY | M1：P0 关闭、CI 绿、不变量脚本上线 |
| 第 2 周 | 11-15 ~ 11-21 | P1-4 任务状态机；P1-1 板块回补；P1-2 样本标识 | M2：P1 完成 4/5 |
| 第 3 周 | 11-22 ~ 11-28 | P1-3 鉴权；P2-1/6/8/9/10 快速项 | M3：P1 5/5、P2 5/10 |
| 第 4 周 | 11-29 ~ 12-05 | P2-2/3/4/5/7 | M4：P2 10/10 |
| 第 5 周 | 12-06 ~ 12-12 | P3-1/2/3/4；全量回归；复盘 | M5：全部关闭 |

缓冲：预留 2–3 天。P1-1 受 WAF 影响、P1-3 受方案选择影响，允许顺延但不跳过验收。

## 13. 指标与监控

| 指标 | 目标 | 采集方式 |
|---|---|---|
| 回归逃逸数 | 0（7 天内） | 修复后新缺陷计数 |
| CI 通过率 | 100% | GitHub Actions |
| 集成测试数 | ≥ 15 | pytest 统计 |
| 不变量违规 | 0 | `check_invariants.py` |
| 僵尸 running 任务 | 0（>2h） | `ingest_job` |
| 测试写入残留 | 0 | 回测/任务表对账 |
| 数据新鲜度 | 15/15 | `/health` |
| 主包体积 | 较 1013 kB 明显下降 | `vite build` |
| P1/P2/P3 关闭率 | 按里程碑 | 跟踪表 |

## 14. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| P0 订正授权延迟 | 高 | 11-05 数据继续含未来函数 | 先给预览 SQL 与影响范围，等待授权，不擅自执行 |
| 数据源 WAF/断连 | 中 | P1-1 延期 | 多源降级；partial 显式标识；不伪造 |
| CI 测试库与生产 schema 漂移 | 中 | 集成测试不可信 | 用 alembic head + schema 对比；CI 固定 PG 大版本 |
| 测试误连生产库 | 中 | 数据污染 | 库名 `_test` 硬校验；CI 独立凭据 |
| 改动范围膨胀 | 高 | 回归率上升 | 5 文件/200 行预算；超限拆两个提交 |
| Windows 计划任务权限 | 低 | watchdog 升级受阻 | 保留手动启动脚本；变更前记录任务 XML |
| 历史数据口径变化 | 中 | 回测/评分口径不一致 | 数据血缘表 + 黄金样本对比 + 版本号 |

## 15. 变更与提交规范

- 分支：低风险改动可直接在 `main`；高风险（状态机、数据订正）先建短期分支。
- 提交格式：`fix(<scope>): <描述>（P0-36 / P1-1）`；一个提交只含本任务文件。
- 每个提交必须带测试证据与真实路径证据；无测试的必须写明原因。
- 数据库订正：预览 SQL → 用户确认 → 单次执行 → 对账；禁止原样重试被拦截的命令。
- 默认不 push；用户明确要求才推送。
- 每完成一项，按三段汇报；每周给一次台账更新。

## 16. 跟踪表

| 编号 | 优先级 | 任务 | 代价 | 目标周 | 依赖 | 状态 |
|---|---|---|---:|---|---|---|
| P0-1 | P0 | 11-05 两融未来函数订正 | 1–2h | W1 | 授权 | 待授权 |
| W0-1 | 基建 | CI 流水线 | 0.5d | W1 | — | 完成（11-07） |
| W0-2 | 基建 | 集成测试骨架与 6 组不变量 | 1–2d | W1 | W0-1 | 完成（11-07） |
| W0-3 | 基建 | `check_invariants.py` | 0.5d | W1 | W0-2 | 完成（11-07） |
| W0-4 | 基建 | 状态枚举与新鲜度单一入口 | 0.5d | W1 | W0-2 | 完成（11-07） |
| W0-5 | 基建 | 测试残留清理 | 0.5d | W1 | 授权 | 完成（11-07） |
| P1-1 | P1 | 板块名单完整性 | 0.5–1d | W2 | W0-3 | 完成（11-07） |
| P1-2 | P1 | 信号样本标识 | 0.5d | W2 | — | 完成（11-08） |
| P1-3 | P1 | 自选隔离与鉴权 | 1h–2d | W3 | 方案确认 | 完成（11-08，单机收口） |
| P1-4 | P1 | 任务状态机 | 0.5–3d | W2 | W0-4 | 已完成（11-08，短期方案） |
| P1-5 | P1 | NO_PROXY 固化 | 0.5d | W1 | — | 完成（11-07） |
| P2-1 | P2 | partial 板块提示 | 1h | W3 | P1-1 | 完成（11-08） |
| P2-2 | P2 | 矩阵复用关联 | 0.5–1d | W4 | — | 完成（11-08） |
| P2-3 | P2 | 纸面增量重算 | 1–2d | W4 | — | 完成（11-08） |
| P2-4 | P2 | 回测异步化 | 1–2d | W4 | P2-2 | 完成（11-08） |
| P2-5 | P2 | 共识榜批量 kline | 0.5d | W4 | — | 完成（11-08） |
| P2-6 | P2 | 移动端命令面板 | 1h | W3 | — | 完成（11-08） |
| P2-7 | P2 | 历史 run 元数据标注 | 0.5–1d | W4 | — | 完成（11-08） |
| P2-8 | P2 | 测试隔离与残留清理 | 0.5d | W3 | W0-5 | 待授权 |
| P2-9 | P2 | 回测默认结束日 | 1h | W3 | — | 完成（11-08） |
| P2-10 | P2 | 无效资金流代码清理 | 15min | W3 | 授权 | 待授权 |
| P3-1 | P3 | 前端分包 | 0.5–1d | W5 | — | 待开始 |
| P3-2 | P3 | 评分榜 sparkline | 0.5d | W5 | — | 完成（11-08） |
| P3-3 | P3 | `/ladder` 路由 | 15min | W5 | — | 完成（11-08） |
| P3-4 | P3 | 视口与生产验收 | 1d | W5 | P3-1 | 待开始 |

## 17. 立即可执行的前 5 步

1. 用户确认是否授权 11-05 两融订正；我先提供只读预览与影响行数。
2. 建立 CI（W0-1），先接现有单元测试与前端构建，不改变业务代码。
3. 建立 `gfna_test` 测试库与事务回滚夹具（W0-2），先落 6 组不变量。
4. 落地 `check_invariants.py`（W0-3），在现有 12 条僵尸任务上验证能报错。
5. 预览并清理测试残留（W0-5 / P2-8），之后禁止测试写入生产库。

> 本计划只描述执行方案，不包含代码或数据库变更。实际执行仍按“一次一项、单独提交、默认不 push”。
