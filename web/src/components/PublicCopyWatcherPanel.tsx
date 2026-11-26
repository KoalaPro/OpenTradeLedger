import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Bell,
  Check,
  ChevronDown,
  Copy,
  Info,
  Plus,
  RefreshCw,
} from 'lucide-react'
import { useI18n } from '../i18n'
import { api } from '../lib/api'
import { useCurrency } from '../lib/currency'
import { fmtPrice, fmtSignedPct, pnlTone } from '../lib/format'
import type { CopyWatcherStatusPayload, CopyWatcherTargetItem } from '../lib/types'
import { AnimatedCollapse } from './AnimatedCollapse'
import { InstrumentMark } from './InstrumentMark'

interface PublicCopyWatcherPanelProps {
  onGoConfigureBark?: () => void
}

export function PublicCopyWatcherPanel({ onGoConfigureBark }: PublicCopyWatcherPanelProps) {
  const { locale } = useI18n()
  const cur = useCurrency()
  const isZh = locale !== 'en'

  const [status, setStatus] = useState<CopyWatcherStatusPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [rawInput, setRawInput] = useState('')
  const [newSlotAlert, setNewSlotAlert] = useState(true)
  const [newTradeAlert, setNewTradeAlert] = useState(true)
  const [adding, setAdding] = useState(false)
  const [guideOpen, setGuideOpen] = useState(true)
  const [userToggledGuide, setUserToggledGuide] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [slotIntervalInput, setSlotIntervalInput] = useState('10')
  const [tradeIntervalInput, setTradeIntervalInput] = useState('30')
  const [cooldownInput, setCooldownInput] = useState('180')
  const [savingConfig, setSavingConfig] = useState(false)
  const [expandedOrders, setExpandedOrders] = useState<Record<string, boolean>>({})
  const [testingId, setTestingId] = useState<string>('')
  const [copiedId, setCopiedId] = useState<string>('')
  const [feedback, setFeedback] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const flash = (kind: 'ok' | 'err', text: string) => {
    setFeedback({ kind, text })
    if (kind === 'ok') {
      window.setTimeout(() => {
        setFeedback(prev => (prev?.text === text ? null : prev))
      }, 3200)
    }
  }

  // A read started before a successful edit must not replace its newer switches.
  const readGeneration = useRef(0)
  const [busyTargets, setBusyTargets] = useState<Set<string>>(new Set())
  const markTarget = (id: string, busy: boolean) => setBusyTargets(previous => {
    const next = new Set(previous)
    if (busy) next.add(id)
    else next.delete(id)
    return next
  })

  const fetchStatus = useCallback(async (forceRefresh = false) => {
    const generation = ++readGeneration.current
    if (forceRefresh) setRefreshing(true)
    try {
      const res = await api.getCopyWatcher(forceRefresh)
      if (generation !== readGeneration.current) return
      setStatus(res)
      setSlotIntervalInput(String(res.interval_sec ?? 10))
      setTradeIntervalInput(String(res.trade_poll_sec ?? 30))
      setCooldownInput(String(res.cooldown_sec ?? 180))
      if (!userToggledGuide) {
        setGuideOpen((res.items || []).length === 0)
      }
    } catch (err) {
      if (generation === readGeneration.current) flash('err', err instanceof Error ? err.message : 'Failed to load radar status')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [userToggledGuide])

  useEffect(() => {
    void fetchStatus(false)
    const timer = window.setInterval(() => {
      const generation = ++readGeneration.current
      void api
        .getCopyWatcher(false)
        .then(res => { if (generation === readGeneration.current) setStatus(res) })
        .catch(() => {})
    }, 10000)
    return () => window.clearInterval(timer)
  }, [fetchStatus])

  const hasBark = Boolean(status?.channels?.bark)

  const handleAdd = async (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    if (!rawInput.trim() || adding) return
    const existingIds = new Set((status?.items || []).map(x => x.portfolio_id))
    const inputIds = Array.from(rawInput.matchAll(/(?:^|\D)(\d{15,22})(?!\d)/g), m => m[1])
    const allAlreadyExisted = inputIds.length > 0 && inputIds.every(id => existingIds.has(id))
    setAdding(true)
    setFeedback(null)
    try {
      const res = await api.addCopyWatcherTargets(rawInput.trim(), hasBark && newSlotAlert, hasBark && newTradeAlert)
      ++readGeneration.current
      setStatus(current => {
        if (!current || current === status || current.demo_mode !== res.demo_mode) return res
        const additions = res.items.filter(item => inputIds.includes(item.portfolio_id) && !existingIds.has(item.portfolio_id) &&
          !current.items.some(existing => existing.portfolio_id === item.portfolio_id))
        return { ...current, items: [...current.items, ...additions] }
      })
      setRawInput('')
      if (!userToggledGuide) setGuideOpen(false)
      const slotSec = res.interval_sec ?? status?.interval_sec ?? 10
      const tradeSec = res.trade_poll_sec ?? status?.trade_poll_sec ?? 30
      const historyUnavailable = res.items.some(item => inputIds.includes(item.portfolio_id) && item.trade_stale)
      if (historyUnavailable) {
        flash('err', isZh ? '目标已保存。公开成交数据暂不可用，稍后将自动重试。' : 'Target saved. Public trade history is unavailable and will be retried.')
      } else if (allAlreadyExisted) {
        flash(
          'ok',
          isZh
            ? '盯盘目标已保存：已刷新最新快照，并完整保留你原有的监控、响铃与推送开关设置'
            : 'Watch target saved: refreshed snapshot and preserved your existing alert & monitoring toggles',
        )
      } else {
        flash(
          'ok',
          isZh
            ? `盯盘目标已保存！已接入后台 ${slotSec} 秒空位扫描与 ${tradeSec} 秒持仓推算雷达`
            : `Watch target saved · Active on ${slotSec}s slot & ${tradeSec}s position radar`,
        )
      }
    } catch (err) {
      flash('err', err instanceof Error ? err.message : 'Failed to add lead trader')
    } finally {
      setAdding(false)
    }
  }

  const handlePatch = async (
    item: CopyWatcherTargetItem,
    patch: { enabled?: boolean; slot_alert?: boolean; trade_alert?: boolean },
  ) => {
    markTarget(item.portfolio_id, true)
    try {
      const res = await api.patchCopyWatcherTarget(item.portfolio_id, patch)
      ++readGeneration.current
      const updated = res.items.find(target => target.portfolio_id === item.portfolio_id)
      if (updated) setStatus(current => current && ({
        ...current, items: current.items.map(target => target.portfolio_id === updated.portfolio_id ? updated : target),
      }))
      if (typeof patch.slot_alert === 'boolean') {
        flash(
          'ok',
          isZh
            ? `已${patch.slot_alert ? '开启' : '关闭'}「${item.nickname}」空位秒抢响铃`
            : `${patch.slot_alert ? 'Enabled' : 'Disabled'} slot alarm for ${item.nickname}`,
        )
      } else if (typeof patch.trade_alert === 'boolean') {
        flash(
          'ok',
          isZh
            ? `已${patch.trade_alert ? '开启' : '关闭'}「${item.nickname}」开平仓信号推送`
            : `${patch.trade_alert ? 'Enabled' : 'Disabled'} trade alerts for ${item.nickname}`,
        )
      } else if (typeof patch.enabled === 'boolean') {
        flash(
          'ok',
          isZh
            ? `已${patch.enabled ? '恢复' : '暂停'}「${item.nickname}」后台盯盘`
            : `${patch.enabled ? 'Resumed' : 'Paused'} radar for ${item.nickname}`,
        )
      }
    } catch (err) {
      flash('err', err instanceof Error ? err.message : 'Failed to update target settings')
    } finally {
      markTarget(item.portfolio_id, false)
    }
  }

  const handleDelete = async (portfolioId: string, nickname: string) => {
    markTarget(portfolioId, true)
    try {
      await api.deleteCopyWatcherTarget(portfolioId)
      ++readGeneration.current
      setStatus(current => current && ({ ...current, items: current.items.filter(item => item.portfolio_id !== portfolioId) }))
      flash('ok', isZh ? `已停止盯盘并移除：${nickname}` : `Removed ${nickname}`)
    } catch (err) {
      flash('err', err instanceof Error ? err.message : 'Failed to remove target')
    } finally {
      markTarget(portfolioId, false)
    }
  }

  const handleSaveConfig = async () => {
    if (savingConfig) return
    const slotSec = Number(slotIntervalInput)
    const tradeSec = Number(tradeIntervalInput)
    const coolSec = Number(cooldownInput)
    setSavingConfig(true)
    setFeedback(null)
    try {
      const res = await api.updateCopyWatcherConfig({
        interval_sec: slotSec,
        trade_interval_sec: tradeSec,
        cooldown_sec: coolSec,
      })
      ++readGeneration.current
      setStatus(current => current && ({ ...current, interval_sec: res.interval_sec, trade_poll_sec: res.trade_poll_sec, cooldown_sec: res.cooldown_sec }))
      setSlotIntervalInput(String(res.interval_sec))
      setTradeIntervalInput(String(res.trade_poll_sec))
      setCooldownInput(String(res.cooldown_sec))
      setConfigOpen(false)
      flash(
        'ok',
        isZh
          ? `盯盘频率已生效：名额每 ${res.interval_sec}s 扫描 · 持仓每 ${res.trade_poll_sec}s 推算 · 响铃冷却 ${res.cooldown_sec}s`
          : `Polling updated: Slots ${res.interval_sec}s · Positions ${res.trade_poll_sec}s · Cooldown ${res.cooldown_sec}s`,
      )
    } catch (err) {
      flash('err', err instanceof Error ? err.message : 'Failed to save polling intervals')
    } finally {
      setSavingConfig(false)
    }
  }

  const handleTestRing = async (portfolioId: string) => {
    setTestingId(portfolioId)
    setFeedback(null)
    try {
      const res = await api.testNotify({ portfolio_id: portfolioId })
      const sentStr = (res.sent || []).join(', ')
      flash(
        'ok',
        isZh
          ? `测试提醒已送达（${sentStr}），在手机上点击通知即可验证直跳币安 App`
          : `Test alert sent (${sentStr}) · tap notification on mobile to open Binance App`,
      )
    } catch (err) {
      flash('err', err instanceof Error ? err.message : 'Test push failed')
    } finally {
      setTestingId('')
    }
  }

  const copyLinkSafe = async (id: string, url: string) => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(url)
      } else {
        const ta = document.createElement('textarea')
        ta.value = url
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
      }
      setCopiedId(id)
      window.setTimeout(() => setCopiedId(prev => (prev === id ? '' : prev)), 1800)
    } catch {
      // ignore clipboard restrictions
    }
  }

  const items = status?.items || []

  return (
    <div data-testid="public-watcher-view" className="otl-panel-swap">
      {/* Unconfigured Bark Reminder */}
      {status && !hasBark && (
        <div
          data-testid="watcher-bark-unconfigured-banner"
          className="otl-status-warn mt-4 mx-5 px-4 py-3 rounded-md flex flex-wrap items-center justify-between gap-3 text-[12.5px]"
        >
          <div className="flex items-center gap-2 text-[var(--fg)]">
            <Bell size={14} className="text-[var(--warn)] shrink-0" />
            <span>
              {isZh
                ? '尚未配置 Bark。后台会继续扫描，结果可在本页查看。绑定 Bark 后可开启手机提醒。'
                : 'Bark is not configured. You can monitor traders on this page. Configure Bark to receive slot and trade alerts on your phone.'}
            </span>
          </div>
          {onGoConfigureBark && (
            <button
              type="button"
              data-testid="watcher-go-bark-btn"
              onClick={onGoConfigureBark}
              className="otl-btn otl-btn-primary h-7 px-3"
            >
              {isZh ? '去配置 Bark 推送 →' : 'Configure Bark →'}
            </button>
          )}
        </div>
      )}

      {/* Control Header & Add Trader Chapter */}
      <section className="py-5 px-5 border-b border-[var(--border)] space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div className="space-y-0.5">
            <div className="flex flex-wrap items-baseline gap-2.5">
              <h2 className="t-h2">
                {isZh
                  ? '公开带单盯盘雷达'
                  : 'Public Copy-Trade Radar'}
              </h2>
              {status?.demo_mode && (
                <span className="text-[12px] text-[var(--fg-3)]">
                  · {isZh ? '演示示例（粘贴真实链接添加后自动切为实盘盯盘）' : 'Demo Preview'}
                </span>
              )}
            </div>
            <p className="m-0 text-[12px] text-[var(--fg-3)]">
              {isZh
                ? '盯紧满员热门交易员空余车位（iOS Bark 持续响铃直跳币安 App 秒抢），并根据公开历史委托推算其实时在途持仓'
                : 'Monitor full lead traders for open copy slots (Bark alarm + Binance App deep link) and infer live open positions'}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-4 text-[12px]">
            <button
              type="button"
              data-no-press="true"
              data-testid="watcher-config-toggle-btn"
              onClick={() => setConfigOpen(v => !v)}
              className="inline-flex items-center gap-1 font-medium text-[var(--accent)] hover:underline bg-transparent border-0 p-0 cursor-pointer"
            >
              <span>
                {isZh
                  ? `频率设置 (${status?.interval_sec ?? 10}s / ${status?.trade_poll_sec ?? 30}s)`
                  : `Frequency (${status?.interval_sec ?? 10}s / ${status?.trade_poll_sec ?? 30}s)`}
              </span>
              <ChevronDown
                size={13}
                className={`transition-transform duration-150 ${configOpen ? 'rotate-180' : ''}`}
              />
            </button>

            <button
              type="button"
              data-no-press="true"
              data-testid="watcher-toggle-guide-btn"
              onClick={() => {
                setUserToggledGuide(true)
                setGuideOpen(v => !v)
              }}
              className="inline-flex items-center gap-1 text-[var(--fg-2)] hover:text-[var(--fg)] hover:underline bg-transparent border-0 p-0 cursor-pointer"
            >
              <span>
                {isZh ? '如何获取带单员链接？' : 'How to get trader link?'}
              </span>
              <ChevronDown
                size={13}
                className={`transition-transform duration-150 ${guideOpen ? 'rotate-180' : ''}`}
              />
            </button>

            <button
              type="button"
              data-testid="watcher-refresh-btn"
              disabled={refreshing || loading}
              onClick={() => void fetchStatus(true)}
              className="otl-btn h-7 px-2.5"
            >
              <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
              <span>{isZh ? '立即刷新' : 'Refresh'}</span>
            </button>
          </div>
        </div>

        {/* Inline Customizable Polling Frequency & Cooldown Panel */}
        <AnimatedCollapse open={configOpen}>
          <div
            data-testid="watcher-config-panel"
            className="pt-3 border-t border-[var(--border)] space-y-3 text-[13px]"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[var(--fg-2)]">
                {isZh ? '快捷频率预设（保存后立即生效，无需重启）' : 'Quick Polling Presets (Applies immediately)'}
              </span>
              <div className="flex flex-wrap items-center gap-1.5">
                {[
                  { labelZh: '极速抢位 (5s / 15s)', labelEn: 'Fast (5s / 15s)', s: '5', t: '15', c: '120' },
                  { labelZh: '标准推荐 (10s / 30s)', labelEn: 'Standard (10s / 30s)', s: '10', t: '30', c: '180' },
                  { labelZh: '低频省流 (30s / 60s)', labelEn: 'Relaxed (30s / 60s)', s: '30', t: '60', c: '300' },
                ].map(preset => (
                  <button
                    key={preset.s}
                    type="button"
                    onClick={() => {
                      setSlotIntervalInput(preset.s)
                      setTradeIntervalInput(preset.t)
                      setCooldownInput(preset.c)
                    }}
                    className="otl-btn h-7 px-2.5"
                  >
                    {isZh ? preset.labelZh : preset.labelEn}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 items-end">
              <div className="space-y-1">
                <label className="block text-[13px] text-[var(--fg-3)]">
                  {isZh ? '名额扫描间隔 (3~300 秒)' : 'Slot Scan (3–300s)'}
                </label>
                <input
                  type="number"
                  min={3}
                  max={300}
                  data-testid="watcher-interval-input"
                  value={slotIntervalInput}
                  onChange={e => setSlotIntervalInput(e.target.value)}
                  className="otl-input w-full"
                />
              </div>

              <div className="space-y-1">
                <label className="block text-[13px] text-[var(--fg-3)]">
                  {isZh ? '持仓推算间隔 (10~600 秒)' : 'Position Poll (10–600s)'}
                </label>
                <input
                  type="number"
                  min={10}
                  max={600}
                  data-testid="watcher-trade-interval-input"
                  value={tradeIntervalInput}
                  onChange={e => setTradeIntervalInput(e.target.value)}
                  className="otl-input w-full"
                />
              </div>

              <div className="space-y-1">
                <label className="block text-[13px] text-[var(--fg-3)]">
                  {isZh ? '重复响铃冷却 (30~3600 秒)' : 'Alarm Cooldown (30–3600s)'}
                </label>
                <input
                  type="number"
                  min={30}
                  max={3600}
                  data-testid="watcher-cooldown-input"
                  value={cooldownInput}
                  onChange={e => setCooldownInput(e.target.value)}
                  className="otl-input w-full"
                />
              </div>

              <button
                type="button"
                data-testid="watcher-save-config-btn"
                disabled={savingConfig}
                onClick={() => void handleSaveConfig()}
                className="otl-btn otl-btn-primary h-[34px]"
              >
                {savingConfig ? (isZh ? '保存中…' : 'Saving…') : isZh ? '保存频率' : 'Save Frequency'}
              </button>
            </div>
          </div>
        </AnimatedCollapse>

        {/* Collapsible Beginner Guide */}
        <AnimatedCollapse open={guideOpen}>
          <div
            data-testid="watcher-howto-guide"
            className="pt-3 border-t border-[var(--border)] space-y-2.5 text-[13px]"
          >
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 text-[var(--fg-2)]">
              <div className="space-y-1">
                <div className="font-semibold text-[var(--fg)]">
                  {isZh ? '方法一：从手机币安 App 获取' : 'Method 1: Binance Mobile App'}
                </div>
                <p className="m-0 leading-relaxed">
                  {isZh
                    ? '打开币安 App → 合约 → 跟单广场 → 点进带单员主页右上角分享复制链接，无需删改文字，直接整段粘贴至下方输入框即可。'
                    : 'Open Binance App → Futures → Copy Trading → Share trader profile → Paste the full copied text below.'}
                </p>
              </div>

              <div className="space-y-1">
                <div className="font-semibold text-[var(--fg)]">
                  {isZh ? '方法二：从电脑浏览器获取' : 'Method 2: Desktop Browser'}
                </div>
                <p className="m-0 leading-relaxed">
                  {isZh ? '复制形如 ' : 'Copy a URL like '}
                  <code className="font-mono text-[var(--fg)]">binance.com/.../lead-details/398877...</code>
                  {isZh ? ' 的网址或末尾 15~22 位数字 ID 粘贴添加。' : ' or paste the 15–22 digit portfolio ID.'}
                </p>
              </div>
            </div>

            <div className="inline-flex items-start gap-1.5 text-[var(--fg-3)]">
              <Info size={14} className="shrink-0 mt-0.5" />
              <span>
                {isZh
                  ? '首次添加交易员时自动静默对齐历史水位（不补发旧单），仅对新开/平仓动作及空余名额触发推送。'
                  : 'Historical orders are anchored silently upon adding; alerts only fire on newly opened slots or fresh trades.'}
              </span>
            </div>
          </div>
        </AnimatedCollapse>

        {/* Add Lead Trader Form */}
        <form onSubmit={e => void handleAdd(e)} className="space-y-2.5">
          <div className="flex flex-col sm:flex-row gap-2.5">
            <input
              type="text"
              data-testid="watcher-add-input"
              value={rawInput}
              onChange={e => setRawInput(e.target.value)}
              placeholder={
                isZh
                  ? '直接整段粘贴币安 App 分享文案、带单员网页链接或 15~22 位组合 ID…'
                  : 'Paste Binance share text, lead-details URL, or 15–22 digit portfolio ID…'
              }
              className="otl-input min-h-[44px] sm:min-h-0 sm:flex-1"
            />
            <button
              type="submit"
              data-testid="watcher-add-btn"
              disabled={adding || !rawInput.trim()}
              className="otl-btn otl-btn-primary h-[34px] px-4"
            >
              <Plus size={14} />
              <span>
                {adding
                  ? isZh
                    ? '正在验证并拉取…'
                    : 'Verifying…'
                  : isZh
                    ? '添加盯盘目标'
                    : 'Add Trader'}
              </span>
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-4 text-[13px] text-[var(--fg-2)]">
            <label className="inline-flex items-center gap-1.5 cursor-pointer select-none">
              <input
                type="checkbox"
                disabled={!hasBark}
                checked={hasBark && newSlotAlert}
                onChange={e => setNewSlotAlert(e.target.checked)}
              />
              <span>
                {isZh
                  ? '空出跟单名额时持续响铃（抢车位）'
                  : 'Urgent alarm when copy slot opens'}
              </span>
            </label>
            <label className="inline-flex items-center gap-1.5 cursor-pointer select-none">
              <input
                type="checkbox"
                disabled={!hasBark}
                checked={hasBark && newTradeAlert}
                onChange={e => setNewTradeAlert(e.target.checked)}
              />
              <span>
                {isZh
                  ? '带单员新开仓 / 平仓时推送信号（盯盘跟单）'
                  : 'Chime alert on new open / close trades'}
              </span>
            </label>
          </div>
        </form>

        {feedback && (
          <div
            data-testid="watcher-feedback-banner"
            className={`px-3.5 py-2 rounded-md text-[13px] font-medium ${
              feedback.kind === 'ok' ? 'otl-status-ok' : 'otl-status-err'
            }`}
          >
            {feedback.text}
          </div>
        )}
      </section>

      {/* Watched Lead Traders List */}
      {loading ? (
        <div className="py-12 text-center text-[13px] text-[var(--fg-3)]">
          {isZh ? '正在读取盯盘雷达状态…' : 'Loading radar…'}
        </div>
      ) : items.length === 0 ? (
        <div
          data-testid="watcher-empty-state"
          className="py-10 px-5 text-center text-[13px] text-[var(--fg-3)] border-b border-[var(--border)]"
        >
          {isZh
            ? '当前监控列表为空 · 请按上方方法复制任意币安带单员分享链接粘贴至输入框，即可开启 24 小时空位抢单与持仓追踪。'
            : 'No lead traders watched yet · paste a Binance Copy Trading link above to start 24/7 monitoring.'}
        </div>
      ) : (
        <div className="divide-y divide-[var(--border)]">
          {items.map(item => {
            const showOrders = Boolean(expandedOrders[item.portfolio_id])
            const idTail = item.portfolio_id.slice(-6)
            return (
              <section
                key={item.portfolio_id}
                data-testid={`watcher-card-${item.portfolio_id}`}
                className="py-5 px-5 space-y-3.5"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2.5 min-w-0 text-[12px]">
                    <h3 className="t-h2">{item.nickname}</h3>

                    {!item.enabled ? (
                      <span className="text-[var(--fg-3)]">
                        · {isZh ? '已暂停盯盘' : 'Paused'}
                      </span>
                    ) : item.probe_error ? (
                      <span className="text-[var(--warn)]">
                        · {isZh ? '名额数据待更新' : 'Slot data unavailable'}
                      </span>
                    ) : item.has_open_slot ? (
                      <span
                        data-testid={`slot-open-badge-${item.portfolio_id}`}
                        className="inline-flex items-center gap-1.5 font-semibold txt-pos"
                      >
                        <span className="otl-venue-dot" style={{ background: 'var(--pos)' }} />
                        <span>
                          {isZh
                            ? `空出 ${item.available_slots} 个名额 (${item.current_copy_count} / ${item.max_copy_count})`
                            : `${item.available_slots} Slots Open (${item.current_copy_count} / ${item.max_copy_count})`}
                        </span>
                      </span>
                    ) : item.max_copy_count > 0 ? (
                      <span className="text-[var(--fg-2)]">
                        {isZh
                          ? `满员盯盘中 (${item.current_copy_count} / ${item.max_copy_count})`
                          : `Full · Watching (${item.current_copy_count} / ${item.max_copy_count})`}
                      </span>
                    ) : (
                      <span className="text-[var(--fg-3)]">
                        · {isZh ? '等待首次探测…' : 'Probing…'}
                      </span>
                    )}

                    <span className="text-[var(--fg-3)]">
                      {isZh ? '币安带单' : 'Binance Copy'}
                      {item.min_copy_usd > 0
                        ? isZh
                          ? ` · 起跟 ${item.min_copy_usd.toLocaleString()} U`
                          : ` · Min ${item.min_copy_usd.toLocaleString()} U`
                        : ''}
                      {item.profit_sharing_rate
                        ? isZh
                          ? ` · 分润 ${Number(item.profit_sharing_rate).toFixed(0)}%`
                          : ` · Share ${Number(item.profit_sharing_rate).toFixed(0)}%`
                        : ''}
                      {` · #${idTail}`}
                    </span>

                    <a
                      href={item.lead_url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-medium text-[var(--accent)] hover:underline"
                    >
                      {isZh ? '带单主页 ↗' : 'Profile ↗'}
                    </a>

                    <button
                      type="button"
                      data-testid={`copy-trader-link-${item.portfolio_id}`}
                      onClick={() => void copyLinkSafe(item.portfolio_id, item.lead_url)}
                      className="otl-btn-ghost h-6 px-1.5"
                      title={item.lead_url}
                    >
                      {copiedId === item.portfolio_id ? <Check size={12} /> : <Copy size={12} />}
                      <span>
                        {copiedId === item.portfolio_id
                          ? isZh
                            ? '已复制 ✓'
                            : 'Copied ✓'
                          : isZh
                            ? '复制链接'
                            : 'Copy Link'}
                      </span>
                    </button>
                  </div>

                  {!status?.demo_mode && (
                    <div className="flex flex-wrap items-center gap-3.5 text-[12px]">
                      {!hasBark && (
                        <span className="text-[var(--fg-3)]">{isZh ? '手机提醒待配置' : 'Set up Bark for mobile alerts'}</span>
                      )}
                      <label className="inline-flex items-center gap-1.5 cursor-pointer select-none text-[var(--fg-2)]">
                        <span>{isZh ? '空位响铃' : 'Slot Alarm'}</span>
                        <button
                          type="button"
                          role="switch"
                          aria-label={isZh ? '空位响铃' : 'Slot Alarm'}
                          disabled={!hasBark || busyTargets.has(item.portfolio_id)}
                          aria-checked={hasBark && item.slot_alert}
                          data-testid={`toggle-slot-alert-${item.portfolio_id}`}
                          onClick={() => void handlePatch(item, { slot_alert: !item.slot_alert })}
                          className="otl-switch disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          <span className="otl-switch-knob" />
                        </button>
                      </label>

                      <label className="inline-flex items-center gap-1.5 cursor-pointer select-none text-[var(--fg-2)]">
                        <span>{isZh ? '调仓推送' : 'Trade Chime'}</span>
                        <button
                          type="button"
                          role="switch"
                          aria-label={isZh ? '调仓推送' : 'Trade Chime'}
                          disabled={!hasBark || busyTargets.has(item.portfolio_id)}
                          aria-checked={hasBark && item.trade_alert}
                          data-testid={`toggle-trade-alert-${item.portfolio_id}`}
                          onClick={() => void handlePatch(item, { trade_alert: !item.trade_alert })}
                          className="otl-switch disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          <span className="otl-switch-knob" />
                        </button>
                      </label>

                      <label className="inline-flex items-center gap-1.5 cursor-pointer select-none text-[var(--fg-2)]">
                        <span>{isZh ? '启用' : 'Active'}</span>
                        <button
                          type="button"
                          role="switch"
                          aria-label={isZh ? '启用盯盘' : 'Enable Watcher'}
                          disabled={busyTargets.has(item.portfolio_id)}
                          aria-checked={item.enabled}
                          data-testid={`toggle-enabled-${item.portfolio_id}`}
                          onClick={() => void handlePatch(item, { enabled: !item.enabled })}
                          className="otl-switch"
                        >
                          <span className="otl-switch-knob" />
                        </button>
                      </label>

                      <button
                        type="button"
                        data-testid={`test-ring-${item.portfolio_id}`}
                        disabled={!hasBark || testingId === item.portfolio_id}
                        onClick={() => void handleTestRing(item.portfolio_id)}
                        className="otl-btn"
                      >
                        {testingId === item.portfolio_id
                          ? isZh
                            ? '推送中…'
                            : 'Sending…'
                          : isZh
                            ? '测试响铃'
                            : 'Test Ring'}
                      </button>

                      <button
                        type="button"
                        data-testid={`remove-watcher-${item.portfolio_id}`}
                        disabled={busyTargets.has(item.portfolio_id)}
                        onClick={() => void handleDelete(item.portfolio_id, item.nickname)}
                        className="otl-btn text-[var(--fg-3)] hover:text-[var(--err)]"
                        title={isZh ? '移除该盯盘目标' : 'Remove watched trader'}
                      >
                        {isZh ? '移除' : 'Remove'}
                      </button>
                    </div>
                  )}
                </div>

                {(item.probe_error || item.probe_error_zh) && (
                  <div className="pl-3 border-l-2 border-[var(--err)] text-[13px] text-[var(--err)]">
                    {isZh ? item.probe_error_zh || item.probe_error : item.probe_error}
                    {item.slot_updated_at_ms ? (isZh ? ' · 上次名额数据 ' : ' · Last slot data ') + new Date(item.slot_updated_at_ms).toLocaleString(locale) : ''}
                  </div>
                )}

                {item.trade_stale && (
                  <div className="pl-3 border-l-2 border-[var(--err)] text-[13px] text-[var(--err)]">
                    {isZh ? '公开成交数据暂不可用。' : 'Public trade data unavailable. '}
                    {item.trade_updated_at_ms
                      ? (isZh ? '上次成功读取于 ' : 'Last updated ') + new Date(item.trade_updated_at_ms).toLocaleString(locale)
                      : (isZh ? '尚无可核实的更新时间。' : 'Last update time is unknown.')}
                  </div>
                )}
                {Boolean(item.pending_alert_count) && (
                  <div className="text-[13px] text-[var(--warn)]">
                    {isZh ? `${item.pending_alert_count} 条手机提醒待重试` : `${item.pending_alert_count} mobile alerts awaiting retry`}
                  </div>
                )}

                {/* Inferred Live Open Positions Strip */}
                <div className="pt-3 border-t border-[var(--border)] flex flex-wrap items-center justify-between gap-3 text-[12.5px]">
                  <div className="flex flex-wrap items-center gap-4">
                    <span className="text-[var(--fg-3)]">
                      {isZh ? '推算实时在途持仓' : 'Inferred Live Position'}
                    </span>
                    {item.inferred_positions.length > 0 ? (
                      item.inferred_positions.map(pos => (
                        <span
                          key={`${item.portfolio_id}-${pos.symbol}-${pos.position_side}`}
                          className="inline-flex items-center gap-2 flex-wrap"
                        >
                          <InstrumentMark instrument={pos.symbol} exchange="BINANCE" />
                          <b className="text-[13px] text-[var(--fg)]">{pos.symbol}</b>
                          <span className={`font-semibold ${pos.position_side === 'LONG' ? 'txt-pos' : 'txt-neg'}`}>
                            {isZh ? pos.side_zh : pos.position_side}
                          </span>
                          <span className="text-[13px] text-[var(--fg)] sensitive">{pos.qty}</span>
                          <span className="text-[var(--fg-3)] sensitive">
                            @{fmtPrice(pos.avg_entry_price)} ({cur.money(pos.notional_usd, 0)})
                          </span>
                          {pos.mark_price > 0 && (
                            <b className={`sensitive ${pnlTone(pos.unrealized_pnl_usd)}`}>
                              {cur.signedMoney(pos.unrealized_pnl_usd)} ({fmtSignedPct(pos.unrealized_roi_pct)})
                            </b>
                          )}
                        </span>
                      ))
                    ) : (
                      <span className="inline-flex items-center gap-2 flex-wrap text-[var(--fg-3)]">
                        <span>{isZh ? '暂无可展示的推算持仓' : 'No inferred positions to display'}</span>
                        {item.recent_orders[0] && (
                          <>
                            <span>· {isZh ? '最近动作：' : 'Last: '}</span>
                            <InstrumentMark instrument={item.recent_orders[0].symbol} exchange="BINANCE" />
                            <span>
                              {`${item.recent_orders[0].time_bj} ${item.recent_orders[0].symbol} ${
                                isZh
                                  ? item.recent_orders[0].action_zh
                                  : item.recent_orders[0].action_en || item.recent_orders[0].action_zh
                              } `}
                              <span className="sensitive">
                                {`${item.recent_orders[0].executed_qty} @ ${fmtPrice(item.recent_orders[0].avg_price)}`}
                              </span>
                            </span>
                          </>
                        )}
                      </span>
                    )}
                  </div>

                  {Boolean(item.uncertain_symbols?.length) && (
                    <span className="text-[13px] text-[var(--fg-2)]">
                      {item.uncertain_symbols?.join('、')}
                      {isZh ? ' 的单向成交缺少持仓基线，无法确认净持仓。' : ' one-way fills lack a position baseline. Net exposure is unknown.'}
                    </span>
                  )}

                  {item.recent_orders.length > 0 && (
                    <button
                      type="button"
                      data-no-press="true"
                      onClick={() =>
                        setExpandedOrders(prev => ({
                          ...prev,
                          [item.portfolio_id]: !prev[item.portfolio_id],
                        }))
                      }
                      className="inline-flex items-center gap-1 text-[13px] text-[var(--fg-2)] hover:text-[var(--fg)] hover:underline bg-transparent border-0 p-0 cursor-pointer"
                    >
                      <span>
                        {showOrders
                          ? isZh
                            ? '收起公开成交记录'
                            : 'Hide Recent Orders'
                          : isZh
                            ? `查看最近公开成交 (${item.recent_orders.length} 笔)`
                            : `Recent Public Orders (${item.recent_orders.length})`}
                      </span>
                      <ChevronDown
                        size={13}
                        className={`transition-transform duration-150 ${showOrders ? 'rotate-180' : ''}`}
                      />
                    </button>
                  )}
                </div>

                {/* Expandable Recent Public Orders Table */}
                <AnimatedCollapse open={showOrders && item.recent_orders.length > 0}>
                  <div className="overflow-x-auto border-t border-[var(--border)] mt-1 -mx-5">
                    <table className="otl-table">
                      <thead>
                        <tr>
                          <th>{isZh ? '成交时间 (北京)' : 'Time (UTC+8)'}</th>
                          <th>{isZh ? '合约标的' : 'Symbol'}</th>
                          <th>{isZh ? '开平动作' : 'Action'}</th>
                          <th className="text-right">{isZh ? '成交数量' : 'Qty'}</th>
                          <th className="text-right">{isZh ? '成交均价' : 'Avg Price'}</th>
                          <th className="text-right">{isZh ? '已实现盈亏' : 'Realized PnL'}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {item.recent_orders.map((ord, idx) => (
                          <tr key={`${ord.order_time_ms}-${ord.symbol}-${idx}`}>
                            <td className="whitespace-nowrap text-[var(--fg-2)]">{ord.time_bj}</td>
                            <td className="whitespace-nowrap">
                              <span className="inline-flex items-center gap-2">
                                <InstrumentMark instrument={ord.symbol} exchange="BINANCE" />
                                <b>{ord.symbol}</b>
                                <span className="text-[13px] text-[var(--fg-3)]">
                                  {ord.order_type === 'MARKET'
                                    ? isZh
                                      ? '市价'
                                      : 'Market'
                                    : isZh
                                      ? '限价'
                                      : 'Limit'}
                                </span>
                              </span>
                            </td>
                            <td className="whitespace-nowrap font-medium">
                              {isZh ? ord.action_zh : ord.action_en || ord.action_zh}
                            </td>
                            <td className="text-right whitespace-nowrap sensitive">{ord.executed_qty}</td>
                            <td className="text-right whitespace-nowrap">{fmtPrice(ord.avg_price)}</td>
                            <td
                              className={`text-right font-semibold whitespace-nowrap sensitive ${
                                Math.abs(ord.realized_pnl) > 0.005 ? pnlTone(ord.realized_pnl) : 'text-[var(--fg-3)]'
                              }`}
                            >
                              {Math.abs(ord.realized_pnl) > 0.005 ? cur.signedMoney(ord.realized_pnl) : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </AnimatedCollapse>
              </section>
            )
          })}
        </div>
      )}
    </div>
  )
}
