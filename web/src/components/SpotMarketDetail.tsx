import { useI18n } from '../i18n'
import type { SpotMarket } from '../lib/types'

export function lowLiquidity(market?: SpotMarket) {
  return market?.liquidity === 'zero_volume' || market?.liquidity === 'holding_exceeds_volume'
}

export function SpotMarketDetail({ asset, market }: { asset: string; market: SpotMarket }) {
  const { locale } = useI18n()
  const en = locale === 'en'
  if (!market.quote_asset || !market.pair) {
    return <p className="text-[var(--fg-3)]">{en ? '24h market volume unavailable.' : '暂无该交易对的 24 小时成交额。'}</p>
  }
  const quoted = (amount: number) =>
    amount.toLocaleString(locale, { maximumSignificantDigits: 12 }) + ' ' + market.quote_asset
  const volume = market.volume_24h_quote == null
    ? (en ? 'Unavailable' : '暂无数据')
    : quoted(market.volume_24h_quote)
  return (
    <div className="space-y-1 text-[12px]">
      <strong>{asset}/{market.quote_asset} {market.pair.startsWith('@') && ` · ${market.pair}`}</strong>
      <div className="flex flex-wrap gap-x-4 text-[var(--fg-2)]">
        <span>
          {en ? 'Exchange quote' : '交易所报价'} <span className="sensitive">{market.price == null ? '—' : quoted(market.price)}</span> (allMids)
        </span>
        <span>{en ? '24h turnover' : '24 小时成交额'} <span className="sensitive">{volume}</span></span>
      </div>
      {market.source && <div className="text-[var(--fg-3)]">{en ? 'Volume source' : '成交额来源'} · {market.source}</div>}
      {lowLiquidity(market) && (
        <p className="text-[var(--warn)]">
          {market.liquidity === 'zero_volume'
            ? (en ? 'This pair had zero turnover in the 24 hours before the snapshot.' : '快照记录时，此交易对近 24 小时成交额为零。')
            : (en ? 'The quoted holding value exceeds this pair’s entire 24h turnover.' : '这笔持仓的账面估值超过该交易对过去 24 小时的全部成交额。')}
          {' '}{en ? 'Sale proceeds may be lower than the quoted value.' : '实际卖出所得可能低于账面估值。'}
        </p>
      )}
    </div>
  )
}
