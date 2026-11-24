import { useEffect, useState } from 'react'
import { ArrowUpRight, ChevronDown, ChevronUp } from 'lucide-react'
import { useI18n } from '../i18n'
import type { MessageKey } from '../i18n/zh-CN'
import { api } from '../lib/api'
import { useCurrency } from '../lib/currency'
import { fmtDuration, fmtPrice, fmtSignedPct, pnlTone } from '../lib/format'
import type { ClosedTrade, OpenPosition, TradeLegFee } from '../lib/types'
import { FloatingModal } from './FloatingModal'
import { InstrumentMark } from './InstrumentMark'
import { VenueBadge } from './VenueBadge'

interface TradeDrilldownDrawerProps {
  venueId: string
  venueName?: string
  symbol: string
  rangeKey: string
  open: boolean
  onClose: () => void
  onOpenInTrades: (venueId: string, symbol: string) => void
}

function modeText(code: string, t: (k: MessageKey) => string): string {
  const known: Record<string, MessageKey> = {
    LIMIT_MAKER: 'mode_LIMIT_MAKER',
    MARKET_TAKER: 'mode_MARKET_TAKER',
    STOP_MARKET: 'mode_STOP_MARKET',
    TP_MARKET: 'mode_TP_MARKET',
    REVERSE_MARKET: 'mode_REVERSE_MARKET',
  }
  return known[code] ? t(known[code]) : code
}

function CompactLegRow({ title, netLabel, leg }: { title: string; netLabel: string; leg: TradeLegFee }) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  return (
    <div className="otl-surface-2 rounded-lg p-3 space-y-2 text-[12px]">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="px-1.5 py-0.5 rounded text-[10.5px] font-semibold bg-[var(--surface)] border border-[var(--border)]">
            {title}
          </span>
          <span className="text-[var(--fg)] font-medium truncate">
            {modeText(leg.mode_code, t)} · {leg.fee_bps.toFixed(1)} bps
          </span>
        </div>
        <span className="text-[11px] text-[var(--fg-3)] whitespace-nowrap">
          {leg.time_str} {leg.avg_price > 0 ? `· ${fmtPrice(leg.avg_price)}` : ''}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 pt-1.5 border-t border-[var(--border)]">
        <div>
          <div className="text-[10.5px] text-[var(--fg-3)]">{t('leg_gross_fee')}</div>
          <div className="font-semibold sensitive">
            {leg.gross_fee_usd > 0 ? `-${cur.money(leg.gross_fee_usd, 3)}` : cur.money(0, 3)}
          </div>
        </div>
        <div>
          <div className="text-[10.5px] text-[var(--fg-3)]">{t('leg_rebate_disc')}</div>
          <div className={`font-semibold sensitive ${leg.rebate_usd > 0 ? 'txt-pos' : 'text-[var(--fg-3)]'}`}>
            {leg.gross_fee_usd <= 0
              ? t('leg_zero_fee')
              : leg.rebate_usd > 0
                ? `+${cur.money(leg.rebate_usd, 3)} (${leg.rebate_pct.toFixed(0)}%)`
                : cur.money(0, 3)}
          </div>
          {leg.discount_saved_usd > 0.0001 && (
            <div className="text-[10px] txt-pos mt-0.5 sensitive whitespace-nowrap">
              {locale === 'en'
                ? `Pre-disc +${cur.money(leg.discount_saved_usd, 3)}`
                : `抵扣已省 +${cur.money(leg.discount_saved_usd, 3)}`}
            </div>
          )}
        </div>
        <div className="text-right">
          <div className="text-[10.5px] text-[var(--fg-3)]">{netLabel}</div>
          <div className="font-semibold sensitive">
            {leg.net_fee_usd > 0 ? `-${cur.money(leg.net_fee_usd, 3)}` : cur.money(0, 3)}
          </div>
        </div>
      </div>
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
  const [loading, setLoading] = useState(true)
  const [trades, setTrades] = useState<ClosedTrade[]>([])
  const [livePos, setLivePos] = useState<OpenPosition | null>(null)
  const [expandedId, setExpandedId] = useState<string>('')

  useEffect(() => {
    if (!open || !symbol) return
    let alive = true
    setLoading(true)
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
          setExpandedId(allDash.trades[0]?.trade_id ?? '')
          return
        }
        setTrades(dash.trades)
        setExpandedId(dash.trades[0]?.trade_id ?? '')
      })
      .catch(() => {
        if (alive) {
          setTrades([])
          setLivePos(null)
        }
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [open, venueId, symbol, rangeKey])

  const totalNet = trades.reduce((a, b) => a + b.net_pnl_usd, 0)
  const totalFee = trades.reduce((a, b) => a + b.total_fee_usd, 0)
  const totalReb = trades.reduce((a, b) => a + b.total_rebate_usd + b.total_discount_saved_usd, 0)
  const wins = trades.filter(tr => tr.net_pnl_usd > 0).length
  const winRate = trades.length ? (wins / trades.length) * 100 : 0

  return (
    <FloatingModal
      open={open}
      onClose={onClose}
      testId="trade-drilldown-drawer"
      maxWidthClass="max-w-[600px]"
      title={
        <span className="inline-flex items-center gap-2">
          <InstrumentMark instrument={symbol} exchange={venueId.toUpperCase()} />
          <span>{symbol}</span>
          <VenueBadge venueId={venueId} name={venueName || venueId.toUpperCase()} />
        </span>
      }
      subtitle={
        trades.length > 0
          ? `${trades.length} ${locale === 'en' ? 'trades' : '笔交割'} · ${t('kpi_winrate')} ${winRate.toFixed(1)}%`
          : undefined
      }
      headerActions={
        trades.length > 0 ? (
          <button
            type="button"
            onClick={() => onOpenInTrades(venueId, symbol)}
            className="inline-flex items-center gap-1 h-7 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[11.5px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer whitespace-nowrap"
          >
            <span>{locale === 'en' ? 'Open in Trades' : '去合约页查看'}</span>
            <ArrowUpRight size={12} />
          </button>
        ) : undefined
      }
    >
      {/* Live Open Position Strip (when inspecting an active open position) */}
      {livePos && (
        <div className="px-5 py-3 border-b border-[var(--border)] bg-[var(--surface)] flex flex-wrap items-center justify-between gap-3 text-[12px]">
          <div className="flex items-center gap-2">
            <span
              className="px-2 py-0.5 rounded text-[11px] font-semibold"
              style={{
                background: livePos.side === 'LONG' ? 'var(--pos-soft)' : 'var(--neg-soft)',
                color: livePos.side === 'LONG' ? 'var(--pos)' : 'var(--neg)',
              }}
            >
              {livePos.side === 'LONG' ? t('side_long') : t('side_short')} {livePos.leverage}x
            </span>
            <span className="text-[var(--fg-2)]">
              {fmtPrice(livePos.entry_price)} → {fmtPrice(livePos.mark_price)}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-[var(--fg-3)] sensitive">{cur.money(livePos.notional_usd, 0)}</span>
            <span className={`font-semibold sensitive ${pnlTone(livePos.unrealized_pnl_usd)}`}>
              {cur.signedMoney(livePos.unrealized_pnl_usd)}
              {Math.abs(livePos.unrealized_roi_pct) >= 0.01 ? ` (${fmtSignedPct(livePos.unrealized_roi_pct)})` : ''}
            </span>
          </div>
        </div>
      )}

      {/* Historical Summary Strip */}
      {trades.length > 0 && (
        <div className="px-5 py-3 border-b border-[var(--border)] bg-[var(--surface-2)] grid grid-cols-3 gap-3 text-[12px]">
          <div>
            <div className="text-[11px] text-[var(--fg-3)]">{t('th_net_pnl')}</div>
            <div className={`text-[15px] font-bold mt-0.5 sensitive ${pnlTone(totalNet)}`}>
              {cur.signedMoney(totalNet)}
            </div>
          </div>
          <div>
            <div className="text-[11px] text-[var(--fg-3)]">{t('wf_total_fee')}</div>
            <div className="text-[14px] font-semibold mt-0.5 sensitive text-[var(--fg)]">
              -{cur.money(totalFee)}
            </div>
          </div>
          <div className="text-right">
            <div className="text-[11px] text-[var(--fg-3)]">{t('wf_total_rebate')}</div>
            <div className="text-[14px] font-semibold mt-0.5 sensitive txt-pos">
              +{cur.money(totalReb)}
            </div>
          </div>
        </div>
      )}

      {/* Trade Accordion List */}
      <div className="p-4 space-y-2.5">
        {loading ? (
          <div className="py-12 text-center text-[13px] text-[var(--fg-3)]">
            {locale === 'en' ? 'Loading…' : '正在加载明细…'}
          </div>
        ) : trades.length === 0 ? (
          <div className="py-10 text-center text-[13px] text-[var(--fg-3)]">{t('empty_trades')}</div>
        ) : (
          trades.map(tr => {
            const isExp = expandedId === tr.trade_id
            return (
              <div key={tr.trade_id} className="otl-card overflow-hidden">
                <button
                  type="button"
                  onClick={() => setExpandedId(isExp ? '' : tr.trade_id)}
                  className="w-full px-4 py-3 flex items-center justify-between gap-3 text-left hover:bg-[var(--surface-hover)] transition-colors cursor-pointer"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span
                        className="px-1.5 py-0.5 rounded text-[10.5px] font-semibold"
                        style={{
                          background: tr.side === 'LONG' ? 'var(--pos-soft)' : 'var(--neg-soft)',
                          color: tr.side === 'LONG' ? 'var(--pos)' : 'var(--neg)',
                        }}
                      >
                        {tr.side === 'LONG' ? t('side_long') : t('side_short')}
                      </span>
                      <span className="text-[12.5px] font-medium text-[var(--fg)] whitespace-nowrap">
                        {tr.close_time_str}
                      </span>
                      <span className="text-[11.5px] text-[var(--fg-3)] whitespace-nowrap">
                        · {fmtDuration(tr.holding_sec, locale)}
                      </span>
                    </div>
                    <div className="text-[11px] text-[var(--fg-3)] mt-1 sensitive">
                      {t('fee_entry_short')} -{cur.plainMoney(tr.entry_leg.gross_fee_usd, 2)} ·{' '}
                      {t('fee_exit_short')} -{cur.plainMoney(tr.exit_leg.gross_fee_usd, 2)} ·{' '}
                      {t('fee_net_short')} -{cur.money(tr.net_fee_usd, 2)}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    <div className="text-right">
                      <div className={`text-[14px] font-bold sensitive ${pnlTone(tr.net_pnl_usd)}`}>
                        {cur.signedMoney(tr.net_pnl_usd)}
                      </div>
                      <div className="text-[10.5px] text-[var(--fg-3)] sensitive">
                        {cur.signedPlainMoney(tr.book_pnl_usd)}
                      </div>
                    </div>
                    {isExp ? (
                      <ChevronUp size={15} className="text-[var(--fg-3)]" />
                    ) : (
                      <ChevronDown size={15} className="text-[var(--fg-3)]" />
                    )}
                  </div>
                </button>

                {isExp && (
                  <div className="otl-drawer-expand px-4 pb-4 pt-1 border-t border-[var(--border)] space-y-2.5">
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
                    <div className="pt-1 space-y-1 text-[12px]">
                      <div className="flex justify-between">
                        <span className="text-[var(--fg-2)]">{t('wf_gross_pnl')}</span>
                        <span className={`font-medium sensitive ${pnlTone(tr.gross_pnl_usd)}`}>
                          {cur.signedMoney(tr.gross_pnl_usd)}
                        </span>
                      </div>
                      {Math.abs(tr.funding_pnl_usd) > 0.0001 && (
                        <div className="flex justify-between">
                          <span className="text-[var(--fg-2)]">{t('wf_funding')}</span>
                          <span className="sensitive">{cur.signedMoney(tr.funding_pnl_usd)}</span>
                        </div>
                      )}
                      <div className="flex justify-between">
                        <span className="text-[var(--fg-2)]">{t('wf_total_fee')}</span>
                        <span className="sensitive">-{cur.money(tr.total_fee_usd, 3)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-[var(--fg-2)]">{t('wf_total_rebate')}</span>
                        <span className="txt-pos sensitive">
                          +{cur.money(tr.total_rebate_usd, 3)}
                        </span>
                      </div>
                      {tr.total_discount_saved_usd > 0.0001 && (
                        <div className="flex justify-between text-[11px] text-[var(--fg-3)]">
                          <span>
                            {locale === 'en' ? 'Pre-deduction discount saved' : '扣费前平台币抵扣已省'}
                          </span>
                          <span className="txt-pos sensitive">+{cur.money(tr.total_discount_saved_usd, 3)}</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>
    </FloatingModal>
  )
}
