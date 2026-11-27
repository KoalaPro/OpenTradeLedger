<div align="center">

<img src="web/src/assets/ledger-logo.png" width="76" alt="OpenTradeLedger Logo" />

# OpenTradeLedger · 交易账本

**A self-hosted, read-only trading ledger for reconciling trades, fees, funding payments, and P&L across crypto exchanges.**

[![CI](https://github.com/KoalaPro/OpenTradeLedger/actions/workflows/ci.yml/badge.svg)](https://github.com/KoalaPro/OpenTradeLedger/actions/workflows/ci.yml)
[![Docker GHCR](https://github.com/KoalaPro/OpenTradeLedger/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/KoalaPro/OpenTradeLedger/pkgs/container/opentradeledger)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](pyproject.toml)
[![React 19 + Vite](https://img.shields.io/badge/Frontend-React%2019%20%2B%20Tailwind%20v4-38BDF8.svg)](web/package.json)
[![Read-only exchange access](https://img.shields.io/badge/Exchange%20access-Read--only-10B981.svg)](backend/src/opentradeledger/security/readonly_guard.py)

[简体中文主文档 (README.md)](README.md) · [Architecture Reference](docs/PROJECT.md) · [Changelog](CHANGELOG.md) · [Contributing Guide](CONTRIBUTING.md)

</div>

---

## Live Demo & Screenshots

> All videos and screenshots below are captured in built-in **Demo Mode** using 100% synthetic data.

<div align="center">

[![OpenTradeLedger Live Demo](docs/screenshots/demo-preview.gif)](docs/demo/opentradeledger-demo.mp4)

**[🎬 Watch / Download Full HD Demo Video (MP4 · 1600×1000)](docs/demo/opentradeledger-demo.mp4)**

</div>

### Overview — Hairline Ledger Layout, Multi-Exchange Holdings & `0.00 U` Closed-Loop Reconciliation (Obsidian Dark)
![OpenTradeLedger Overview Dark](docs/screenshots/overview-dark.png)

### Entry and exit fees
![Trade details showing entry and exit fees, maker or taker rates, and rebates](docs/screenshots/trades-per-leg-fees.png)

### 11 Read-Only Venues — Multi-Account Isolation & Keyless Hyperliquid `0x` Sub-Account Discovery
![OpenTradeLedger 11 Venues Settings](docs/screenshots/settings-11-venues.png)

### Multi-Theme, Bilingual, Live GBP FX & Asset Structure Modal (Minimal Light EN)
![OpenTradeLedger Overview Light EN GBP](docs/screenshots/overview-light-en-gbp.png)

---

## Key Capabilities

- **Closed-Loop Accounting (`0.00 U` Drift Check)**: Automatically reconciles `Opening Equity + Net Transfers + Realized PnL + Fees/Rebates + Funding + Unrealized PnL = Current Equity` per account partition.
- **Entry and exit fees.** Review maker and taker fees, effective rates in basis points, settled rebates, and BNB or MX discounts where the exchange provides them. Partial exits stay under the same position. Expand its timeline to check each fill and its fees.
- **11 Independent Read-Only Exchanges + Same-Venue Multi-Account**: Built-in support for **Binance**, **OKX (v5)**, **Bybit (v5)**, **Gate**, **Bitget (UTA)**, **MEXC**, **Hyperliquid (Keyless `0x` + Sub-Account Auto-Discovery)**, **KuCoin**, **BingX**, **HTX**, and **EasiCoin**, with remote UID deduplication (`DUPLICATE_ACCOUNT_UID`), `Main` / `Sub` role badges, and archive-on-disconnect support.
- **Public Copy-Trading Slot Radar & Bark Push**: Keyless public copy-trading slot monitor and inferred position tracker with customizable intervals and instant iOS Bark alerts.
- **Bark alerts for your own trades.** When enabled, Bark groups newly recorded reductions and closes into one alert per account per sync. Opening trades, additions, the initial history import, and repeat syncs stay silent.
- **Read-only exchange access.** The adapters restrict requests to read-only endpoints. Hyperliquid uses `POST /info` with an explicit list of allowed query types to look up a public wallet address.
- **Local storage and NAS updates.** The app encrypts exchange credentials with Fernet, restricts the vault file to its owner, and uses `scrypt` for the local access password. Browser-based updates verify the package’s SHA-256 checksum.

---

## Quick Start

```bash
curl -fsSLO https://raw.githubusercontent.com/KoalaPro/OpenTradeLedger/main/docker-compose.yml
docker compose up -d
```

Open **http://127.0.0.1:8012** to explore Demo Mode immediately, or see [README.md](README.md) for full NAS GUI deployment instructions.

---

## License

MIT License — see [LICENSE](LICENSE).
