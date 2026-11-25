import { useI18n } from '../i18n'
import { useCurrency } from '../lib/currency'
import type { SpotHolding, SpotWalletBalance } from '../lib/types'
import { InstrumentMark } from './InstrumentMark'
import { VenueBadge } from './VenueBadge'

export function formatWalletType(wallet: string, isEn: boolean): string {
  switch (wallet.toUpperCase()) {
    case 'UTA':
    case 'UNIFIED': return isEn ? 'Unified Account' : '统一账户'
    case 'SPOT': return isEn ? 'Spot Account' : '现货账户'
    case 'FUNDING': return isEn ? 'Funding Account' : '资金账户'
    case 'EARN':
    case 'SAVINGS':
    case 'FINANCE': return isEn ? 'Earn / Savings' : '理财账户'
    case 'MULTIPLE': return isEn ? 'Multiple wallets' : '多个账户'
    default: return wallet || '—'
  }
}

export function SpotWalletTable({ balances, spotTotal, selected }: {
  balances: SpotWalletBalance[]
  spotTotal: number
  selected: SpotHolding | null
}) {
  const cur = useCurrency()
  const { locale, t } = useI18n()
  const en = locale === 'en'
  return (
    <section className="border-t border-[var(--border)] pt-3">
      <div className="pb-2.5 flex items-center justify-between gap-2 text-[13px]">
        <span className="text-[var(--fg-2)]">
          {en ? 'Portfolio allocation · Spot, Earn & Dust' : '账本归入现货、理财与尾仓的净值'}
        </span>
        <strong className="text-[14px] text-[var(--fg)] sensitive">{cur.money(spotTotal)}</strong>
      </div>
      {balances.length > 0 ? (
        <div className="overflow-auto max-h-72 border-t border-[var(--border)]">
          <table className="otl-table">
            <caption className="text-left px-2.5 pt-2.5 pb-1 text-[13px] text-[var(--fg-3)]">
              {en ? 'Synced wallet balances' : '交易所钱包余额'}
            </caption>
            <thead>
              <tr>
                <th>{en ? 'Asset' : '币种'}</th>
                <th>{t('th_venue')}</th>
                <th>{en ? 'Wallet' : '所在账户'}</th>
                <th className="text-right">{en ? 'Quantity' : '数量'}</th>
                <th className="text-right">{en ? 'Est. value' : '估值'}</th>
              </tr>
            </thead>
            <tbody>
              {balances.map(b => (
                <tr
                  key={`${b.venue_id}-${b.wallet_type}-${b.asset}`}
                  className={b.venue_id === selected?.venue_id && b.asset === selected.asset ? 'selected' : undefined}
                >
                  <td>
                    <span className="inline-flex items-center gap-2 font-semibold">
                      <InstrumentMark instrument={`${b.asset}_USDT`} exchange={b.venue_id.toUpperCase()} />
                      <span>{b.asset}</span>
                    </span>
                  </td>
                  <td><VenueBadge venueId={b.venue_id} name={b.venue_name} /></td>
                  <td className="text-[var(--fg-2)]">{formatWalletType(b.wallet_type, en)}</td>
                  <td className="text-right sensitive">{b.qty.toLocaleString(en ? 'en-US' : 'zh-CN', { maximumSignificantDigits: 14 })}</td>
                  <td className="text-right font-medium sensitive">{b.market_value_usd == null ? '—' : cur.money(b.market_value_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="py-4 text-[13px] text-[var(--fg-3)]">
          {en ? 'This snapshot contains no itemized wallet balances.' : '当前快照没有逐币钱包余额。'}
        </p>
      )}
      {balances.some(b => b.wallet_type === 'UNIFIED' || b.wallet_type === 'UTA') && (
        <p className="pt-2.5 m-0 text-[13px] text-[var(--fg-3)]">
          {en ? 'Unified wallet balances can also back futures margin.' : '统一账户余额可能同时用于合约保证金。'}
        </p>
      )}
    </section>
  )
}
