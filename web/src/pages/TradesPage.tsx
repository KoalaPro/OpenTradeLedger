import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Check } from 'lucide-react'
import { InstrumentMark } from '../components/InstrumentMark'
import { formatDisplayKpiValue } from '../components/KpiCard'
import { SegmentedPills } from '../components/SegmentedPills'
import { VenueBadge } from '../components/VenueBadge'
import { useI18n } from '../i18n'
import type { MessageKey } from '../i18n/zh-CN'
import { useCurrency } from '../lib/currency'
import { fmtDuration, fmtPrice, pnlTone } from '../lib/format'
import type { ClosedTrade, DashboardData, TradeLegFee } from '../lib/types'

const TAG_KEYS: Array<{ id: string; key: MessageKey }> = [
  { id: 'planned', key: 'tag_planned' },
  { id: 'clean_tp', key: 'tag_clean_tp' },
  { id: 'clean_sl', key: 'tag_clean_sl' },
  { id: 'taker_drag', key: 'tag_taker_drag' },
  { id: 'chased', key: 'tag_chased' },
  { id: 'overheld', key: 'tag_overheld' },
]

function modeLabel(code: string, t: (k: MessageKey) => string): string {
  const known: Record<string, MessageKey> = {
    LIMIT_MAKER: 'mode_LIMIT_MAKER',
    MARKET_TAKER: 'mode_MARKET_TAKER',
    STOP_MARKET: 'mode_STOP_MARKET',
    TP_MARKET: 'mode_TP_MARKET',
    REVERSE_MARKET: 'mode_REVERSE_MARKET',
    UNKNOWN: 'mode_UNKNOWN',
  }
  return known[code] ? t(known[code]) : code
}

function LegFeeCard({
  title,
  netLabel,
  leg,
  testId,
  padClass = '',
}: {
  title: string
  netLabel: string
  leg: TradeLegFee
  testId: string
  padClass?: string
}) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const feeUnknown = leg.fee_known === false
  return (
    <div data-testid={testId} className={`space-y-2 ${padClass}`}>
      <div>
        <div className="font-semibold text-[13px] text-[var(--fg)]">{title}</div>
        <div className="text-[12px] text-[var(--fg-2)] truncate">
          {feeUnknown ? t('leg_fee_unknown') : `${modeLabel(leg.mode_code, t)} · ${leg.fee_bps.toFixed(1)} bps`}
        </div>
        <div className="text-[12px] text-[var(--fg-3)] whitespace-nowrap">
          {leg.time_str} {leg.avg_price > 0 ? `· ${fmtPrice(leg.avg_price)}` : ''}
        </div>
      </div>

      <div className="pt-2 border-t border-[var(--border)] space-y-1 text-[12px]">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--fg-3)]">{t('leg_gross_fee')}</span>
          <span className={`whitespace-nowrap sensitive ${feeUnknown ? 'text-[var(--fg-3)]' : ''}`}>
            {feeUnknown ? t('leg_fee_unknown') : leg.gross_fee_usd > 0 ? `-${cur.money(leg.gross_fee_usd, 3)}` : cur.money(0, 3)}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--fg-3)]">{t('leg_rebate_disc')}</span>
          <span className={`whitespace-nowrap sensitive ${!feeUnknown && leg.rebate_usd > 0 ? 'txt-pos' : 'text-[var(--fg-3)]'}`}>
            {feeUnknown
              ? '—'
              : leg.gross_fee_usd <= 0
                ? t('leg_zero_fee')
                : leg.rebate_usd > 0
                  ? `+${cur.money(leg.rebate_usd, 3)} (${leg.rebate_pct.toFixed(0)}%)`
                  : cur.money(0, 3)}
          </span>
        </div>
        {!feeUnknown && leg.discount_saved_usd > 0.0001 && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[var(--fg-3)]">
              {locale === 'en' ? 'Pre-discount' : '抵扣已省'}
            </span>
            <span className="txt-pos sensitive whitespace-nowrap">
              +{cur.money(leg.discount_saved_usd, 3)}
            </span>
          </div>
        )}
        <div className="flex items-center justify-between gap-2 font-semibold pt-1">
          <span className="text-[var(--fg-2)]">{netLabel}</span>
          <span className={`whitespace-nowrap sensitive ${feeUnknown ? 'text-[var(--fg-3)]' : ''}`}>
            {feeUnknown ? '—' : leg.net_fee_usd > 0 ? `-${cur.money(leg.net_fee_usd, 3)}` : cur.money(0, 3)}
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
}: {
  data: DashboardData
  venueFilter: string
  onVenueChange: (v: string) => void
  symbolFilter: string
  onSymbolChange: (s: string) => void
  onSaveJournal: (tradeId: string, tags: string[], note: string) => Promise<void>
  onInspectSymbol?: (venueId: string, symbol: string, venueName: string, rangeKey: string) => void
}) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const [detailed, setDetailed] = useState(true)
  const [sortBy, setSortBy] = useState<'CLOSE_DESC' | 'OPEN_DESC' | 'PNL_DESC' | 'PNL_ASC'>('CLOSE_DESC')
  const [selectedId, setSelectedId] = useState<string>('')
  const [swapDir, setSwapDir] = useState<'next' | 'prev'>('next')
  const [draftMap, setDraftMap] = useState<Record<string, { tags: string[]; note: string }>>({})
  const [saving, setSaving] = useState(false)
  const [savedAck, setSavedAck] = useState(false)
  const tableWrapRef = useRef<HTMLDivElement | null>(null)
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({})
  const [rowGlider, setRowGlider] = useState<{ top: number; height: number; visible: boolean }>({
    top: 0,
    height: 0,
    visible: false,
  })

  useEffect(() => {
    setSelectedId('')
  }, [venueFilter, symbolFilter])

  const sortedTrades = useMemo(() => {
    const arr = [...data.trades]
    arr.sort((a, b) => {
      if (sortBy === 'OPEN_DESC') return b.open_time_ms - a.open_time_ms
      if (sortBy === 'PNL_DESC') return b.net_pnl_usd - a.net_pnl_usd
      if (sortBy === 'PNL_ASC') return a.net_pnl_usd - b.net_pnl_usd
      return b.close_time_ms - a.close_time_ms
    })
    return arr
  }, [data.trades, sortBy])

  const selected: ClosedTrade | null = useMemo(() => {
    if (!sortedTrades.length) return null
    return sortedTrades.find(tr => tr.trade_id === selectedId) ?? sortedTrades[0]
  }, [sortedTrades, selectedId])

  const selectedIndex = selected ? sortedTrades.indexOf(selected) : -1
  const currentDraft = selected ? draftMap[selected.trade_id] : undefined
  const draftTags = currentDraft ? currentDraft.tags : (selected?.tags ?? [])
  const draftNote = currentDraft ? currentDraft.note : (selected?.note ?? '')

  useEffect(() => {
    if (selected) {
      setSavedAck(false)
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
    if (!selected) return
    setSaving(true)
    try {
      await onSaveJournal(selected.trade_id, draftTags, draftNote)
      setDraftMap(prev => {
        const next = { ...prev }
        delete next[selected.trade_id]
        return next
      })
      setSavedAck(true)
      setTimeout(() => setSavedAck(false), 2000)
    } finally {
      setSaving(false)
    }
  }

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
      <div className="grid grid-cols-1 xl:grid-cols-12 border-b border-[var(--border)] items-start">
        <div
          className="xl:col-span-7 xl:pr-4 xl:border-r border-[var(--border)]"
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
                          <div className="flex items-center gap-2">
                            <InstrumentMark instrument={tr.symbol} exchange={tr.venue_id.toUpperCase()} />
                            <strong className="text-[13px] font-semibold whitespace-nowrap">{tr.symbol}</strong>
                            <span
                              className={`text-[12px] font-semibold whitespace-nowrap ${
                                tr.side === 'LONG' ? 'txt-pos' : 'txt-neg'
                              }`}
                            >
                              {tr.side === 'LONG' ? t('side_long') : t('side_short')}
                            </span>
                            {tr.is_partial_close && (
                              <span className="text-[12px] text-[var(--fg-3)] whitespace-nowrap">
                                · {t('partial_badge')}
                              </span>
                            )}
                          </div>
                          <div className="text-[12px] text-[var(--fg-3)] mt-0.5 whitespace-nowrap">
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
                          <td className="hidden sm:table-cell text-right whitespace-nowrap">
                            <div className="text-[12.5px] font-medium sensitive">
                              {t('fee_net_short')}{' '}
                              {tr.net_fee_usd > 0 ? `-${cur.money(tr.net_fee_usd, 2)}` : cur.money(0, 2)}
                              {tr.effective_relief_pct > 0 && (
                                <span className="txt-pos ml-1">
                                  (-{tr.effective_relief_pct.toFixed(0)}%)
                                </span>
                              )}
                            </div>
                            <div className="text-[12px] text-[var(--fg-3)] sensitive">
                              {tr.entry_leg.fee_known === false || tr.exit_leg.fee_known === false
                                ? t('leg_fee_unknown')
                                : `${t('fee_entry_short')} -${cur.plainMoney(tr.entry_leg.gross_fee_usd, 2)} · ${t('fee_exit_short')} -${cur.plainMoney(tr.exit_leg.gross_fee_usd, 2)}`}
                            </div>
                          </td>
                        )}
                        <td className="text-right whitespace-nowrap">
                          <div className={`text-[13px] font-semibold sensitive ${pnlTone(tr.net_pnl_usd)}`}>
                            {cur.signedPlainMoney(tr.net_pnl_usd)}
                          </div>
                          <div className="text-[12px] text-[var(--fg-3)] sensitive">
                            {locale === 'en' ? 'Pre-rebate ' : '返佣前 '}
                            {cur.signedPlainMoney(tr.book_pnl_usd)}
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

        {/* Right Settlement Inspector */}
        <aside
          data-testid="trade-detail-panel"
          className="xl:col-span-5 py-5 px-5 xl:pl-7 xl:pr-5 border-t xl:border-t-0 border-[var(--border)] flex flex-col gap-5 xl:sticky xl:top-[68px]"
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
            <>
              <div
                key={selected.trade_id}
                className={`${swapDir === 'prev' ? 'otl-swap-prev' : 'otl-swap-next'} flex flex-col`}
              >
                <div className="flex items-start justify-between gap-3 pb-3.5 border-b border-[var(--border)]">
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
                    </div>
                    <div className="text-[12px] text-[var(--fg-3)] mt-1">
                      {selected.qty > 0 ? `${selected.qty} ${selected.symbol.split(/[_/-]/)[0]} · ` : ''}
                      {fmtDuration(selected.holding_sec, locale)}
                      {selected.effective_relief_pct > 0
                        ? ` · ${t('detail_relief_short')} ${selected.effective_relief_pct.toFixed(1)}%`
                        : ''}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={`t-kpi sensitive whitespace-nowrap ${pnlTone(selected.net_pnl_usd)}`}>
                      {formatDisplayKpiValue(cur.signedMoney(selected.net_pnl_usd))}
                    </div>
                    <div className="text-[12px] text-[var(--fg-3)] mt-0.5">
                      {selected.rebate_status === 'PENDING' ? t('detail_net_pending') : t('detail_net_after_rebate')}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 divide-y sm:divide-y-0 sm:divide-x divide-[var(--border)] py-3.5 border-b border-[var(--border)] gap-3 sm:gap-0">
                  <LegFeeCard
                    testId="entry-leg-fee-card"
                    title={t('leg_entry')}
                    netLabel={t('leg_entry_net')}
                    leg={selected.entry_leg}
                    padClass="sm:pr-4"
                  />
                  <LegFeeCard
                    testId="exit-leg-fee-card"
                    title={t('leg_exit')}
                    netLabel={t('leg_exit_net')}
                    leg={selected.exit_leg}
                    padClass="pt-3 sm:pt-0 sm:pl-4"
                  />
                </div>

                <div className="pt-3.5 space-y-1.5 text-[12.5px]">
                  <div className="flex justify-between">
                    <span className="text-[var(--fg-2)]">{t('wf_gross_pnl')}</span>
                    <span className={`font-medium sensitive ${pnlTone(selected.gross_pnl_usd)}`}>
                      {cur.signedMoney(selected.gross_pnl_usd)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--fg-2)]">{t('wf_funding')}</span>
                    <span className="sensitive">
                      {Math.abs(selected.funding_pnl_usd) > 0.0001
                        ? cur.signedMoney(selected.funding_pnl_usd)
                        : cur.money(0)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--fg-2)]">{t('wf_total_fee')}</span>
                    <span className="sensitive">-{cur.money(selected.total_fee_usd, 3)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--fg-2)]">{t('wf_total_rebate')}</span>
                    <span className="txt-pos sensitive">+{cur.money(selected.total_rebate_usd, 3)}</span>
                  </div>
                  <div className="flex justify-between pt-2 border-t border-[var(--border)] text-[13px] font-semibold">
                    <span>{t('wf_final_net')}</span>
                    <span className={`sensitive ${pnlTone(selected.net_pnl_usd)}`}>
                      {cur.signedMoney(selected.net_pnl_usd)}
                    </span>
                  </div>
                  {selected.total_discount_saved_usd > 0.0001 && (
                    <div className="flex justify-between text-[12px] text-[var(--fg-3)]">
                      <span>
                        {locale === 'en'
                          ? 'Pre-deduction token fee discount already saved'
                          : '注：扣费前平台币抵扣已另省（不重复计入返佣）'}
                      </span>
                      <span className="txt-pos sensitive">+{cur.money(selected.total_discount_saved_usd, 3)}</span>
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
                    disabled={saving}
                    onClick={() => void handleSave()}
                    className="otl-btn h-7 px-2.5"
                  >
                    {savedAck && <Check size={12} className="txt-pos" />}
                    <span>{saving ? t('journal_saving') : savedAck ? t('journal_saved') : t('journal_save')}</span>
                  </button>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {TAG_KEYS.map(tk => {
                    const on = draftTags.includes(tk.id)
                    return (
                      <button
                        key={tk.id}
                        type="button"
                        onClick={() => {
                          const nextTags = draftTags.includes(tk.id)
                            ? draftTags.filter(x => x !== tk.id)
                            : [...draftTags, tk.id]
                          setDraftMap(prev => ({
                            ...prev,
                            [selected.trade_id]: { tags: nextTags, note: draftNote },
                          }))
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
                    const val = e.target.value
                    setDraftMap(prev => ({
                      ...prev,
                      [selected.trade_id]: { tags: draftTags, note: val },
                    }))
                  }}
                  placeholder={t('journal_placeholder')}
                  className="otl-input w-full h-auto py-2 placeholder:text-[var(--fg-3)]"
                />
              </div>
            </>
          )}
        </aside>
      </div>

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
                      <td className="text-right whitespace-nowrap sensitive">{row.trade_count} / {row.qty}</td>
                      <td className="text-right whitespace-nowrap text-[var(--fg-2)]">
                        {row.trade_count ? `${row.maker_bps.toFixed(1)} / ${row.taker_bps.toFixed(1)}` : '— / —'}
                      </td>
                      <td className="text-right whitespace-nowrap sensitive">
                        {cur.signedPlainMoney(-row.gross_fee_usd, 4)} /{' '}
                        <span className="txt-pos">{cur.signedPlainMoney(row.rebate_usd, 4)}</span>
                      </td>
                      <td className="text-right whitespace-nowrap sensitive">{cur.money(row.net_fee_usd, 4)}</td>
                      <td className={`text-right font-semibold whitespace-nowrap sensitive ${pnlTone(row.net_pnl_usd)}`}>
                        {cur.signedMoney(row.net_pnl_usd)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
