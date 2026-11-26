# Changelog / 版本更新日志

All notable changes to **OpenTradeLedger (交易账本)** are documented in this file.

---

## [v1.4.0] — 11 平台扩展（Hyperliquid / KuCoin / BingX / HTX）、同交易所多账号与主/子账户识别

- **新增 4 大热门交易所只读适配器（7 → 11 平台全独立架构）**：
  - **Hyperliquid（链上免密钥 `0x` 接入 + 子账户自动发现）**：无需任何私钥或 API Key，仅输入公开 42 位 `0x` 钱包地址即可拉取链上永续仓位、链上现货、逐笔成交（区分挂单 Maker 负费率返费与吃单 Taker 手续费）、资金费及充提划转实现 `0.00 U` 闭环核账；填入主钱包地址时支持一键检测并批量勾选导入名下链上子账户（`subAccounts`）。
  - **KuCoin（库币）**：支持 API Key v2 签名与口令（Passphrase）鉴权，覆盖合约账户总览、历史平仓与逐笔成交分腿费率、现货储蓄/币币余额及充提闭环核账。
  - **BingX**：支持永续合约（v2/v3）总权益、在途持仓、成交流水与资金费/返佣账单及现货余额闭环核账。
  - **火币 HTX**：支持 SignatureVersion=2 鉴权，覆盖 USDT 本位全仓永续合约（账单与逐笔成交分腿费用/返费）及现货账户闭环核账。
- **全平台原生支持「同一交易所多账号」与「主账户 / 子账户 / UID 自动识别」**：
  - 解耦交易所适配器类型（`adapter_id`）与本地账户分区主键（`account_id`）：存量单账号原位保留零迁移，同平台点击「+ 添加账号」即可无限扩展主账户、策略子账户或独立返佣账号，各账号独立轮询、独立 `0.00 U` 闭环核账、独立筛选并自动汇总至全账户总净资产。
  - 自动探测远端脱敏 `UID` 并识别 `主账户` / `子账户` / `链上钱包` 身份徽标（支持 OKX、Bybit、Bitget、HTX、KuCoin、Hyperliquid、币安、Gate、BingX、易币），存量旧账号首次同步或点测试连接时自动回填脱敏 `UID` 与主/子角色；内置 `DUPLICATE_ACCOUNT_UID` 防重复添加（杜绝同一账号两把 Key 导致总净资产翻倍）与 `ACCOUNT_UID_MISMATCH` 换钥防串账保护。
  - 完善多账号细节：支持副账号与新增四平台交易对自动拉取 TradingView 图标、同平台多账号对账瀑布分项唯一键与中英文账户前缀对齐、多账号默认序号标题（如 `Gate #1`）跨页一致展示，以及 Gate U 卡/支付消费与退回（`code=3501 / 3516` 等）准确归入本金出入金而非交易损耗。
  - 全量验收加固：封存分区纳入 `ARCHIVED_ACCOUNT_UID_EXISTS` 冲突拦截并保留自定义账户备注、首绑强制核验有效 `UID`、「+ 添加账号」身份故障阻断重复入账、无 `UID` 旧在用/封存分区按历史指纹去重自愈、已清空旧随机副分区孤儿复盘笔记自动认领找回、交易笔记与 AI 复盘提示词严格按账户分区隔离；11 家适配器统一阻断子接口 `5xx/429/超时` 及 `HTTP 200` 缺字段/错结构畸形载荷覆盖已有资产（仅合法空数组/零值更新归零），并将缺权限/结构异常/分页截断 `warnings` 全链路透出至顶栏「部分同步」徽标与页面横幅；修正 Hyperliquid `spotMeta` 估值、HLP 金库权益与同毫秒边界 `2,001+` 笔及多页回溯，KuCoin `400009` v3 Key 跨 HTTP 401 自动协商与 91 天 7 日窗口历史回溯，BingX 官方 v3 裸 JSON 数组兼容与 `status=6` 入账过滤（排除 `status=1` 未入账），HTX 跨仓 `from_id` 分页与 90 天 `47h` 窗口切片，Bybit 充提状态过滤、30 天时间窗回溯与半年以上稀疏平仓扫描，易币单窗口 `5,000` 条划转分页，MEXC 增量充提同步及币安同毫秒 `fromId`/`orderId` 游标补拉；补齐公开盯盘币安 `11012005`（系统繁忙）自动短退避重试、前端周期/平台切换失败原位重试与防笔记串写、笔记保存途中继续输入防丢字与按交易隔离状态、设密页刷新保留子钱包失败提示与补接自动跳过已接入钱包、设置页常驻解锁、Bark/AI 密钥一键清除、全站（含冲突报错框）`.sensitive` 隐私遮罩、零回撤 `0.00 U`/`JP¥0` 负零消除、`Decimal` `ROUND_HALF_UP` 舍入及 `390px` 手机端完整导航。
  - 代码库结构收敛与领域测试整理：将后端 `api/app.py` 拆分为核心路由装配、`routes_exchanges.py` 多账号管理路由、`schemas.py` 请求模型与 `http_helpers.py` 协议工具；将前端 `SettingsPage.tsx` 拆分为 `ExchangeConnectModal`、`ExchangeDeleteModal`、`PreferencesAndGoalSection` 与 `settingsHelpers` 等高内聚子组件；并将前后端多轮验收测试统一按业务领域归档命名。

## 未发布（早期积累已并入 v1.4.0）

- 易币更新只读密钥时核对签名账户 UID。同账户换钥保留历史，跨账户写入和过期编辑会在保存前提示。旧账本会核对已有成交、订单与划转的归属。
- OKX 永续按合约规格和成交金额核算数量与费率，明细区分手续费支出和成交返费，已计入交易所净收益的同笔返费只计一次。缺少可靠规格时显示张数。
- 锁屏和实账／演示切换同步到同浏览器其他标签，首次显示金额时遵循隐私偏好。连续切换币种和多个盯盘开关时保留最新操作结果。
- 复盘按明确的货币单位换算金额，保留费率和风险倍数。归因明细加载失败可原位重试。修复手机直达盯盘的横向溢出，以及资产弹窗的键盘焦点。

- 重构全站前端视觉与排版体系：移除多层嵌套卡片与零散微标签，统一改为无嵌套 `1px` 细线原生账本布局，收敛全站字号阶梯（`24 / 18 / 15 / 13 / 12px`）并补齐 `1400px` 居中舞台外边距及 KPI 格、表格首尾列与详情弹窗的边缘呼吸留白；重塑曜石暗色、深海蓝与纯白画布皇家蓝极简浅色三套高对比主题，补齐顶栏常驻吸顶、三色主题圆点与涨跌色快捷切换，将设置页调整为上下双章均衡分栏；恢复品牌流光解锁/新开标签页入场幕布与全套弹簧编排微动效，修正负收益曲线底边渐变与合约主列表窄屏列宽，并对齐下划线滑块页签、双腿费用并排对比、细线月度盈亏日历与无分隔符交易对圆标图标。
- 锁屏健康检查收敛账户信息，演示与正式账本分别保存复盘、发送明细及交易笔记。
- 日期明确的 OKX 理财利息与返佣进入期间统计、日历及累计曲线。周期切换忽略过期响应，复盘输入携带所选期间金额。
- OKX 缺少成交分腿时保留总费，并区分实测费率、配置参考与未知。
- 公开带单标记过期或无法推算的持仓，Bark 合并调仓时保留完整数量。临时失败可重试，空位冷却可跨重启。
- 首个真实交易所接入成功后退出演示并进入账本安全设置。修正代理保存反馈，以及首页持仓类别消失后表格变空的问题。

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
