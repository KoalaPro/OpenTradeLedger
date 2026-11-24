import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Check } from 'lucide-react'
import { InstrumentMark } from '../components/InstrumentMark'
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
  }
  return known[code] ? t(known[code]) : code
}

function LegFeeCard({
  title,
  netLabel,
  leg,
  testId,
}: {
  title: string
  netLabel: string
  leg: TradeLegFee
  testId: string
}) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  return (
    <div data-testid={testId} className="otl-surface-2 rounded-xl p-3.5 flex flex-col gap-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-[var(--surface)] border border-[var(--border)] text-[var(--fg)] whitespace-nowrap">
            {title}
          </span>
          <span className="text-[12.5px] font-medium text-[var(--fg)] truncate">
            {modeLabel(leg.mode_code, t)} · {leg.fee_bps.toFixed(1)} bps
          </span>
        </div>
        <span className="text-[11.5px] text-[var(--fg-3)] whitespace-nowrap">
          {leg.time_str} {leg.avg_price > 0 ? `· ${fmtPrice(leg.avg_price)}` : ''}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 pt-2 border-t border-[var(--border)] text-[12px]">
        <div>
          <div className="text-[11px] text-[var(--fg-3)]">{t('leg_gross_fee')}</div>
          <div className="font-semibold whitespace-nowrap sensitive mt-0.5">
            {leg.gross_fee_usd > 0 ? `-${cur.money(leg.gross_fee_usd, 3)}` : cur.money(0, 3)}
          </div>
        </div>
        <div>
          <div className="text-[11px] text-[var(--fg-3)]">{t('leg_rebate_disc')}</div>
          <div className={`font-semibold whitespace-nowrap sensitive mt-0.5 ${leg.rebate_usd > 0 ? 'txt-pos' : 'text-[var(--fg-3)]'}`}>
            {leg.gross_fee_usd <= 0
              ? t('leg_zero_fee')
              : leg.rebate_usd > 0
                ? `+${cur.money(leg.rebate_usd, 3)} (${leg.rebate_pct.toFixed(0)}%)`
                : cur.money(0, 3)}
          </div>
          {leg.discount_saved_usd > 0.0001 && (
            <div className="text-[10.5px] txt-pos mt-0.5 sensitive whitespace-nowrap">
              {locale === 'en'
                ? `Pre-disc +${cur.money(leg.discount_saved_usd, 3)}`
                : `抵扣已省 +${cur.money(leg.discount_saved_usd, 3)}`}
            </div>
          )}
        </div>
        <div className="text-right">
          <div className="text-[11px] text-[var(--fg-3)]">{netLabel}</div>
          <div className="font-semibold whitespace-nowrap sensitive mt-0.5">
            {leg.net_fee_usd > 0 ? `-${cur.money(leg.net_fee_usd, 3)}` : cur.money(0, 3)}
          </div>
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
}: {
  data: DashboardData
  venueFilter: string
  onVenueChange: (v: string) => void
  symbolFilter: string
  onSymbolChange: (s: string) => void
  onSaveJournal: (tradeId: string, tags: string[], note: string) => Promise<void>
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
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({})

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

  return (
    <div data-testid="trades-page" className="otl-stagger space-y-4">
      {/* Filter Toolbar — fully glider-backed venue & view density pills */}
      <div className="flex flex-wrap items-center justify-between gap-2.5">
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
            className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] text-[var(--fg)] cursor-pointer"
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
              className="otl-panel-swap h-8 px-2.5 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] text-[11.5px] text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer whitespace-nowrap"
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
            className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] text-[var(--fg)] cursor-pointer"
          >
            <option value="CLOSE_DESC">{t('sort_close_desc')}</option>
            <option value="OPEN_DESC">{t('sort_open_desc')}</option>
            <option value="PNL_DESC">{t('sort_pnl_desc')}</option>
            <option value="PNL_ASC">{t('sort_pnl_asc')}</option>
          </select>
        </div>
      </div>

      {/* Main Split: Trade List + Per-Leg Settlement Inspector */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
        <div
          className="xl:col-span-7 otl-card overflow-hidden"
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
            <div className="max-h-[72vh] overflow-y-auto">
              <table className="otl-table">
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
                        className={`clickable ${active ? 'selected' : ''}`}
                        onClick={() => selectTradeAt(tr.trade_id, idx)}
                      >
                        <td>
                          <div className="flex items-center gap-2">
                            <InstrumentMark instrument={tr.symbol} exchange={tr.venue_id.toUpperCase()} />
                            <strong className="text-[13px] whitespace-nowrap">{tr.symbol}</strong>
                            <span
                              className="px-1.5 py-0.5 rounded text-[10.5px] font-semibold whitespace-nowrap"
                              style={{
                                background: tr.side === 'LONG' ? 'var(--pos-soft)' : 'var(--neg-soft)',
                                color: tr.side === 'LONG' ? 'var(--pos)' : 'var(--neg)',
                              }}
                            >
                              {tr.side === 'LONG' ? t('side_long') : t('side_short')}
                            </span>
                            {tr.is_partial_close && (
                              <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--surface-2)] text-[var(--fg-3)] border border-[var(--border)] whitespace-nowrap">
                                {t('partial_badge')}
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-[var(--fg-3)] mt-0.5 whitespace-nowrap">
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
                            <div className="text-[12px] font-medium sensitive">
                              {t('fee_net_short')}{' '}
                              {tr.net_fee_usd > 0 ? `-${cur.money(tr.net_fee_usd, 2)}` : cur.money(0, 2)}
                              {tr.effective_relief_pct > 0 && (
                                <span className="txt-pos ml-1 text-[11px]">
                                  (-{tr.effective_relief_pct.toFixed(0)}%)
                                </span>
                              )}
                            </div>
                            <div className="text-[11px] text-[var(--fg-3)] sensitive">
                              {t('fee_entry_short')} -{cur.plainMoney(tr.entry_leg.gross_fee_usd, 2)} ·{' '}
                              {t('fee_exit_short')} -{cur.plainMoney(tr.exit_leg.gross_fee_usd, 2)}
                            </div>
                          </td>
                        )}
                        <td className="text-right whitespace-nowrap">
                          <div className={`text-[14px] font-semibold sensitive ${pnlTone(tr.net_pnl_usd)}`}>
                            {cur.signedPlainMoney(tr.net_pnl_usd)}
                          </div>
                          <div className="text-[11px] text-[var(--fg-3)] sensitive">
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
        <aside data-testid="trade-detail-panel" className="xl:col-span-5 otl-card p-5 flex flex-col gap-4 xl:sticky xl:top-4">
          <div className="flex items-center justify-between gap-2">
            <h2 className="m-0 text-[14.5px] font-semibold text-[var(--fg)]">{t('detail_title')}</h2>
            {sortedTrades.length > 0 && (
              <div className="flex items-center gap-1.5 text-[11.5px] text-[var(--fg-3)]">
                <span className="tabular-nums">
                  {selectedIndex + 1} / {sortedTrades.length}
                </span>
                <button
                  type="button"
                  disabled={selectedIndex <= 0}
                  onClick={() => step(-1)}
                  className="p-1 rounded border border-[var(--border)] bg-[var(--surface-2)] hover:bg-[var(--surface-hover)] disabled:opacity-35 cursor-pointer"
                  aria-label="Previous"
                >
                  <ArrowUp size={13} />
                </button>
                <button
                  type="button"
                  disabled={selectedIndex < 0 || selectedIndex + 1 >= sortedTrades.length}
                  onClick={() => step(1)}
                  className="p-1 rounded border border-[var(--border)] bg-[var(--surface-2)] hover:bg-[var(--surface-hover)] disabled:opacity-35 cursor-pointer"
                  aria-label="Next"
                >
                  <ArrowDown size={13} />
                </button>
              </div>
            )}
          </div>

          {!selected ? (
            <div className="py-12 text-center text-[12.5px] text-[var(--fg-3)]">{t('empty_trades')}</div>
          ) : (
            <>
              <div
                key={selected.trade_id}
                className={`${swapDir === 'prev' ? 'otl-swap-prev' : 'otl-swap-next'} flex flex-col gap-4`}
              >
                <div className="flex items-start justify-between gap-3 pb-3 border-b border-[var(--border)]">
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <VenueBadge
                        venueId={selected.venue_id}
                        name={locale === 'en' ? selected.venue_name : selected.venue_name_zh || selected.venue_name}
                      />
                      <strong className="text-[15.5px]">{selected.symbol}</strong>
                      <span
                        className="px-2 py-0.5 rounded-md text-[11px] font-semibold"
                        style={{
                          background: selected.side === 'LONG' ? 'var(--pos-soft)' : 'var(--neg-soft)',
                          color: selected.side === 'LONG' ? 'var(--pos)' : 'var(--neg)',
                        }}
                      >
                        {selected.side === 'LONG' ? t('side_long') : t('side_short')}
                      </span>
                    </div>
                    <div className="text-[12px] text-[var(--fg-3)] mt-1">
                      {selected.qty > 0
                        ? `${selected.qty} ${selected.symbol.split(/[_/-]/)[0]} · `
                        : ''}
                      {fmtDuration(selected.holding_sec, locale)}
                      {selected.effective_relief_pct > 0
                        ? ` · ${t('detail_relief_short')} ${selected.effective_relief_pct.toFixed(1)}%`
                        : ''}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={`text-[24px] font-bold tracking-tight leading-none sensitive ${pnlTone(selected.net_pnl_usd)}`}>
                      {cur.signedMoney(selected.net_pnl_usd)}
                    </div>
                    <div className="text-[11px] text-[var(--fg-3)] mt-1">
                      {selected.rebate_status === 'PENDING' ? t('detail_net_pending') : t('detail_net_after_rebate')}
                    </div>
                  </div>
                </div>

                {/* Mandatory Per-Leg Entry & Exit Fee Cards */}
                <LegFeeCard
                  testId="entry-leg-fee-card"
                  title={t('leg_entry')}
                  netLabel={t('leg_entry_net')}
                  leg={selected.entry_leg}
                />
                <LegFeeCard
                  testId="exit-leg-fee-card"
                  title={t('leg_exit')}
                  netLabel={t('leg_exit_net')}
                  leg={selected.exit_leg}
                />

                {/* Clean Reconciliation Waterfall */}
                <div className="space-y-1.5 text-[12.5px]">
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
                    <span className="txt-pos sensitive">
                      +{cur.money(selected.total_rebate_usd, 3)}
                    </span>
                  </div>
                  <div className="flex justify-between pt-2 border-t border-[var(--border)] font-semibold">
                    <span>{t('wf_final_net')}</span>
                    <span className={`sensitive ${pnlTone(selected.net_pnl_usd)}`}>
                      {cur.signedMoney(selected.net_pnl_usd)}
                    </span>
                  </div>
                  {selected.total_discount_saved_usd > 0.0001 && (
                    <div className="flex justify-between text-[11px] text-[var(--fg-3)]">
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

              {/* Generic Journal Tags & Notes (kept outside key={selected.trade_id} to preserve DOM focus & synchronous drafts!) */}
              <div className="pt-3 border-t border-[var(--border)] space-y-2.5">
                <div className="flex items-center justify-between">
                  <span className="text-[12px] font-medium text-[var(--fg-2)]">{t('journal_title')}</span>
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => void handleSave()}
                    className="inline-flex items-center gap-1 h-7 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[11.5px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer"
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
                        className="px-2.5 py-1 rounded-md text-[11px] font-medium border cursor-pointer"
                        style={{
                          background: on ? 'var(--accent-soft)' : 'var(--surface-2)',
                          borderColor: on ? 'var(--accent)' : 'var(--border)',
                          color: on ? 'var(--accent)' : 'var(--fg-2)',
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
                  className="w-full rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] px-3 py-2 text-[12px] text-[var(--fg)] placeholder:text-[var(--fg-3)]"
                />
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  )
}
