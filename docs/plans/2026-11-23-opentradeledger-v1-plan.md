# OpenTradeLedger（交易账本）v1.0 开源重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在独立干净的新仓库 `~/OpenTradeLedger` 中构建可开源至 GitHub 的多交易所只读核账与复盘系统「交易账本（OpenTradeLedger）」，实现插件化交易所适配器、本地 Fernet 加密凭据、零误差精算核销、内置 Demo 模式、中英双语 + 极性/隐私/三主题极简现代前端、进/出场手续费逐腿拆清，并支持无缝接管本机 `8012` 实盘数据。

**Architecture:** 后端基于 Python + FastAPI + SQLite WAL，采用 `StrictReadOnlyGuard` + `CredentialVault (Fernet)` + `BaseExchangeAdapter / AdapterRegistry` 插件架构，无损封装经实盘验证的多所精算内核并归一化为动态 `exchanges[]` 契约；前端基于 React 19 + TypeScript + Vite + Tailwind CSS v4 + Lucide + Recharts，完全按 6 页面开源原型（总览、合约交易、收益分析、成本与复盘、扩展账本、设置与连接）模块化构建。

**Tech Stack:** Python 3.12+ (FastAPI, Uvicorn, HTTPX, Cryptography, Pytest), React 19, TypeScript, Vite, Tailwind CSS v4, Lucide React, Recharts, Vitest, Docker.

**Spec:** `docs/specs/2026-11-23-opentradeledger-v1-design.md`

## Global Constraints

- 严格只读：所有交易所签名客户端仅允许 `HTTP GET` 且路径命中显式白名单，非 `GET` 立即抛出 `ReadOnlyViolationError`。
- 零隐私与零硬编码：严禁在仓库代码、测试、文档中出现个人 UID、密钥、内网 IP、固定目标金额 `9000`、固定带单员 ID、个人标签或写死的交易所数量文案（如「五所」「5 家交易所」）。
- 进/出场手续费逐腿披露：每笔平仓记录均须同时拆分展示进场腿与出场腿的手续费、返佣/抵扣及单腿净费。
- 左侧导航极简无小字：仅图标 + 页面名称，无任何数字角标。
- 完整支持中英双语（`zh-CN` / `en`）、红盈绿亏 / 绿盈红亏切换、一键隐私打码、曜石暗色 / 深海蓝 / 极简浅色三主题。

---

### Task 1: 仓库骨架、MIT 协议、安全配置与隐私审计门禁

**Files:**
- Create: `LICENSE`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `pyproject.toml`
- Create: `scripts/privacy_audit.py`
- Create: `backend/src/opentradeledger/__init__.py`
- Create: `backend/src/opentradeledger/config.py`
- Test: `backend/tests/test_privacy_and_config.py`

**Interfaces:**
- Produces: `AppConfig`（`host`, `port`, `data_dir`, `demo_mode`）、`UserPreferences`（`locale`, `theme`, `polarity`, `privacy_mode`, `goal_enabled`, `goal_target_usd`, `modules`, `ai`, `notifications`）、`run_privacy_audit(root: Path) -> list[str]`。

- [ ] **Step 1: 编写测试 `backend/tests/test_privacy_and_config.py`**
  覆盖 `UserPreferences` 默认值（`goal_target_usd` 默认 `None` / 未启用、默认无写死带单员与内网 AI URL、`locale="zh-CN"`, `theme="obsidian"`, `polarity="cn"`）以及 `run_privacy_audit` 能拦截真实内网 IP、疑似硬编码密钥与个人私有路径并在干净仓库返回空列表。
- [ ] **Step 2: 运行 pytest 确认红灯**
  Run: `"$HOME/交易系统/策略系统/.venv/bin/python" -m pytest backend/tests/test_privacy_and_config.py -v`
- [ ] **Step 3: 创建基础配置、MIT 协议、`.gitignore`、`.env.example`、`pyproject.toml`、`config.py` 与 `scripts/privacy_audit.py`**
- [ ] **Step 4: 运行 pytest 与隐私扫描脚本确认绿灯**
  Run: `"$HOME/交易系统/策略系统/.venv/bin/python" -m pytest backend/tests/test_privacy_and_config.py -v && "$HOME/交易系统/策略系统/.venv/bin/python" scripts/privacy_audit.py`
- [ ] **Step 5: 提交 Task 1**

---

### Task 2: 安全基座（纯 GET 只读守卫 + Fernet 本地加密凭据仓）

**Files:**
- Create: `backend/src/opentradeledger/security/__init__.py`
- Create: `backend/src/opentradeledger/security/readonly_guard.py`
- Create: `backend/src/opentradeledger/security/vault.py`
- Test: `backend/tests/test_security.py`

**Interfaces:**
- Consumes: `opentradeledger.config`
- Produces:
  - `ReadOnlyViolationError`
  - `assert_readonly_request(method: str, path: str, allowed_prefixes: tuple[str, ...]) -> None`
  - `ExchangeCredentialRecord(venue_id, label, api_key, api_secret, passphrase, extra, enabled)`
  - `CredentialVault(data_dir: Path)`: `list_masked()`, `get_decrypted(venue_id)`, `upsert(record)`, `delete(venue_id)`, `import_from_dotenv(env_path: Path)`

- [ ] **Step 1: 编写测试 `backend/tests/test_security.py`**
  验证非 `GET`（`POST`/`PUT`/`DELETE`）与非白名单路径被 `assert_readonly_request` 拦截；验证 `CredentialVault` 加密落盘后密文不含明文密钥、主密钥文件权限 `0o600`、`list_masked()` 仅返回脱敏掩码、且支持兼容读取 `.env` 现有键名（`GATE_API_KEY`、`BINANCE_API_KEY`、`BITGET_API_KEY`、`MEXC_API_KEY`、`EASICOIN_API_KEY`）。
- [ ] **Step 2: 运行 pytest 确认红灯**
- [ ] **Step 3: 实现 `readonly_guard.py` 与 `vault.py`**
- [ ] **Step 4: 运行 pytest 确认绿灯**
- [ ] **Step 5: 提交 Task 2**

---

### Task 3: 插件化交易所适配器注册表与七大交易所只读适配器（含 OKX v5 & Bybit v5）

**Files:**
- Create: `backend/src/opentradeledger/adapters/__init__.py`
- Create: `backend/src/opentradeledger/adapters/base.py`
- Create: `backend/src/opentradeledger/adapters/registry.py`
- Create: `backend/src/opentradeledger/adapters/binance/client.py`
- Create: `backend/src/opentradeledger/adapters/okx/client.py`
- Create: `backend/src/opentradeledger/adapters/bybit/client.py`
- Create: `backend/src/opentradeledger/adapters/gate/client.py`
- Create: `backend/src/opentradeledger/adapters/bitget/client.py`
- Create: `backend/src/opentradeledger/adapters/mexc/client.py`
- Create: `backend/src/opentradeledger/adapters/easicoin/client.py`
- Create: `backend/src/opentradeledger/adapters/stock_channels.py`
- Test: `backend/tests/test_adapters_registry.py`

**Interfaces:**
- Consumes: `opentradeledger.security.readonly_guard`, `opentradeledger.security.vault`
- Produces:
  - `ExchangeCapabilities(futures, rebates, token_discount, copy_trading, stocks, tokenized_stocks)`
  - `AdapterMeta(venue_id, display_name, color_token, requires_passphrase, capabilities, docs_url)`
  - `BaseExchangeAdapter`（`meta`, `test_connection(cred)`, `create_client(cred)`）
  - `AdapterRegistry`: `register(adapter)`, `get(venue_id)`, `list_all()`

- [ ] **Step 1: 编写测试 `backend/tests/test_adapters_registry.py`**
  验证动态注册自定义新适配器（模拟 `hyperliquid` 插件）后 `AdapterRegistry.list_all()` 自动包含新交易所；验证内置 7 个适配器（`binance`/`okx`/`bybit`/`gate`/`bitget`/`mexc`/`easicoin`）均已注册，OKX v5 与 Bybit v5 签名严格符合官方规范且全部共享 `assert_readonly_request` 拦截非 `GET` 调用。
- [ ] **Step 2: 运行 pytest 确认红灯**
- [ ] **Step 3: 实现 `base.py`、`registry.py`、新增 `okx/client.py` & `bybit/client.py`（纯 GET 签名、余额/持仓/历史仓位/逐笔成交与手续费/实时费率/资金流水/充提），并将既有五家只读客户端与 `stock_channels.py` 去私有化重构迁入对应 `adapters/<venue>/`**
- [ ] **Step 4: 运行 pytest 确认绿灯**
- [ ] **Step 5: 提交 Task 3**

---

### Task 4: 核心领域模型、SQLite 存储、脱敏 Demo 数据集与归一化总账引擎

**Files:**
- Create: `backend/src/opentradeledger/core/__init__.py`
- Create: `backend/src/opentradeledger/core/models.py`
- Create: `backend/src/opentradeledger/core/store.py`
- Create: `backend/src/opentradeledger/core/demo_provider.py`
- Create: `backend/src/opentradeledger/core/accounting_engine.py`
- Create: `backend/src/opentradeledger/core/ledger_service.py`
- Create: `backend/src/opentradeledger/core/sync_manager.py`
- Create: `backend/src/opentradeledger/core/logo_cache.py`
- Test: `backend/tests/test_ledger_and_demo.py`

**Interfaces:**
- Consumes: `opentradeledger.adapters.registry`, `opentradeledger.security.vault`, `opentradeledger.config`
- Produces:
  - `LedgerStore(db_path: Path)`（兼容既有 `gate_manual.db` 表结构 + 新增 `app_preferences` 持久化表）
  - `build_demo_dashboard(range_key, venue_filter, symbol_filter, prefs) -> dict`
  - `LedgerService(store, vault, registry)`: `get_dashboard(range_key, venue, symbol, demo_mode)`、`save_journal(trade_id, tags, note)`、`get_preferences()`、`update_preferences(patch)`
  - 严格输出规范化结构：`exchanges[]`（动态数组）、`trades[]`（每笔强制含 `entry_leg` / `exit_leg` 各自 `gross_fee_usd`/`discount_saved_usd`/`rebate_usd`/`net_fee_usd`）、`fee_matrix[]`、`reconciliation`、`equity_curve`、`calendar_days`、`symbol_attribution`、`extensions.{copy_trading, stocks}`。

- [ ] **Step 1: 编写测试 `backend/tests/test_ledger_and_demo.py`**
  验证 Demo 模式下 `exchanges[]` 动态输出、每笔 `trade` 均包含完整的 `entry_leg` 与 `exit_leg` 独立手续费及 `entry_leg.net_fee_usd + exit_leg.net_fee_usd == trade.net_fee_usd` 恒等式、闭环对账 `residual_usd == 0.0`、`venue` / `range` / `symbol` 筛选联动、复盘笔记与偏好设置持久化，以及实盘精算内核核心场景回归测试（Gate 小数张/返佣、币安分批平仓/BNB 抵扣、Bitget 税务/T+1 返佣、MEXC MX/MXPOINT、易币 70% 返佣）。
- [ ] **Step 2: 运行 pytest 确认红灯**
- [ ] **Step 3: 实现 `models.py`、`store.py`、`demo_provider.py`、`accounting_engine.py`（脱敏无损封装精算核心并彻底移除 `9000` / 私有标识）、`ledger_service.py`、`sync_manager.py`、`logo_cache.py`**
- [ ] **Step 4: 运行 pytest 确认绿灯**
- [ ] **Step 5: 提交 Task 4**

---

### Task 5: 通用 AI 复盘引擎、多通道通知器、可选盯盘雷达与 FastAPI 接口层

**Files:**
- Create: `backend/src/opentradeledger/core/ai_reviewer.py`
- Create: `backend/src/opentradeledger/core/notifier.py`
- Create: `backend/src/opentradeledger/core/copy_watcher.py`
- Create: `backend/src/opentradeledger/api/__init__.py`
- Create: `backend/src/opentradeledger/api/app.py`
- Create: `scripts/run_server.py`
- Test: `backend/tests/test_api_and_integrations.py`

**Interfaces:**
- Consumes: `LedgerService`, `CredentialVault`, `AdapterRegistry`, `SyncManager`
- Produces: `create_app(data_dir: Path, demo_mode: bool = False) -> FastAPI`

- [ ] **Step 1: 编写测试 `backend/tests/test_api_and_integrations.py`**
  使用 FastAPI `TestClient` 验证全部 API 端点：`/api/v1/health` 返回只读声明、`/api/v1/dashboard`（Demo 与空账户自动降级 Demo）、`/api/v1/exchanges` 增删查永不泄漏明文 Key、`/api/v1/settings` 读写语言/极性/主题/模块开关/自定义目标/AI 配置、`/api/v1/journal/{id}` 持久化、`/api/v1/ai-review` 支持本地精算兜底与 OpenAI 兼容 mock、以及 `notifier.py` 的 Webhook / Telegram / Bark 载荷格式。
- [ ] **Step 2: 运行 pytest 确认红灯**
- [ ] **Step 3: 实现 `ai_reviewer.py`（彻底移除写死内网 NAS IP 与跨仓路径）、`notifier.py`、`copy_watcher.py`（默认空目标，不写死任何带单员 ID）、`api/app.py` 与 `scripts/run_server.py`**
- [ ] **Step 4: 运行全量后端 pytest 与隐私审计确认绿灯**
- [ ] **Step 5: 提交 Task 5**

---

### Task 6: 全新现代前端脚手架、主题系统、中英双语 i18n 与核心复用组件

**Files:**
- Create: `web/package.json`
- Create: `web/tsconfig.json`
- Create: `web/vite.config.ts`
- Create: `web/vitest.config.ts`
- Create: `web/index.html`
- Create: `web/src/main.tsx`
- Create: `web/src/styles/theme.css`
- Create: `web/src/lib/types.ts`
- Create: `web/src/lib/format.ts`
- Create: `web/src/lib/api.ts`
- Create: `web/src/i18n/zh-CN.ts`
- Create: `web/src/i18n/en.ts`
- Create: `web/src/i18n/index.tsx`
- Create: `web/src/components/Sidebar.tsx`
- Create: `web/src/components/TopBar.tsx`
- Create: `web/src/components/KpiCard.tsx`
- Create: `web/src/components/VenueBadge.tsx`
- Create: `web/src/components/InstrumentMark.tsx`
- Create: `web/src/components/SimpleMarkdown.tsx`
- Create: `web/src/components/EquityChart.tsx`
- Create: `web/src/components/MonthlyCalendar.tsx`
- Test: `web/src/__tests__/core_components.test.tsx`

**Interfaces:**
- Consumes: `/api/v1/*` 归一化插件契约
- Produces: `I18nProvider` / `useI18n()`、`Sidebar`（零小字单行导航）、`TopBar`、`KpiCard`、`VenueBadge`、`SimpleMarkdown`（纯 React 节点渲染，防 XSS）、`EquityChart`（Recharts 响应式面积折线图）、`MonthlyCalendar`。

- [ ] **Step 1: 初始化 `web/` 依赖（React 19, Vite, TypeScript, Tailwind CSS v4, lucide-react, recharts, vitest, @testing-library/react, jsdom）**
- [ ] **Step 2: 编写测试 `web/src/__tests__/core_components.test.tsx`**
  验证 `SimpleMarkdown` 正确解析 `**粗体**`、`*斜体*`、有序/无序列表且转义 `<script>`；验证 `format.ts` 千分位与符号格式化；验证 `i18n` 中英切换；验证 `Sidebar` 导航项绝不包含任何计数小字。
- [ ] **Step 3: 实现 `theme.css`（曜石暗色/深海蓝/极简浅色 + 红盈绿亏/绿盈红亏 + 隐私打码 blur）、`i18n/`、`lib/` 与全部 `components/`**
- [ ] **Step 4: 运行 `vitest` 与 `tsc --noEmit` 确认绿灯**
- [ ] **Step 5: 提交 Task 6**

---

### Task 7: 落地 6 大开源产品页面与完整前端集成测试

**Files:**
- Create: `web/src/pages/OverviewPage.tsx`
- Create: `web/src/pages/TradesPage.tsx`
- Create: `web/src/pages/AnalyticsPage.tsx`
- Create: `web/src/pages/FeesAiPage.tsx`
- Create: `web/src/pages/ExtensionsPage.tsx`
- Create: `web/src/pages/SettingsPage.tsx`
- Create: `web/src/App.tsx`
- Test: `web/src/__tests__/app_pages.test.tsx`

**Interfaces:**
- Consumes: `web/src/components/*`, `web/src/lib/*`, `web/src/i18n/*`
- Produces: 完整 6 页面 SPA 应用并构建产物至 `web/dist/`。

- [ ] **Step 1: 编写测试 `web/src/__tests__/app_pages.test.tsx`**
  验证：
  1. 总览页动态渲染 `exchanges[]` 分布、支持展开全宽闭环对账抽屉；
  2. 合约交易页详细列表显示 `进 / 出场` 费用，右侧交割台无需切 Tab 同时清晰呈现「进场手续费」与「出场手续费」卡片（先扣/返佣/单腿净费）；
  3. 收益分析页自定义目标开关生效、月历单行抬头与全宽归因表正常渲染；
  4. 成本与复盘页渲染跨平台实测费率对比矩阵表、旧快照提醒与 Markdown 建议；
  5. 扩展账本页跟单返佣全宽 5 列表与股票跨所永续全宽 6 列表零缺失；
  6. 设置与连接页可切换中英双语、红盈绿亏/绿盈红亏、隐私打码、三套主题、模块开关及提交新增只读交易所。
- [ ] **Step 2: 实现全部 6 个页面组件与 `App.tsx`**
- [ ] **Step 3: 运行 `vitest`、`tsc --noEmit` 与 `npm run build` 确认全绿**
- [ ] **Step 4: 提交 Task 7**

---

### Task 8: Docker 容器化、开源文档（中英 README / CONTRIBUTING）、隐私门禁与双模式浏览器实机验收

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `README.md`
- Create: `README.zh-CN.md`
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: 编写 `Dockerfile`、`docker-compose.yml`、`README.md`、`README.zh-CN.md`、`CONTRIBUTING.md`**
- [ ] **Step 2: 运行全链路自动化门禁**
  - `"$HOME/交易系统/策略系统/.venv/bin/python" scripts/privacy_audit.py`
  - `"$HOME/交易系统/策略系统/.venv/bin/python" -m pytest -q backend/tests`
  - `cd web && npm run typecheck && npm run test && npm run build`
- [ ] **Step 3: 实机浏览器双模式视觉验收**
  - 以 Demo 模式临时启动（如端口 `8019` 不冲突现有 `8012`）并用 `browser` subagent 在 `1440×900` 逐页截图核验全部 6 个页面 + 中英切换 + 极性切换 + 隐私打码 + 浅色/暗色主题；
  - 用现有 `data_gate_manual` 只读方式核验实盘数据闭环精度与展示无损（绝不把真实数据复制到仓库受控路径）。
- [ ] **Step 4: 提交 Task 8 并输出最终验收报告（含本机 `8012` 平滑切换指引与 GitHub 推送步骤）**
