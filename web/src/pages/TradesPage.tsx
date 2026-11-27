import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ArrowDown, ArrowUp, Check } from 'lucide-react'
import { InstrumentMark } from '../components/InstrumentMark'
import { formatDisplayKpiValue } from '../components/KpiCard'
import { SegmentedPills } from '../components/SegmentedPills'
import { PositionTimeline, PartialSettlement, isScaleOutTrade, lifecycleStatus } from '../components/PositionTimeline'
import { VenueBadge } from '../components/VenueBadge'
import { useI18n } from '../i18n'
import type { MessageKey } from '../i18n/zh-CN'
import { useCurrency } from '../lib/currency'
import { formatExecutionFee } from '../lib/executionFee'
import { collectActiveWarnings, fmtContractQty, fmtLegRate, fmtDuration, fmtPrice, pnlTone } from '../lib/format'
import type { ClosedTrade, DashboardData, DashboardWarningItem, TradeLegFee } from '../lib/types'

const TAG_KEYS: Array<{ id: string; key: MessageKey }> = [
  { id: 'planned', key: 'tag_planned' },
  { id: 'clean_tp', key: 'tag_clean_tp' },
  { id: 'clean_sl', key: 'tag_clean_sl' },
  { id: 'taker_drag', key: 'tag_taker_drag' },
  { id: 'chased', key: 'tag_chased' },
  { id: 'overheld', key: 'tag_overheld' },
]

function tagsEqual(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, idx) => v === b[idx])
}

function modeLabel(code: string, t: (k: MessageKey) => string): string {
  const known: Record<string, MessageKey> = {
    LIMIT_MAKER: 'mode_LIMIT_MAKER',
    MARKET_TAKER: 'mode_MARKET_TAKER',
    STOP_MARKET: 'mode_STOP_MARKET',
    TP_MARKET: 'mode_TP_MARKET',
    REVERSE_MARKET: 'mode_REVERSE_MARKET',
    MIXED: 'mode_MIXED',
    UNKNOWN: 'mode_UNKNOWN',
  }
  return known[code] ? t(known[code]) : code
}

function LegFeeCard({
  title,
  subtitleBadge,
  netLabel,
  leg,
  feeDecimals = 3,
  testId,
  padClass = '',
}: {
  title: string
  subtitleBadge?: string
  netLabel: string
  leg: TradeLegFee
  feeDecimals?: number
  testId: string
  padClass?: string
}) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const feeUnknown = leg.fee_known === false
  return (
    <div data-testid={testId} className={`space-y-2 ${padClass}`}>
      <div>
        <div className="font-semibold text-[13px] text-[var(--fg)] flex items-center gap-1.5 flex-wrap">
          <span>{title}</span>
          {subtitleBadge && (
            <span className="font-normal text-[12px] text-[var(--fg-3)]">· {subtitleBadge}</span>
          )}
        </div>
        <div className="text-[12px] text-[var(--fg-2)] truncate">
          {feeUnknown ? t('leg_fee_unknown') : `${modeLabel(leg.mode_code, t)} · ${fmtLegRate(leg.fee_bps, locale)}`}
        </div>
        <div className="text-[12px] text-[var(--fg-3)] whitespace-nowrap">
          {leg.time_str}
          {leg.avg_price > 0 && (
            <>
              {' · '}
              <span className="sensitive">{fmtPrice(leg.avg_price)}</span>
            </>
          )}
        </div>
      </div>

      <div className="pt-2 border-t border-[var(--border)] space-y-1 text-[12px] tabular-nums">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--fg-3)]">{t('leg_gross_fee')}</span>
          <span className={`whitespace-nowrap sensitive ${feeUnknown ? 'text-[var(--fg-3)]' : ''}`}>
            {feeUnknown ? t('leg_fee_unknown') : formatExecutionFee(cur, -leg.gross_fee_usd, feeDecimals)}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--fg-3)]">{t('leg_rebate_disc')}</span>
          <span className={`whitespace-nowrap sensitive ${!feeUnknown && leg.rebate_usd > 0 ? 'txt-pos' : 'text-[var(--fg-3)]'}`}>
            {feeUnknown
              ? '—'
              : leg.rebate_usd > 0
                ? `${formatExecutionFee(cur, leg.rebate_usd, feeDecimals)}${leg.gross_fee_usd > 0 ? ` (${leg.rebate_pct.toFixed(0)}%)` : ''}`
                : leg.gross_fee_usd === 0 ? t('leg_zero_fee') : cur.money(0, feeDecimals)}
          </span>
        </div>
        {!feeUnknown && leg.discount_saved_usd > 0 && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[var(--fg-3)]">
              {locale === 'en' ? 'Pre-discount' : '抵扣已省'}
            </span>
            <span className="txt-pos sensitive whitespace-nowrap">
              {formatExecutionFee(cur, leg.discount_saved_usd, feeDecimals)}
            </span>
          </div>
        )}
        <div className="flex items-center justify-between gap-2 font-semibold pt-1">
          <span className="text-[var(--fg-2)]">{netLabel}</span>
          <span className={`whitespace-nowrap sensitive ${feeUnknown ? 'text-[var(--fg-3)]' : ''}`}>
            {feeUnknown ? '—' : formatExecutionFee(cur, -leg.net_fee_usd, feeDecimals)}
          </span>
        </div>
      </div>
    </div>
  )
}

export function TradesPage({
  data,
  venueFilter,
  onVenueChange,
  symbolFilter,
  onSymbolChange,
  onSaveJournal,
  onInspectSymbol,
  scopeReady = true,
  scopeLoading = false,
  scopeError = '',
  onRetryScope,
  activeWarnings,
}: {
  data: DashboardData
  venueFilter: string
  onVenueChange: (v: string) => void
  symbolFilter: string
  onSymbolChange: (s: string) => void
  onSaveJournal: (tradeId: string, tags: string[], note: string) => Promise<void>
  onInspectSymbol?: (venueId: string, symbol: string, venueName: string, rangeKey: string) => void
  scopeReady?: boolean
  scopeLoading?: boolean
  scopeError?: string
  onRetryScope?: () => void
  activeWarnings?: DashboardWarningItem[]
}) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const [detailed, setDetailed] = useState(true)
  const [sortBy, setSortBy] = useState<'CLOSE_DESC' | 'OPEN_DESC' | 'PNL_DESC' | 'PNL_ASC'>('CLOSE_DESC')
  const [selectedId, setSelectedId] = useState<string>('')
  const [swapDir, setSwapDir] = useState<'next' | 'prev'>('next')
  const [draftMap, setDraftMap] = useState<Record<string, { tags: string[]; note: string }>>({})
  const draftMapRef = useRef<Record<string, { tags: string[]; note: string }>>({})
  const [committedMap, setCommittedMap] = useState<
    Record<
      string,
      { tags: string[]; note: string; prevServerTags: string[]; prevServerNote: string }
    >
  >({})
  const [savingCountById, setSavingCountById] = useState<Record<string, number>>({})
  const [savedAckById, setSavedAckById] = useState<Record<string, boolean>>({})
  const [saveErrorById, setSaveErrorById] = useState<Record<string, string>>({})
  const savedTimersRef = useRef<Record<string, number>>({})
  const detailRef = useRef<HTMLElement>(null)
  const tableWrapRef = useRef<HTMLDivElement | null>(null)
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({})
  const [rowGlider, setRowGlider] = useState<{ top: number; height: number; visible: boolean }>({
    top: 0,
    height: 0,
    visible: false,
  })

  const clearSavedIndicatorsFor = (tradeId: string) => {
    if (!tradeId) return
    const timer = savedTimersRef.current[tradeId]
    if (timer !== undefined) {
      window.clearTimeout(timer)
      delete savedTimersRef.current[tradeId]
    }
    setSavedAckById(prev => (prev[tradeId] ? { ...prev, [tradeId]: false } : prev))
    setSaveErrorById(prev => (prev[tradeId] ? { ...prev, [tradeId]: '' } : prev))
  }

  useEffect(() => {
    const timers = savedTimersRef.current
    return () => {
      for (const timer of Object.values(timers)) {
        window.clearTimeout(timer)
      }
    }
  }, [])

  useEffect(() => {
    setSelectedId('')
  }, [venueFilter, symbolFilter])

  const localDataScopeConflict = useMemo(() => {
    const respVenue = data.venue_filter
    const respSymbol = data.symbol_filter
    if (respVenue) {
      const wantV = venueFilter.toUpperCase() === 'ALL' ? 'ALL' : venueFilter.toLowerCase()
      const gotV = respVenue.toUpperCase() === 'ALL' ? 'ALL' : respVenue.toLowerCase()
      if (wantV !== gotV) return true
    }
    if (respSymbol) {
      const wantS = symbolFilter.toUpperCase() === 'ALL' ? 'ALL' : symbolFilter.toUpperCase()
      const gotS = respSymbol.toUpperCase() === 'ALL' ? 'ALL' : respSymbol.toUpperCase()
      if (wantS !== gotS) return true
    }
    return false
  }, [data.venue_filter, data.symbol_filter, venueFilter, symbolFilter])

  const effectiveScopeReady = scopeReady && !localDataScopeConflict && !scopeError

  const sortedTrades = useMemo(() => {
    if (!effectiveScopeReady) return []
    const arr = data.trades.filter(tr => {
      if (venueFilter !== 'ALL' && tr.venue_id.toLowerCase() !== venueFilter.toLowerCase()) {
        return false
      }
      if (symbolFilter !== 'ALL' && tr.symbol.toUpperCase() !== symbolFilter.toUpperCase()) {
        return false
      }
      return true
    })
    arr.sort((a, b) => {
      if (sortBy === 'OPEN_DESC') return b.open_time_ms - a.open_time_ms
      if (sortBy === 'PNL_DESC' || sortBy === 'PNL_ASC') {
        const unknownOrder = Number(a.net_pnl_known === false) - Number(b.net_pnl_known === false)
        if (unknownOrder) return unknownOrder
        if (a.net_pnl_known !== false) return sortBy === 'PNL_DESC' ? b.net_pnl_usd - a.net_pnl_usd : a.net_pnl_usd - b.net_pnl_usd
      }
      return b.close_time_ms - a.close_time_ms
    })
    return arr
  }, [data.trades, sortBy, venueFilter, symbolFilter, effectiveScopeReady])

  const selected: ClosedTrade | null = useMemo(() => {
    if (!sortedTrades.length) return null
    return sortedTrades.find(tr => tr.trade_id === selectedId) ?? sortedTrades[0]
  }, [sortedTrades, selectedId])

  const selectedScaleOut = isScaleOutTrade(selected)
  const selectedIndex = selected ? sortedTrades.indexOf(selected) : -1
  const activeTradeId = selected?.trade_id ?? ''
  const currentDraft = activeTradeId ? draftMap[activeTradeId] : undefined
  const committedForSelected = useMemo(() => {
    if (!selected) return undefined
    const c = committedMap[selected.trade_id]
    if (!c) return undefined
    const serverUnchanged =
      (selected.note ?? '') === c.prevServerNote && tagsEqual(selected.tags ?? [], c.prevServerTags)
    return serverUnchanged ? c : undefined
  }, [selected, committedMap])
  const draftTags = currentDraft
    ? currentDraft.tags
    : committedForSelected
      ? committedForSelected.tags
      : (selected?.tags ?? [])
  const draftNote = currentDraft
    ? currentDraft.note
    : committedForSelected
      ? committedForSelected.note
      : (selected?.note ?? '')
  const saving = activeTradeId ? (savingCountById[activeTradeId] ?? 0) > 0 : false
  const savedAck = activeTradeId ? Boolean(savedAckById[activeTradeId]) : false
  const saveError = activeTradeId ? (saveErrorById[activeTradeId] ?? '') : ''
  const canSaveJournal = Boolean(selected && effectiveScopeReady && !scopeLoading && !scopeError)

  useEffect(() => {
    if (selected && !window.matchMedia?.('(max-width: 1279px)').matches) {
      rowRefs.current[selected.trade_id]?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
    }
  }, [selected?.trade_id])

  useLayoutEffect(() => {
    const syncRowBar = () => {
      const wrap = tableWrapRef.current
      const activeRow = selected ? rowRefs.current[selected.trade_id] : null
      if (!wrap || !activeRow || activeRow.offsetHeight === 0) {
        setRowGlider(g => (g.visible ? { ...g, visible: false } : g))
        return
      }
      setRowGlider({
        top: activeRow.offsetTop + 6,
        height: Math.max(12, activeRow.offsetHeight - 12),
        visible: true,
      })
    }
    syncRowBar()
    if (typeof ResizeObserver !== 'undefined' && tableWrapRef.current) {
      const ro = new ResizeObserver(() => syncRowBar())
      ro.observe(tableWrapRef.current)
      return () => ro.disconnect()
    }
  }, [selected?.trade_id, detailed, sortedTrades.length])

  const selectTradeAt = (nextId: string, nextIdx: number) => {
    setSwapDir(nextIdx < selectedIndex ? 'prev' : 'next')
    setSelectedId(nextId)
    if (window.matchMedia?.('(max-width: 1279px)').matches) {
      requestAnimationFrame(() => detailRef.current?.scrollIntoView({ block: 'start' }))
    }
  }

  const step = (offset: number) => {
    const targetIdx = selectedIndex + offset
    const next = sortedTrades[targetIdx]
    if (next) {
      setSwapDir(offset < 0 ? 'prev' : 'next')
      setSelectedId(next.trade_id)
    }
  }

  const handleSave = async () => {
    if (!selected || !canSaveJournal) return
    const targetId = selected.trade_id
    const sentTags = [...draftTags]
    const sentNote = draftNote
    const origServerTags = [...(selected.tags ?? [])]
    const origServerNote = selected.note ?? ''

    const existingTimer = savedTimersRef.current[targetId]
    if (existingTimer !== undefined) {
      window.clearTimeout(existingTimer)
      delete savedTimersRef.current[targetId]
    }
    setSavingCountById(prev => ({ ...prev, [targetId]: (prev[targetId] ?? 0) + 1 }))
    setSaveErrorById(prev => (prev[targetId] ? { ...prev, [targetId]: '' } : prev))
    setSavedAckById(prev => (prev[targetId] ? { ...prev, [targetId]: false } : prev))

    try {
      await onSaveJournal(targetId, sentTags, sentNote)
      setCommittedMap(prev => ({
        ...prev,
        [targetId]: {
          tags: sentTags,
          note: sentNote,
          prevServerTags: origServerTags,
          prevServerNote: origServerNote,
        },
      }))

      const latestDraft = draftMapRef.current[targetId]
      const unchanged =
        !latestDraft || (latestDraft.note === sentNote && tagsEqual(latestDraft.tags, sentTags))

      setDraftMap(prev => {
        const cur = prev[targetId]
        if (!cur || (cur.note === sentNote && tagsEqual(cur.tags, sentTags))) {
          if (!(targetId in prev)) return prev
          const next = { ...prev }
          delete next[targetId]
          draftMapRef.current = next
          return next
        }
        return prev
      })

      if (unchanged) {
        if (targetId in draftMapRef.current) {
          const nextRef = { ...draftMapRef.current }
          delete nextRef[targetId]
          draftMapRef.current = nextRef
        }
        setSavedAckById(prev => ({ ...prev, [targetId]: true }))
        const prevTimer = savedTimersRef.current[targetId]
        if (prevTimer !== undefined) window.clearTimeout(prevTimer)
        savedTimersRef.current[targetId] = window.setTimeout(() => {
          setSavedAckById(prev => (prev[targetId] ? { ...prev, [targetId]: false } : prev))
          delete savedTimersRef.current[targetId]
        }, 2000)
      }
    } catch (err) {
      const msg =
        err instanceof Error
          ? err.message
          : locale === 'en'
            ? 'Failed to save journal note'
            : '保存复盘笔记失败，请稍后重试'
      setSaveErrorById(prev => ({ ...prev, [targetId]: msg }))
    } finally {
      setSavingCountById(prev => {
        const nextCount = Math.max(0, (prev[targetId] ?? 1) - 1)
        if (nextCount === 0) {
          if (!(targetId in prev)) return prev
          const next = { ...prev }
          delete next[targetId]
          return next
        }
        return { ...prev, [targetId]: nextCount }
      })
    }
  }

  const truncationWarnings = useMemo(() => {
    const allWarnings = activeWarnings ?? collectActiveWarnings(data)
    const TRUNC_RE = /截断|上限|分页|最多|仅拉取|未完整|历史|成交|交割|truncat|limit|cap|max|pagination|partial\s+history|trades?\b|fills?\b/i
    return allWarnings.filter(w => {
      if (venueFilter !== 'ALL' && w.venue_id.toLowerCase() !== venueFilter.toLowerCase()) {
        return false
      }
      const joined = `${w.summary} ${w.summary_zh} ${w.warnings.join(' ')}`
      return TRUNC_RE.test(joined)
    })
  }, [activeWarnings, data, venueFilter])

  const normalizedVenueValue = useMemo(() => {
    if (venueFilter === 'ALL') return 'ALL'
    const matched = data.exchanges.find(ex => ex.venue_id.toLowerCase() === venueFilter.toLowerCase())
    return matched ? matched.venue_id : venueFilter
  }, [venueFilter, data.exchanges])

  const venueOptions = useMemo(
    () => [
      { value: 'ALL', label: t('all_venues'), testId: 'venue-filter-ALL' },
      ...data.exchanges.map(ex => ({
        value: ex.venue_id,
        label: locale === 'en' ? ex.display_name : ex.display_name_zh || ex.display_name,
        testId: `venue-filter-${ex.venue_id}`,
      })),
    ],
    [data.exchanges, locale, t],
  )

  const perpHistory = data.extensions.stocks.perpetuals.filter(
    row =>
      (venueFilter === 'ALL' || row.venue_id.toLowerCase() === venueFilter.toLowerCase()) &&
      (symbolFilter === 'ALL' || row.symbol.toUpperCase() === symbolFilter.toUpperCase()),
  )

  return (
    <div data-testid="trades-page" className="otl-stagger">
      {!data.demo_mode && truncationWarnings.length > 0 && (
        <div
          role="status"
          data-testid="trades-warnings-banner"
          className="otl-status-warn mt-4 px-4 py-2.5 rounded-lg text-[12.5px] flex items-start gap-2.5"
        >
          <AlertTriangle size={15} className="shrink-0 mt-0.5" />
          <div className="space-y-0.5">
            {truncationWarnings.map(w => {
              const name = locale === 'en' ? w.display_name : w.display_name_zh || w.display_name
              const summary = locale === 'en' ? w.summary || w.summary_zh : w.summary_zh || w.summary
              const extra = w.warnings.filter(msg => msg && !summary.includes(msg))
              return (
                <div key={w.venue_id}>
                  <strong>{name}</strong>：{summary}
                  {extra.length > 0 ? `（${extra.join('；')}）` : ''}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Flush Filter Toolbar */}
      <div className="py-3.5 px-4 sm:px-5 border-b border-[var(--border)] flex flex-wrap items-center justify-between gap-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <SegmentedPills
            wrap
            value={normalizedVenueValue}
            onChange={onVenueChange}
            options={venueOptions}
          />

          <select
            aria-label={t('all_symbols')}
            value={symbolFilter}
            onChange={e => onSymbolChange(e.target.value)}
            className="otl-select h-8 px-2.5 text-[12px] cursor-pointer"
          >
            <option value="ALL">{t('all_symbols')}</option>
            {symbolFilter !== 'ALL' && !data.available_symbols.includes(symbolFilter) && (
              <option value={symbolFilter}>{symbolFilter}</option>
            )}
            {data.available_symbols.map(sym => (
              <option key={sym} value={sym}>
                {sym}
              </option>
            ))}
          </select>

          {(venueFilter !== 'ALL' || symbolFilter !== 'ALL') && (
            <button
              type="button"
              data-testid="reset-trade-filters-btn"
              onClick={() => {
                onVenueChange('ALL')
                onSymbolChange('ALL')
              }}
              className="otl-btn otl-panel-swap h-8"
            >
              {locale === 'en' ? 'Reset' : '重置筛选'}
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          <SegmentedPills
            value={detailed ? 'detailed' : 'compact'}
            onChange={mode => setDetailed(mode === 'detailed')}
            options={[
              { value: 'detailed', label: t('view_detailed') },
              { value: 'compact', label: t('view_compact') },
            ]}
          />
          <select
            aria-label="Sort"
            value={sortBy}
            onChange={e => setSortBy(e.target.value as typeof sortBy)}
            className="otl-select h-8 px-2.5 text-[12px] cursor-pointer"
          >
            <option value="CLOSE_DESC">{t('sort_close_desc')}</option>
            <option value="OPEN_DESC">{t('sort_open_desc')}</option>
            <option value="PNL_DESC">{t('sort_pnl_desc')}</option>
            <option value="PNL_ASC">{t('sort_pnl_asc')}</option>
          </select>
        </div>
      </div>

      {/* 7:5 Master-Detail Hairline Split */}
      {!effectiveScopeReady ? (
        scopeError && !scopeLoading ? (
          <div
            role="alert"
            data-testid="dashboard-load-error-state"
            className="py-16 text-center space-y-3 border-b border-[var(--border)]"
          >
            <p className="m-0 text-[13px] text-[var(--fg-2)]">
              {locale === 'en'
                ? 'Could not load trades for the selected filter. Previous account data is hidden to prevent cross-account edits.'
                : '当前筛选条件下的交易数据加载失败（已隐藏旧筛选数据，防止跨平台误写笔记）。'}
            </p>
            {onRetryScope && (
              <button
                type="button"
                data-testid="dashboard-retry-btn"
                onClick={onRetryScope}
                className="otl-btn otl-btn-primary"
              >
                {locale === 'en' ? 'Retry' : '重试'}
              </button>
            )}
          </div>
        ) : (
          <div
            role="status"
            className="py-20 text-center text-[13px] text-[var(--fg-3)] border-b border-[var(--border)]"
          >
            {locale === 'en' ? 'Loading…' : '加载中…'}
          </div>
        )
      ) : (
      <>
      <div className="grid grid-cols-1 xl:grid-cols-12 border-b border-[var(--border)] items-start">
        <div
          className="min-w-0 xl:col-span-7 xl:border-r border-[var(--border)] xl:sticky xl:top-[68px]"
          tabIndex={0}
          onKeyDown={e => {
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              step(1)
            } else if (e.key === 'ArrowUp') {
              e.preventDefault()
              step(-1)
            }
          }}
        >
          {sortedTrades.length === 0 ? (
            <div className="py-16 text-center text-[13px] text-[var(--fg-3)]">{t('empty_trades')}</div>
          ) : (
            <div ref={tableWrapRef} className="otl-table-wrap max-h-[72vh] overflow-y-auto overflow-x-auto">
              <div
                aria-hidden="true"
                className="otl-row-glider"
                style={{
                  opacity: rowGlider.visible ? 1 : 0,
                  height: `${rowGlider.height}px`,
                  transform: `translate3d(0, ${rowGlider.top}px, 0)`,
                }}
              />
              <table className={`otl-table otl-trades-table ${detailed ? '' : 'compact'}`}>
                <thead className="sticky top-0 z-10">
                  <tr>
                    <th>{t('th_symbol_time')}</th>
                    <th>{t('th_venue')}</th>
                    {detailed && <th className="hidden sm:table-cell text-right">{t('th_entry_exit_fee')}</th>}
                    <th className="text-right">{t('th_net_pnl')}</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedTrades.map((tr, idx) => {
                    const active = selected?.trade_id === tr.trade_id
                    const batches = tr.exit_batches && tr.exit_batches.length > 1 ? tr.exit_batches : []
                    const hasBatches = batches.length > 1
                    const rowStillHolding = Boolean(tr.position_events?.length && tr.position_events.at(-1)?.action !== 'CLOSE')
                    return (
                      <tr
                        key={tr.trade_id}
                        ref={el => {
                          rowRefs.current[tr.trade_id] = el
                        }}
                        data-testid={`trade-row-${tr.trade_id}`}
                        data-glider-row={rowGlider.visible ? 'true' : undefined}
                        className={`clickable ${active ? 'selected' : ''}`}
                        onClick={() => selectTradeAt(tr.trade_id, idx)}
                      >
                        <td>
                          <div className="flex flex-wrap items-center gap-1.5">
                            <InstrumentMark instrument={tr.symbol} exchange={tr.venue_id.toUpperCase()} />
                            <button
                              type="button"
                              className="bg-transparent border-0 p-0 text-[13px] font-semibold whitespace-nowrap cursor-pointer"
                              aria-label={`${tr.symbol} · ${tr.venue_name} · ${tr.close_time_str}`}
                              aria-current={active ? 'true' : undefined}
                            >{tr.symbol}</button>
                            <span
                              className={`text-[12px] font-semibold whitespace-nowrap ${
                                tr.side === 'LONG' ? 'txt-pos' : 'txt-neg'
                              }`}
                            >
                              {tr.side === 'LONG' ? t('side_long') : t('side_short')}
                            </span>
                            {rowStillHolding ? (
                              <span className="px-1.5 py-0.5 rounded border border-[var(--border)] text-[11px] font-medium text-[var(--accent)] whitespace-nowrap">
                                {t(lifecycleStatus(tr.position_events!))}
                              </span>
                            ) : hasBatches ? (
                              <span className="px-1.5 py-0.5 rounded border border-[var(--border)] text-[11px] font-medium text-[var(--fg-2)] whitespace-nowrap">
                                {locale === 'en' ? `${batches.length} Exits` : `分 ${batches.length} 笔`}
                              </span>
                            ) : (
                              tr.is_partial_close && (
                                <span className="text-[12px] text-[var(--fg-3)] whitespace-nowrap">
                                  · {t('partial_badge')}
                                </span>
                              )
                            )}
                          </div>
                          <div className="otl-trade-row-time text-[12px] text-[var(--fg-3)] mt-0.5">
                            {tr.close_time_str} · {fmtDuration(tr.holding_sec, locale)}
                          </div>
                        </td>
                        <td>
                          <VenueBadge
                            venueId={tr.venue_id}
                            name={locale === 'en' ? tr.venue_name : tr.venue_name_zh || tr.venue_name}
                          />
                        </td>
                        {detailed && (
                          <td className="hidden sm:table-cell text-right tabular-nums">
                            <div className="text-[12.5px] font-medium sensitive">
                              {t('fee_net_short')}{' '}
                              {tr.total_fee_known === false ? t('lifecycle_unknown') : cur.signedMoney(-tr.net_fee_usd, 2)}
                              {tr.total_fee_known !== false && tr.effective_relief_pct > 0 && (
                                <span className="txt-pos ml-1">
                                  (-{tr.effective_relief_pct.toFixed(0)}%)
                                </span>
                              )}
                            </div>
                            <div className="text-[12px] text-[var(--fg-3)] sensitive">
                              {tr.entry_leg.fee_known === false || tr.exit_leg.fee_known === false
                                ? t('leg_fee_unknown')
                                : <>
                                    <span className="inline-block">{t('fee_entry_short')} {cur.signedPlainMoney(-tr.entry_leg.gross_fee_usd, 2)}</span>
                                    {' · '}
                                    <span className="inline-block">{t('fee_exit_short')} {cur.signedPlainMoney(-tr.exit_leg.gross_fee_usd, 2)}</span>
                                  </>}
                            </div>
                          </td>
                        )}
                        <td className="text-right whitespace-nowrap">
                          <div className={`text-[13px] font-semibold sensitive ${pnlTone(tr.net_pnl_known === false ? 0 : tr.net_pnl_usd)}`}>
                            {tr.net_pnl_known === false ? t('lifecycle_unknown') : cur.signedPlainMoney(tr.net_pnl_usd)}
                          </div>
                          <div className="text-[12px] text-[var(--fg-3)] sensitive">
                            {locale === 'en' ? 'Pre-rebate ' : '返佣前 '}
                            {tr.net_pnl_known === false ? t('lifecycle_unknown') : cur.signedPlainMoney(tr.book_pnl_usd)}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Right Settlement Inspector: Always col-span-5, compact & symmetric height */}
        <aside
          ref={detailRef}
          data-testid="trade-detail-panel"
          className="otl-trade-inspector min-w-0 xl:col-span-5 py-5 px-4 sm:px-5 xl:px-6 border-t xl:border-t-0 border-[var(--border)] flex flex-col gap-5 xl:sticky xl:top-[68px]"
        >
          <div className="flex items-center justify-between gap-2">
            <h2 className="t-h2">{t('detail_title')}</h2>
            {sortedTrades.length > 0 && (
              <div className="flex items-center gap-1.5 text-[12px] text-[var(--fg-3)]">
                <span className="tabular-nums mr-1">
                  {selectedIndex + 1} / {sortedTrades.length}
                </span>
                <button
                  type="button"
                  disabled={selectedIndex <= 0}
                  onClick={() => step(-1)}
                  className="otl-btn h-7 w-7 px-0"
                  aria-label="Previous"
                >
                  <ArrowUp size={13} />
                </button>
                <button
                  type="button"
                  disabled={selectedIndex < 0 || selectedIndex + 1 >= sortedTrades.length}
                  onClick={() => step(1)}
                  className="otl-btn h-7 w-7 px-0"
                  aria-label="Next"
                >
                  <ArrowDown size={13} />
                </button>
              </div>
            )}
          </div>

          {!selected ? (
            <div className="py-12 text-center text-[13px] text-[var(--fg-3)]">{t('empty_trades')}</div>
          ) : (
            (() => {
              const selectedBatches =
                selected.exit_batches && selected.exit_batches.length > 1 ? selected.exit_batches : []
              const selectedHasBatches = selectedBatches.length > 1
              const stillHolding = Boolean(selected.position_events?.length && selected.position_events.at(-1)?.action !== 'CLOSE')
              const feeDec = selectedScaleOut ? 4 : 3
              return (
            <>
              <div
                key={selected.trade_id}
                className={`${swapDir === 'prev' ? 'otl-swap-prev' : 'otl-swap-next'} flex flex-col`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3 pb-3.5 border-b border-[var(--border)]">
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <InstrumentMark instrument={selected.symbol} exchange={selected.venue_id.toUpperCase()} />
                      <strong className="text-[15px] font-semibold">{selected.symbol}</strong>
                      <span
                        className={`text-[12px] font-semibold ${
                          selected.side === 'LONG' ? 'txt-pos' : 'txt-neg'
                        }`}
                      >
                        {selected.side === 'LONG' ? t('side_long') : t('side_short')}
                      </span>
                      <VenueBadge
                        venueId={selected.venue_id}
                        name={locale === 'en' ? selected.venue_name : selected.venue_name_zh || selected.venue_name}
                      />
                      {stillHolding ? (
                        <span className="px-1.5 py-0.5 rounded border border-[var(--border)] text-[11px] font-medium text-[var(--accent)] whitespace-nowrap">
                          {t(lifecycleStatus(selected.position_events!))}
                        </span>
                      ) : selectedHasBatches ? (
                        <span className="px-1.5 py-0.5 rounded border border-[var(--border)] text-[11px] font-medium text-[var(--fg-2)] whitespace-nowrap">
                          {locale === 'en' ? `${selectedBatches.length} Exits` : `分 ${selectedBatches.length} 笔`}
                        </span>
                      ) : null}
                    </div>
                    <div className="text-[12px] text-[var(--fg-3)] mt-1">
                      {(selected.qty || selected.contract_count) ? (
                        <span className="sensitive">{fmtContractQty(selected, locale)} · </span>
                      ) : null}
                      {(selected.entry_leg.avg_price > 0 || selected.exit_leg.avg_price > 0) && (
                        <span className="sensitive">
                          {fmtPrice(selected.entry_leg.avg_price)} →{' '}
                          {selectedHasBatches ? (locale === 'en' ? 'Avg ' : '均 ') : ''}
                          {fmtPrice(selected.exit_leg.avg_price)} ·{' '}
                        </span>
                      )}
                      {fmtDuration(selected.holding_sec, locale)}
                      {selected.total_fee_known !== false && selected.effective_relief_pct > 0
                        ? ` · ${t('detail_relief_short')} ${selected.effective_relief_pct.toFixed(1)}%`
                        : ''}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={`t-kpi sensitive whitespace-nowrap ${pnlTone(selected.net_pnl_known === false ? 0 : selected.net_pnl_usd)}`}>
                      {selected.net_pnl_known === false ? t('lifecycle_unknown') : formatDisplayKpiValue(cur.signedMoney(selected.net_pnl_usd))}
                    </div>
                    <div className="text-[12px] text-[var(--fg-3)] mt-0.5">
                      {selected.rebate_status === 'PENDING' ? t('detail_net_pending') : t(stillHolding ? 'lifecycle_current_net' : 'detail_net_after_rebate')}
                    </div>
                  </div>
                </div>

                {/* Always-visible Symmetric Entry | Exit Fee Cards (Maker/Taker mode, bps & per-leg rebate %) */}
                <div className="grid grid-cols-1 sm:grid-cols-2 divide-y sm:divide-y-0 sm:divide-x divide-[var(--border)] py-3.5 border-b border-[var(--border)] gap-3 sm:gap-0">
                  <LegFeeCard
                    testId="entry-leg-fee-card"
                    title={t('leg_entry')}
                    netLabel={t('leg_entry_net')}
                    leg={selected.entry_leg}
                    feeDecimals={feeDec}
                    padClass="sm:pr-4"
                  />
                  <LegFeeCard
                    testId="exit-leg-fee-card"
                    title={t('leg_exit')}
                    subtitleBadge={
                      selectedHasBatches
                        ? locale === 'en'
                          ? `${selectedBatches.length} exits`
                          : `${selectedBatches.length} 笔汇总`
                        : undefined
                    }
                    netLabel={t('leg_exit_net')}
                    leg={selected.exit_leg}
                    feeDecimals={feeDec}
                    padClass="pt-3 sm:pt-0 sm:pl-4"
                  />
                </div>

                <div data-testid="trade-settlement" className="pt-3.5 space-y-1.5 text-[12.5px] tabular-nums">
                  <div className="flex justify-between">
                    <span className="text-[var(--fg-2)]">{t('wf_gross_pnl')}</span>
                    <span className={`font-medium sensitive ${pnlTone(selected.gross_pnl_usd)}`}>
                      {cur.signedMoney(selected.gross_pnl_usd, selectedHasBatches ? 4 : 2)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--fg-2)]">{t('wf_funding')}</span>
                    <span className="sensitive">
                      {Math.abs(selected.funding_pnl_usd) > 0.0001
                        ? cur.signedMoney(selected.funding_pnl_usd, selectedHasBatches ? 4 : 2)
                        : cur.money(0)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--fg-2)]">{t('wf_total_fee')}</span>
                    <span className="sensitive">
                      {selected.total_fee_known === false ? t('lifecycle_unknown') : formatExecutionFee(cur, -selected.total_fee_usd, feeDec)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--fg-2)]">{t('wf_total_rebate')}</span>
                    <span className="txt-pos sensitive">
                      {selected.total_fee_known === false ? t('lifecycle_unknown') : formatExecutionFee(cur, selected.total_rebate_usd, feeDec)}
                    </span>
                  </div>
                  {Boolean(selected.settlement_adjustment_usd) && (
                    <div className="flex justify-between">
                      <span>{locale === 'en' ? 'Unattributed settlement adjustment' : '未归因结算差额'}</span>
                      <span className="sensitive">{formatExecutionFee(cur, selected.settlement_adjustment_usd!, 2)}</span>
                    </div>
                  )}
                  <div className="flex justify-between pt-2 border-t border-[var(--border)] text-[13px] font-semibold">
                    <span>{t(stillHolding ? 'lifecycle_current_net' : 'wf_final_net')}</span>
                    <span className={`sensitive ${pnlTone(selected.net_pnl_known === false ? 0 : selected.net_pnl_usd)}`}>
                      {selected.net_pnl_known === false ? t('lifecycle_unknown') : cur.signedMoney(selected.net_pnl_usd)}
                    </span>
                  </div>
                  <PartialSettlement trade={selected} />
                  {selected.total_fee_known !== false && selected.total_discount_saved_usd > 0 && (
                    <div className="flex justify-between text-[12px] text-[var(--fg-3)]">
                      <span>
                        {locale === 'en'
                          ? 'Pre-deduction token fee discount already saved'
                          : '注：扣费前平台币抵扣已另省（不重复计入返佣）'}
                      </span>
                      <span className="txt-pos sensitive">
                        {formatExecutionFee(cur, selected.total_discount_saved_usd, feeDec)}
                      </span>
                    </div>
                  )}
                </div>
              </div>

              {/* Journal Tags & Notes (kept outside key={selected.trade_id} to preserve synchronous drafts) */}
              <div className="pt-3.5 border-t border-[var(--border)] space-y-2.5">
                <div className="flex items-center justify-between">
                  <span className="text-[12px] font-medium text-[var(--fg-2)]">{t('journal_title')}</span>
                  <button
                    type="button"
                    data-testid="journal-save-btn"
                    disabled={saving || !canSaveJournal}
                    onClick={() => void handleSave()}
                    className="otl-btn h-7 px-2.5"
                  >
                    {savedAck && <Check size={12} className="txt-pos" />}
                    <span>{saving ? t('journal_saving') : savedAck ? t('journal_saved') : t('journal_save')}</span>
                  </button>
                </div>
                {saveError && (
                  <div
                    role="alert"
                    data-testid="journal-save-error"
                    className="otl-status-err px-3 py-2 rounded-md text-[12px] font-medium"
                  >
                    {saveError}
                  </div>
                )}
                <div className="flex flex-wrap gap-1.5">
                  {TAG_KEYS.map(tk => {
                    const on = draftTags.includes(tk.id)
                    return (
                      <button
                        key={tk.id}
                        type="button"
                        onClick={() => {
                          const targetId = selected.trade_id
                          clearSavedIndicatorsFor(targetId)
                          const base = draftMapRef.current[targetId] ?? {
                            tags: draftTags,
                            note: draftNote,
                          }
                          const nextTags = base.tags.includes(tk.id)
                            ? base.tags.filter(x => x !== tk.id)
                            : [...base.tags, tk.id]
                          const nextEntry = { tags: nextTags, note: base.note }
                          draftMapRef.current = { ...draftMapRef.current, [targetId]: nextEntry }
                          setDraftMap(prev => {
                            const curBase = prev[targetId] ?? base
                            const updatedTags = curBase.tags.includes(tk.id)
                              ? curBase.tags.filter(x => x !== tk.id)
                              : [...curBase.tags, tk.id]
                            const next = {
                              ...prev,
                              [targetId]: { tags: updatedTags, note: curBase.note },
                            }
                            draftMapRef.current = next
                            return next
                          })
                        }}
                        className="px-2.5 py-1 rounded-[5px] text-[12px] font-medium border cursor-pointer"
                        style={{
                          background: on ? 'var(--accent-soft)' : 'transparent',
                          borderColor: on ? 'var(--accent)' : 'var(--border)',
                          color: on ? 'var(--fg)' : 'var(--fg-2)',
                        }}
                      >
                        {t(tk.key)}
                      </button>
                    )
                  })}
                </div>
                <textarea
                  rows={2}
                  value={draftNote}
                  onChange={e => {
                    const targetId = selected.trade_id
                    clearSavedIndicatorsFor(targetId)
                    const val = e.target.value
                    const baseTags = draftMapRef.current[targetId]?.tags ?? draftTags
                    draftMapRef.current = {
                      ...draftMapRef.current,
                      [targetId]: { tags: baseTags, note: val },
                    }
                    setDraftMap(prev => {
                      const curTags = prev[targetId]?.tags ?? baseTags
                      const next = {
                        ...prev,
                        [targetId]: { tags: curTags, note: val },
                      }
                      draftMapRef.current = next
                      return next
                    })
                  }}
                  placeholder={t('journal_placeholder')}
                  className="otl-input w-full h-auto py-2 placeholder:text-[var(--fg-3)]"
                />
              </div>
            </>
              )
            })()
          )}
        </aside>
      </div>

      {/* Full-Width Horizontal Position Timeline (ONLY for scale-out / partial / in-flight positions) */}
      {selected && selectedScaleOut && (
        <div className="px-4 sm:px-5 py-4 border-b border-[var(--border)] bg-[var(--surface-2)]/35">
          <PositionTimeline trade={selected} horizontal />
        </div>
      )}

      {/* All-Time Equity & Commodity Perps Fee History */}
      <section className="py-6 space-y-3">
        <h2 id="perp-history-title" className="t-h2 px-5 pb-2.5 border-b border-[var(--border)]">
          {t('st_perps')} · {locale === 'en' ? 'All time' : '全部历史'}
        </h2>
        {perpHistory.length === 0 ? (
          <p className="m-0 py-8 text-center text-[13px] text-[var(--fg-3)]">
            {locale === 'en'
              ? 'No equity or commodity perpetual records match these filters.'
              : '当前筛选下没有股票或商品永续合约记录。'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table aria-labelledby="perp-history-title" className="otl-table">
              <thead>
                <tr>
                  <th scope="col">{t('th_symbol')}</th>
                  <th scope="col">{t('th_venue')}</th>
                  <th scope="col" className="text-right">{t('th_trades')} / {t('th_qty')}</th>
                  <th scope="col" className="text-right">{locale === 'en' ? 'Maker / Taker (bps)' : '挂单 / 吃单 (bps)'}</th>
                  <th scope="col" className="text-right">{t('th_fee_rebate')}</th>
                  <th scope="col" className="text-right">{t('kpi_net_fee_paid')}</th>
                  <th scope="col" className="text-right">{t('th_net_pnl')}</th>
                </tr>
              </thead>
              <tbody>
                {perpHistory.map(row => {
                  const venueName = locale === 'en' ? row.venue_name : row.venue_name_zh || row.venue_name
                  return (
                    <tr key={`${row.venue_id}-${row.symbol}`}>
                      <td className="whitespace-nowrap">
                        <div className="flex items-center gap-2 font-semibold">
                          <InstrumentMark instrument={row.symbol} exchange={row.venue_id.toUpperCase()} />
                          {onInspectSymbol ? (
                            <button
                              type="button"
                              aria-label={`${venueName} ${row.symbol} · ${locale === 'en' ? 'View trade history' : '查看历史成交'}`}
                              className="text-[var(--accent)] hover:underline cursor-pointer"
                              onClick={() => onInspectSymbol(row.venue_id, row.symbol, venueName, 'all')}
                            >
                              {row.symbol}
                            </button>
                          ) : (
                            row.symbol
                          )}
                        </div>
                        <div className="pl-7 text-[12px] text-[var(--fg-3)]">
                          {locale === 'en' ? row.category : row.category_zh || row.category}
                        </div>
                      </td>
                      <td>
                        <VenueBadge venueId={row.venue_id} name={venueName} />
                      </td>
                      <td className="text-right whitespace-nowrap sensitive">{row.trade_count} / {row.contract_count != null ? fmtContractQty(row, locale) : row.qty}</td>
                      <td className="text-right whitespace-nowrap text-[var(--fg-2)]">
                        {row.trade_count && row.total_fee_known !== false ? `${row.maker_bps?.toFixed(1) ?? '—'} / ${row.taker_bps?.toFixed(1) ?? '—'}` : '— / —'}
                      </td>
                      <td className="text-right whitespace-nowrap sensitive">
                        {row.total_fee_known === false ? t('lifecycle_unknown') : <>
                          {cur.signedPlainMoney(-row.gross_fee_usd, 4)} /{' '}
                          <span className="txt-pos">{cur.signedPlainMoney(row.rebate_usd, 4)}</span>
                        </>}
                      </td>
                      <td className="text-right whitespace-nowrap sensitive">{row.total_fee_known === false ? t('lifecycle_unknown') : cur.money(row.net_fee_usd, 4)}</td>
                      <td className={`text-right font-semibold whitespace-nowrap sensitive ${pnlTone(row.net_pnl_known === false ? 0 : row.net_pnl_usd)}`}>
                        {row.net_pnl_known === false ? t('lifecycle_unknown') : cur.signedMoney(row.net_pnl_usd)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      </>
      )}
    </div>
  )
}
