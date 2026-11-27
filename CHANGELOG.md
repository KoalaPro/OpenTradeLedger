# Changelog / 版本更新日志

All notable changes to **OpenTradeLedger (交易账本)** are documented in this file.

---

## [Unreleased]

- **全 11 家交易所及子账户统一 1:1 美股代币识别与历史死代码瘦身**：
  - 在 `adapters/stock_channels.py` 新增统一兜底提升函数 `promote_spot_stock_tokens_from_wallets`，并接入 `core/ledger_service.py` 逐账号聚合管线（位于多账号盖章 `stamp_bundle_for_account` 之前）：覆盖 KuCoin、BingX、HTX、Hyperliquid 及全部 11 家交易所的独立子账户与资金账户，按原始交易所协议代码识别 `xStocks (*X)`、`Ondo (*ON)`、`*STOCK`、Bitget `rStock` 与币安 `bStock`，自动从现货列表归入「股票与代币账户」且绝不重复统计，全平台维持 `0.00 U` 闭环核账。
  - OKX 与 Bybit 统一切换至 `classify_spot_stock_token`（140 只美股/ETF 词典 + 加密币防误判排除表 + 中文公司名），同步保留平台原生持仓均价与浮盈，移除旧版 45 词重复函数 `is_tokenized_stock_symbol`；补齐 BingX `test_connection` 主/子账户角色自动识别（`/openApi/subAccount/v1/list`），并将 11 家适配器 `tokenized_stocks` 能力标记全部对齐。
  - 彻底清理 `adapters/stock_channels.py` 中 830+ 行已无调用的历史遗留旧聚合函数 `build_multi_exchange_stock_ledger` 与 `EXCHANGE_LABELS_ZH`，功能扩至全平台与子账户的同时净减生产代码 740+ 行。

- Bark 更换设备时先验证投递回执，验证失败保留原设备。个人合约账户按本轮新增的减仓或平仓合并提醒，首次导入和重复同步保持静默。修正币安开仓误报和 BingX 保本减仓漏报。
- 修正分批手续费的方向、币种和逐笔精度。KuCoin 区分张数和币数，易币区分成交返费、资金费和其它结算调整。缺少折算凭据时，交割详情、汇总和复盘标明未知，有交易所原生净额的记录仍显示该金额。
- 合约时间线按实际成交展示费用和挂吃属性，跨日委托在各自日期列示。手机列表首屏可见净盈亏。隐私模式覆盖个人持仓均价，日历使用账户名称，离线汇率标明来源，持仓、归因和逐笔展开支持键盘操作。

- 合约交易按账户、合约和持仓方向展示一段连续持仓。详情时间线列出开仓、加仓、减仓和清仓，同一委托的连续成交可以展开核对。每次减仓展示成交数量、当时的持仓成本、实扣手续费、分摊开仓费和扣费后盈亏。
- 修正 Gate 和 OKX 在先减仓后加仓时的批次成本、Gate 和 Bitget 快速重开时的成交归属、Bitget 和 MEXC 的零盈亏识别，以及 Bybit 多笔建仓时的分组。旧账根据本地成交重新计算，并按账户和成交记录关联已保存的复盘笔记。
- 收益日历和按标的查看的收益曲线按成交日期归集可核实的平仓收益和费用。点击日历可查看各笔持仓对当日收益的贡献。平台的独立结算流水和日终净值快照沿用各自的核算口径。

- **分批止盈全宽分笔台账、单次入场费精算与 Gate 计划止盈识别**：
  - 修正 11 大交易所适配器对同一持仓周期分批止盈/减仓的记账口径：同一次建仓分多批平仓完结统一收拢为 **1 条完整平仓记录**（仅计入 **1 笔总入场手续费**、交易笔数计 `1`），并在 `exit_batches` 中保留按时间排序的各批平仓明细（各批出场费用与价差毛利之和严格守恒于主记录）；修正 Gate 适配器将盈利触发的 `ao-` 计划止盈单误判为 `止损市价` 的问题，按委托盈亏/触发方向准确识别为 `止盈市价 / TP Market`。
  - 重构合约交易页 `7:5` 对称排版与全宽横向时间线：修复左表因长手续费文本与 `6:6` 分栏导致「实收净盈亏」列在 1280px/1440px 下完全被截断的问题；普通单笔平仓移除冗余 `已清仓` 与强塞时间线，右侧交割明细对所有订单常驻直显「进场手续费 | 出场手续费」双栏（明确展示限价挂单/市价吃单/止盈市价、`bps` 与进出场各自返佣比例）；仅真正分批或部分减仓订单在下方横跨 12 列全宽横向展示「持仓时间线」（融合挂/吃单方式、各自返佣比例、逐笔手续费保留成交精度 / 盈亏保留 2 位小数及可展开逐笔成交），消除右栏超长与左侧大片白洞。
  - 修复 Bybit、OKX、KuCoin、EasiCoin 在缺少仓位级分摊字段或在途部分平仓时重复累加整单入场成交手续费的问题，以及 Bitget 分批平仓尾批重复计入整仓挂单名义成交额的问题；自动兼容继承旧版分批子单已保存的复盘标签与笔记，保持全平台 `0.00 U` 闭环核账零误差。

---

## [v1.4.0] — 11 平台只读核账、同交易所多账号（主/子账户识别）、细线账本界面重构与全链路精算加固

- **新增 4 大交易所只读适配器（7 → 11 平台全独立对等架构）**：
  - **Hyperliquid（链上免密钥 `0x` 接入 + 子账户自动发现）**：无需任何私钥或 API Key，仅输入公开 42 位 `0x` 钱包地址即可同步链上永续仓位、链上现货、HLP 金库权益、逐笔成交（区分挂单 Maker 负费率返费与吃单 Taker 手续费）、资金费及充提划转实现 `0.00 U` 闭环核账；填入主钱包地址时由服务端按完整地址比对并一键探测勾选导入名下链上子账户（`subAccounts`）。
  - **KuCoin（库币）**：支持 API Key v2/v3 自动协商签名与口令（Passphrase）鉴权，覆盖合约账户总览、91 天 7 日窗口历史平仓与逐笔成交分腿费率、现货储蓄/币币余额及充提闭环核账。
  - **BingX**：支持永续合约（v2/v3）总权益、在途持仓、成交流水与资金费/返佣账单、现货余额及已入账（`status=6`）充提闭环核账。
  - **火币 HTX**：支持 SignatureVersion=2 鉴权，覆盖 USDT 本位全仓永续合约（90 天 `47h` 窗口切片与 `from_id` 跨仓分页账单、分腿费用/返费）及现货账户闭环核账。
- **全平台原生支持「同一交易所多账号」与「主账户 / 子账户 / UID 自动识别」**：
  - 解耦交易所协议类型（`adapter_id`）与本地账户分区主键（`account_id`）：存量单账号零迁移无损兼容，同平台点击「+ 添加账号」可无限扩展主账户、策略子账户或独立返佣号，各分区独立轮询、独立 `0.00 U` 闭环核账并自动汇总至总净资产。
  - 自动探测远端脱敏 `UID` 并展示 `主账户` / `子账户` / `链上钱包` 身份徽标；内置 `DUPLICATE_ACCOUNT_UID` / `ARCHIVED_ACCOUNT_UID_EXISTS` 判重防资产翻倍与 `ACCOUNT_UID_MISMATCH` 换钥防串账保护；无 `UID` 旧封存分区支持各自凭原账户历史独立重绑，保留旧分区笔记原文与跨账户同号订单笔记隔离。
- **11 所钱包金额数值强校验、深分页完整性与错情精细化披露**：
  - 写入 SQLite 前严格校验币种与有限数值金额，拦截伪正常 `HTTP 200` 缺字段/空串/非数值或仅含部分子字段的响应覆盖已有资产；OKX 与 Bybit 可在下次正常同步时自动修复旧版异常余额快照；全平台保留显式的 `0` 可用保证金。
  - Gate 可选跟单接口返回未开放（`permission not open yet`）时不再误报资产或本金缺失，并将钱包总余额与现货资金流水异常拆分为独立中文提示；Gate U 卡/支付消费与退回准确归入本金出入金；OKX 永续按合约面值与成交金额精确核算张数、分腿费率与返费去重。
  - Bybit 首次平仓回补扩展至近 730 天（按请求时刻预留 15 分钟边界缓冲，中途报错保留已读分页）；解除币安密集增量分页硬上限、支持易币单窗超 50 页（`5,000+` 条）划转翻页与回补完整性校验，并补齐 Hyperliquid 同毫秒满 `2,000` 笔边界补拉与截断提示。
  - 公开带单盯盘分别显示币安 `持仓历史`、`订单历史`、`组合详情` 的 `11012005` 繁忙与 `-1003 / 429` 限流原因，自动短退避重试并保留上次有效推算持仓。
- **细线原生账本视觉重构与全端交互打磨**：
  - 重构为无嵌套 `1px` 细线账本布局与三套高对比主题（曜石暗色、深海蓝、皇家蓝极简浅色），补齐顶栏常驻快捷控件、品牌流光入场幕布与弹簧微动效；全站（含冲突报错框与探测列表）覆盖 `.sensitive` 隐私遮罩，完善复盘笔记边存边敲防丢字、周期/平台切换失败原位重试、首次设密跨标签保留子钱包提示及 `390px` 手机端完整导航。
  - 工程模块化整理：后端路由拆分为 `app.py`、`routes_exchanges.py`、`schemas.py`、`http_helpers.py`，前端设置页拆分为 `components/settings/*` 独立弹窗与分区子组件，前后端测试套件全面按业务领域归档。

## [v1.3.7] — 费率矩阵未知分腿归因、AI 外发状态透明化、Bark 纯净通知、美股与合约分离及设置页模块化

- **美股合约与正股/代币分离 & 首页持仓点击看详情**：将美股与商品永续合约彻底移出「股票与代币账户」模块、统一作为普通衍生品合约核算；首页《当前持仓》支持查看全账户现货与理财尾仓构成，正股/代币与现货行均支持点击打开详情弹窗，关闭扩展模块后不再跳空白页。
- **手机通知收敛为 Bark & 交易所瞬时超时防抖**：精简移除旧版 Telegram 与 Webhook 配置并自动清理残留字段；未绑定 Bark 时仍可正常监控名额与持仓；修复空消息超时被归为 `Unknown API error` 的问题，并对瞬时网络波动与上游 `5xx` 增加连续 3 次失败防抖。
- **未拆分手续费在成本矩阵与 AI 数据中的一致归因**：当平仓账单仅有总手续费而无进/出场逐笔拆分时，平台费率矩阵与发送给 AI 的上下文均准确计入已知总手续费与净手续费，挂/吃单费率列标注「分腿未知 · 综合 X.XX bps」而不再显示 `0.00 bps` 或 `0 U`。
- **AI 请求外发四态透明化 & 零成交复盘自动迁移**：明确区分「0 笔成交本地短路」「纯本地精算」「已外发请求但 5xx/异常回退本地」与「模型正常生成复盘」四类状态；旧版零成交缓存读取时自动迁移，总览页与收益分析页零成交时胜率显示 `—`。
- **盯盘重复添加保留原开关 & 代码库工程模块化**：粘贴已存在的带单员 ID 仅刷新快照而不重置已关闭的提醒开关；拆分设置页独立配置卡片组件、按业务领域整理前后端测试套件并收敛架构文档。

---

## [v1.3.6] — 「今日推进」严格当日校验 & 新装 NAS 跨日快照历史归口

- **「今日推进」严格当日校验**：修正目标进度卡片中的「今日推进」严格匹配北京时间当日净损益，避免无交易日沿用上一个有流水的历史日期变动额。
- **每日净值快照落库与首日历史尾差归口**：补全 Gate 每日净值快照自动落库，并将新装实例首日快照前的历史跟单池、美股打新及各平台无时间戳估值调整归口至真实起始日，保持 `0.00 U` 闭环核账零误差。

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
