import { ArrowDownLeft, ArrowUpRight, Check, ChevronDown, Plus } from 'lucide-react'
import { useI18n } from '../i18n'
import type { MessageKey } from '../i18n/zh-CN'
import { useCurrency } from '../lib/currency'
import { formatExecutionFee } from '../lib/executionFee'
import { fmtContractQty, fmtLegRate, fmtPrice, pnlTone } from '../lib/format'
import { isPositionExit, positionOrders, sumAmounts, sumKnown } from '../lib/positionEvents'
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

export function isScaleOutTrade(trade: ClosedTrade | null | undefined): boolean {
  if (!trade) return false
  const events = trade.position_events ?? []
  if (events.length > 0 && events.at(-1)?.action !== 'CLOSE') return true
  if ((trade.exit_batches?.length ?? 0) > 1) return true
  if (events.length > 0 && positionOrders(events).length > 2) return true
  return false
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

function batchRebatePct(b: TradeExitBatch): number {
  if (b.rebate_pct != null && b.rebate_pct > 0) return b.rebate_pct
  return b.gross_fee_usd > 0 ? (b.rebate_usd / b.gross_fee_usd) * 100 : 0
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
  return <div><dt>{label}</dt><dd data-wide={value.length >= 13 || undefined} className={`sensitive ${tone}`}>{value}</dd></div>
}

function usePositionCash() {
  const { t } = useI18n()
  const cur = useCurrency()
  const unknown = t('lifecycle_unknown')
  return (value: number | null, fee = false) => {
    if (value == null) return unknown
    return fee ? formatExecutionFee(cur, -value) : cur.signedMoney(value, 2)
  }
}

export function BatchNetResult({ batch }: { batch: TradeExitBatch }) {
  const { t } = useI18n()
  const cash = usePositionCash()
  const net = batch.fee_known === false ? null : batch.net_pnl_usd
  return (
    <div className="mt-1 text-[var(--fg-2)]">
      {t(batch.net_basis === 'round_trip' ? 'lifecycle_net' : 'lifecycle_exit_only')}{' '}
      <b className={`sensitive ${pnlTone(net ?? 0)}`}>{cash(net)}</b>
    </div>
  )
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
  return (
    <div className="space-y-1.5 pt-2 border-t border-[var(--border)]">
      <div className="flex justify-between gap-2">
        <span className="text-[var(--fg-2)]">{t('lifecycle_closed_portion')}</span>
        <b className={`sensitive ${pnlTone(realized ?? 0)}`}>{cash(realized)}</b>
      </div>
      <div className="flex justify-between gap-2">
        <span className="text-[var(--fg-2)]">{t('lifecycle_carried_fee')}</span>
        <span className="sensitive">{cash(sumAmounts([entryFees, allocated == null ? null : -allocated]), true)}</span>
      </div>
    </div>
  )
}

function EventMetrics({ event, trade }: { event: PositionEvent; trade: ClosedTrade }) {
  const { t, locale } = useI18n()
  const unknown = t('lifecycle_unknown')
  const exit = isPositionExit(event)
  const basis = exit ? event.cost_basis_price : event.avg_entry_price
  const cash = usePositionCash()
  return (
    <>
      <dl>
        <Metric label={t('lifecycle_remaining')} value={quantity(event.remaining_qty, event, trade, locale)} />
        <Metric label={t(exit ? 'lifecycle_before_exit' : 'lifecycle_average')} value={basis == null ? unknown : fmtPrice(basis)} />
        <Metric label={t('lifecycle_fee')} value={cash(event.fee_usd, true)} />
      </dl>
      {exit && (
        <dl className="otl-position-realized">
          <Metric label={t('lifecycle_gross')} value={cash(event.gross_pnl_usd)} />
          <Metric label={t('lifecycle_entry_fee')} value={cash(event.allocated_entry_fee_usd, true)} />
          <Metric label={t('lifecycle_net')} value={cash(event.net_pnl_usd)} tone={pnlTone(event.net_pnl_usd ?? 0)} />
        </dl>
      )}
    </>
  )
}

type FeeSource = ClosedTrade['entry_leg'] | TradeExitBatch

function orderFeeSource(order: PositionEvent, peers: PositionEvent[], trade: ClosedTrade): FeeSource | undefined {
  const exit = isPositionExit(order)
  if (!exit) {
    // While the position is open, the parent entry leg may cover only allocated fees.
    return peers.length === 1 && trade.position_events?.at(-1)?.action === 'CLOSE' ? trade.entry_leg : undefined
  }
  const batches = trade.exit_batches ?? []
  if (batches.length && order.order_id) {
    const matches = batches.filter(batch => batch.order_id === order.order_id)
    if (matches.length === 1 && peers.filter(peer => peer.order_id === order.order_id).length === 1) return matches[0]
  }
  if (peers.length === 1) return trade.exit_leg
  // Historical batches without an ID can still be identified by exact time and size.
  const matches = batches.filter(batch => !batch.order_id && batch.time_ms === order.time_ms &&
    (order.qty_unit === 'contracts' ? batch.contract_count : batch.qty) === order.qty)
  const unambiguous = peers.filter(peer => peer.time_ms === order.time_ms && peer.qty === order.qty && peer.qty_unit === order.qty_unit).length === 1
  return matches.length === 1 && unambiguous ? matches[0] : undefined
}

function ExecutionTerms({ event, source }: { event: PositionEvent; source?: FeeSource }) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const en = locale === 'en'
  const sourceMode = source?.mode_code
  const role = event.role
  const mode = sourceMode && sourceMode !== 'UNKNOWN' && (!role || source?.role === role)
    ? modeLabel(sourceMode, t)
    : role === 'MAKER' ? (en ? 'Maker' : '挂单 Maker')
    : role === 'TAKER' ? (en ? 'Taker' : '吃单 Taker')
    : role === 'MIXED' ? t('mode_MIXED') : t('mode_UNKNOWN')
  const feeSource = source?.fee_known === false || event.fee_usd == null ? undefined : source
  const rebate = event.rebate_usd ?? feeSource?.rebate_usd
  const net = event.net_fee_usd ?? feeSource?.net_fee_usd
  const rate = event.fee_bps ?? feeSource?.fee_bps ?? null
  const gross = event.fee_usd
  const pending = (event.rebate_status ?? (source && 'rebate_status' in source ? source.rebate_status : undefined)) === 'PENDING'
  return (
    <div className="otl-position-mode">
      <span>{mode} · {fmtLegRate(rate, locale)}</span>
      <span className="sensitive">
        {rebate == null ? (en ? 'Rebate unknown' : '返佣未知') : (
          <span className={rebate > 0 ? 'txt-pos' : undefined}>
            {pending ? (en ? 'Est. rebate ' : '预估返 ') : (en ? 'Reb ' : '返 ')}{formatExecutionFee(cur, rebate)}
            {rebate > 0 && gross != null && gross > 0 ? ` (${(rebate / gross * 100).toFixed(0)}%)` : ''}
          </span>
        )}
        {' · '}
        {en ? 'Net ' : '净 '}{net == null ? t('lifecycle_unknown') : formatExecutionFee(cur, -net)}
      </span>
    </div>
  )
}

function BatchOnlyTimelineStrip({
  trade,
  batches,
  horizontal,
}: {
  trade: ClosedTrade
  batches: TradeExitBatch[]
  horizontal: boolean
}) {
  const { t, locale } = useI18n()
  const cur = useCurrency()
  const en = trade.entry_leg
  const enUnknown = en.fee_known === false

  return (
    <section
      data-testid={horizontal ? 'scaleout-timeline-strip' : undefined}
      className={`otl-position-lifecycle ${horizontal ? 'otl-position-horizontal' : ''}`}
    >
      <header>
        <div className="flex items-center gap-2 flex-wrap">
          <h3>{t('lifecycle')}</h3>
          <span className="text-[12px] text-[var(--fg-3)]">
            {trade.symbol} · {trade.side === 'LONG' ? t('side_long') : t('side_short')} ·{' '}
            {locale === 'en' ? `${batches.length} Exits` : `分 ${batches.length} 笔平仓`}
          </span>
        </div>
        <span className="otl-position-status" data-flat="true">
          {t('lifecycle_flat')}
        </span>
      </header>
      <ol aria-label={t('lifecycle')}>
        <li data-action="OPEN" data-testid="timeline-entry-step">
          <span className="otl-position-icon" aria-hidden="true"><ArrowUpRight size={13} strokeWidth={2} /></span>
          <div className="otl-position-body">
            <div className="otl-position-heading">
              <div>
                <h4>{t('lifecycle_open')}</h4>
                <span className="sensitive">
                  {(trade.qty != null || trade.contract_count != null) ? fmtContractQty(trade, locale) : ''}
                  {en.avg_price > 0 ? ` @ ${fmtPrice(en.avg_price)}` : ''}
                </span>
              </div>
              <time title="UTC+8">{en.time_str}</time>
            </div>
            <div className="otl-position-mode">
              {enUnknown ? t('leg_fee_unknown') : `${modeLabel(en.mode_code, t)} · ${fmtLegRate(en.fee_bps, locale)}`}
            </div>
            <dl>
              <Metric label={t('leg_gross_fee')} value={enUnknown ? t('leg_fee_unknown') : formatExecutionFee(cur, -en.gross_fee_usd)} />
              <Metric
                label={t('leg_rebate_disc')}
                value={
                  enUnknown
                    ? '—'
                    : en.rebate_usd > 0
                      ? `${formatExecutionFee(cur, en.rebate_usd)}${en.gross_fee_usd > 0 ? ` (${en.rebate_pct.toFixed(0)}%)` : ''}`
                      : cur.money(0, 4)
                }
                tone={!enUnknown && en.rebate_usd > 0 ? 'txt-pos' : ''}
              />
              <Metric label={t('leg_entry_net')} value={enUnknown ? '—' : formatExecutionFee(cur, -en.net_fee_usd)} />
            </dl>
          </div>
        </li>

        {batches.map((b, idx) => {
          const isLast = idx === batches.length - 1
          const bUnknown = b.fee_known === false
          const rebPct = batchRebatePct(b)
          const Icon = isLast ? Check : ArrowDownLeft
          return (
            <li
              key={b.batch_index}
              data-action={isLast ? 'CLOSE' : 'REDUCE'}
              data-testid={`exit-batch-row-${b.batch_index}`}
            >
              <span className="otl-position-icon" aria-hidden="true"><Icon size={13} strokeWidth={2} /></span>
              <div className="otl-position-body">
                <div className="otl-position-heading">
                  <div>
                    <h4>{locale === 'en' ? `Exit #${b.batch_index}` : `平仓第 ${b.batch_index} 笔`}</h4>
                    <span className="sensitive">
                      {(b.qty != null || b.contract_count != null)
                        ? fmtContractQty({ symbol: trade.symbol, qty: b.qty, contract_count: b.contract_count }, locale)
                        : ''}
                      {b.avg_price > 0 ? ` @ ${fmtPrice(b.avg_price)}` : ''}
                    </span>
                  </div>
                  <time title="UTC+8">{b.time_str}</time>
                </div>
                <div className="otl-position-mode">
                  {bUnknown
                    ? t('leg_fee_unknown')
                    : `${modeLabel(b.mode_code, t)}${b.fee_bps != null ? ` · ${fmtLegRate(b.fee_bps, locale)}` : ''}`}
                </div>
                <dl>
                  <Metric label={t('leg_gross_fee')} value={bUnknown ? t('leg_fee_unknown') : formatExecutionFee(cur, -b.gross_fee_usd)} />
                  <Metric
                    label={t('leg_rebate_disc')}
                    value={
                      bUnknown
                        ? '—'
                        : b.rebate_usd > 0
                          ? `${formatExecutionFee(cur, b.rebate_usd)}${b.gross_fee_usd > 0 ? ` (${rebPct.toFixed(0)}%)` : ''}`
                          : cur.money(0, 4)
                    }
                    tone={!bUnknown && b.rebate_usd > 0 ? 'txt-pos' : ''}
                  />
                  <Metric label={t('leg_exit_net')} value={bUnknown ? '—' : formatExecutionFee(cur, -b.net_fee_usd)} />
                </dl>
                <dl className="otl-position-realized">
                  <Metric
                    label={t('lifecycle_gross')}
                    value={b.gross_pnl_usd == null ? t('lifecycle_unknown') : cur.signedMoney(b.gross_pnl_usd, 2)}
                    tone={pnlTone(b.gross_pnl_usd ?? 0)}
                  />
                  <Metric
                    label={t(b.net_basis === 'round_trip' ? 'lifecycle_net' : 'lifecycle_exit_only')}
                    value={bUnknown || b.net_pnl_usd == null ? t('lifecycle_unknown') : cur.signedMoney(b.net_pnl_usd, 2)}
                    tone={pnlTone(b.net_pnl_usd ?? 0)}
                  />
                </dl>
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

export function PositionTimeline({
  trade,
  horizontal = false,
}: {
  trade: ClosedTrade
  horizontal?: boolean
}) {
  const { t, locale } = useI18n()
  const events = trade.position_events ?? []
  const batches = trade.exit_batches && trade.exit_batches.length > 1 ? trade.exit_batches : []

  if (!events.length) {
    if (batches.length > 1) {
      return <BatchOnlyTimelineStrip trade={trade} batches={batches} horizontal={horizontal} />
    }
    return null
  }

  const orders = positionOrders(events)
  const entries = orders.map(group => group.order).filter(order => !isPositionExit(order))
  const exits = orders.map(group => group.order).filter(isPositionExit)

  return (
    <section
      data-testid={horizontal ? 'scaleout-timeline-strip' : undefined}
      className={`otl-position-lifecycle ${horizontal ? 'otl-position-horizontal' : ''}`}
    >
      <header>
        <div className="flex items-center gap-2 flex-wrap">
          <h3>{t('lifecycle')}</h3>
          <span className="text-[12px] text-[var(--fg-3)]">
            {trade.symbol} · {trade.side === 'LONG' ? t('side_long') : t('side_short')} ·{' '}
            {locale === 'en' ? `${orders.length} steps` : `共 ${orders.length} 个执行节点`}
          </span>
        </div>
        <span className="otl-position-status" data-flat={events.at(-1)?.action === 'CLOSE'}>
          {t(lifecycleStatus(events))}
        </span>
      </header>
      <ol aria-label={t('lifecycle')}>
        {orders.map(({ order, fills }, index) => {
          const { label, icon: Icon } = actions[order.action]
          const source = orderFeeSource(order, isPositionExit(order) ? exits : entries, trade)
          return (
            <li key={`${order.order_id}-${order.time_ms}-${index}`} data-action={order.action}>
              <span className="otl-position-icon" aria-hidden="true"><Icon size={13} strokeWidth={2} /></span>
              <div className="otl-position-body">
                <div className="otl-position-heading">
                  <div>
                    <h4>{t(label)}</h4>
                    <span className="sensitive">{quantity(order.qty, order, trade, locale)} @ {fmtPrice(order.price)}</span>
                  </div>
                  <time title="UTC+8">{eventTime(order)}</time>
                </div>
                <ExecutionTerms event={order} source={source} />
                <EventMetrics event={order} trade={trade} />
                {fills.length > 1 && (
                  <details className="otl-position-fills">
                    <summary>{fills.length} {t('lifecycle_fills')}<ChevronDown size={12} aria-hidden="true" /></summary>
                    <div className="otl-position-fill-list">
                      {fills.map((fill, i) => (
                        <div key={i} className="otl-position-fill">
                          <div className="otl-position-heading">
                            <span className="sensitive">{quantity(fill.qty, fill, trade, locale)} @ {fmtPrice(fill.price)}</span>
                            <time title="UTC+8">{eventTime(fill, true)}</time>
                          </div>
                          <ExecutionTerms event={fill} />
                          <EventMetrics event={fill} trade={trade} />
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
