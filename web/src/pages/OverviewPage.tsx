import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { EquityChart } from '../components/EquityChart'
import { FloatingModal } from '../components/FloatingModal'
import { InstrumentMark } from '../components/InstrumentMark'
import { KpiCard } from '../components/KpiCard'
import { SpotWalletTable, formatWalletType } from '../components/SpotWalletTable'
import { UnderlineTabs } from '../components/UnderlineTabs'
import { VenueBadge } from '../components/VenueBadge'
import { useI18n } from '../i18n'
import { useCurrency } from '../lib/currency'
import { collectActiveWarnings, fmtContractQty, fmtPrice, fmtSignedPct, pnlTone } from '../lib/format'
import type { DashboardData, DashboardWarningItem, ExtensionSubTab, NavPage, SpotHolding } from '../lib/types'

type StockHoldingItem = DashboardData['extensions']['stocks']['holdings'][number]

const BAR_COLORS: Record<string, string> = {
  binance: 'var(--dot-binance)',
  okx: 'var(--dot-okx)',
  bybit: 'var(--dot-bybit)',
  gate: 'var(--dot-gate)',
  bitget: 'var(--dot-bitget)',
  mexc: 'var(--dot-mexc)',
  easicoin: 'var(--dot-easicoin)',
  hyperliquid: 'var(--dot-hyperliquid)',
  kucoin: 'var(--dot-kucoin)',
  bingx: 'var(--dot-bingx)',
  htx: 'var(--dot-htx)',
}

export function OverviewPage({
  data,
  activeWarnings,
  enableStockTab = false,
  enableWatcherTab = true,
  onNavigate,
  onInspectSymbol,
}: {
  data: DashboardData
  activeWarnings?: DashboardWarningItem[]
  enableStockTab?: boolean
  enableWatcherTab?: boolean
  onNavigate: (page: NavPage, extTab?: ExtensionSubTab) => void
  onInspectSymbol?: (venueId: string, symbol: string, venueName?: string) => void
}) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const warnings = useMemo(
    () => activeWarnings ?? collectActiveWarnings(data),
    [activeWarnings, data],
  )
  const [openModal, setOpenModal] = useState<null | 'equity' | 'reconcile' | 'wallet'>(null)
  const [selectedStock, setSelectedStock] = useState<StockHoldingItem | null>(null)
  const [selectedSpot, setSelectedSpot] = useState<SpotHolding | null>(null)
  const [holdingTab, setHoldingTab] = useState<'all' | 'futures' | 'stocks' | 'spot'>('all')
  const [barsReady, setBarsReady] = useState(false)

  useEffect(() => {
    const reduced =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced || typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
      setBarsReady(true)
      return
    }
    const id = window.requestAnimationFrame(() => setBarsReady(true))
    return () => window.cancelAnimationFrame(id)
  }, [])

  const showEquity = openModal === 'equity'
  const showReconcile = openModal === 'reconcile'
  const s = data.summary
  const futuresList = data.open_positions || []
  const stockList = data.extensions?.stocks?.holdings || []
  const spotList = data.spot_holdings || []
  const wallets = data.spot_wallet_balances ?? spotList
  const totalHoldingsCount = futuresList.length + stockList.length + spotList.length
  const activeCategoriesCount =
    (futuresList.length > 0 ? 1 : 0) + (stockList.length > 0 ? 1 : 0) + (spotList.length > 0 ? 1 : 0)
  const selectedCount = {
    all: totalHoldingsCount,
    futures: futuresList.length,
    stocks: stockList.length,
    spot: spotList.length,
  }[holdingTab]
  if (holdingTab !== 'all' && selectedCount === 0) setHoldingTab('all')

  const showFutures = holdingTab === 'all' || holdingTab === 'futures'
  const showStocks = holdingTab === 'all' || holdingTab === 'stocks'
  const showSpot = holdingTab === 'all' || holdingTab === 'spot'
  const totalExEquity = Math.max(
    0.0001,
    data.exchanges.reduce((acc, ex) => acc + Math.max(0, ex.equity_usd), 0),
  )
  const totalAvailableMargin = data.exchanges.reduce((acc, ex) => acc + Math.max(0, ex.available_margin_usd), 0)
  const eb = data.equity_breakdown
  const futuresBucketVal = eb ? eb.futures_margin_usd : totalAvailableMargin
  const copyActiveEq = eb ? eb.copy_equity_usd : Math.max(0, data.extensions?.copy_trading?.active_equity_usd || 0)
  const stockEq = eb ? eb.stocks_equity_usd : Math.max(0, data.extensions?.stocks?.equity_usd || 0)
  const spotTotalVal = eb
    ? eb.spot_and_other_usd
    : Math.max(0, s.total_equity_usd - futuresBucketVal - copyActiveEq - stockEq)
  const sortedExchanges = [...data.exchanges].sort((a, b) => b.equity_usd - a.equity_usd)
  const curveHeadlinePnl =
    data.equity_curve.length > 0
      ? data.equity_curve[data.equity_curve.length - 1].cum_net_usd
      : s.whole_net_pnl_usd

  const holdingFilterOptions = (
    [
      ['all', locale === 'en' ? 'All' : '全部', true],
      ['futures', locale === 'en' ? 'Futures' : '合约', futuresList.length > 0],
      ['stocks', locale === 'en' ? 'Equities' : '美股', stockList.length > 0],
      ['spot', locale === 'en' ? 'Spot' : '现货', spotList.length > 0],
    ] as const
  )
    .filter(([, , visible]) => visible)
    .map(([value, label]) => ({ value, label }))

  const futuresShare = s.total_equity_usd > 0 ? (futuresBucketVal / s.total_equity_usd) * 100 : 0
  const copyShare = s.total_equity_usd > 0 ? (copyActiveEq / s.total_equity_usd) * 100 : 0
  const stockShare = s.total_equity_usd > 0 ? (stockEq / s.total_equity_usd) * 100 : 0
  const selectedSpotShare =
    selectedSpot && s.total_equity_usd > 0
      ? (selectedSpot.market_value_usd / s.total_equity_usd) * 100
      : 0
  const spotShare = s.total_equity_usd > 0 ? (spotTotalVal / s.total_equity_usd) * 100 : 0

  return (
    <div data-testid="overview-page" className="otl-stagger">
      {!data.demo_mode && warnings.length > 0 && (
        <div
          role="status"
          data-testid="overview-warnings-banner"
          className="otl-status-warn mt-4 px-4 py-3 rounded-lg text-[12.5px] flex flex-wrap items-start justify-between gap-3"
        >
          <div className="flex items-start gap-2.5 min-w-0 flex-1">
            <AlertTriangle size={15} className="shrink-0 mt-0.5" />
            <div className="space-y-1 min-w-0">
              <div className="font-semibold">
                {locale === 'en'
                  ? 'Partial Sync Warning · Some balances or histories may be incomplete'
                  : '部分同步告警 · 以下平台存在权限受限或历史数据截断'}
              </div>
              <ul className="m-0 pl-4 list-disc space-y-0.5">
                {warnings.map(w => {
                  const name = locale === 'en' ? w.display_name : w.display_name_zh || w.display_name
                  const summary = locale === 'en' ? w.summary || w.summary_zh : w.summary_zh || w.summary
                  const extraDetails = w.warnings.filter(msg => msg && !summary.includes(msg))
                  return (
                    <li key={w.venue_id}>
                      <strong>{name}</strong>：{summary}
                      {extraDetails.length > 0 ? `（${extraDetails.join('；')}）` : ''}
                    </li>
                  )
                })}
              </ul>
            </div>
          </div>
          <button
            type="button"
            onClick={() => onNavigate('settings')}
            className="otl-btn h-7 px-2.5 shrink-0"
          >
            {locale === 'en' ? 'Manage Accounts →' : '查看账户详情 →'}
          </button>
        </div>
      )}

      {/* Flush 4-Column Horizontal Ledger Strip */}
      <section className="otl-ledger-strip otl-kpi-grid">
        <KpiCard
          testId="kpi-equity"
          label={t('kpi_total_equity')}
          badge={
            <span className="text-[12px] font-medium text-[var(--accent)]">
              {locale === 'en' ? 'Breakdown ↗' : '资产构成 ↗'}
            </span>
          }
          active={showEquity}
          onClick={() => setOpenModal(d => (d === 'equity' ? null : 'equity'))}
          value={cur.money(s.total_equity_usd)}
          sub={
            <span className="whitespace-nowrap">
              {t('kpi_net_principal')} <span className="sensitive">{cur.money(s.net_principal_usd)}</span>
            </span>
          }
        />

        <KpiCard
          testId="kpi-whole-pnl"
          label={t('kpi_whole_pnl')}
          badge={
            <span className="text-[12px] font-medium text-[var(--accent)]">
              {t('kpi_reconcile_btn')} ↗
            </span>
          }
          active={showReconcile}
          onClick={() => setOpenModal(d => (d === 'reconcile' ? null : 'reconcile'))}
          value={cur.signedMoney(s.whole_net_pnl_usd)}
          valueClass={pnlTone(s.whole_net_pnl_usd)}
          sub={
            <span>
              {Math.abs(data.reconciliation.residual_usd) < 0.01 ? (
                t('kpi_reconciled_zero')
              ) : (
                <span className="text-[var(--warn)] font-medium whitespace-nowrap">
                  {locale === 'en' ? 'Drift ' : '待核对差额 '}
                  <span data-testid="kpi-residual-drift" className="sensitive">
                    {cur.signedMoney(data.reconciliation.residual_usd)}
                  </span>
                </span>
              )}{' '}
              · <b className={`whitespace-nowrap ${pnlTone(s.whole_roi_pct)}`}>{fmtSignedPct(s.whole_roi_pct)}</b>
            </span>
          }
        />

        <KpiCard
          testId="kpi-futures-pnl"
          label={t('kpi_futures_pnl')}
          value={cur.signedMoney(s.futures_net_pnl_usd)}
          valueClass={pnlTone(s.futures_net_pnl_usd)}
          sub={
            <span>
              <span className="whitespace-nowrap">
                {s.trade_count} {locale === 'en' ? 'trades' : '笔交割'}
              </span>{' '}
              ·{' '}
              <span className="whitespace-nowrap">
                {t('kpi_winrate')} {s.trade_count > 0 ? `${s.win_rate_pct.toFixed(1)}%` : '—'}
              </span>
            </span>
          }
        />

        <KpiCard
          testId="kpi-fee-relief"
          label={t('kpi_fee_saved')}
          value={cur.signedMoney(s.total_rebate_usd + s.total_discount_saved_usd)}
          valueClass="txt-pos"
          sub={
            <span>
              <span className="whitespace-nowrap">
                {t('detail_relief_short')} {s.combined_relief_pct.toFixed(1)}%
              </span>{' '}
              ·{' '}
              <span className="whitespace-nowrap">
                {t('kpi_net_fee_paid')} <span className="sensitive">{cur.signedMoney(-s.net_fee_usd)}</span>
              </span>
            </span>
          }
        />
      </section>

      {/* Total Equity Breakdown Modal */}
      <FloatingModal
        open={showEquity}
        onClose={() => setOpenModal(null)}
        testId="equity-drawer"
        maxWidthClass="max-w-[860px]"
        title={locale === 'en' ? 'Total Equity Breakdown' : '总净资产构成明细'}
        subtitle={
          <span className="inline-flex items-center gap-3 flex-wrap">
            <span>
              {t('kpi_total_equity')}:{' '}
              <b className="text-[var(--fg)] sensitive">{cur.money(s.total_equity_usd)}</b>
            </span>
            <span>
              {t('kpi_net_principal')}:{' '}
              <b className="text-[var(--fg)] sensitive">{cur.money(s.net_principal_usd)}</b>
            </span>
          </span>
        }
      >
        <div className="border-b border-[var(--border)] grid grid-cols-2 sm:grid-cols-4 divide-y sm:divide-y-0 sm:divide-x divide-[var(--border)] text-[12px]">
          {[
            {
              title: locale === 'en' ? 'Futures Margin Equity' : '合约保证金权益',
              val: futuresBucketVal,
              pct: futuresShare,
              color: 'var(--accent)',
            },
            {
              title: locale === 'en' ? 'Active Copy Pool' : '跟单池在途净值',
              val: copyActiveEq,
              pct: copyShare,
              color: 'var(--dot-okx)',
            },
            {
              title: locale === 'en' ? 'Equities & Tokens' : '美股与股票代币',
              val: stockEq,
              pct: stockShare,
              color: 'var(--dot-binance)',
            },
            {
              wallet: true,
              title: locale === 'en' ? 'Spot, Earn & Token Dust' : '现货、理财与空投尾仓',
              val: spotTotalVal,
              pct: spotShare,
              color: 'var(--ok)',
            },
          ].map(b => {
            const Tile = b.wallet ? 'button' : 'div'
            return (
              <Tile
                key={b.title}
                {...(b.wallet ? { type: 'button' as const, onClick: () => setOpenModal('wallet') } : {})}
                className={`px-6 py-4 flex flex-col gap-1.5 text-left ${
                  b.wallet ? 'cursor-pointer hover:bg-[var(--surface-hover)]' : ''
                }`}
              >
                <div className="text-[var(--fg-3)] truncate">
                  {b.title}
                  {b.wallet && <span aria-hidden="true"> ↗</span>}
                </div>
                <div className="text-[15px] font-semibold text-[var(--fg)] sensitive">{cur.money(b.val)}</div>
                <div className="flex items-center justify-between gap-2 mt-0.5">
                  <div className="h-1 flex-1 rounded-full bg-[var(--track)] overflow-hidden">
                    <div
                      className="otl-bar-fill h-full rounded-full"
                      style={{
                        width: `${showEquity ? Math.min(100, Math.max(0, b.pct)) : 0}%`,
                        background: b.color,
                      }}
                    />
                  </div>
                  <span className="text-[var(--fg-3)]">{b.pct.toFixed(1)}%</span>
                </div>
              </Tile>
            )
          })}
        </div>

        <div className="overflow-x-auto">
          <table className="otl-table otl-modal-table">
            <thead>
              <tr>
                <th>{t('th_venue')}</th>
                <th className="text-right">
                  {locale === 'en' ? `Live Equity (${cur.code})` : `实时总资产 (${cur.code})`}
                </th>
                <th className="text-right">{locale === 'en' ? 'Share' : '资产占比'}</th>
                <th className="text-right">{locale === 'en' ? 'Available Margin' : '可用保证金'}</th>
                <th className="text-right">{t('kpi_net_principal')}</th>
                <th className="text-right">{t('kpi_whole_pnl')}</th>
              </tr>
            </thead>
            <tbody>
              {sortedExchanges.map(ex => {
                const pct = (Math.max(0, ex.equity_usd) / totalExEquity) * 100
                return (
                  <tr key={ex.venue_id}>
                    <td>
                      <div className="inline-flex items-center gap-2">
                        <VenueBadge
                          venueId={ex.venue_id}
                          name={locale === 'en' ? ex.display_name : ex.display_name_zh || ex.display_name}
                        />
                        {ex.health_status === 'archived' && (
                          <span className="text-[12px] text-[var(--fg-3)] whitespace-nowrap">
                            · {locale === 'en' ? 'Archived' : '已封存'}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="text-right font-semibold whitespace-nowrap sensitive">
                      {cur.plainMoney(ex.equity_usd)}
                    </td>
                    <td className="text-right whitespace-nowrap text-[var(--fg-2)]">
                      {pct.toFixed(1)}%
                    </td>
                    <td className="text-right whitespace-nowrap text-[var(--fg-2)] sensitive">
                      {cur.plainMoney(ex.available_margin_usd)}
                    </td>
                    <td className="text-right whitespace-nowrap text-[var(--fg-2)] sensitive">
                      {cur.plainMoney(ex.net_principal_usd)}
                    </td>
                    <td className={`text-right font-semibold whitespace-nowrap sensitive ${pnlTone(ex.net_pnl_usd)}`}>
                      {cur.signedPlainMoney(ex.net_pnl_usd)}
                    </td>
                  </tr>
                )
              })}
              <tr className="bg-[var(--surface-2)] font-semibold">
                <td>{locale === 'en' ? 'Total' : '全账户合计'}</td>
                <td className="text-right whitespace-nowrap sensitive">{cur.plainMoney(s.total_equity_usd)}</td>
                <td className="text-right whitespace-nowrap">100.0%</td>
                <td className="text-right whitespace-nowrap sensitive">{cur.plainMoney(totalAvailableMargin)}</td>
                <td className="text-right whitespace-nowrap sensitive">{cur.plainMoney(s.net_principal_usd)}</td>
                <td className={`text-right whitespace-nowrap sensitive ${pnlTone(s.whole_net_pnl_usd)}`}>
                  {cur.signedPlainMoney(s.whole_net_pnl_usd)}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </FloatingModal>

      {/* Closed-Loop Reconciliation Modal */}
      <FloatingModal
        open={showReconcile}
        onClose={() => setOpenModal(null)}
        testId="reconcile-drawer"
        maxWidthClass="max-w-[820px]"
        title={t('ov_reconcile_title')}
        subtitle={t('ov_reconcile_sub')}
      >
        <div className="px-6 py-3 bg-[var(--surface-2)] border-b border-[var(--border)] flex items-center gap-5 text-[12.5px] text-[var(--fg-2)] flex-wrap">
          <span>
            {t('kpi_net_principal')}:{' '}
            <b className="text-[var(--fg)] sensitive">{cur.money(data.reconciliation.net_principal_usd)}</b>
          </span>
          <span>
            {t('kpi_total_equity')}:{' '}
            <b className="text-[var(--fg)] sensitive">{cur.money(data.reconciliation.total_equity_usd)}</b>
          </span>
          <span>
            {t('kpi_whole_pnl')}:{' '}
            <b className={`sensitive ${pnlTone(data.reconciliation.net_pnl_usd)}`}>
              {cur.signedMoney(data.reconciliation.net_pnl_usd)}
            </b>
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="otl-table otl-modal-table">
            <thead>
              <tr>
                <th>{t('th_item')}</th>
                <th>{t('th_detail')}</th>
                <th className="text-right">
                  {locale === 'en' ? `Net Contribution (${cur.code})` : `净贡献 (${cur.code})`}
                </th>
              </tr>
            </thead>
            <tbody>
              {data.reconciliation.items.map(item => (
                <tr key={item.key}>
                  <td className="font-medium whitespace-nowrap">
                    {locale === 'en' ? item.label : item.label_zh || item.label}
                  </td>
                  <td className="text-[var(--fg-2)] sensitive">
                    {cur.convertText(locale === 'en' ? item.detail : item.detail_zh || item.detail)}
                  </td>
                  <td className={`text-right font-semibold whitespace-nowrap sensitive ${pnlTone(item.amount_usd)}`}>
                    {cur.signedPlainMoney(item.amount_usd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </FloatingModal>

      {data.goal && data.goal.enabled && (
        <div
          onClick={() => onNavigate('analytics')}
          className="py-3 px-5 border-b border-[var(--border)] flex flex-wrap items-center justify-between gap-3 text-[12.5px] cursor-pointer hover:bg-[var(--surface-hover)]"
        >
          <div className="flex items-baseline gap-2.5">
            <span className="font-semibold text-[var(--fg)]">{data.goal.label || t('an_goal_default')}</span>
            <span className="text-[var(--fg-2)] sensitive">
              {cur.plainMoney(data.goal.current_equity_usd)} / {cur.money(data.goal.target_usd)} (
              {((Math.round((Number(data.goal.progress_pct) + Number.EPSILON) * 10) / 10)).toFixed(1)}%)
            </span>
          </div>
          <div className="w-full sm:w-56 h-1.5 rounded-full bg-[var(--track)] overflow-hidden">
            <div
              className="otl-bar-fill h-full rounded-full"
              style={{
                width: `${barsReady ? Math.min(100, Math.max(0, data.goal.progress_pct)) : 0}%`,
                background: 'var(--accent)',
              }}
            />
          </div>
        </div>
      )}

      {/* 7:5 Hairline Split: Cumulative Net Equity Curve × Venue Asset Allocation */}
      <section className="grid grid-cols-1 lg:grid-cols-12 border-b border-[var(--border)]">
        <div className="lg:col-span-7 py-6 px-5 lg:pl-5 lg:pr-8 lg:border-r border-[var(--border)] flex flex-col justify-between gap-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h2 className="t-h2">{t('ov_curve_title')}</h2>
              <p className="m-0 text-[12px] text-[var(--fg-3)] mt-0.5">{t('ov_curve_sub')}</p>
            </div>
            <span className={`t-h1 sensitive whitespace-nowrap ${pnlTone(curveHeadlinePnl)}`}>
              {cur.signedMoney(curveHeadlinePnl)}
            </span>
          </div>
          <EquityChart points={data.equity_curve} height={214} />
        </div>

        <div className="lg:col-span-5 py-6 px-5 lg:pl-8 lg:pr-5 border-t lg:border-t-0 border-[var(--border)] flex flex-col justify-between gap-4">
          <div className="space-y-3">
            <div className="flex items-baseline justify-between gap-2">
              <h2 className="t-h2">{t('ov_alloc_title')}</h2>
              <div className="flex items-center gap-4 text-[12px]">
                {enableWatcherTab && (
                  <button
                    type="button"
                    data-testid="overview-open-watcher-btn"
                    onClick={() => onNavigate('extensions', 'watcher')}
                    className="text-[var(--fg-2)] hover:text-[var(--fg)] hover:underline cursor-pointer whitespace-nowrap"
                  >
                    {locale === 'en' ? 'Public Radar' : '公开盯盘'}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => onNavigate('settings')}
                  className="font-medium text-[var(--accent)] hover:underline cursor-pointer whitespace-nowrap"
                >
                  {t('ov_manage_accounts')} →
                </button>
              </div>
            </div>

            <div className="h-1.5 rounded-full overflow-hidden flex gap-0.5 bg-[var(--track)]">
              {data.exchanges.map(ex => {
                const pct = Math.max(0, (ex.equity_usd / totalExEquity) * 100)
                if (pct < 0.3) return null
                return (
                  <div
                    key={ex.venue_id}
                    className="otl-bar-fill h-full"
                    style={{
                      width: `${barsReady ? pct : 0}%`,
                      background: BAR_COLORS[ex.color_token] || 'var(--accent)',
                    }}
                  />
                )
              })}
            </div>
          </div>

          <div className="divide-y divide-[var(--border)]">
            {data.exchanges.map(ex => {
              const pct = (Math.max(0, ex.equity_usd) / totalExEquity) * 100
              return (
                <div key={ex.venue_id} className="py-2.5 flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <VenueBadge
                      venueId={ex.venue_id}
                      name={locale === 'en' ? ex.display_name : ex.display_name_zh || ex.display_name}
                    />
                    <span className="text-[12px] text-[var(--fg-3)] whitespace-nowrap">{pct.toFixed(1)}%</span>
                    {ex.health_status === 'archived' && (
                      <span
                        data-testid={`overview-archived-badge-${ex.venue_id}`}
                        title={
                          locale === 'en'
                            ? 'API unbound · local historical ledger archived'
                            : 'API 已解绑 · 本地历史账单封存保留'
                        }
                        className="text-[12px] text-[var(--fg-3)] whitespace-nowrap"
                      >
                        · {locale === 'en' ? 'Archived' : '已封存'}
                      </span>
                    )}
                    {ex.health_status === 'error' && (
                      <button
                        type="button"
                        title={locale === 'en' ? ex.error_reason : ex.error_reason_zh || ex.error_reason}
                        onClick={() => onNavigate('settings')}
                        className="text-[12px] font-medium text-[var(--err)] hover:underline cursor-pointer whitespace-nowrap"
                      >
                        · {locale === 'en' ? 'API Error' : '连接报错'}
                      </button>
                    )}
                  </div>
                  <div className="flex items-center gap-3.5 whitespace-nowrap">
                    <span className={`text-[12.5px] sensitive ${pnlTone(ex.net_pnl_usd)}`}>
                      {cur.signedPlainMoney(ex.net_pnl_usd)}
                    </span>
                    <strong className="font-semibold sensitive text-[var(--fg)]">{cur.money(ex.equity_usd)}</strong>
                  </div>
                </div>
              )
            })}
            {data.exchanges.length > 0 && (
              <div className="pt-3 flex items-center justify-between text-[12px] text-[var(--fg-2)]">
                <span>{locale === 'en' ? 'Total Available Margin' : '可用保证金合计'}</span>
                <span className="font-semibold text-[var(--fg)] sensitive">{cur.money(totalAvailableMargin)}</span>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* Flush Unified Open Holdings */}
      <section data-testid="overview-holdings" className="py-6 space-y-3">
        <div className="px-5 border-b border-[var(--border)] flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-baseline gap-2.5 py-2">
            <h2 className="t-h2">{t('ov_open_positions')}</h2>
            <span className="text-[12px] text-[var(--fg-3)]">{totalHoldingsCount}</span>
          </div>

          <div className="flex items-center flex-wrap gap-5">
            {activeCategoriesCount > 1 && (
              <UnderlineTabs
                value={holdingTab}
                onChange={setHoldingTab}
                options={holdingFilterOptions}
              />
            )}
            {(spotTotalVal > 0 || wallets.length > 0) && (
              <button
                type="button"
                className="text-[12.5px] font-medium text-[var(--accent)] hover:underline cursor-pointer py-2"
                onClick={() => setOpenModal('wallet')}
              >
                {locale === 'en' ? 'Wallet Balances' : '钱包明细'}
              </button>
            )}
          </div>
        </div>

        {totalHoldingsCount === 0 ? (
          <div className="py-10 text-center text-[13px] text-[var(--fg-3)]">{t('ov_no_open_positions')}</div>
        ) : (
          <div key={holdingTab} className="otl-panel-swap overflow-x-auto">
            <table className="otl-table">
              <thead>
                <tr>
                  <th>{t('th_symbol')}</th>
                  <th>{t('th_venue')}</th>
                  <th>{locale === 'en' ? 'Type / Side' : '类型 / 方向'}</th>
                  <th className="text-right">{locale === 'en' ? 'Size / Value' : '持仓规模 / 市值'}</th>
                  <th className="text-right">{locale === 'en' ? 'Cost / Live Price' : '均价 / 现价'}</th>
                  <th className="text-right">{locale === 'en' ? 'PnL / Weight' : '盈亏 / 占比'}</th>
                </tr>
              </thead>
              <tbody>
                {showFutures &&
                  futuresList.map(pos => (
                    <tr
                      key={`fut-${pos.venue_id}-${pos.symbol}-${pos.side}`}
                      className={onInspectSymbol ? 'clickable' : undefined}
                      onClick={() => onInspectSymbol?.(pos.venue_id, pos.symbol, pos.venue_name)}
                    >
                      <td className="whitespace-nowrap">
                        <span className="inline-flex items-center gap-2 font-semibold">
                          <InstrumentMark instrument={pos.symbol} exchange={pos.venue_id.toUpperCase()} />
                          {pos.symbol}
                        </span>
                      </td>
                      <td>
                        <VenueBadge venueId={pos.venue_id} name={pos.venue_name} />
                      </td>
                      <td className="whitespace-nowrap">
                        <span className={`font-semibold ${pos.side === 'LONG' ? 'txt-pos' : 'txt-neg'}`}>
                          {pos.side === 'LONG' ? t('side_long') : t('side_short')} {pos.leverage}x
                        </span>
                      </td>
                      <td className="text-right whitespace-nowrap sensitive">
                        {pos.contract_count != null ? fmtContractQty(pos, locale) : pos.qty} ({cur.money(pos.notional_usd, 0)})
                      </td>
                      <td className="text-right whitespace-nowrap text-[var(--fg-2)]">
                        {fmtPrice(pos.entry_price)} → {fmtPrice(pos.mark_price)}
                      </td>
                      <td className={`text-right font-semibold whitespace-nowrap sensitive ${pnlTone(pos.unrealized_pnl_usd)}`}>
                        {cur.signedMoney(pos.unrealized_pnl_usd)}
                        {Math.abs(pos.unrealized_roi_pct) >= 0.01 ? ` (${fmtSignedPct(pos.unrealized_roi_pct)})` : ''}
                      </td>
                    </tr>
                  ))}

                {showStocks &&
                  stockList.map(h => (
                    <tr
                      key={`stk-${h.venue_id}-${h.symbol}`}
                      className="clickable"
                      onClick={() => setSelectedStock(h)}
                    >
                      <td className="whitespace-nowrap">
                        <span className="inline-flex items-center gap-2 font-semibold">
                          <InstrumentMark instrument={h.symbol} exchange="US" stock />
                          {h.symbol}
                        </span>
                      </td>
                      <td>
                        <VenueBadge venueId={h.venue_id} name={h.venue_name} />
                      </td>
                      <td className="whitespace-nowrap text-[var(--fg-2)]">
                        {locale === 'en' ? h.channel : h.channel_zh}
                      </td>
                      <td className="text-right whitespace-nowrap sensitive">
                        {h.shares} ({cur.money(h.market_value_usd)})
                      </td>
                      <td className="text-right whitespace-nowrap text-[var(--fg-2)]">
                        {fmtPrice(h.avg_cost_usd)} → {fmtPrice(h.current_price_usd)}
                      </td>
                      <td className={`text-right font-semibold whitespace-nowrap sensitive ${pnlTone(h.pnl_usd)}`}>
                        {cur.signedMoney(h.pnl_usd)} ({fmtSignedPct(h.pnl_pct)})
                      </td>
                    </tr>
                  ))}

                {showSpot &&
                  spotList.map(sp => {
                    const weightPct = s.total_equity_usd > 0 ? (sp.market_value_usd / s.total_equity_usd) * 100 : 0
                    return (
                      <tr
                        key={`spot-${sp.venue_id}-${sp.asset}`}
                        className="clickable"
                        onClick={() => setSelectedSpot(sp)}
                      >
                        <td className="whitespace-nowrap">
                          <span className="inline-flex items-center gap-2 font-semibold">
                            <InstrumentMark instrument={`${sp.asset}_USDT`} exchange={sp.venue_id.toUpperCase()} />
                            {sp.asset}
                          </span>
                        </td>
                        <td>
                          <VenueBadge venueId={sp.venue_id} name={sp.venue_name} />
                        </td>
                        <td className="whitespace-nowrap text-[var(--fg-2)]">
                          {locale === 'en' ? 'Spot' : '现货'}
                        </td>
                        <td className="text-right whitespace-nowrap sensitive">
                          {sp.qty} ({cur.money(sp.market_value_usd)})
                        </td>
                        <td className="text-right whitespace-nowrap text-[var(--fg-2)]">
                          {sp.price_usd > 0 ? fmtPrice(sp.price_usd) : '—'}
                        </td>
                        <td className="text-right whitespace-nowrap text-[var(--fg-2)]">
                          {locale === 'en' ? `${weightPct.toFixed(1)}% weight` : `占比 ${weightPct.toFixed(1)}%`}
                        </td>
                      </tr>
                    )
                  })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Stock / Tokenized Equity Detail Modal */}
      <FloatingModal
        open={selectedStock !== null}
        onClose={() => setSelectedStock(null)}
        testId="stock-holding-detail-modal"
        maxWidthClass="max-w-[700px]"
        title={
          selectedStock
            ? `${selectedStock.symbol}${
                selectedStock.name && selectedStock.name !== selectedStock.symbol ? ` · ${selectedStock.name}` : ''
              }`
            : ''
        }
        subtitle={
          selectedStock ? (
            <span className="inline-flex items-center gap-2 flex-wrap">
              <VenueBadge
                venueId={selectedStock.venue_id}
                name={locale === 'en' ? selectedStock.venue_name : selectedStock.venue_name_zh || selectedStock.venue_name}
              />
              <span className="text-[12px] text-[var(--fg-3)]">
                · {locale === 'en' ? selectedStock.channel : selectedStock.channel_zh}
              </span>
            </span>
          ) : undefined
        }
      >
        {selectedStock && (() => {
          const stkWeightPct = s.total_equity_usd > 0 ? (selectedStock.market_value_usd / s.total_equity_usd) * 100 : 0
          const venueRate = data.extensions?.stocks?.venue_fee_rates?.find(
            r => r.venue_id.toLowerCase() === selectedStock.venue_id.toLowerCase(),
          )
          const relatedLedger = (data.extensions?.stocks?.cash_ledger || []).filter(
            tx =>
              tx.venue_id.toLowerCase() === selectedStock.venue_id.toLowerCase() &&
              (tx.symbol === selectedStock.symbol || tx.symbol === 'USDT' || !tx.symbol),
          )
          return (
            <div className="p-6 space-y-4 text-[12px]">
              <div className="grid grid-cols-2 sm:grid-cols-4 border border-[var(--border)] rounded-md divide-y sm:divide-y-0 sm:divide-x divide-[var(--border)]">
                <div className="p-4 space-y-1">
                  <div className="text-[var(--fg-3)]">{locale === 'en' ? 'Shares / Qty' : '持仓数量'}</div>
                  <div className="text-[14px] font-semibold text-[var(--fg)] sensitive">{selectedStock.shares}</div>
                </div>
                <div className="p-4 space-y-1">
                  <div className="text-[var(--fg-3)]">{locale === 'en' ? 'Market Value' : '实时市值'}</div>
                  <div className="text-[14px] font-semibold text-[var(--fg)] sensitive">
                    {cur.money(selectedStock.market_value_usd)}
                  </div>
                  <div className="text-[var(--fg-3)]">
                    {locale === 'en' ? `${stkWeightPct.toFixed(2)}% of total` : `占总净资产 ${stkWeightPct.toFixed(2)}%`}
                  </div>
                </div>
                <div className="p-4 space-y-1">
                  <div className="text-[var(--fg-3)]">{locale === 'en' ? 'Avg Cost → Live' : '均价 → 现价'}</div>
                  <div className="text-[13.5px] font-semibold text-[var(--fg)]">
                    {fmtPrice(selectedStock.avg_cost_usd)} → {fmtPrice(selectedStock.current_price_usd)}
                  </div>
                </div>
                <div className="p-4 space-y-1">
                  <div className="text-[var(--fg-3)]">{locale === 'en' ? 'Unrealized PnL' : '持仓浮盈亏'}</div>
                  <div className={`text-[14px] font-semibold sensitive ${pnlTone(selectedStock.pnl_usd)}`}>
                    {cur.signedMoney(selectedStock.pnl_usd)} ({fmtSignedPct(selectedStock.pnl_pct)})
                  </div>
                  {venueRate && (
                    <div className="text-[var(--fg-3)]">
                      {locale === 'en'
                        ? `Fee: ${venueRate.summary}`
                        : `费率：${venueRate.summary_zh || venueRate.summary}`}
                    </div>
                  )}
                </div>
              </div>

              {relatedLedger.length > 0 && (
                <div className="border-t border-[var(--border)] pt-3">
                  <div className="pb-2 text-[13px] font-semibold text-[var(--fg)]">
                    {locale === 'en' ? 'Subscription & Cash Ledger' : '关联认购与资金交割流水'}
                  </div>
                  <div className="overflow-x-auto max-h-52 border-t border-[var(--border)]">
                    <table className="otl-table">
                      <thead>
                        <tr>
                          <th>{t('th_time')}</th>
                          <th>{locale === 'en' ? 'Event' : '类型'}</th>
                          <th>{t('th_symbol')}</th>
                          <th className="text-right">{t('th_change')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {relatedLedger.map((tx, idx) => (
                          <tr key={idx}>
                            <td className="whitespace-nowrap text-[var(--fg-2)]">{tx.time_str}</td>
                            <td>{locale === 'en' ? tx.type : tx.type_zh || tx.type}</td>
                            <td className="font-medium whitespace-nowrap">{tx.symbol}</td>
                            <td className="text-right font-semibold whitespace-nowrap sensitive">
                              {cur.convertText(tx.change)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {enableStockTab && (
                <div className="flex justify-end pt-1">
                  <button
                    type="button"
                    data-testid="stock-detail-open-ext-btn"
                    onClick={() => {
                      setSelectedStock(null)
                      onNavigate('extensions', 'stock')
                    }}
                    className="otl-btn"
                  >
                    {locale === 'en' ? 'Open Equities Account →' : '查看完整股票与代币专账 →'}
                  </button>
                </div>
              )}
            </div>
          )
        })()}
      </FloatingModal>

      {/* Spot Holding Detail Modal */}
      <FloatingModal
        open={selectedSpot !== null || openModal === 'wallet'}
        onClose={() => {
          setSelectedSpot(null)
          setOpenModal(null)
        }}
        testId="spot-holding-detail-modal"
        maxWidthClass="max-w-[720px]"
        title={
          selectedSpot
            ? locale === 'en'
              ? `${selectedSpot.asset} · Spot Holding`
              : `${selectedSpot.asset} · 现货资产明细`
            : locale === 'en'
              ? 'Wallet balances'
              : '钱包明细'
        }
        subtitle={
          selectedSpot ? (
            <span className="inline-flex items-center gap-2 flex-wrap">
              <VenueBadge venueId={selectedSpot.venue_id} name={selectedSpot.venue_name} />
              <span className="text-[12px] text-[var(--fg-3)]">
                · {formatWalletType(selectedSpot.wallet_type, locale === 'en')}
              </span>
            </span>
          ) : undefined
        }
      >
        <div className="p-6 space-y-4 text-[12px]">
          {selectedSpot && (
            <div className="grid grid-cols-2 sm:grid-cols-4 border border-[var(--border)] rounded-md divide-y sm:divide-y-0 sm:divide-x divide-[var(--border)]">
              <div className="p-4 space-y-1">
                <div className="text-[var(--fg-3)]">{locale === 'en' ? 'Quantity' : '持仓数量'}</div>
                <div className="text-[14px] font-semibold text-[var(--fg)] sensitive">
                  {selectedSpot.qty} {selectedSpot.asset}
                </div>
              </div>
              <div className="p-4 space-y-1">
                <div className="text-[var(--fg-3)]">{locale === 'en' ? 'Market Value' : '折合市值'}</div>
                <div className="text-[14px] font-semibold text-[var(--fg)] sensitive">
                  {cur.money(selectedSpot.market_value_usd)}
                </div>
              </div>
              <div className="p-4 space-y-1">
                <div className="text-[var(--fg-3)]">{locale === 'en' ? 'Reference Price' : '实时参考单价'}</div>
                <div className="text-[13.5px] font-semibold text-[var(--fg)]">
                  {selectedSpot.price_usd > 0 ? fmtPrice(selectedSpot.price_usd) : '—'}
                </div>
              </div>
              <div className="p-4 space-y-1">
                <div className="text-[var(--fg-3)]">{locale === 'en' ? 'Portfolio Share' : '占总净资产'}</div>
                <div className="text-[14px] font-semibold text-[var(--fg)]">{selectedSpotShare.toFixed(2)}%</div>
              </div>
            </div>
          )}
          <SpotWalletTable balances={wallets} spotTotal={spotTotalVal} selected={selectedSpot} />
        </div>
      </FloatingModal>
    </div>
  )
}
