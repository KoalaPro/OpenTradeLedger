<div align="center">

<img src="web/src/assets/ledger-logo.png" width="76" alt="交易账本 Logo" />

# 交易账本 · OpenTradeLedger

**本地优先、严格只读的多交易所实盘交易账本与精算级核账引擎**

[![CI](https://github.com/KoalaPro/OpenTradeLedger/actions/workflows/ci.yml/badge.svg)](https://github.com/KoalaPro/OpenTradeLedger/actions/workflows/ci.yml)
[![Docker GHCR](https://github.com/KoalaPro/OpenTradeLedger/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/KoalaPro/OpenTradeLedger/pkgs/container/opentradeledger)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](pyproject.toml)
[![React 19 + Vite](https://img.shields.io/badge/前端-React%2019%20%2B%20Tailwind%20v4-38BDF8.svg)](web/package.json)
[![Read-Only API](https://img.shields.io/badge/安全机制-GET--Only%20只读拦截-10B981.svg)](backend/src/opentradeledger/security/readonly_guard.py)

[English README](README.md) · [架构设计文档](docs/specs/2026-11-23-opentradeledger-v1-design.md) · [贡献与新增交易所指南](CONTRIBUTING.md)

</div>

---

## 界面预览

> 以下截图均在内置 **演示模式（Demo Mode）** 下采集，使用 100% 模拟脱敏数据。

### 总览页 — 多交易所统一持仓、资金曲线与盈亏日历（曜石黑）
![交易账本总览曜石黑](docs/screenshots/overview-dark.png)

### 进场 / 出场手续费逐笔独立明示 — 结算瀑布零误差核对
![交易账本逐笔进出场手续费](docs/screenshots/trades-per-leg-fees.png)

### 多主题、中英文、实时英镑（GBP）汇率与总净资产构成抽屉（亮色英文）
![交易账本亮色英文英镑视图](docs/screenshots/overview-light-en-gbp.png)

---

## 为什么做「交易账本」？

市面上的开源资产看板大多只覆盖现货持仓，缺乏合约手续费、真实返佣与资金费精算；而多数交易日志工具停留在手工上传 CSV，既无法自动核对多交易所真实权益，也常常把整笔交易成本糊成一个总数字。

**交易账本（OpenTradeLedger）** 完全运行在你自己的电脑或 NAS 上，**只使用交易所只读 API**，聚焦真实交易者最在意的账务透明度与复盘体验：

- **闭环恒等式核账（`0.00 U` 误差校验）与总净资产构成抽屉**：自动核算 `期初权益 + 净划转 + 已实现盈亏 + 手续费与返佣 + 资金费 + 未实现盈亏 = 当前真实权益`。点击 **总净资产** 平滑展开 4 板块资产结构与分平台净值明细表，点击 **核账卡片** 随时复核每一分钱闭环误差。
- **进场 / 出场手续费独立明示与实时返佣实算**：拒绝模糊的“总手续费”。每笔交易独立拆解 **进场手续费** 与 **出场手续费** 两张成本卡片——明确列出成交模式（挂单 Maker / 市价 Taker / 止损 / 反手）、有效费率（bps）、毛手续费、来自交易所真实返佣流水的实收返佣（绝非套静态比例公式），并单独注明 BNB / MX 等平台币扣前折扣，确保单笔结算瀑布 `0.0000` 精准闭合。
- **总览多品类统一持仓**：将 **合约持仓**、**美股 / 1:1 代币化美股** 与 **自动识别的加密现货**（自动过滤稳定币与 `< $1` 尘埃余额，不与代币化美股重复统计）统一呈现在总览持仓卡片，支持按品类筛选并随时点击展开右侧滑出交易明细抽屉。
- **7 币种实时汇率一键切换**：顶部栏随时在 `USDT`、`USD`、`CNY`、`GBP`、`EUR`、`HKD`、`JPY` 之间无刷新切换全部金额与图表显示，实时拉取公开外汇源（`open.er-api.com` 主源 + `frankfurter.dev` 备源，本地 SQLite 缓存 1 小时）。
- **自适应空仓退避轮询**：活跃账户保持 `20 秒` 高频同步；无仓位且净值 `< 20 U` 的闲置账户自动按 `15分钟 → 30分钟 → 1小时 → 2小时 → 4小时` 退避，大幅节省 API 额度与本机资源，一旦检测到 `≥ 0.05 U` 变动或新挂单立即切回 `20 秒` 实时同步。
- **4 级自动标的图标缓存**：自动识别合约币种（兼容 `1000PEPE` 等倍数前缀）与代币化美股（`NVDAX`、`AAPLX`、`STOCK`、`ON` 等后缀），本地磁盘缓存 30 天并内置确定性 SVG 兜底。
- **7 平台全独立对等架构（零单文件耦合、按平台物理隔离归档）**：开箱内置 **Binance（币安）**、**OKX（v5）**、**Bybit（v5）**、**Gate**、**Bitget（UTA）**、**MEXC**、**EasiCoin** 七套完全独立的只读交易所模块（各自拥有独立的 `client.py` + `sync.py` + `accounting.py` 与 SQLite `venue_id` 独立分区）；单改或清退任一平台绝不干扰其他平台，新增交易所无需改动前端代码或中央聚合器。
- **本地优先安全架构、NAS 密码锁与真实状态诊断**：
  - 底层统一强制经过 `assert_readonly_request()` 拦截器：只放行 `GET` 请求及各交易所只读路径白名单，从代码层面杜绝任何下单、划转或提币请求。
  - 在网页设置页录入的 API Key 经 **Fernet（AES-128-CBC + HMAC-SHA256）** 本地加密保存（密钥文件 `0600` 权限仅存于本地 `data/` 目录），零云端上传、零遥测。
  - **内置 NAS / 局域网本地访问密码与仪式感解锁动效**：首次实盘部署自动弹出欢迎设密引导（支持一键保存本地密码或跳过免密，设置页随时开启/改密/关闭，侧边栏一键锁定）；密码采用 `scrypt` 随机盐哈希存盘 + `HttpOnly` 会话 Cookie（支持记住设备 30 天与防爆破冷却），忘记密码可通过 `--reset-password` 本地安全重置，不伤历史账本。
  - **零误报连接状态与 API 错误原因直显**：仅在接口实测或后台轮询真正成功时显示绿色「已连接」；一旦密钥失效、IP 未加白名单、口令错误或网络超时，立即切换为红色「连接报错」并直接给出可读诊断原因与原始返回。
- **通用 AI 复盘（支持拉取模型列表与喂数透明披露）**：兼容任意 OpenAI 格式大模型接口（OpenAI、DeepSeek、Ollama、OpenRouter 等，自动兼容 `/chat/completions` 与 `/responses`），设置页支持一键 **获取模型列表**（`/v1/models`）直接点选；费率与 AI 页面内置可展开的 **喂给 AI 的数据范围披露**，透明展示实际发送的 8 个脱敏维度（绝不含 API Key、UID 或钱包地址）；未配置模型时自动回退至双语本地精算诊断。
- **现代金融终端级体验**：
  - **三套精致主题**：曜石黑（默认）、深海蓝、纯净亮色，配统一微动效抽屉与面板过渡。
  - **中英文一键切换** & **盈亏配色切换**（「红涨绿跌」↔「绿涨红跌」）。
  - **隐私打码模式**：一键模糊全部金额、盈亏和费率，方便截图分享或投屏复盘。
  - **扩展模块开关 & 公开带单盯盘雷达**：内置免密钥公开带单车位监控（空余名额 iOS Bark 持续响铃 + 点击通知直跳币安 App 秒抢）与实时在途持仓推算，跟单记录、美股模块自由开关，个人净值目标支持就地快捷设置，支持粘贴 Bark 设备链接开启手机推送；未绑定 Bark 时可在页面查看盯盘变化。

---

## 内置只读交易所支持

| 交易所 | 合约账本 | 实时返佣流水 | 平台币抵扣 | 跟单记录 | 现货 / 代币化美股 | 鉴权方式 |
|---|:---:|:---:|:---:|:---:|:---:|---|
| **币安 Binance** | ✓ | ✓ | ✓ (BNB) | ✓ | ✓ | API Key + Secret |
| **OKX (v5)** | ✓ | ✓ | ✓ (OKB) | ✓ | ✓ | API Key + Secret + Passphrase |
| **Bybit (v5)** | ✓ | ✓ | ✓ (MNT) | ✓ | ✓ | API Key + Secret |
| **Gate** | ✓ | ✓ | ✓ (GT/点卡) | ✓ | ✓ | API Key + Secret |
| **Bitget (UTA)** | ✓ | ✓ | — | ✓ | ✓ | API Key + Secret + Passphrase |
| **MEXC** | ✓ | ✓ | ✓ (MX) | — | ✓ | API Key + Secret |
| **EasiCoin** | ✓ | ✓ | — | — | ✓ | API Key + Secret |

需要接入 Hyperliquid、Kraken、KuCoin 等更多交易所？请查看 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 快速开始

### 方式一：全品牌 NAS 图形界面零终端部署（极空间 / 绿联 / 飞牛 fnOS / 群晖 / 威联通通用）

无需开启 SSH、无需敲任何命令，也无需创建隐藏 `.env` 文件：

1. **打开 NAS 自带的「Docker」应用 → 选择「Compose / 项目」→ 点击「新增项目」**，直接粘贴下方配置并点击创建（中国大陆 NAS 已默认启用南京大学 GHCR 高速镜像源，秒级拉满；海外环境也可改为 `ghcr.io/koalapro/opentradeledger:latest`）：

```yaml
services:
  opentradeledger:
    image: ghcr.nju.edu.cn/koalapro/opentradeledger:latest
    container_name: opentradeledger
    ports:
      - "8012:8012"   # 如 8012 端口已被占用，直接改左侧数字（如 "8013:8012"）即可
    environment:
      OTL_HOST: "0.0.0.0"
      OTL_PORT: "8012"
      OTL_DATA_DIR: "/app/data"
      OTL_DEMO_MODE: "true"
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

2. **浏览器打开 `http://你的NAS局域网IP:8012`（或自定义端口）**：
   - 首次进入默认展示脱敏**演示模式**，可直接浏览全部功能；
   - 准备接入实盘时，直接在网页 **「设置与连接」** 关闭「演示数据模式」开关（或直接添加首个只读交易所 / 盯盘目标），系统会自动弹出本地访问密码设置并切入实盘核账，**全程无需改配置文件**。
3. **后续有新版本时：网页内直接「一键在线升级」**：
   - 侧边栏「设置与连接」亮起新版本提示后，点进卡片直接点击 **「一键在线升级」** 按钮——自动下载轻量更新包、完成 SHA-256 安全校验并在 2～3 秒内平滑热重启，**无需终端、无需进 NAS 后台、不挂载高危 `docker.sock`，密码、API 密钥与历史账单 100% 自动保留**。

<details>
<summary>进阶：命令行 Docker Compose 一键启动与更新（可选）</summary>

```bash
curl -fsSLO https://raw.githubusercontent.com/KoalaPro/OpenTradeLedger/main/docker-compose.yml
# 中国大陆环境可切换南京大学 GHCR 镜像源：
# sed -i 's|ghcr.io/koalapro/opentradeledger:latest|ghcr.nju.edu.cn/koalapro/opentradeledger:latest|' docker-compose.yml
docker compose up -d
```

命令行更新底层镜像（保留 `./data` 全部数据）：
```bash
docker compose pull && docker compose up -d
```
</details>

### 方式二：本机直接运行（Python 3.11+ & Node.js 20+）

```bash
# 1. 安装后端依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. 构建前端界面
cd web
npm install
npm run build
cd ..

# 3. 演示模式启动（无需任何 API Key）
python scripts/run_server.py --demo --port 8012
```

如需连接本机已有的数据目录（自动兼容现有 SQLite 数据库与本地 `.env`）：

```bash
python scripts/run_server.py --host 127.0.0.1 --port 8012 --data-dir /path/to/your/data
```

---

## 自动化测试与隐私安全检查

在提交代码或发版前，建议执行完整门禁检查（确保无任何密钥、UID、内网地址或本地路径残留）：

```bash
python scripts/privacy_audit.py
pytest -q backend/tests
cd web && npm run typecheck && npm run test && npm run build
```

> **安全提醒**：在交易所创建 API Key 时，请务必仅勾选 **只读权限（Read-Only）**，严格关闭现货交易、合约交易、资金划转和提现权限，并建议绑定可信 IP 白名单。

---

## 开源协议

本项目基于 [MIT License](LICENSE) 开源。
