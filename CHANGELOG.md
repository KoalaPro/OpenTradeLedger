# Changelog / 版本更新日志

All notable changes to **OpenTradeLedger (交易账本)** are documented in this file.

---

## 待发布

- 手机通知仅保留 Bark。未绑定设备时仍可添加和暂停盯盘，页面显示“手机提醒待配置”。配置 Bark 后可开启目标的空位响铃与调仓推送。
- 在后台扫描期间切换提醒或暂停目标，扫描结束后会保留刚保存的选择。重复添加同一目标也会保留原设置。
- 首次启动新版时会移除本地偏好中旧的 Telegram 和 Webhook 配置，保留 Bark 凭据、原有盯盘目标和其他设置。

## [v1.3.6] — 「今日推进」严格当日校验 & 新装 NAS 跨日快照历史归口修正

- **「今日推进」严格限定当日损益**：修正自定义目标进度条中「今日推进」直接读取净值曲线末尾节点（`equity_curve[-1]`）而未校验日期等于北京时间今日的问题；当日无任何盈亏变动时严格返回 `0.00 U`，不再把历史交易日的盈亏误显为今日推进。
- **新装 NAS 历史跟单/打新/代币尾差不再滚入当日**：补全 Gate 每日净值快照（`eod_snapshots`）自动落库，并将首次建立快照前的历史跟单池累计浮盈亏、美股打新认购差额、非 U 空投现值及多平台结算校准尾差归口至各自真实起始日期，避免无开单日被累计历史差额污染。

---

## [v1.3.5] — 合约保证金权益归属修正、未拆分手续费诚实展示、盯盘页签解耦与零成交复盘防误导

- **资产构成精准归属**：修正总净资产构成明细弹窗将挂单冻结/占用保证金误计入「现货/理财与空投尾仓」的问题，7 大平台统一按真实合约账户总权益（含冻结及在途保证金）核算合约份额。
- **未拆分总手续费诚实标注**：当原始平仓账单仅提供总手续费而无逐笔进/出场成交拆分时，双腿卡片与列表明确显示「分腿费用未知」并保留已知总手续费，不再误标为 `0.000 U` 或「免佣」。
- **盯盘页签显隐与后台雷达解耦**：在设置页关闭「显示公开盯盘扩展模块」仅隐藏侧边栏页签，不再静默中断后台空位响铃与调仓扫描。
- **零成交周期复盘提示修正**：当所选统计区间为 0 笔平仓时，直接提示「本周期无成交，暂无可分析样本」，不再输出 `0.0%` 胜率、`0 分钟` 持仓或泛化交易纪律建议。

---

## [v1.3.4] — 实盘切换状态反馈优化 & 全局悬浮提示视觉打磨

- **演示模式开关状态准确反馈**：优化首次安装尚未接入 API 时关闭「演示数据模式」开关的状态保持与引导文案，避免开关在空账本状态下视觉回弹。
- **悬浮提示可读性增强**：提升右下角全局浮动提示框在深色主题下的毛玻璃遮罩不透明度，避免与底层文字或开关重叠。

---

## [v1.3.3] — 跨品牌 NAS 网页内一键在线升级 & 全平台零终端部署

- **网页内「一键在线升级」（免终端 · 免进 NAS 后台）**：在「设置与连接 → 版本与更新」检测到新版本时，直接点击「一键在线升级」即可自动下载官方轻量更新包、完成 SHA-256 完整性校验并在 2～3 秒内平滑热重启生效。兼容极空间、绿联、飞牛 fnOS、群晖、威联通及本地 Docker，无需挂载高危 `docker.sock`，NAS 重启后版本与全部数据持久保留。
- **国内网络自动加速回退**：新版本检测支持自动重试，在线升级包支持 GitHub 直连与国内镜像自动回退（全程强制验证官方 Release 权威 SHA-256 哈希）。
- **零 `.env` 实盘平滑切换与锚点直达**：首次接入交易所 API Key、添加盯盘目标或关闭演示开关即自动持久化切换至实盘模式；点击侧边栏新版本徽标或「去配置 Bark 推送」自动平滑滚动定位至对应配置卡片。

---

## [v1.3.2] — 公开带单盯盘自定义扫描秒数与响铃冷却

- **自定义盯盘轮询频率（保存即刻生效）**：「盯盘与专账 → 公开盯盘」新增「频率设置」面板，支持一键切换「极速抢位 (5s/15s)」「标准推荐 (10s/30s)」「低频省流 (30s/60s)」三档预设，也可自由设置名额扫描间隔（`3～300 秒`）、持仓推算间隔（`10～600 秒`）与重复响铃冷却时间（`30～3600 秒`）。

---

## [v1.3.1] — 内置公开带单车位秒抢雷达 & Bark 新手防呆引导

- **内置免密钥公开带单盯盘引擎**：支持监控热门满员带单员空余跟单名额（空位触发 iOS Bark 持续闹铃 + 点击通知直跳币安 App 秒抢）及公开成交流实时持仓推算。
- **双重新手三步获取教程 & Bark 链接智能拆解**：内置手机 App / 网页获取带单员链接图文步骤；Bark 配置支持直接整条粘贴示例链接自动拆解服务器与 Key，点击「测试推送」自动保存生效；新增可选局域网代理配置。

---

## [v1.3.0] — 7 Independent Exchange Modules, Keep-History Unbind & 120fps Motion Engine

## What's New in v1.3.0 / 本次升级重点

### 1. Full 7-Exchange Independent Architecture / 7 平台全独立对等架构与物理隔离分区
- **100% Decoupled Per-Venue Packages**: Every built-in exchange — **Binance**, **OKX (v5)**, **Bybit (v5)**, **Gate**, **Bitget (UTA)**, **MEXC**, and **EasiCoin** — now owns its own isolated `client.py` + `sync.py` + `accounting.py` module with zero cross-venue imports (enforced by AST tests) and zero hardcoded venue `if/elif` branches in `SyncManager` or `LedgerService`.
- **Venue-Partitioned SQLite Storage (`otl_venue_kv` & `otl_venue_records`)**: Automatic zero-loss migration of legacy cached tables into strict `venue_id`-scoped partitions; single-venue filtering and reconciliation streams (`0.00` closed-loop residual per exchange) never bleed across exchanges.
- **OKX v5 & Bybit v5 Actuarial Parity**: Multi-page cursor pagination (`bills-archive`, `asset/bills`, C2C/internal transfers, copy-trading subpositions, funding settlement, live fee rebates, and MNT token fee discounts).

### 2. Keep-History API Unbind & Archive Management / 解绑密钥保留本地历史记录与封存管理
- **Two-Option Remove Modal (Safe Default)**: Removing an exchange in Settings now lets you choose between **Unbind API Key & Keep Local History (Recommended)** and **Delete API Key & Purge All Local Data**.
- **Honest `Archived` (`已封存`) State**: Unbound exchanges with retained history stop all background API polling while keeping historical trades, net principal, and `0.00` closed-loop reconciliation intact, with one-click **Re-bind API** or **Purge Archived Data** available anytime.

### 3. Atomic Credential Updates & Period-Scoped AI Reviews / 原子化密钥更新回滚与按周期隔离复盘
- **Atomic Candidate Sync & Rollback**: Adding or updating an API key verifies and syncs in memory before committing to `vault.enc`; if historical backfill times out mid-way, SQLite partition writes roll back automatically and the previous API key remains untouched.
- **Period-Isolated Reviews (`all` / `30d` / `7d` / `YYYY-MM`)**: AI & local actuarial reviews are stored and retrieved per period with explicit scope badges (`统计周期：近 7 天 · N 笔`) and data-driven staleness reasons instead of generic 2-hour timers.
- **Pre-Generation Choice When AI Is Unconfigured**: Explicitly displays **AI Not Configured (`尚未配置 AI 模型`)** and prompts before generating so users can choose to configure an LLM or run built-in local actuarial rules.

### 4. 120fps Motion Engine & Security Hardening / 120fps 丝滑微动效与安全加固
- **Zero-CLS Layout & Body Portals**: Two-way `AnimatedCollapse` height transitions, `document.body` portaled floating modals/toasts/drawers, pinned TopBar utility controls, 3-step Add-Exchange handshake ceremony, and per-trade unsaved journal draft retention.
- **Encrypted Preferences & Hardened Guards**: Fernet encryption at rest for AI API keys, Telegram Bot Tokens, and Bark Push Keys in SQLite (`0600` file permissions), strict URL/path validation, and full `zh-CN` / `en` localization polish.

### NAS Upgrade / NAS 一键无损升级
```bash
docker compose pull && docker compose up -d
```

---

## [v1.2.0] — In-App Version Update Alerts & Multi-Arch GHCR Docker Images

### 1. In-App Zero-Telemetry Version Update Detection / 看板内新版本自动提醒
- Added read-only GitHub Release checker (`/api/v1/version-check`) with a 6-hour SQLite cache and zero telemetry upload.
- Sidebar **Settings (设置)** displays a subtle `vX.Y.Z` badge whenever a newer release is published.
- New **Version & Updates (版本与更新)** card in Settings with manual **Check Now (检查更新)**, auto-check toggle switch, release notes preview, and one-click LAN-safe copy for the NAS upgrade command.

### 2. Official Multi-Arch GHCR Docker Images (`linux/amd64` & `linux/arm64`) / 官方双架构预构建镜像
- Automated GitHub Actions workflow publishes `ghcr.io/koalapro/opentradeledger:latest` (and versioned tags) for both x86_64 NAS (Synology, fnOS, UGREEN, QNAP) and ARM64 devices.

---

## [v1.1.0] — NAS Access Lock, API Error Diagnostics & Velvet Floating UI

- **Built-in NAS / LAN Password Lock & Brand Unlock Ceremony / 内置 NAS 本地密码锁与仪式感解锁开场**
- **Honest Exchange Health & Bilingual API Error Diagnostics / 零误报连接状态与 API 错误原因直显**
- **Velvet Frosted Floating Modals & Spotlight Micro-Interactions / 流光毛玻璃独立悬浮小窗与微交互**

---

## [v1.0.0] — OpenTradeLedger (交易账本)

- Initial open-source release featuring strict `GET`-only read-only API guards, local Fernet credential encryption, `0.00 U` closed-loop reconciliation, per-leg entry/exit fee & real-time rebate disclosure, 7-currency FX switching, adaptive idle-account polling, and OpenAI-compatible AI review.
