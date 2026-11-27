import type { CurrencyCode } from './types'

/** Keep exchange fill precision, including when the display currency is converted. */
export function formatExecutionFee(
  currency: { code: CurrencyCode; symbol: string; rate: number },
  usd: number,
  minimumDecimals = 4,
) {
  const converted = usd * currency.rate
  if (!Number.isFinite(converted)) return '—'
  // Leave original USD amounts intact. FX multiplication can introduce binary rounding noise.
  const amount = currency.rate === 1 ? usd : Number(converted.toPrecision(15))
  const magnitude = Math.abs(amount)
  const number = magnitude && magnitude < 1e-20
    ? magnitude.toExponential()
    : magnitude.toLocaleString('en-US', { minimumFractionDigits: minimumDecimals, maximumFractionDigits: 20 })
  const sign = amount > 0 ? '+' : amount < 0 ? '-' : ''
  return `${sign}${currency.symbol}${number}${currency.code === 'USDT' ? ' U' : ''}`
}
