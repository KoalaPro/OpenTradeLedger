import { ArrowDownLeft, ArrowUpRight, Check, ChevronDown, Plus } from 'lucide-react'
import type { ReactNode } from 'react'
import { useI18n } from '../i18n'
import { useCurrency } from '../lib/currency'
import { fmtContractQty, fmtPrice, pnlTone } from '../lib/format'
import { isPositionExit, positionOrders, sumKnown } from '../lib/positionEvents'
import type { ClosedTrade, Locale, PositionEvent, TradeExitBatch } from '../lib/types'

const actions = {
  OPEN: { label: 'lifecycle_open', icon: ArrowUpRight },
  ADD: { label: 'lifecycle_add', icon: Plus },
  REDUCE: { label: 'lifecycle_reduce', icon: ArrowDownLeft },
  CLOSE: { label: 'lifecycle_close', icon: Check },
} as const

export function lifecycleStatus(events: PositionEvent[]) {
  if (events.at(-1)?.action === 'CLOSE') return 'lifecycle_flat'
  return events.some(event => event.action === 'REDUCE') ? 'lifecycle_reduced' : 'lifecycle_holding'
}

function quantity(value: number, event: PositionEvent, trade: ClosedTrade, locale: Locale) {
  const contracts = event.qty_unit ? event.qty_unit === 'contracts' : trade.qty == null && trade.contract_count != null
  return fmtContractQty({ symbol: trade.symbol, qty: contracts ? null : value, contract_count: contracts ? value : undefined }, locale)
}

function eventTime(event: PositionEvent, milliseconds = false) {
  if (!(event.time_ms > 0)) return '—'
  // All dates in the settlement ledger use UTC+8.
  return new Date(event.time_ms + 8 * 3600000).toISOString().slice(5, milliseconds ? 23 : 19).replace('T', ' ')
}

function Metric({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return <div><dt>{label}</dt><dd className={`sensitive ${tone}`} data-wide={value.length > 12 || undefined}>{value}</dd></div>
}

function usePositionCash() {
  const { t } = useI18n()
  const cur = useCurrency()
  const unknown = t('lifecycle_unknown')
  return (value: number | null, fee = false) => {
    if (value == null) return unknown
    const decimals = Math.abs(value * cur.rate).toFixed(10).split('.')[1]?.replace(/0+$/, '').length ?? 0
    return cur.signedMoney(fee ? -value : value, Math.max(fee ? 4 : 2, decimals))
  }
}

export function BatchNetResult({ batch }: { batch: TradeExitBatch }) {
  const { t } = useI18n()
  const cash = usePositionCash()
  const net = batch.fee_known === false ? null : batch.net_pnl_usd
  return <div className="mt-1 text-[var(--fg-2)]">
    {t(batch.net_basis === 'round_trip' ? 'lifecycle_net' : 'lifecycle_exit_only')}{' '}
    <b className={`sensitive ${pnlTone(net ?? 0)}`}>{cash(net)}</b>
  </div>
}

export function PartialSettlement({ trade }: { trade: ClosedTrade }) {
  const { t } = useI18n()
  const cash = usePositionCash()
  const events = trade.position_events
  if (!events?.length || events.at(-1)?.action === 'CLOSE') return null
  const exits = events.filter(isPositionExit)
  if (!exits.length) return null
  const entryFees = sumKnown(events.filter(event => !isPositionExit(event)), 'fee_usd')
  const allocated = sumKnown(exits, 'allocated_entry_fee_usd')
  const realized = sumKnown(exits, 'net_pnl_usd')
  return <div className="space-y-1.5 pt-2 border-t border-[var(--border)]">
    <div className="flex justify-between gap-2">
      <span className="text-[var(--fg-2)]">{t('lifecycle_closed_portion')}</span>
      <b className={`sensitive ${pnlTone(realized ?? 0)}`}>{cash(realized)}</b>
    </div>
    <div className="flex justify-between gap-2">
      <span className="text-[var(--fg-2)]">{t('lifecycle_carried_fee')}</span>
      <span className="sensitive">{cash(entryFees == null || allocated == null ? null : entryFees - allocated, true)}</span>
    </div>
  </div>
}

function EventMetrics({ event, trade }: { event: PositionEvent; trade: ClosedTrade }) {
  const { t, locale } = useI18n()
  const unknown = t('lifecycle_unknown')
  const exit = isPositionExit(event)
  const basis = exit ? event.cost_basis_price : event.avg_entry_price
  const cash = usePositionCash()
  return <>
    <dl>
      <Metric label={t('lifecycle_remaining')} value={quantity(event.remaining_qty, event, trade, locale)} />
      <Metric label={t(exit ? 'lifecycle_before_exit' : 'lifecycle_average')} value={basis == null ? unknown : fmtPrice(basis)} />
      <Metric label={t('lifecycle_fee')} value={cash(event.fee_usd, true)} />
    </dl>
    {exit && <dl className="otl-position-realized">
      <Metric label={t('lifecycle_gross')} value={cash(event.gross_pnl_usd)} />
      <Metric label={t('lifecycle_entry_fee')} value={cash(event.allocated_entry_fee_usd, true)} />
      <Metric label={t('lifecycle_net')} value={cash(event.net_pnl_usd)} tone={pnlTone(event.net_pnl_usd ?? 0)} />
    </dl>}
  </>
}

export function PositionTimeline({ trade, children }: { trade: ClosedTrade; children: ReactNode }) {
  const { t, locale } = useI18n()
  const events = trade.position_events
  if (!events?.length) return <>
    <p className="my-3 text-[12px] text-[var(--fg-3)]">{t('lifecycle_no_events')}</p>
    {children}
  </>

  return <section className="otl-position-lifecycle">
    <header>
      <h3>{t('lifecycle')}</h3>
      <span className="otl-position-status" data-flat={events.at(-1)?.action === 'CLOSE'}>
        {t(lifecycleStatus(events))}
      </span>
    </header>
    <ol aria-label={t('lifecycle')}>
      {positionOrders(events).map(({ order, fills }, index) => {
        const { label, icon: Icon } = actions[order.action]
        return <li key={`${order.order_id}-${order.time_ms}-${index}`} data-action={order.action}>
          <span className="otl-position-icon" aria-hidden="true"><Icon size={13} strokeWidth={2} /></span>
          <div className="otl-position-body">
            <div className="otl-position-heading">
              <div>
                <h4>{t(label)}</h4>
                <span className="sensitive">{quantity(order.qty, order, trade, locale)} @ {fmtPrice(order.price)}</span>
              </div>
              <time title="UTC+8">{eventTime(order)}</time>
            </div>
            <EventMetrics event={order} trade={trade} />
            {fills.length > 1 && <details className="otl-position-fills">
              <summary>{fills.length} {t('lifecycle_fills')}<ChevronDown size={12} aria-hidden="true" /></summary>
              <div className="otl-position-fill-list">
                {fills.map((fill, i) => <div key={i} className="otl-position-fill">
                  <div className="otl-position-heading">
                    <span className="sensitive">{quantity(fill.qty, fill, trade, locale)} @ {fmtPrice(fill.price)}</span>
                    <time title="UTC+8">{eventTime(fill, true)}</time>
                  </div>
                  <EventMetrics event={fill} trade={trade} />
                </div>)}
              </div>
            </details>}
          </div>
        </li>
      })}
    </ol>
    <details className="otl-position-fees">
      <summary>{t('lifecycle_fees')}<ChevronDown size={14} aria-hidden="true" /></summary>
      {children}
    </details>
  </section>
}
