import { useCallback, useEffect, useState } from 'react'
import {
  Bell,
  BookOpen,
  Check,
  ChevronDown,
  Copy,
  ExternalLink,
  Info,
  Monitor,
  Plus,
  Radio,
  RefreshCw,
  SlidersHorizontal,
  Smartphone,
  Trash2,
} from 'lucide-react'
import { useI18n } from '../i18n'
import { api } from '../lib/api'
import { useCurrency } from '../lib/currency'
import { fmtPrice, fmtSignedPct, pnlTone } from '../lib/format'
import type { CopyWatcherStatusPayload, CopyWatcherTargetItem } from '../lib/types'
import { AnimatedCollapse } from './AnimatedCollapse'
import { VenueBadge } from './VenueBadge'

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

  const fetchStatus = useCallback(async (forceRefresh = false) => {
    if (forceRefresh) setRefreshing(true)
    try {
      const res = await api.getCopyWatcher(forceRefresh)
      setStatus(res)
      setSlotIntervalInput(String(res.interval_sec ?? 10))
      setTradeIntervalInput(String(res.trade_poll_sec ?? 30))
      setCooldownInput(String(res.cooldown_sec ?? 180))
      if (!userToggledGuide) {
        setGuideOpen((res.items || []).length === 0)
      }
    } catch (err) {
      flash('err', err instanceof Error ? err.message : 'Failed to load radar status')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [userToggledGuide])

  useEffect(() => {
    void fetchStatus(false)
    const timer = window.setInterval(() => {
      void api
        .getCopyWatcher(false)
        .then(res => setStatus(res))
        .catch(() => {})
    }, 10000)
    return () => window.clearInterval(timer)
  }, [fetchStatus])

  const hasBark = Boolean(status?.channels?.bark)

  const handleAdd = async (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    if (!rawInput.trim() || adding) return
    const existingIds = new Set((status?.items || []).map(x => x.portfolio_id))
    const inputIds = Array.from(rawInput.matchAll(/\b(\d{15,22})\b/g), m => m[1])
    const allAlreadyExisted = inputIds.length > 0 && inputIds.every(id => existingIds.has(id))
    setAdding(true)
    setFeedback(null)
    try {
      const res = await api.addCopyWatcherTargets(rawInput.trim(), hasBark && newSlotAlert, hasBark && newTradeAlert)
      setStatus(res)
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
    try {
      const res = await api.patchCopyWatcherTarget(item.portfolio_id, patch)
      setStatus(res)
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
    }
  }

  const handleDelete = async (portfolioId: string, nickname: string) => {
    try {
      const res = await api.deleteCopyWatcherTarget(portfolioId)
      setStatus(res)
      flash('ok', isZh ? `已停止盯盘并移除：${nickname}` : `Removed ${nickname}`)
    } catch (err) {
      flash('err', err instanceof Error ? err.message : 'Failed to remove target')
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
      setStatus(res)
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
          : `Test alert sent (${sentStr}) — tap notification on mobile to open Binance App`,
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
    <div data-testid="public-watcher-view" className="otl-panel-swap space-y-4">
      {/* Unconfigured Bark/Push Reminder Banner */}
      {status && !hasBark && (
        <div
          data-testid="watcher-bark-unconfigured-banner"
          className="px-4 py-3 rounded-xl flex flex-wrap items-center justify-between gap-3 text-[12.5px]"
          style={{
            background: 'var(--warn-soft)',
            border: '1px solid var(--warn-bd)',
            color: 'var(--fg)',
          }}
        >
          <div className="flex items-center gap-2">
            <Bell size={15} className="text-[var(--warn)] shrink-0" />
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
              className="h-7 px-3 rounded-lg text-[12px] font-semibold cursor-pointer whitespace-nowrap"
              style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
            >
              {isZh ? '去配置 Bark 推送 →' : 'Configure Bark →'}
            </button>
          )}
        </div>
      )}

      {/* Control Header & Add Trader Input Card */}
      <section className="otl-card p-5 space-y-3.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="m-0 text-[15px] font-semibold text-[var(--fg)] flex items-center gap-2">
                <Radio size={15} className="text-emerald-400" />
                <span>
                  {isZh
                    ? '公开带单盯盘雷达（跟单空位秒抢 & 实时持仓推算）'
                    : 'Public Copy-Trade Radar (Slot Alarm & Live Position Inference)'}
                </span>
              </h2>
              <button
                type="button"
                onClick={() => setConfigOpen(v => !v)}
                className="px-2 py-0.5 rounded-md text-[11px] font-medium border border-[var(--border)] bg-[var(--surface-2)] text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer"
                title={isZh ? '点击自定义轮询秒数' : 'Click to customize polling seconds'}
              >
                {isZh
                  ? `免 API Key 只读 · 名额 ${status?.interval_sec ?? 10}s / 持仓 ${status?.trade_poll_sec ?? 30}s`
                  : `Keyless Read-Only · Slots ${status?.interval_sec ?? 10}s / Positions ${status?.trade_poll_sec ?? 30}s`}
              </button>
              {status?.demo_mode && (
                <span className="px-2 py-0.5 rounded-md text-[11px] font-medium border border-[var(--border)] bg-[var(--surface-2)] text-[var(--fg-3)]">
                  {isZh ? '演示示例（粘贴真实链接添加后自动切为实盘盯盘）' : 'Demo Preview'}
                </span>
              )}
            </div>
            <p className="m-0 text-[12px] text-[var(--fg-3)]">
              {isZh
                ? '盯紧满员热门交易员空余车位（iOS Bark 持续响铃 + 点击通知直跳币安 App 秒抢），并根据公开历史委托推算其实时在途持仓。'
                : 'Monitor full lead traders for open copy slots (urgent Bark alarm + deep link to Binance App) and infer live open positions from public order flow.'}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              data-testid="watcher-config-toggle-btn"
              onClick={() => setConfigOpen(v => !v)}
              className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] font-medium text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer"
            >
              <SlidersHorizontal size={12} />
              <span>{isZh ? `频率设置 (${status?.interval_sec ?? 10}s)` : `Frequency (${status?.interval_sec ?? 10}s)`}</span>
            </button>

            <button
              type="button"
              data-testid="watcher-refresh-btn"
              disabled={refreshing || loading}
              onClick={() => void fetchStatus(true)}
              className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] disabled:opacity-50 cursor-pointer"
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
            className="otl-surface-2 rounded-xl p-3.5 space-y-3 text-[12px] border border-[var(--border)]"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-semibold text-[var(--fg)]">
                {isZh ? '自定义监控频率与响铃冷却（保存后立即生效，无需重启）' : 'Custom Polling Intervals & Alarm Cooldown'}
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
                    className="h-6 px-2 rounded-md border border-[var(--border-strong)] bg-[var(--surface)] text-[11px] text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer"
                  >
                    {isZh ? preset.labelZh : preset.labelEn}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-4 gap-2.5 items-end">
              <div className="space-y-1">
                <label className="block text-[11px] text-[var(--fg-2)]">
                  {isZh ? '名额扫描间隔（3~300 秒）' : 'Slot Scan (3–300s)'}
                </label>
                <input
                  type="number"
                  min={3}
                  max={300}
                  data-testid="watcher-interval-input"
                  value={slotIntervalInput}
                  onChange={e => setSlotIntervalInput(e.target.value)}
                  className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] text-[var(--fg)]"
                />
              </div>

              <div className="space-y-1">
                <label className="block text-[11px] text-[var(--fg-2)]">
                  {isZh ? '持仓推算间隔（10~600 秒）' : 'Position Poll (10–600s)'}
                </label>
                <input
                  type="number"
                  min={10}
                  max={600}
                  data-testid="watcher-trade-interval-input"
                  value={tradeIntervalInput}
                  onChange={e => setTradeIntervalInput(e.target.value)}
                  className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] text-[var(--fg)]"
                />
              </div>

              <div className="space-y-1">
                <label className="block text-[11px] text-[var(--fg-2)]">
                  {isZh ? '重复响铃冷却（30~3600 秒）' : 'Alarm Cooldown (30–3600s)'}
                </label>
                <input
                  type="number"
                  min={30}
                  max={3600}
                  data-testid="watcher-cooldown-input"
                  value={cooldownInput}
                  onChange={e => setCooldownInput(e.target.value)}
                  className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] text-[var(--fg)]"
                />
              </div>

              <button
                type="button"
                data-testid="watcher-save-config-btn"
                disabled={savingConfig}
                onClick={() => void handleSaveConfig()}
                className="h-8 px-3 rounded-lg text-[12px] font-semibold cursor-pointer disabled:opacity-50 whitespace-nowrap"
                style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
              >
                {savingConfig ? (isZh ? '保存中…' : 'Saving…') : isZh ? '保存频率' : 'Save Frequency'}
              </button>
            </div>
          </div>
        </AnimatedCollapse>

        {/* Add Lead Trader Form */}
        <form onSubmit={e => void handleAdd(e)} className="space-y-2.5">
          <div className="flex flex-col sm:flex-row gap-2">
            <input
              type="text"
              data-testid="watcher-add-input"
              value={rawInput}
              onChange={e => setRawInput(e.target.value)}
              placeholder={
                isZh
                  ? '直接整段粘贴币安 App 分享文案、带单员网页链接（含 lead-details/数字）或 15~22 位组合 ID…'
                  : 'Paste Binance share text, lead-details URL, or 15–22 digit portfolio ID…'
              }
              className="h-9 flex-1 px-3 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12.5px] text-[var(--fg)]"
            />
            <button
              type="submit"
              data-testid="watcher-add-btn"
              disabled={adding || !rawInput.trim()}
              className="inline-flex items-center justify-center gap-1.5 h-9 px-4 rounded-lg text-[12.5px] font-semibold cursor-pointer disabled:opacity-50 whitespace-nowrap"
              style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
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

          {/* Options + Adjacent Guide Toggle Button (zero visual jump) */}
          <div className="flex flex-wrap items-center justify-between gap-2 text-[12px] text-[var(--fg-2)]">
            <div className="flex flex-wrap items-center gap-4">
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

            <button
              type="button"
              data-testid="watcher-toggle-guide-btn"
              onClick={() => {
                setUserToggledGuide(true)
                setGuideOpen(v => !v)
              }}
              className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] text-[11.5px] font-medium text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer"
            >
              <BookOpen size={12} className="text-[var(--accent)]" />
              <span>
                {guideOpen
                  ? isZh
                    ? '收起获取教程'
                    : 'Hide Guide'
                  : isZh
                    ? '如何获取带单员链接？'
                    : 'How to get trader link?'}
              </span>
              <ChevronDown
                size={12}
                className={`transition-transform duration-200 ${guideOpen ? 'rotate-180' : ''}`}
              />
            </button>
          </div>
        </form>

        {/* Feedback Banner */}
        {feedback && (
          <div
            data-testid="watcher-feedback-banner"
            className={`px-3.5 py-2 rounded-lg text-[12px] font-medium ${
              feedback.kind === 'ok' ? 'otl-status-ok' : 'otl-status-err'
            }`}
          >
            {feedback.text}
          </div>
        )}

        {/* Directly Adjacent Collapsible Beginner Guide */}
        <AnimatedCollapse open={guideOpen}>
          <div
            data-testid="watcher-howto-guide"
            className="otl-surface-2 rounded-xl p-4 space-y-2.5 text-[12px] border border-[var(--border)]"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="font-semibold text-[12.5px] text-[var(--fg)] inline-flex items-center gap-1.5">
                <BookOpen size={14} className="text-[var(--accent)] shrink-0" />
                <span>
                  {isZh
                    ? '新手三步上手：怎么找到想跟单 / 想盯盘的交易员链接？'
                    : 'Quick Guide: How to get a Lead Trader Link or Portfolio ID'}
                </span>
              </div>
              <span className="text-[11px] text-[var(--fg-3)]">
                {isZh
                  ? '可直接整段粘贴 App 分享文字，也支持一次粘贴多条链接/ID'
                  : 'Supports full share text or multiple URLs/IDs'}
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-[var(--fg-2)]">
              <div className="p-3 rounded-lg bg-[var(--surface)] border border-[var(--border)] space-y-1.5">
                <div className="font-semibold text-[var(--fg)] inline-flex items-center gap-1.5">
                  <Smartphone size={13} className="text-[var(--accent)] shrink-0" />
                  <span>{isZh ? '方法一：从手机币安 App 获取（最简单）' : 'Method 1: From Binance Mobile App'}</span>
                </div>
                <ol className="m-0 pl-4 space-y-1 list-decimal leading-relaxed">
                  <li>
                    {isZh
                      ? '打开币安 App → 点击底部「合约」→ 顶部切到「跟单」广场；'
                      : 'Open Binance App → Futures → Copy Trading;'}
                  </li>
                  <li>
                    {isZh
                      ? '点进你想抢车位或盯盘的带单员主页，点右上角「分享图标」→「复制链接」；'
                      : 'Open the trader’s profile, tap Share (top-right) → Copy Link;'}
                  </li>
                  <li>
                    {isZh
                      ? '无需删改任何文字，直接把整段分享内容粘贴到上方输入框，点「添加盯盘目标」即可自动提取！'
                      : 'Paste the entire copied text into the input box above and click Add Trader.'}
                  </li>
                </ol>
              </div>

              <div className="p-3 rounded-lg bg-[var(--surface)] border border-[var(--border)] space-y-1.5">
                <div className="font-semibold text-[var(--fg)] inline-flex items-center gap-1.5">
                  <Monitor size={13} className="text-[var(--accent)] shrink-0" />
                  <span>{isZh ? '方法二：从电脑浏览器网页获取' : 'Method 2: From Desktop Browser'}</span>
                </div>
                <ol className="m-0 pl-4 space-y-1 list-decimal leading-relaxed">
                  <li>
                    {isZh
                      ? '在浏览器打开币安合约跟单详情页，地址栏格式形如：'
                      : 'Open any public Binance lead trader page with URL like:'}
                    <code className="block mt-1 px-2 py-0.5 rounded bg-[var(--surface-2)] text-[11px] font-mono break-all text-[var(--fg)]">
                      https://www.binance.com/zh-CN/copy-trading/lead-details/3988776655443322110
                    </code>
                  </li>
                  <li>
                    {isZh
                      ? '复制整条网址（或只复制末尾那串 15～22 位数字 ID）粘贴至上方即可。'
                      : 'Copy & paste either the full URL or the 15–22 digit number at the end.'}
                  </li>
                </ol>
              </div>
            </div>

            <div className="inline-flex items-start gap-1.5 text-[11.5px] text-[var(--fg-3)]">
              <Info size={13} className="shrink-0 mt-0.5" />
              <span>
                {isZh
                  ? '首次添加交易员时会自动静默对齐过往订单水位（绝不补发旧单打扰手机），仅从添加后的新开/平仓动作及空余名额开始推送。'
                  : 'Historical orders are anchored silently upon adding; alerts only fire on newly opened slots or fresh trades.'}
              </span>
            </div>
          </div>
        </AnimatedCollapse>
      </section>

      {/* Watched Lead Traders List */}
      {loading ? (
        <div className="otl-card py-10 text-center text-[13px] text-[var(--fg-3)]">
          {isZh ? '正在读取盯盘雷达状态…' : 'Loading radar…'}
        </div>
      ) : items.length === 0 ? (
        <div
          data-testid="watcher-empty-state"
          className="otl-card py-7 px-5 text-center text-[12.5px] text-[var(--fg-3)]"
        >
          {isZh
            ? '当前监控列表为空 — 请按上方方法复制任意币安带单员分享链接粘贴至输入框，即可开启 24 小时空位抢单与持仓追踪。'
            : 'No lead traders watched yet — paste a Binance Copy Trading link above to start 24/7 monitoring.'}
        </div>
      ) : (
        <div className="space-y-3.5">
          {items.map(item => {
            const showOrders = Boolean(expandedOrders[item.portfolio_id])
            const idTail = item.portfolio_id.slice(-6)
            return (
              <section
                key={item.portfolio_id}
                data-testid={`watcher-card-${item.portfolio_id}`}
                className="otl-card p-5 space-y-3.5"
                style={
                  item.has_open_slot && item.enabled && !item.probe_error
                    ? { borderColor: 'var(--pos-bd)' }
                    : undefined
                }
              >
                {/* Top Header Row: Trader Identity + Slot Badge + Granular Controls */}
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2.5 min-w-0">
                    <VenueBadge venueId={item.venue || 'binance'} name={isZh ? '币安带单' : 'Binance Copy'} />
                    <span className="text-[15px] font-semibold text-[var(--fg)]">
                      {item.nickname}
                    </span>
                    <span className="text-[11.5px] font-mono text-[var(--fg-3)]">#{idTail}</span>

                    {/* Slot Count Pill */}
                    {!item.enabled ? (
                      <span className="px-2.5 py-0.5 rounded-full text-[11.5px] font-medium border border-[var(--border)] bg-[var(--surface-2)] text-[var(--fg-3)]">
                        {isZh ? '已暂停盯盘' : 'Paused'}
                      </span>
                    ) : item.probe_error ? (
                      <span className="text-[12px] text-[var(--warn)]">{isZh ? '名额数据待更新' : 'Slot data unavailable'}</span>
                    ) : item.has_open_slot ? (
                      <span
                        data-testid={`slot-open-badge-${item.portfolio_id}`}
                        className="px-2.5 py-0.5 rounded-full text-[12px] font-semibold"
                        style={{ background: 'var(--pos-soft)', color: 'var(--pos)', border: '1px solid var(--pos-bd)' }}
                      >
                        {isZh
                          ? `空出 ${item.available_slots} 个名额！(${item.current_copy_count}/${item.max_copy_count})`
                          : `${item.available_slots} Slots Open! (${item.current_copy_count}/${item.max_copy_count})`}
                      </span>
                    ) : item.max_copy_count > 0 ? (
                      <span className="px-2.5 py-0.5 rounded-full text-[12px] font-medium border border-[var(--border)] bg-[var(--surface-2)] text-[var(--fg-2)]">
                        {isZh
                          ? `满员盯盘中 (${item.current_copy_count} / ${item.max_copy_count})`
                          : `Full · Watching (${item.current_copy_count} / ${item.max_copy_count})`}
                      </span>
                    ) : (
                      <span className="px-2.5 py-0.5 rounded-full text-[11.5px] border border-[var(--border)] bg-[var(--surface-2)] text-[var(--fg-3)]">
                        {isZh ? '等待首次探测…' : 'Probing…'}
                      </span>
                    )}

                    {item.min_copy_usd > 0 && (
                      <span className="text-[12px] text-[var(--fg-3)]">
                        {isZh ? `起跟 ${item.min_copy_usd.toFixed(0)} U` : `Min ${item.min_copy_usd.toFixed(0)} U`}
                        {item.profit_sharing_rate
                          ? isZh
                            ? ` · 分润 ${Number(item.profit_sharing_rate).toFixed(0)}%`
                            : ` · Share ${Number(item.profit_sharing_rate).toFixed(0)}%`
                          : ''}
                      </span>
                    )}

                    <a
                      href={item.lead_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-[12px] font-medium text-[var(--accent)] hover:underline"
                    >
                      <span>{isZh ? '带单主页' : 'Profile'}</span>
                      <ExternalLink size={11} />
                    </a>

                    <button
                      type="button"
                      data-testid={`copy-trader-link-${item.portfolio_id}`}
                      onClick={() => void copyLinkSafe(item.portfolio_id, item.lead_url)}
                      className="inline-flex items-center gap-1 h-6 px-2 rounded-md border border-[var(--border)] bg-[var(--surface-2)] text-[11px] text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer"
                      title={item.lead_url}
                    >
                      {copiedId === item.portfolio_id ? <Check size={11} /> : <Copy size={11} />}
                      <span>
                        {copiedId === item.portfolio_id
                          ? isZh
                            ? '已复制链接'
                            : 'Copied'
                          : isZh
                            ? '复制链接'
                            : 'Copy Link'}
                      </span>
                    </button>
                  </div>

                  {/* Per-Target Granular Switches & Actions */}
                  {!status?.demo_mode && (
                    <div className="flex flex-wrap items-center gap-3 text-[12px]">
                      {!hasBark && (
                        <span className="text-[var(--fg-3)]">{isZh ? '手机提醒待配置' : 'Set up Bark for mobile alerts'}</span>
                      )}
                      <label className="inline-flex items-center gap-1.5 cursor-pointer select-none text-[var(--fg-2)]">
                        <span>{isZh ? '空位响铃' : 'Slot Alarm'}</span>
                        <button
                          type="button"
                          role="switch"
                          aria-label={isZh ? '空位响铃' : 'Slot Alarm'}
                          disabled={!hasBark}
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
                          disabled={!hasBark}
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
                        className="inline-flex items-center gap-1 h-7 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[11.5px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] disabled:opacity-50 cursor-pointer"
                      >
                        <Bell size={12} />
                        <span>
                          {testingId === item.portfolio_id
                            ? isZh
                              ? '推送中…'
                              : 'Sending…'
                            : isZh
                              ? '测试响铃'
                              : 'Test Ring'}
                        </span>
                      </button>

                      <button
                        type="button"
                        data-testid={`remove-watcher-${item.portfolio_id}`}
                        onClick={() => void handleDelete(item.portfolio_id, item.nickname)}
                        className="inline-flex items-center gap-1 h-7 px-2 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] text-[11.5px] text-[var(--fg-3)] hover:text-rose-400 cursor-pointer"
                        title={isZh ? '移除该盯盘目标' : 'Remove watched trader'}
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  )}
                </div>

                {/* Per-Target Probe Error Diagnostic */}
                {(item.probe_error || item.probe_error_zh) && (
                  <div className="otl-status-err px-3 py-2 rounded-lg text-[12px]">
                    {isZh ? item.probe_error_zh || item.probe_error : item.probe_error}
                    {item.slot_updated_at_ms ? (isZh ? ' · 上次名额数据 ' : ' · Last slot data ') + new Date(item.slot_updated_at_ms).toLocaleString(locale) : ''}
                  </div>
                )}

                {item.trade_stale && (
                  <div className="otl-status-err px-3 py-2 rounded-lg text-[12px]">
                    {isZh ? '公开成交数据暂不可用。' : 'Public trade data unavailable. '}
                    {item.trade_updated_at_ms
                      ? (isZh ? '上次成功读取于 ' : 'Last updated ') + new Date(item.trade_updated_at_ms).toLocaleString(locale)
                      : (isZh ? '尚无可核实的更新时间。' : 'Last update time is unknown.')}
                  </div>
                )}
                {Boolean(item.pending_alert_count) && (
                  <div className="text-[12px] text-[var(--warn)]">
                    {isZh ? `${item.pending_alert_count} 条手机提醒待重试` : `${item.pending_alert_count} mobile alerts awaiting retry`}
                  </div>
                )}

                {/* Inferred Live Open Positions Strip */}
                <div className="pt-3 border-t border-[var(--border)] flex flex-wrap items-center justify-between gap-2 text-[12px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-[var(--fg-2)]">
                      {isZh ? '推算实时在途持仓：' : 'Inferred Open Positions:'}
                    </span>
                    {item.inferred_positions.length > 0 ? (
                      item.inferred_positions.map(pos => (
                        <span
                          key={`${item.portfolio_id}-${pos.symbol}-${pos.position_side}`}
                          className="inline-flex items-center gap-2 px-2.5 py-1 rounded-lg bg-[var(--surface-2)] border border-[var(--border)] font-mono text-[12px]"
                        >
                          <b className="text-[var(--fg)]">{pos.symbol}</b>
                          <span className={`font-bold ${pos.position_side === 'LONG' ? 'txt-pos' : 'txt-neg'}`}>
                            {isZh ? pos.side_zh : pos.position_side}
                          </span>
                          <span>{pos.qty}</span>
                          <span className="text-[var(--fg-3)]">
                            @{fmtPrice(pos.avg_entry_price)} ({cur.money(pos.notional_usd, 0)})
                          </span>
                          {pos.mark_price > 0 && (
                            <span className={`font-semibold ${pnlTone(pos.unrealized_pnl_usd)}`}>
                              {cur.signedMoney(pos.unrealized_pnl_usd)} ({fmtSignedPct(pos.unrealized_roi_pct)})
                            </span>
                          )}
                        </span>
                      ))
                    ) : (
                      <span className="text-[var(--fg-3)]">
                        {isZh ? '暂无可展示的推算持仓' : 'No inferred positions to display'}
                        {item.recent_orders[0]
                          ? isZh
                            ? ` · 最近动作：${item.recent_orders[0].time_bj} ${item.recent_orders[0].symbol} ${item.recent_orders[0].action_zh} ${item.recent_orders[0].executed_qty} @ ${fmtPrice(item.recent_orders[0].avg_price)}`
                            : ` · Last: ${item.recent_orders[0].time_bj} ${item.recent_orders[0].symbol} ${item.recent_orders[0].action_en || item.recent_orders[0].action_zh}`
                          : ''}
                      </span>
                    )}
                  </div>

                  {Boolean(item.uncertain_symbols?.length) && (
                    <span className="text-[12px] text-[var(--fg-2)]">
                      {item.uncertain_symbols?.join('、')}
                      {isZh ? ' 的单向成交缺少持仓基线，无法确认净持仓。' : ' one-way fills lack a position baseline. Net exposure is unknown.'}
                    </span>
                  )}

                  {item.recent_orders.length > 0 && (
                    <button
                      type="button"
                      onClick={() =>
                        setExpandedOrders(prev => ({
                          ...prev,
                          [item.portfolio_id]: !prev[item.portfolio_id],
                        }))
                      }
                      className="inline-flex items-center gap-1 text-[12px] font-medium text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer"
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
                        className={`transition-transform duration-200 ${showOrders ? 'rotate-180' : ''}`}
                      />
                    </button>
                  )}
                </div>

                {/* Expandable Recent Public Orders Table */}
                <AnimatedCollapse open={showOrders && item.recent_orders.length > 0}>
                  <div className="overflow-x-auto rounded-lg border border-[var(--border)] mt-1">
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
                            <td className="font-mono whitespace-nowrap text-[var(--fg-2)]">{ord.time_bj}</td>
                            <td className="whitespace-nowrap">
                              <b>{ord.symbol}</b>
                              <span className="ml-1.5 text-[11px] text-[var(--fg-3)]">
                                {ord.order_type === 'MARKET'
                                  ? isZh
                                    ? '市价'
                                    : 'Market'
                                  : isZh
                                    ? '限价'
                                    : 'Limit'}
                              </span>
                            </td>
                            <td className="whitespace-nowrap font-medium">
                              {isZh ? ord.action_zh : ord.action_en || ord.action_zh}
                            </td>
                            <td className="text-right font-mono whitespace-nowrap">{ord.executed_qty}</td>
                            <td className="text-right font-mono whitespace-nowrap">{fmtPrice(ord.avg_price)}</td>
                            <td
                              className={`text-right font-mono font-semibold whitespace-nowrap ${
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
