# OpenTradeLedger（交易账本）开源架构与视觉设计规格书（v1.0）

**OpenTradeLedger（中文品牌名：交易账本）** 是一款面向 GitHub 开源的**本地优先（Local-First）、多交易所纯只读 API 直连、逐笔进/出场手续费与返佣动态精算、全账户零误差闭环对账与 AI 复盘系统**。

本项目初始化于独立干净的 Git 仓库（`~/OpenTradeLedger`，MIT License），彻底剥离个人私有历史、个人账户配置与硬编码交易所分支，既支持社区用户零门槛一键体验（内置 Demo 模式 + Docker Compose），也可无损接管个人多交易所实盘只读数据库。

---

## 1. 核心设计原则与硬性红线

1. **严格只读安全守卫（`StrictReadOnlyGuard`）**
   - 所有交易所适配器底层共享统一 HTTP 守卫：在发起网络请求前强制校验 `method == "GET"` 且请求路径命中适配器声明的只读白名单，任何非 `GET` 请求直接抛出 `ReadOnlyViolationError`。
   - 公开无密钥盯盘模块同样强制只读且显式禁止携带任何 API 认证请求头。
2. **零个人隐私与零硬编码红线**
   - 仓库内所有源码、默认配置、单元测试、示例数据与文档中严禁出现：真实 UID、真实 API Key 或掩码、个人实盘盈亏、内网 IP（如 `192.168.*`）、个人跨目录路径、固定回本目标金额、固定带单员 ID、个人专属标签（如内部端口编号）以及写死的交易所数量文案（如「五所」「5 家交易所」「3 平台」）。
   - 内置 `scripts/privacy_audit.py` 自动化隐私门禁脚本，CI 与提交前零告警通过。
3. **本地优先与凭据加密**
   - 所有账本记录、净值快照、复盘笔记与密钥 100% 留在用户本机 `data/`（被 `.gitignore` 严格排除），绝不上传第三方云端。
   - 网页「设置与连接」录入的只读 API Key、AI Key 与通知密钥统一使用 `cryptography.fernet` 加密存储于 `data/vault.enc`（密钥文件 `data/.vault.key` 强制 `chmod 600`），同时兼容读取 `data/.env` 方便老用户平滑迁移。
4. **插件化多交易所扩展（`BaseExchangeAdapter`）**
   - 前端与总账展示层严禁出现 `has_binance` / `gate_equity_usd` 等写死某家交易所的字段，全部改为按 `exchanges: ExchangeSummary[]` 与 `venue_id` 动态渲染。
   - 后端通过 `AdapterRegistry` 注册标准适配器；新增交易所（如 OKX / Bybit / Hyperliquid）只需在 `backend/src/opentradeledger/adapters/<venue>/` 实现标准协议并注册，前端界面与总账核心零代码修改。
5. **产品级开源体验（i18n + 极性切换 + 隐私模式 + Demo 模式）**
   - 完整内置 **简体中文（`zh-CN`）** 与 **English（`en`）** 双语，界面文案彻底产品化（不暴露底层 API 枚举名如 `fct_out`、`pu_rebate`、`subBizType`）。
   - 支持 **红盈绿亏（CN）** 与 **绿盈红亏（Global）** 一键切换。
   - 支持 **一键隐私打码模式（Privacy / Hide Balances）**，用于安全截图与分享。
   - 支持 **曜石暗色（Obsidian，默认）**、**深海蓝（Ocean）**、**极简浅色（Light）** 三套高对比度主题。
   - 未配置任何交易所密钥时支持 **内置脱敏 Demo 演示模式**（随时可一键切换），访客零配置体验全站全部功能。

---

## 2. 仓库与模块目录结构

```text
OpenTradeLedger/
├── LICENSE                                  # MIT License
├── README.md                                # 英文主文档（特性、架构、Docker、Demo、隐私安全）
├── README.zh-CN.md                          # 中文主文档（交易账本）
├── CONTRIBUTING.md                          # 新交易所适配器开发指南
├── Dockerfile                               # 多阶段构建（Node 构建前端 + Python 运行时）
├── docker-compose.yml                       # 一键启动（含 Demo 与持久化卷说明）
├── .env.example                             # 全占位符通用配置模板
├── .gitignore                               # 严格忽略 data/、.env、*.db、*.key、*.enc
├── pyproject.toml                           # Python 包与 pytest/ruff 配置
├── scripts/
│   ├── privacy_audit.py                     # 开源隐私安全扫描门禁
│   └── run_server.py                        # 统一启动入口（--host --port --data-dir --demo）
├── backend/
│   ├── src/opentradeledger/
│   │   ├── __init__.py
│   │   ├── config.py                        # 运行时配置与用户偏好 schema
│   │   ├── security/
│   │   │   ├── readonly_guard.py            # 纯 GET + 白名单强制守卫
│   │   │   └── vault.py                     # Fernet 本地加密凭据仓 + .env 只读导入
│   │   ├── adapters/
│   │   │   ├── base.py                      # ExchangeCapabilities & BaseExchangeAdapter 抽象接口
│   │   │   ├── registry.py                  # AdapterRegistry 动态注册与元信息表
│   │   │   ├── binance/                     # Binance 只读客户端与账本适配器
│   │   │   ├── okx/                         # OKX v5 只读客户端与账本适配器（含 Passphrase）
│   │   │   ├── bybit/                       # Bybit v5 统一账户只读客户端与账本适配器
│   │   │   ├── gate/                        # Gate 只读客户端与账本适配器
│   │   │   ├── bitget/                      # Bitget UTA 只读客户端与账本适配器
│   │   │   ├── mexc/                        # MEXC 只读客户端与账本适配器
│   │   │   └── easicoin/                    # EasiCoin 只读客户端与账本适配器
│   │   ├── core/
│   │   │   ├── models.py                    # 统一插件化领域模型（含强制拆分的 EntryLeg / ExitLeg）
│   │   │   ├── store.py                     # SQLite WAL 存储（含设置/笔记/快照/退避调度）
│   │   │   ├── accounting_legacy.py         # 无损迁移的五所 0.00 U 精算内核（通过适配器归一化输出）
│   │   │   ├── ledger_service.py            # 统一归一化总账服务（将实盘或 Demo 统一输出为插件化 Schema）
│   │   │   ├── sync_manager.py              # 阶梯退避动态同步引擎（活跃 20s / 闲置 15m→4h / 变动瞬醒）
│   │   │   ├── demo_provider.py             # 完整脱敏多交易所演示账本生成器
│   │   │   ├── ai_reviewer.py               # 通用 OpenAI 兼容 AI 复盘引擎 + 本地精算兜底
│   │   │   ├── notifier.py                  # 通用 Webhook / Telegram / Bark 多通道通知器
│   │   │   ├── copy_watcher.py              # 可选带单空位与在途持仓雷达（默认无内置目标）
│   │   │   └── logo_cache.py                # 标的图标缓存服务
│   │   └── api/
│   │       └── app.py                       # FastAPI REST API + 前端静态托管
│   └── tests/                               # 全量脱敏自动化测试套件
└── web/
    ├── package.json                         # React 19 + TS + Vite + Tailwind v4 + lucide-react + recharts + vitest
    ├── index.html
    ├── vite.config.ts
    ├── vitest.config.ts
    └── src/
        ├── main.tsx
        ├── App.tsx                          # 全局状态、偏好持久化、壳层路由
        ├── i18n/                            # zh-CN.ts / en.ts / index.ts
        ├── styles/theme.css                 # Tailwind v4 + 3 主题 + 2 极性 + 隐私打码样式变量
        ├── lib/                             # api.ts / format.ts / types.ts
        ├── components/                      # Sidebar / TopBar / KpiCard / VenueBadge / InstrumentMark / SimpleMarkdown / EquityChart / MonthlyCalendar
        └── pages/                           # OverviewPage / TradesPage / AnalyticsPage / FeesAiPage / ExtensionsPage / SettingsPage
```

---

## 3. 核心 API 与插件化数据契约

后端向前端暴露完全去硬编码的规范化 REST API：

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/v1/health` | `GET` | 返回 `read_only: true`、`trading_enabled: false`、`demo_mode`、`version` |
| `/api/v1/dashboard` | `GET` | 查询参数 `range`、`venue`（默认 `ALL`）、`symbol`；返回统一插件化 `DashboardResponse` |
| `/api/v1/sync` | `POST` | 触发同步（支持 `force_all: true` 穿透低频退避） |
| `/api/v1/journal/{trade_id}` | `PUT` | 保存单笔交易复盘标签与笔记 |
| `/api/v1/settings` | `GET` / `PUT` | 读取/更新用户偏好（语言、涨跌色、主题、隐私默认态、自定义目标金额、扩展模块开关、AI 接口、通知渠道） |
| `/api/v1/exchanges` | `GET` / `POST` / `DELETE` | 列出所有可用适配器与已配置账户；添加/测试/删除只读交易所凭据（加密落盘，响应永不返回明文密钥） |
| `/api/v1/exchanges/{id}/test` | `POST` | 纯 GET 校验只读连通性并返回只读权限状态 |
| `/api/v1/demo` | `POST` | 开启或关闭 Demo 演示模式 |
| `/api/v1/ai-review` | `POST` | 调用用户配置的 OpenAI 兼容接口（或本地精算引擎兜底）生成复盘报告 |
| `/api/v1/extensions/copy-watcher` | `GET` / `PUT` | 查询/配置可选带单员空位监控目标 |
| `/api/v1/extensions/copy-watcher/test-notify` | `POST` | 发送测试通知至用户已配置的 Webhook / Telegram / Bark |

### 关键领域模型（`DashboardResponse`）
- `exchanges: ExchangeSummary[]`：动态数组，每项包含 `id`、`name`、`color_token`、`connected`、`equity_usd`、`net_principal_usd`、`net_pnl_usd`、`available_margin_usd`、`rebate_rate_pct`、`discount_rate_pct`、`capabilities`（`futures` / `copy` / `stocks`）。前端完全遍历该数组，不再写死任何交易所数量或专属字段。
- `trades: ClosedTrade[]`：每笔平仓记录**强制包含完整的 `entry_leg` 与 `exit_leg`**，每腿均明确给出：
  - `mode_label`（挂单 / 吃单 / 止损市价 / 止盈市价）、`fee_bps`、`time_str`、`avg_price`、`notional_usd`
  - `gross_fee_usd`（先扣手续费）
  - `discount_saved_usd` & `discount_asset`（平台币抵扣省额）
  - `rebate_usd` & `rebate_pct` & `rebate_status`（返佣金额、实返比例、状态）
  - `net_fee_usd`（该腿扣除抵扣与返佣后的**单腿实付净手续费**）
- `fee_matrix: VenueFeeStats[]`：按已接入交易所动态汇总实测挂/吃毛费率、净费率、平台币抵扣/赠金、实测返佣与综合减免率，直接驱动「成本与复盘」的对比矩阵表。
- `reconciliation: ReconciliationSummary`：全账户闭环核对（累计净本金、当前总资产、综合净盈亏、残差与逐项归因表）。
- `extensions`: `copy_trading` 与 `stocks` 可选扩展账本数据。

---

## 4. 前端视觉与页面规范（1:1 落地已批准原型）

1. **极简侧栏（`Sidebar.tsx`）**：
   - 顶部简洁品牌图标 + 名称（中文 `交易账本` / 英文 `OpenTradeLedger`）。
   - 导航项全部采用**纯线性 SVG 图标 + 单行名称**，**彻底删除所有计数小字与固定交易所数量**：总览 / 合约交易 / 收益分析 / 成本与复盘 / 扩展账本（当跟单和股票模块均关闭时自动隐藏）/ 设置与连接。
   - 底部只读状态指示点 + 最近同步时间 + 稳定的「同步数据」按钮（仅用户主动点击时置灰，后台静默轮询不闪烁）。
2. **总览（`OverviewPage.tsx`）**：
   - 4 张严格基线对齐的 KPI 卡片（总净资产、全账户净盈亏、合约实收净利、手续费减免与返佣）；
   - 上层等高双栏：左侧 Recharts 渐变面积净值曲线 × 右侧动态平台分布条与明细；
   - 可一键展开的全宽「全账户闭环对账」抽屉（彻底无浅色硬编码、无左侧大留白）；
   - 下方全宽「当前持仓」表格。
3. **合约交易（`TradesPage.tsx`）**：
   - 顶部动态平台筛选（按 `exchanges[]` 生成）+ 标的筛选 + 排序 + 精简/详细切换；
   - 左侧平仓记录表：详细视图的「进 / 出场手续费」列同时清晰展示净付费用与 `进 -X.XX · 出 -Y.YY`；支持键盘 `↑ / ↓` 切换选中订单；
   - 右侧交割凭证台：**无需切换 Tab 即直接展示两张独立卡片——「进场手续费」与「出场手续费」**（方式、bps、成交时间均价、先扣手续费、返佣/抵扣与比例、单腿净费），下方接简洁对账瀑布与通用复盘标签/笔记（无个人硬编码标签）。
4. **收益分析（`AnalyticsPage.tsx`）**：
   - 顶部自定义目标进度条（可在设置页开启/关闭及修改目标金额，无任何硬编码数字）；
   - 上层等高双栏：累计净盈亏曲线（全账户 / 仅合约切换）× 极简抬头月度日历（单行标题 + 下拉选月 + 盈亏统计）；
   - 下层全宽收益归因表（全账户业务归因 / 按标的归因），金额与单位强制 `whitespace-nowrap`。
5. **成本与复盘（`FeesAiPage.tsx`）**：
   - 顶部全宽「跨平台实测费率、平台币抵扣与返佣对比矩阵表」+ 吃单改挂单增厚测算徽章；
   - 下方 AI 交易复盘：旧报告时效提醒条 + 3 张关键行为指标卡 + 安全 `SimpleMarkdown` 渲染的结构化复盘建议 + 一键重新生成。
6. **扩展账本（`ExtensionsPage.tsx`）**：
   - 子 Tab 切换「跟单专账」与「股票与代币账户」（按设置页开关动态显隐）；
   - 跟单专账：4 KPI + 可选带单空位监控条（目标自配）+ 上双栏本金划转/标的拆解 + 下全宽 5 列跟单返佣流水；
   - 股票与代币账户：4 KPI + 动态正股与代币监听状态条（不写死交易所数量）+ 上双栏持股/资金流水（日期 `nowrap`）+ 下全宽 6 列跨平台股票与商品永续表。
7. **设置与连接（`SettingsPage.tsx`）**：
   - 左侧「交易所只读适配器管理」：查看已接入账户、测试只读连通性、弹窗添加/删除任意已注册交易所只读 Key（Fernet 加密）、展示可扩展 Adapter 列表；
   - 右侧「显示偏好、可选模块与集成」：语言（中文/English）、主题（Obsidian/Ocean/Light）、盈亏极性（红盈绿亏/绿盈红亏）、默认隐私打码、自定义目标本金、扩展模块开关、OpenAI 兼容 AI 配置、Webhook/Telegram/Bark 通知配置与 Demo 模式切换。

---

## 5. 验收与开源发布门禁

1. **隐私审计门禁**：`python scripts/privacy_audit.py` 扫描全仓库 **0 处泄露**（无真实 UID、无密钥、无内网 IP、无私人系统名称）。
2. **后端全量测试**：`pytest -q backend/tests` 全通过（覆盖只读守卫、Fernet 加密仓、Adapter 注册表、Demo 模式、动态 API Schema、迁移脱敏后的实盘精算边界用例、AI 复盘与多通道通知）。
3. **前端质量门禁**：`tsc --noEmit` 0 错误、`vitest run` 全通过（覆盖 i18n、极性切换、隐私打码、进/出场手续费逐腿渲染、Settings 交互、SimpleMarkdown 防注入）、`npm run build` 0 警告。
4. **实机双模式视觉验收**：
   - **Demo 模式**（`--demo`）与 **本机实盘数据模式**（挂载现有 `data_gate_manual` 只读核验）分别通过浏览器在 `1440×900` 下巡检全部 6 个页面及中英/极性/主题/隐私切换：0 横向裁切、0 断行劈词、0 暗色穿透、0 Console 报错。
5. **Docker 验证**：`Dockerfile` 与 `docker-compose.yml` 语法及启动流程核验完整，README 中英文文档齐全。
