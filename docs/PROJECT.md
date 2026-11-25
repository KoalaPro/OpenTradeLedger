# OpenTradeLedger（交易账本）项目与架构说明

`OpenTradeLedger` 是本地优先、严格只读的多交易所实盘核账账本与精算复盘系统，支持本机直接运行及跨品牌 NAS（极空间、绿联、飞牛 fnOS、群晖、威联通）Docker 零终端部署。完整安装与使用指南见 [中文 README](../README.zh-CN.md) 与 [English README](../README.md)。

---

## 1. 核心架构边界与安全红线

1. **严格只读（`GET`-Only）网络护栏**：所有交易所适配器统一经由 `security/readonly_guard.py` 发起请求，底层强制拦截任何非只读写操作（下单、撤单、划转、提币）。
2. **本地落盘加密与零遥测**：
   - 交易所 API 密钥保存在 `data/vault.enc`（Fernet 对称加密，权限 `0600`）；AI API Key 与 Bark Push Key 同样在 SQLite 偏好表中加密存储。
   - 局域网 / NAS 访问密码采用 `scrypt` 随机加盐哈希存储于本地，无云端账号依赖、零遥测上报。
3. **闭环恒等式核账（`0.00 U` 误差校验）**：
   - 每个平台分区独立满足：`期初权益 + 净充提划转 + 已实现盈亏 + 手续费与真实返佣 + 资金费 + 未实现盈亏 + 理财/空投/汇率估值项 = 当前真实总净资产`。
   - 任何分平台残差均在「闭环对账」页实时披露，绝不静默吞没差额。

---

## 2. 代码目录结构

```text
OpenTradeLedger/
├── backend/
│   ├── src/opentradeledger/
│   │   ├── adapters/        # 7 大交易所全独立包（各自含 client.py / sync.py / accounting.py）
│   │   ├── api/             # FastAPI 路由层（账本聚合、设置、鉴权、公开盯盘、OTA 热更新）
│   │   ├── core/            # 统一领域模型与枚举定义
│   │   ├── demo/            # 内置 100% 脱敏模拟数据集
│   │   ├── security/        # GET-only 只读拦截器、Fernet 密钥保险箱、scrypt 会话锁
│   │   ├── services/        # SyncManager、LedgerService、AI 复盘、Bark 通知、公开盯盘、OTA 升级
│   │   └── storage/         # SQLite 按 venue_id 物理分区存储与自动迁移
│   └── tests/               # 后端单元测试与隐私/架构 AST 守卫测试
├── web/
│   └── src/
│       ├── components/      # 通用 UI 组件、持仓/现货明细表与 settings/ 独立设置卡片
│       ├── pages/           # 总览、交易流水、收益分析、费率与 AI、盯盘与专账、对账、设置页
│       ├── lib/             # API 客户端、7 币种实时汇率换算、品类与工具函数
│       ├── i18n.tsx         # 简体中文 / English 双语字典
│       └── __tests__/       # Vitest + Testing Library 前端交互测试
├── scripts/
│   ├── run_server.py        # 生产启动入口（支持 /app/data/.ota/ 热更新槽位自动挂载）
│   └── privacy_audit.py     # 提交前隐私与敏感凭证扫描脚本
└── docs/
    ├── PROJECT.md           # 本架构与模块说明文档
    └── screenshots/         # README 演示模式界面截图
```

---

## 3. 核心业务口径与模块约定

### 3.1 七平台全独立对等架构与封存机制
- 内置 **Binance**、**OKX (v5)**、**Bybit (v5)**、**Gate**、**Bitget (UTA)**、**MEXC**、**EasiCoin** 七套完全解耦的适配器包（由 AST 测试强制禁止跨平台互引）。
- SQLite 存储按 `venue_id` 严格隔离（`otl_venue_kv` / `otl_venue_records`）。移除交易所时支持 **「仅解绑 API，保留本地历史记录（已封存）」** 与 **「彻底删除 API 及该分区全部本地数据」** 两种模式，互不影响其他平台。

### 3.2 资产分类与双腿手续费精算口径
- **衍生品合约 vs 正股/代币 vs 全量现货**：
  - 美股永续合约、大宗商品永续与加密永续/交割合约统一归入**合约衍生品**核算，合约账户总权益包含挂单冻结及仓位占用保证金。
  - **股票与代币账户**仅收录真实正股及 1:1 代币化股票现货/打新认购（绝不混入杠杆衍生品合约）；关闭扩展页签仅隐藏导航入口，不影响总净资产与闭环核账真实性。
  - 首页《当前持仓》独立透出 `spot_wallet_balances` 全账户现货、理财及小额尾仓构成，点击现货或股票行可直接查看明细弹窗。
- **进/出场手续费披露**：有逐笔成交拆分的账单独立展示进场腿与出场腿费率（bps）、毛手续费、真实返佣流水及平台币抵扣；原始接口仅提供合并总手续费的记录诚实标注 `分腿费用未知`，同时将已知总手续费准确计入平台费率矩阵（显示综合 bps）与 AI 复盘聚合上下文。

### 3.3 公开带单盯盘、Bark 通知与 AI 外发透明化
- **公开带单盯盘**：免 API Key 监控公开带单员跟单余位与实时成交流推算持仓；重复粘贴已有带单员 ID 仅刷新快照，保留用户既有的监控与提醒开关。
- **Bark 推送与异常防抖**：手机通知统一使用 iOS Bark（支持直接粘贴完整示例链接自动拆解 Server 与 Key）。账户同步网络超时或上游 `5xx` 繁忙采用连续 3 次失败防抖后才触发告警，避免瞬时抖动误报。
- **AI 复盘外发四态溯源**：仅发送聚合统计指标（绝不发送 API Key 或原始订单号），并在界面明确标识 `zero_trades_local`（0 笔成交本地短路未外发）、`local_only`（纯本地精算未外发）、`remote_failed_fallback`（已发请求但远端异常回退本地）与 `remote_llm`（远端模型正常生成）。

复盘正文、发送明细和交易笔记按实际数据来源区分正式与演示。复盘查询同时匹配来源和统计周期。升级前无法确定来源的报告保存在原 SQLite 表，用户重新生成后即可在对应账本查看新报告。公开健康检查只提供服务与版本信息，逐平台调度及权益详情需要有效的登录会话。

公开带单监控保留最近成功取得的成交快照。接口异常或单向模式缺少起始仓位时，页面标出数据状态；调仓通知按目标、成交与方向区分。Bark 投递状态和已成功的空位提醒冷却保存在本地账本中。

### 3.4 跨品牌 NAS 网页内一键在线升级（OTA）
- `services/self_updater.py` 从官方 GitHub Release（含国内镜像自动回退）下载轻量代码更新包，强制校验官方权威 `SHA-256` 哈希后解压至持久化目录 `/app/data/.ota/releases/vX.Y.Z` 并更新 `/app/data/.ota/active.json`。
- `scripts/run_server.py` 启动时优先挂载 `active.json` 指向的版本槽位，无需挂载高危 `docker.sock`，容器重启后版本与全部账本数据 100% 保留。

---

## 4. 本地验证命令

```bash
# 1. 隐私安全扫描
python scripts/privacy_audit.py

# 2. 后端全量测试
PYTHONPATH=backend/src pytest -q backend/tests

# 3. 前端类型检查、单测与生产构建
cd web && npm run typecheck && npm run test && npm run build
```
