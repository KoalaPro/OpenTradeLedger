# Changelog / 版本更新日志

All notable changes to **OpenTradeLedger (交易账本)** are documented in this file.

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
