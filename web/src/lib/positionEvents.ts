import type { PositionEvent } from './types'

export function isPositionExit(event: PositionEvent) {
  return event.action === 'REDUCE' || event.action === 'CLOSE'
}

function settlementDay(ms: number) {
  return Math.floor((ms + 8 * 3600000) / 86400000)
}

export function sumKnown(
  fills: PositionEvent[],
  field: 'fee_usd' | 'gross_pnl_usd' | 'allocated_entry_fee_usd' | 'net_pnl_usd',
) {
  let total = 0
  for (const fill of fills) {
    const value = fill[field]
    if (value == null) return null
    total += value
  }
  return total
}

export function positionOrders(events: PositionEvent[]) {
  const groups: PositionEvent[][] = []
  for (const event of events) {
    const group = groups.at(-1)
    const previous = group?.at(-1)
    if (group && previous && event.order_id && event.order_id === previous.order_id
      && isPositionExit(event) === isPositionExit(previous)
      && settlementDay(event.time_ms) === settlementDay(previous.time_ms)
      && event.qty_unit === previous.qty_unit) {
      group.push(event)
    } else {
      groups.push([event])
    }
  }
  return groups.map(fills => {
    const first = fills[0]
    if (fills.length === 1) return { order: first, fills }
    const last = fills[fills.length - 1]
    const qty = fills.reduce((sum, event) => sum + event.qty, 0)
    const order: PositionEvent = {
      ...last,
      action: first.action === 'OPEN' ? 'OPEN' : last.action,
      price: fills.reduce((sum, event) => sum + event.price * event.qty, 0) / qty,
      qty,
      cost_basis_price: first.cost_basis_price,
      fee_usd: sumKnown(fills, 'fee_usd'),
      allocated_entry_fee_usd: sumKnown(fills, 'allocated_entry_fee_usd'),
      gross_pnl_usd: sumKnown(fills, 'gross_pnl_usd'),
      net_pnl_usd: sumKnown(fills, 'net_pnl_usd'),
    }
    return { order, fills }
  })
}
