import { useDialogFocus } from './useDialogFocus'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ArrowUpRight, ChevronDown, X } from 'lucide-react'
import { useI18n } from '../i18n'
import type { MessageKey } from '../i18n/zh-CN'
import { api } from '../lib/api'
import { useCurrency } from '../lib/currency'
import { fmtContractQty, fmtLegRate, fmtDuration, fmtPrice, fmtSignedPct, pnlTone } from '../lib/format'
import type { ClosedTrade, OpenPosition, TradeExitBatch, TradeLegFee } from '../lib/types'
import { AnimatedCollapse } from './AnimatedCollapse'
import { InstrumentMark } from './InstrumentMark'
import { VenueBadge } from './VenueBadge'

interface TradeDrilldownDrawerProps {
  venueId: string
  venueName?: string
  symbol: string
  rangeKey: string
  open: boolean
  onClose: () => void
  onOpenInTrades: (venueId: string, symbol: string, effectiveRangeKey?: string) => void
}

function modeText(code: string, t: (k: MessageKey) => string): string {
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

function batchRebatePct(b: TradeExitBatch): number {
  if (b.rebate_pct != null && b.rebate_pct > 0) return b.rebate_pct
  return b.gross_fee_usd > 0 ? (b.rebate_usd / b.gross_fee_usd) * 100 : 0
}

function CompactLegRow({
  title,
  netLabel,
  leg,
}: {
  title: string
  netLabel: string
  leg: TradeLegFee
}) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const feeUnknown = leg.fee_known === false
  return (
    <div className="py-2.5 border-t border-[var(--border)] space-y-1.5 text-[12px]">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-semibold text-[var(--fg-2)]">{title}</span>
          <span className="text-[var(--fg)] truncate">
            · {feeUnknown ? t('leg_fee_unknown') : `${modeText(leg.mode_code, t)} · ${fmtLegRate(leg.fee_bps, locale)}`}
          </span>
        </div>
        <span className="text-[var(--fg-3)] whitespace-nowrap">
          {leg.time_str}
          {leg.avg_price > 0 && (
            <>
              {' · '}
              <span className="sensitive">{fmtPrice(leg.avg_price)}</span>
            </>
          )}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <div>
          <div className="text-[var(--fg-3)]">{t('leg_gross_fee')}</div>
          <div className={`font-semibold sensitive ${feeUnknown ? 'text-[var(--fg-3)]' : ''}`}>
            {feeUnknown ? t('leg_fee_unknown') : leg.gross_fee_usd > 0 ? `-${cur.money(leg.gross_fee_usd, 3)}` : cur.money(0, 3)}
          </div>
        </div>
        <div>
          <div className="text-[var(--fg-3)]">{t('leg_rebate_disc')}</div>
          <div className={`font-semibold sensitive ${!feeUnknown && leg.rebate_usd > 0 ? 'txt-pos' : 'text-[var(--fg-3)]'}`}>
            {feeUnknown
              ? '—'
              : leg.rebate_usd > 0
                ? `+${cur.money(leg.rebate_usd, 3)}${leg.gross_fee_usd > 0 ? ` (${leg.rebate_pct.toFixed(0)}%)` : ''}`
                : leg.gross_fee_usd <= 0 ? t('leg_zero_fee') : cur.money(0, 3)}
          </div>
          {!feeUnknown && leg.discount_saved_usd > 0.0001 && (
            <div className="txt-pos mt-0.5 sensitive whitespace-nowrap">
              {locale === 'en'
                ? `Pre-disc +${cur.money(leg.discount_saved_usd, 3)}`
                : `抵扣已省 +${cur.money(leg.discount_saved_usd, 3)}`}
            </div>
          )}
        </div>
        <div className="text-right">
          <div className="text-[var(--fg-3)]">{netLabel}</div>
          <div className={`font-semibold sensitive ${feeUnknown ? 'text-[var(--fg-3)]' : ''}`}>
            {feeUnknown ? '—' : cur.signedMoney(-leg.net_fee_usd, 3)}
          </div>
        </div>
      </div>
    </div>
  )
}

function CompactMultiBatchLedger({
  trade,
  batches,
}: {
  trade: ClosedTrade
  batches: TradeExitBatch[]
}) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const enLeg = trade.entry_leg
  const enFeeUnknown = enLeg.fee_known === false

  return (
    <div className="divide-y divide-[var(--border)] border-t border-[var(--border)] tabular-nums text-[12px]">
      <div className="py-2.5 flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-0.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-[var(--fg)] whitespace-nowrap">
              {locale === 'en' ? 'Entry' : '开仓进场'}
            </span>
            {(trade.qty != null || trade.contract_count != null) && (
              <span className="text-[var(--fg)] sensitive whitespace-nowrap">
                {fmtContractQty(trade, locale)}
              </span>
            )}
            {enLeg.avg_price > 0 && (
              <span className="text-[var(--fg)] whitespace-nowrap">
                @ <span className="sensitive">{fmtPrice(enLeg.avg_price)}</span>
              </span>
            )}
          </div>
          <div className="text-[var(--fg-3)]">
            {enLeg.time_str} ·{' '}
            {enFeeUnknown
              ? t('leg_fee_unknown')
              : `${modeText(enLeg.mode_code, t)} · ${fmtLegRate(enLeg.fee_bps, locale)}`}
          </div>
        </div>
        <div className="text-right shrink-0 space-y-0.5">
          <div className="text-[var(--fg-2)] whitespace-nowrap sensitive">
            {enFeeUnknown ? (
              <span className="text-[var(--fg-3)]">{t('leg_fee_unknown')}</span>
            ) : (
              <>
                {locale === 'en' ? 'Gross ' : '先扣 '}
                {enLeg.gross_fee_usd > 0 ? `-${cur.plainMoney(enLeg.gross_fee_usd, 4)}` : cur.plainMoney(0, 4)}
                {enLeg.rebate_usd > 0 && (
                  <>
                    {' · '}
                    <span className="txt-pos">
                      {locale === 'en' ? 'Reb ' : '返 '}+{cur.plainMoney(enLeg.rebate_usd, 4)}
                      {enLeg.gross_fee_usd > 0 ? ` (${enLeg.rebate_pct.toFixed(0)}%)` : ''}
                    </span>
                  </>
                )}
              </>
            )}
          </div>
          <div className="font-semibold text-[var(--fg)] whitespace-nowrap sensitive">
            {t('leg_entry_net')} {enFeeUnknown ? '—' : cur.signedMoney(-enLeg.net_fee_usd, 4)}
          </div>
        </div>
      </div>

      {batches.map(b => {
        const bFeeUnknown = b.fee_known === false
        const rebPct = batchRebatePct(b)
        return (
          <div key={b.batch_index} className="py-2.5 flex items-start justify-between gap-3">
            <div className="min-w-0 space-y-0.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-semibold text-[var(--fg)] whitespace-nowrap">
                  {locale === 'en' ? `Exit #${b.batch_index}` : `平仓第 ${b.batch_index} 笔`}
                </span>
                {(b.qty != null || b.contract_count != null) && (
                  <span className="text-[var(--fg)] sensitive whitespace-nowrap">
                    {fmtContractQty(
                      { symbol: trade.symbol, qty: b.qty, contract_count: b.contract_count },
                      locale,
                    )}
                  </span>
                )}
                {b.avg_price > 0 && (
                  <span className="text-[var(--fg)] whitespace-nowrap">
                    @ <span className="sensitive">{fmtPrice(b.avg_price)}</span>
                  </span>
                )}
                <span className={`font-medium whitespace-nowrap sensitive ${pnlTone(b.gross_pnl_usd)}`}>
                  {locale === 'en' ? 'Spread ' : '价差 '}
                  {cur.signedMoney(b.gross_pnl_usd)}
                </span>
              </div>
              <div className="text-[var(--fg-3)]">
                {b.time_str} ·{' '}
                <span className="font-medium text-[var(--fg-2)]">
                  {bFeeUnknown ? t('leg_fee_unknown') : modeText(b.mode_code, t)}
                </span>
                {!bFeeUnknown && b.fee_bps != null ? ` · ${fmtLegRate(b.fee_bps, locale)}` : ''}
              </div>
            </div>
            <div className="text-right shrink-0 space-y-0.5">
              <div className="text-[var(--fg-2)] whitespace-nowrap sensitive">
                {bFeeUnknown ? (
                  <span className="text-[var(--fg-3)]">{t('leg_fee_unknown')}</span>
                ) : (
                  <>
                    {locale === 'en' ? 'Gross ' : '先扣 '}
                    {b.gross_fee_usd > 0 ? `-${cur.plainMoney(b.gross_fee_usd, 4)}` : cur.plainMoney(0, 4)}
                    {b.rebate_usd > 0 && (
                      <>
                        {' · '}
                        <span className="txt-pos">
                          {locale === 'en' ? 'Reb ' : '返 '}+{cur.plainMoney(b.rebate_usd, 4)}
                          {b.gross_fee_usd > 0 ? ` (${rebPct.toFixed(0)}%)` : ''}
                        </span>
                      </>
                    )}
                  </>
                )}
              </div>
              <div className="font-semibold text-[var(--fg)] whitespace-nowrap sensitive">
                {t('leg_exit_net')} {bFeeUnknown ? '—' : cur.signedMoney(-b.net_fee_usd, 4)}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function TradeDrilldownDrawer({
  venueId,
  venueName,
  symbol,
  rangeKey,
  open,
  onClose,
  onOpenInTrades,
}: TradeDrilldownDrawerProps) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const [rendered, setRendered] = useState(open)
  const [closing, setClosing] = useState(false)
  const closeTimerRef = useRef<number | null>(null)

  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [trades, setTrades] = useState<ClosedTrade[]>([])
  const [livePos, setLivePos] = useState<OpenPosition | null>(null)
  const [expandedId, setExpandedId] = useState<string>('')
  const [fellBackToAll, setFellBackToAll] = useState(false)

  useEffect(() => {
    if (open) {
      if (closeTimerRef.current !== null) {
        window.clearTimeout(closeTimerRef.current)
        closeTimerRef.current = null
      }
      setClosing(false)
      setRendered(true)
    } else if (rendered) {
      const reduced =
        typeof window !== 'undefined' &&
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches
      if (reduced) {
        setRendered(false)
        setClosing(false)
        return
      }
      setClosing(true)
      closeTimerRef.current = window.setTimeout(() => {
        setRendered(false)
        setClosing(false)
        closeTimerRef.current = null
      }, 180)
    }
  }, [open, rendered])

  useEffect(() => {
    return () => {
      if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current)
    }
  }, [])

  const dialogRef = useDialogFocus(open, onClose)

  useEffect(() => {
    if (!open || !symbol) return
    let alive = true
    setLoading(true)
    setLoadFailed(false)
    setFellBackToAll(false)
    setTrades([])
    setLivePos(null)
    api
      .getDashboard(rangeKey, venueId, symbol)
      .then(async dash => {
        if (!alive) return
        const matchedPos =
          (dash.open_positions || []).find(
            p =>
              p.venue_id.toLowerCase() === venueId.toLowerCase() &&
              p.symbol.toUpperCase() === symbol.toUpperCase(),
          ) || null
        setLivePos(matchedPos)
        if (dash.trades.length === 0 && rangeKey !== 'all') {
          const allDash = await api.getDashboard('all', venueId, symbol)
          if (!alive) return
          setTrades(allDash.trades)
          setFellBackToAll(allDash.trades.length > 0)
          setExpandedId(allDash.trades[0]?.trade_id ?? '')
          return
        }
        setTrades(dash.trades)
        setExpandedId(dash.trades[0]?.trade_id ?? '')
      })
      .catch(() => {
        if (alive) {
          setLoadFailed(true)
        }
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [open, venueId, symbol, rangeKey, attempt])

  const shouldRender = open || rendered
  if (!shouldRender) return null

  const isClosingVisual = !open || closing
  const totalNet = trades.reduce((a, b) => a + b.net_pnl_usd, 0)
  const totalFee = trades.reduce((a, b) => a + b.total_fee_usd, 0)
  const totalReb = trades.reduce((a, b) => a + b.total_rebate_usd + b.total_discount_saved_usd, 0)
  const wins = trades.filter(tr => tr.net_pnl_usd > 0).length
  const winRate = trades.length ? (wins / trades.length) * 100 : 0

  const drawerNode = (
    <div
      data-testid="trade-drilldown-drawer"
      ref={dialogRef}
      inert={!open}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-label={`${symbol} · ${venueName || venueId.toUpperCase()}`}
      onClick={e => {
        if (e.target === e.currentTarget) onClose()
      }}
      className={`otl-drawer-backdrop fixed inset-0 z-50 flex justify-end ${
        isClosingVisual ? 'closing' : ''
      }`}
    >
      <div className="otl-drawer-panel w-full max-w-[520px] h-full flex flex-col overflow-hidden">
        <div className="px-6 py-4 border-b border-[var(--border)] flex items-center justify-between gap-3 shrink-0">
          <div className="min-w-0">
            <div className="t-h2 inline-flex items-center gap-2.5 flex-wrap">
              <InstrumentMark instrument={symbol} exchange={venueId.toUpperCase()} />
              <span>{symbol}</span>
              <VenueBadge venueId={venueId} name={venueName || venueId.toUpperCase()} />
            </div>
            {trades.length > 0 && (
              <div className="text-[12px] text-[var(--fg-3)] mt-0.5 truncate">
                {trades.length} {locale === 'en' ? 'trades' : '笔交割'} · {t('kpi_winrate')}{' '}
                {winRate.toFixed(1)}%
              </div>
            )}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {trades.length > 0 && (
              <button
                type="button"
                onClick={() => onOpenInTrades(venueId, symbol, fellBackToAll ? 'all' : undefined)}
                className="otl-btn h-7 px-2.5"
              >
                <span>{locale === 'en' ? 'Open in Trades' : '去合约页查看'}</span>
                <ArrowUpRight size={12} />
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label={locale === 'en' ? 'Close' : '关闭'}
              title={locale === 'en' ? 'Close' : '关闭'}
              className="otl-btn h-7 w-7 px-0 text-[var(--fg-2)] hover:text-[var(--fg)]"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {fellBackToAll && trades.length > 0 && (
            <div
              data-testid="drilldown-fallback-banner"
              className="otl-status-warn mx-6 mt-3 px-3 py-2 rounded-md text-[12px] font-medium"
            >
              {locale === 'en'
                ? 'No closed trades in the selected period · showing all-time history instead'
                : '当前所选周期无交割记录，已为您展示全部历史记录'}
            </div>
          )}

          {livePos && (
            <div className="px-6 py-3 border-b border-[var(--border)] flex flex-wrap items-center justify-between gap-3 text-[12.5px]">
              <div className="flex items-center gap-2">
                <span className={`font-semibold ${livePos.side === 'LONG' ? 'txt-pos' : 'txt-neg'}`}>
                  {livePos.side === 'LONG' ? t('side_long') : t('side_short')} {livePos.leverage}x
                </span>
                <span className="text-[var(--fg-2)] sensitive">
                  {fmtPrice(livePos.entry_price)} → {fmtPrice(livePos.mark_price)}
                </span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-[var(--fg-3)] sensitive">
                  {(livePos.contract_count != null || livePos.qty != null) && `${fmtContractQty(livePos, locale)} · `}
                  {cur.money(livePos.notional_usd, 0)}
                </span>
                <span className={`font-semibold sensitive ${pnlTone(livePos.unrealized_pnl_usd)}`}>
                  {cur.signedMoney(livePos.unrealized_pnl_usd)}
                  {Math.abs(livePos.unrealized_roi_pct) >= 0.01
                    ? ` (${fmtSignedPct(livePos.unrealized_roi_pct)})`
                    : ''}
                </span>
              </div>
            </div>
          )}

          {trades.length > 0 && (
            <div className="px-6 py-3.5 border-b border-[var(--border)] bg-[var(--surface-2)] grid grid-cols-3 gap-3 text-[12px]">
              <div>
                <div className="text-[var(--fg-3)]">{t('th_net_pnl')}</div>
                <div className={`text-[15px] font-semibold mt-0.5 sensitive ${pnlTone(totalNet)}`}>
                  {cur.signedMoney(totalNet)}
                </div>
              </div>
              <div>
                <div className="text-[var(--fg-3)]">{t('wf_total_fee')}</div>
                <div className="text-[14px] font-semibold mt-0.5 sensitive text-[var(--fg)]">
                  -{cur.money(totalFee)}
                </div>
              </div>
              <div className="text-right">
                <div className="text-[var(--fg-3)]">{t('wf_total_rebate')}</div>
                <div className="text-[14px] font-semibold mt-0.5 sensitive txt-pos">
                  +{cur.money(totalReb)}
                </div>
              </div>
            </div>
          )}

          <div className="divide-y divide-[var(--border)]">
            {loading ? (
              <div className="p-6 space-y-3">
                <div className="text-center text-[12.5px] text-[var(--fg-3)]">
                  {locale === 'en' ? 'Loading…' : '正在加载明细…'}
                </div>
                {[0, 1, 2].map(idx => (
                  <div
                    key={idx}
                    className="h-14 rounded-md border border-[var(--border)] bg-[var(--surface-2)] animate-pulse"
                  />
                ))}
              </div>
            ) : loadFailed ? (
              <div role="alert" className="py-10 text-center space-y-3">
                <p>{locale === 'en' ? 'Could not load trade details.' : '交易明细加载失败'}</p>
                <button type="button" className="otl-btn" onClick={() => setAttempt(n => n + 1)}>
                  {locale === 'en' ? 'Retry' : '重试'}
                </button>
              </div>
            ) : trades.length === 0 ? (
              <div className="py-12 text-center text-[13px] text-[var(--fg-3)]">{t('empty_trades')}</div>
            ) : (
              trades.map(tr => {
                const isExp = expandedId === tr.trade_id
                return (
                  <div key={tr.trade_id}>
                    <button
                      type="button"
                      data-no-press="true"
                      onClick={() => setExpandedId(isExp ? '' : tr.trade_id)}
                      className="w-full px-6 py-3.5 flex items-center justify-between gap-3 text-left hover:bg-[var(--surface-hover)] cursor-pointer"
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 text-[13px] flex-wrap">
                          <span className={`font-semibold ${tr.side === 'LONG' ? 'txt-pos' : 'txt-neg'}`}>
                            {tr.side === 'LONG' ? t('side_long') : t('side_short')}
                          </span>
                          <span className="font-medium text-[var(--fg)] whitespace-nowrap">
                            {tr.close_time_str}
                          </span>
                          <span className="text-[12px] text-[var(--fg-3)] whitespace-nowrap">
                            {tr.exit_batches && tr.exit_batches.length > 1
                              ? `· ${locale === 'en' ? `${tr.exit_batches.length} Exits` : `分 ${tr.exit_batches.length} 笔`} `
                              : ''}
                            · {fmtDuration(tr.holding_sec, locale)}
                            {(tr.contract_count != null || tr.qty != null) && (
                              <span className="sensitive"> · {fmtContractQty(tr, locale)}</span>
                            )}
                          </span>
                        </div>
                        <div className="text-[12px] text-[var(--fg-3)] mt-0.5 sensitive">
                          {t('fee_entry_short')} {tr.entry_leg.fee_known === false ? t('leg_fee_unknown') : `-${cur.plainMoney(tr.entry_leg.gross_fee_usd, 2)}`} ·{' '}
                          {t('fee_exit_short')} {tr.exit_leg.fee_known === false ? t('leg_fee_unknown') : `-${cur.plainMoney(tr.exit_leg.gross_fee_usd, 2)}`} ·{' '}
                          {t('fee_net_short')} {cur.signedMoney(-tr.net_fee_usd, 2)}
                        </div>
                      </div>

                      <div className="flex items-center gap-2.5 shrink-0">
                        <div className="text-right">
                          <div className={`text-[13px] font-semibold sensitive ${pnlTone(tr.net_pnl_usd)}`}>
                            {cur.signedMoney(tr.net_pnl_usd)}
                          </div>
                          <div className="text-[12px] text-[var(--fg-3)] sensitive">
                            {locale === 'en' ? 'Pre-rebate ' : '返佣前 '}
                            {cur.signedPlainMoney(tr.book_pnl_usd)}
                          </div>
                        </div>
                        <ChevronDown
                          size={15}
                          className={`text-[var(--fg-3)] transition-transform duration-150 ${
                            isExp ? 'rotate-180' : ''
                          }`}
                        />
                      </div>
                    </button>

                    <AnimatedCollapse
                      open={isExp}
                      innerClassName="px-6 pb-4 bg-[var(--surface-2)]"
                    >
                      {tr.exit_batches && tr.exit_batches.length > 1 ? (
                        <CompactMultiBatchLedger trade={tr} batches={tr.exit_batches} />
                      ) : (
                        <>
                          <CompactLegRow
                            title={t('leg_entry')}
                            netLabel={t('leg_entry_net')}
                            leg={tr.entry_leg}
                          />
                          <CompactLegRow
                            title={t('leg_exit')}
                            netLabel={t('leg_exit_net')}
                            leg={tr.exit_leg}
                          />
                        </>
                      )}
                      <div className="pt-2.5 border-t border-[var(--border)] space-y-1 text-[12px] tabular-nums">
                        <div className="flex justify-between">
                          <span className="text-[var(--fg-2)]">{t('wf_gross_pnl')}</span>
                          <span className={`font-medium sensitive ${pnlTone(tr.gross_pnl_usd)}`}>
                            {cur.signedMoney(
                              tr.gross_pnl_usd,
                              tr.exit_batches && tr.exit_batches.length > 1 ? 4 : 2,
                            )}
                          </span>
                        </div>
                        {Math.abs(tr.funding_pnl_usd) > 0.0001 && (
                          <div className="flex justify-between">
                            <span className="text-[var(--fg-2)]">{t('wf_funding')}</span>
                            <span className="sensitive">
                              {cur.signedMoney(
                                tr.funding_pnl_usd,
                                tr.exit_batches && tr.exit_batches.length > 1 ? 4 : 2,
                              )}
                            </span>
                          </div>
                        )}
                        <div className="flex justify-between">
                          <span className="text-[var(--fg-2)]">{t('wf_total_fee')}</span>
                          <span className="sensitive">
                            -{cur.money(tr.total_fee_usd, tr.exit_batches && tr.exit_batches.length > 1 ? 4 : 3)}
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-[var(--fg-2)]">{t('wf_total_rebate')}</span>
                          <span className="txt-pos sensitive">
                            +{cur.money(tr.total_rebate_usd, tr.exit_batches && tr.exit_batches.length > 1 ? 4 : 3)}
                          </span>
                        </div>
                        {tr.total_discount_saved_usd > 0.0001 && (
                          <div className="flex justify-between text-[var(--fg-3)]">
                            <span>
                              {locale === 'en' ? 'Pre-deduction discount saved' : '扣费前平台币抵扣已省'}
                            </span>
                            <span className="txt-pos sensitive">
                              +{cur.money(tr.total_discount_saved_usd, 3)}
                            </span>
                          </div>
                        )}
                      </div>
                    </AnimatedCollapse>
                  </div>
                )
              })
            )}
          </div>
        </div>
      </div>
    </div>
  )

  return typeof document !== 'undefined' ? createPortal(drawerNode, document.body) : drawerNode
}
