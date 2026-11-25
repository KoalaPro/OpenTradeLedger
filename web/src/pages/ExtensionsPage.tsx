import { useEffect, useState } from 'react'
import { InstrumentMark } from '../components/InstrumentMark'
import { KpiCard } from '../components/KpiCard'
import { PublicCopyWatcherPanel } from '../components/PublicCopyWatcherPanel'
import { SegmentedPills } from '../components/SegmentedPills'
import { VenueBadge } from '../components/VenueBadge'
import { useI18n } from '../i18n'
import { useCurrency } from '../lib/currency'
import { fmtSignedPct, fmtUsd, pnlTone } from '../lib/format'
import type { DashboardData, ExtensionSubTab } from '../lib/types'

export function ExtensionsPage({
  data,
  enableWatcher = true,
  enableCopy,
  enableStocks,
  requestedTab,
  onTabChange,
  onInspectSymbol,
  onGoSettings,
}: {
  data: DashboardData
  enableWatcher?: boolean
  enableCopy: boolean
  enableStocks: boolean
  requestedTab?: ExtensionSubTab
  onTabChange?: (tab: ExtensionSubTab) => void
  onInspectSymbol?: (venueId: string, symbol: string, venueName?: string) => void
  onGoSettings?: () => void
}) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const vName = (en: string, zh?: string) => (locale === 'en' ? en : zh || en)

  const pickFallbackTab = (): ExtensionSubTab => {
    if (enableWatcher) return 'watcher'
    if (enableCopy) return 'copy'
    return 'stock'
  }

  const resolveInitialTab = (req?: ExtensionSubTab): ExtensionSubTab => {
    if (req === 'watcher' && enableWatcher) return 'watcher'
    if (req === 'copy' && enableCopy) return 'copy'
    if (req === 'stock' && enableStocks) return 'stock'
    return pickFallbackTab()
  }

  const [subTab, setSubTab] = useState<ExtensionSubTab>(() => resolveInitialTab(requestedTab))

  useEffect(() => {
    if (requestedTab) {
      setSubTab(resolveInitialTab(requestedTab))
    }
  }, [requestedTab, enableWatcher, enableCopy, enableStocks])

  const activeTab: ExtensionSubTab =
    (subTab === 'watcher' && enableWatcher) ||
    (subTab === 'copy' && enableCopy) ||
    (subTab === 'stock' && enableStocks)
      ? subTab
      : pickFallbackTab()

  const handleSelectTab = (next: ExtensionSubTab) => {
    setSubTab(next)
    onTabChange?.(next)
  }

  const cp = data.extensions.copy_trading
  const st = data.extensions.stocks
  const holdingVenueSet = new Set(st.holdings.map(h => h.venue_id.toLowerCase()))
  const activeFeeRates = st.venue_fee_rates
    .filter(vr => holdingVenueSet.has(vr.venue_id.toLowerCase()))
    .map(vr => {
      const rawSummary = locale === 'en' ? vr.summary : vr.summary_zh || vr.summary
      const dispVenue = vName(vr.venue_name, vr.venue_name_zh)
      const cleaned = rawSummary
        .replace(new RegExp(`^${vr.venue_name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*`, 'i'), '')
        .replace(new RegExp(`^${dispVenue.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*`, 'i'), '')
        .replace(new RegExp(`^${vr.venue_id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*`, 'i'), '')
        .trim()
      return { ...vr, dispVenue, cleanedSummary: cleaned || rawSummary }
    })
  const activeRateHero = (() => {
    if (!activeFeeRates.length) return '—'
    const pcts = activeFeeRates
      .flatMap(r => Array.from(r.cleanedSummary.matchAll(/(\d+(?:\.\d+)?)%/g)).map(m => Number(m[1])))
      .filter(n => Number.isFinite(n))
    if (!pcts.length) return activeFeeRates[0].cleanedSummary
    const lo = Math.min(...pcts)
    const hi = Math.max(...pcts)
    return lo === hi ? `${lo}%` : `${lo}% ~ ${hi}%`
  })()

  return (
    <div data-testid="extensions-page" className="otl-stagger space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SegmentedPills
          value={activeTab}
          onChange={handleSelectTab}
          options={[
            ...(enableWatcher ? [{ value: 'watcher' as const, label: t('ext_watcher_tab'), testId: 'ext-tab-watcher' }] : []),
            ...(enableCopy ? [{ value: 'copy' as const, label: t('ext_copy_tab'), testId: 'ext-tab-copy' }] : []),
            ...(enableStocks ? [{ value: 'stock' as const, label: t('ext_stock_tab'), testId: 'ext-tab-stock' }] : []),
          ]}
        />
        <span className="text-[12px] text-[var(--fg-3)]">{t('ext_toggle_hint')}</span>
      </div>

      {activeTab === 'watcher' && enableWatcher && (
        <PublicCopyWatcherPanel onGoConfigureBark={onGoSettings} />
      )}

      {activeTab === 'copy' && enableCopy && (
        <div key="copy" data-testid="copy-module-view" className="otl-panel-swap space-y-4">
          <div className="otl-kpi-grid grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
            <KpiCard label={t('cp_invested')} value={cur.money(cp.total_invested_usd)} />
            <KpiCard
              label={t('cp_active_eq')}
              value={cur.money(cp.active_equity_usd)}
              sub={
                <span data-testid="copy-book-pnl-sub" className={`sensitive ${pnlTone(cp.book_pnl_usd)}`}>
                  {cur.signedMoney(cp.book_pnl_usd)}
                </span>
              }
            />
            <KpiCard
              label={t('cp_rebates')}
              value={cur.signedMoney(cp.rebate_total_usd)}
              valueClass="txt-pos"
            />
            <KpiCard
              label={t('cp_combined')}
              value={cur.signedMoney(cp.combined_pnl_usd)}
              valueClass={pnlTone(cp.combined_pnl_usd)}
              sub={fmtSignedPct(cp.combined_roi_pct)}
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
            <section className="otl-card overflow-hidden">
              <div className="px-5 py-3 border-b border-[var(--border)] text-[14px] font-semibold">
                {t('cp_transfers')}
              </div>
              <div className="overflow-x-auto">
                <table className="otl-table">
                  <thead>
                    <tr>
                      <th>{t('th_venue')}</th>
                      <th>{t('th_time')}</th>
                      <th>{t('th_direction')}</th>
                      <th className="text-right">
                        {locale === 'en' ? `Amount (${cur.code})` : `金额 (${cur.code})`}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {cp.transfers.map((tr, i) => (
                      <tr key={i}>
                        <td>
                          <VenueBadge venueId={tr.venue_id} name={vName(tr.venue_name, tr.venue_name_zh)} />
                        </td>
                        <td className="whitespace-nowrap text-[var(--fg-2)]">{tr.time_str}</td>
                        <td>{locale === 'en' ? tr.direction : tr.direction_zh || tr.direction}</td>
                        <td className="text-right font-semibold whitespace-nowrap sensitive">
                          {cur.plainMoney(tr.amount_usd)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="otl-card overflow-hidden">
              <div className="px-5 py-3 border-b border-[var(--border)] text-[14px] font-semibold">
                {t('cp_platforms')}
              </div>
              <div className="overflow-x-auto">
                <table className="otl-table">
                  <thead>
                    <tr>
                      <th>{t('th_venue')}</th>
                      <th className="text-right">{t('cp_invested')}</th>
                      <th className="text-right">{t('cp_rebates')}</th>
                      <th className="text-right">{t('th_net_pnl')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cp.platforms.map(p => (
                      <tr key={p.venue_id}>
                        <td className="whitespace-nowrap">
                          <VenueBadge venueId={p.venue_id} name={vName(p.venue_name, p.venue_name_zh)} />
                        </td>
                        <td className="text-right whitespace-nowrap sensitive">{cur.money(p.invested_usd)}</td>
                        <td className="text-right whitespace-nowrap txt-pos sensitive">
                          +{cur.money(p.rebate_usd)} ({p.rebate_count})
                        </td>
                        <td className={`text-right font-semibold whitespace-nowrap sensitive ${pnlTone(p.combined_pnl_usd)}`}>
                          {cur.signedMoney(p.combined_pnl_usd)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>

          {/* Full-width 5-column Copy Rebate Stream — zero truncation */}
          <section data-testid="copy-rebates-fullwidth" className="otl-card overflow-hidden">
            <div className="px-5 py-3 border-b border-[var(--border)] text-[14px] font-semibold">
              {t('cp_rebate_stream')}
            </div>
            <div className="overflow-x-auto">
              <table className="otl-table">
                <thead>
                  <tr>
                    <th>{t('th_venue')}</th>
                    <th>{t('th_time')}</th>
                    <th>{t('th_dest')}</th>
                    <th className="text-right">{t('th_est_fee')}</th>
                    <th className="text-right">{t('th_real_rebate')}</th>
                  </tr>
                </thead>
                <tbody>
                  {cp.rebates.map((rb, i) => (
                    <tr key={i}>
                      <td>
                        <VenueBadge venueId={rb.venue_id} name={vName(rb.venue_name, rb.venue_name_zh)} />
                      </td>
                      <td className="whitespace-nowrap text-[var(--fg-2)]">{rb.time_str}</td>
                      <td className="text-[var(--fg-2)]">
                        {locale === 'en' ? rb.destination : rb.destination_zh || rb.destination}
                      </td>
                      <td className="text-right whitespace-nowrap text-[var(--fg-3)] sensitive">
                        ~{cur.money(rb.est_gross_fee_usd, 3)}
                      </td>
                      <td className="text-right font-semibold whitespace-nowrap txt-pos sensitive">
                        +{cur.money(rb.rebate_usd, 4)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}

      {activeTab === 'stock' && enableStocks && (
        <div key="stock" data-testid="stock-module-view" className="otl-panel-swap space-y-4">
          <div className="otl-kpi-grid grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
            <KpiCard label={t('st_equity')} value={cur.money(st.equity_usd)} />
            <KpiCard
              label={t('st_pnl')}
              value={cur.signedMoney(st.unrealized_pnl_usd)}
              valueClass={pnlTone(st.unrealized_pnl_usd)}
            />
            <KpiCard
              label={locale === 'en' ? 'Holding Venue Fee' : '持仓平台手续费'}
              value={activeRateHero}
              sensitive={false}
              sub={
                activeFeeRates.length > 0 ? (
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    {activeFeeRates.map((vr, idx) => (
                      <span
                        key={vr.venue_id}
                        className="px-1.5 py-0.5 rounded text-[10.5px] bg-[var(--surface-2)] text-[var(--fg-2)] whitespace-nowrap"
                      >
                        {idx > 0 ? ' ' : ''}{vr.dispVenue} {vr.cleanedSummary}
                      </span>
                    ))}
                  </div>
                ) : (
                  <span className="text-[11px] text-[var(--fg-3)]">
                    {locale === 'en' ? 'No active equity holdings' : '暂无正股 / 代币持仓'}
                  </span>
                )
              }
            />
            <KpiCard label={t('st_fee')} value={cur.money(st.non_perp_fees_usd, 4)} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
            <section className="lg:col-span-7 otl-card overflow-hidden">
              <div className="px-5 py-3 border-b border-[var(--border)] text-[14px] font-semibold">
                {t('st_holdings')}
              </div>
              {st.holdings.length === 0 ? (
                <div className="py-10 text-center text-[12.5px] text-[var(--fg-3)]">{t('st_empty_holdings')}</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="otl-table">
                    <thead>
                      <tr>
                        <th>{t('th_symbol')}</th>
                        <th>{t('th_venue')}</th>
                        <th className="text-right">{t('th_qty')}</th>
                        <th className="text-right">{t('th_entry_mark')}</th>
                        <th className="text-right">{t('th_upnl')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {st.holdings.map(h => (
                        <tr key={`${h.venue_id}-${h.symbol}`}>
                          <td className="whitespace-nowrap">
                            <span className="inline-flex items-center gap-2 font-semibold">
                              <InstrumentMark instrument={h.symbol} exchange="US" stock />
                              {h.symbol}
                            </span>
                            <span className="text-[11px] text-[var(--fg-3)] ml-1.5">
                              {locale === 'en' ? h.channel : h.channel_zh}
                            </span>
                          </td>
                          <td>
                            <VenueBadge venueId={h.venue_id} name={vName(h.venue_name, h.venue_name_zh)} />
                          </td>
                          <td className="text-right whitespace-nowrap sensitive">{h.shares}</td>
                          <td className="text-right whitespace-nowrap text-[var(--fg-2)]">
                            {fmtUsd(h.avg_cost_usd)} → {fmtUsd(h.current_price_usd)}
                          </td>
                          <td className={`text-right font-semibold whitespace-nowrap sensitive ${pnlTone(h.pnl_usd)}`}>
                            {cur.signedMoney(h.pnl_usd)} ({fmtSignedPct(h.pnl_pct)})
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="lg:col-span-5 otl-card overflow-hidden">
              <div className="px-5 py-3 border-b border-[var(--border)] text-[14px] font-semibold">
                {t('st_cash')}
              </div>
              <div className="overflow-x-auto">
                <table className="otl-table">
                  <thead>
                    <tr>
                      <th>{t('th_time')}</th>
                      <th>{t('th_venue')}</th>
                      <th>{t('th_symbol')}</th>
                      <th className="text-right">{t('th_change')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {st.cash_ledger.map((tx, i) => (
                      <tr key={i}>
                        <td className="whitespace-nowrap text-[var(--fg-2)]">{tx.time_str}</td>
                        <td>
                          <VenueBadge venueId={tx.venue_id} name={vName(tx.venue_name, tx.venue_name_zh)} />
                        </td>
                        <td className="font-medium whitespace-nowrap">{tx.symbol}</td>
                        <td className="text-right font-semibold whitespace-nowrap sensitive">
                          {cur.convertText(tx.change)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        </div>
      )}
    </div>
  )
}
