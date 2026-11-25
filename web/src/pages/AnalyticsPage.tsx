import { useEffect, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { AnimatedCollapse } from '../components/AnimatedCollapse'
import { EquityChart } from '../components/EquityChart'
import { InstrumentMark } from '../components/InstrumentMark'
import { KpiCard } from '../components/KpiCard'
import { MonthlyCalendar } from '../components/MonthlyCalendar'
import { UnderlineTabs } from '../components/UnderlineTabs'
import { VenueBadge } from '../components/VenueBadge'
import { useI18n } from '../i18n'
import { useCurrency } from '../lib/currency'
import { fmtSignedPct, pnlTone } from '../lib/format'
import type { DashboardData, UserSettings } from '../lib/types'

export function AnalyticsPage({
  data,
  rangeKey,
  onInspectSymbol,
  settings,
  onUpdateSettings,
}: {
  data: DashboardData
  rangeKey: string
  onRangeChange?: (rk: string) => void
  onInspectSymbol?: (venueId: string, symbol: string, venueName?: string) => void
  settings?: UserSettings
  onUpdateSettings?: (patch: Record<string, unknown>) => Promise<void | string>
}) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const [attrMode, setAttrMode] = useState<'symbol' | 'business'>('symbol')
  const [calMonth, setCalMonth] = useState<string>('')
  const [editingGoal, setEditingGoal] = useState(false)
  const [goalLabel, setGoalLabel] = useState(settings?.goal_label || data.goal?.label || '')
  const [goalAmount, setGoalAmount] = useState(
    String(settings?.goal_target_usd ?? data.goal?.target_usd ?? ''),
  )
  const [savingGoal, setSavingGoal] = useState(false)
  const [barReady, setBarReady] = useState(false)

  useEffect(() => {
    const reduced =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced || typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
      setBarReady(true)
      return
    }
    const id = window.requestAnimationFrame(() => setBarReady(true))
    return () => window.cancelAnimationFrame(id)
  }, [])

  useEffect(() => {
    if (!editingGoal) {
      setGoalLabel(settings?.goal_label || data.goal?.label || '')
      setGoalAmount(String(settings?.goal_target_usd ?? data.goal?.target_usd ?? ''))
    }
  }, [settings?.goal_label, settings?.goal_target_usd, data.goal?.label, data.goal?.target_usd, editingGoal])

  const s = data.summary
  const activeWholePnl = rangeKey === 'all' ? s.whole_net_pnl_usd : (s.range_whole_net_pnl_usd ?? s.whole_net_pnl_usd)
  const activeWholeRoi = rangeKey === 'all' ? s.whole_roi_pct : (s.range_whole_roi_pct ?? s.whole_roi_pct)
  const activeBusinessItems =
    rangeKey === 'all' ? data.reconciliation.items : (data.reconciliation.range_items ?? data.reconciliation.items)

  const handleSaveGoal = async () => {
    if (!onUpdateSettings) return
    const num = Number(goalAmount)
    if (!Number.isFinite(num) || num <= 0) return
    setSavingGoal(true)
    try {
      const failure = await onUpdateSettings({
        goal_enabled: true,
        goal_label: goalLabel.trim(),
        goal_target_usd: num,
      })
      if (!failure) setEditingGoal(false)
    } finally {
      setSavingGoal(false)
    }
  }

  const handleDisableGoal = async () => {
    if (!onUpdateSettings) return
    setSavingGoal(true)
    try {
      const failure = await onUpdateSettings({ goal_enabled: false })
      if (!failure) setEditingGoal(false)
    } finally {
      setSavingGoal(false)
    }
  }

  return (
    <div data-testid="analytics-page" className="otl-stagger">
      {/* Compact Single-Row Custom Equity Goal Strip or Inline Setup Trigger */}
      {data.goal && data.goal.enabled ? (
        <section
          data-testid="custom-goal-banner"
          className="py-4 border-b border-[var(--border)]"
        >
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex flex-wrap items-baseline gap-3 text-[13px]">
              <span className="text-[14px] font-semibold text-[var(--fg)]">
                {data.goal.label || t('an_goal_default')}
              </span>
              <span className="text-[var(--fg-2)] whitespace-nowrap sensitive">
                {cur.plainMoney(data.goal.current_equity_usd)} / {cur.money(data.goal.target_usd)} (
                {data.goal.progress_pct.toFixed(1)}%)
              </span>
              <span className="text-[var(--fg-3)]">
                {data.goal.is_reached ? t('an_goal_surplus') : t('an_goal_remaining')}{' '}
                <b className="text-[var(--fg)] sensitive">
                  {cur.money(data.goal.is_reached ? data.goal.surplus_usd : data.goal.remaining_usd)}
                </b>{' '}
                · {t('an_goal_today')}{' '}
                <b className={`sensitive ${pnlTone(data.goal.today_delta_usd)}`}>
                  {cur.signedMoney(data.goal.today_delta_usd)}
                </b>
              </span>
            </div>

            <div className="flex items-center gap-4 flex-1 max-w-[380px] min-w-[220px]">
              <div className="h-1.5 flex-1 rounded-full bg-[var(--track)] overflow-hidden">
                <div
                  className="otl-bar-fill h-full rounded-full"
                  style={{
                    width: `${barReady ? Math.min(100, Math.max(0, data.goal.progress_pct)) : 0}%`,
                    background: 'var(--accent)',
                  }}
                />
              </div>
              {onUpdateSettings && (
                <button
                  type="button"
                  data-no-press="true"
                  data-testid="edit-goal-btn"
                  onClick={() => setEditingGoal(v => !v)}
                  className="inline-flex items-center gap-1 text-[13px] font-medium text-[var(--accent)] hover:underline bg-transparent border-0 p-0 cursor-pointer shrink-0"
                >
                  <span>{locale === 'en' ? 'Edit Goal' : '调整目标'}</span>
                  <ChevronDown
                    size={13}
                    className={`transition-transform duration-150 ${editingGoal ? 'rotate-180' : ''}`}
                  />
                </button>
              )}
            </div>
          </div>

          {onUpdateSettings && (
            <AnimatedCollapse
              open={editingGoal}
              innerClassName="pt-3.5 flex flex-wrap items-center gap-2.5"
            >
              <input
                type="text"
                value={goalLabel}
                onChange={e => setGoalLabel(e.target.value)}
                placeholder={locale === 'en' ? 'Goal name (e.g. 2026 Equity Target)' : '目标名称（如：阶段净值目标）'}
                className="otl-input flex-1 min-w-[160px]"
              />
              <input
                type="number"
                value={goalAmount}
                onChange={e => setGoalAmount(e.target.value)}
                placeholder={t('set_goal_target')}
                className="otl-input w-44"
              />
              <button
                type="button"
                disabled={savingGoal}
                onClick={() => void handleSaveGoal()}
                className="otl-btn otl-btn-primary h-[34px]"
              >
                {t('save_config')}
              </button>
              <button
                type="button"
                disabled={savingGoal}
                onClick={() => void handleDisableGoal()}
                className="otl-btn h-[34px] text-[var(--fg-2)]"
              >
                {locale === 'en' ? 'Hide Goal' : '关闭进度条'}
              </button>
            </AnimatedCollapse>
          )}
        </section>
      ) : (
        onUpdateSettings && (
          <div className="py-3.5 border-b border-[var(--border)]">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-[13px] text-[var(--fg-3)]">
                {locale === 'en' ? 'Custom equity goal bar is currently hidden' : '自定义净值目标进度条当前已隐藏'}
              </span>
              <button
                type="button"
                data-testid="setup-goal-btn"
                onClick={() => setEditingGoal(v => !v)}
                className="otl-btn"
              >
                {locale === 'en' ? 'Set Custom Equity Goal' : '设置自定义净值目标'}
              </button>
            </div>
            <AnimatedCollapse open={editingGoal} innerClassName="pt-3">
              <div className="flex flex-wrap items-center gap-2.5">
                <input
                  type="text"
                  value={goalLabel}
                  onChange={e => setGoalLabel(e.target.value)}
                  placeholder={locale === 'en' ? 'Goal name (e.g. Milestone 50k)' : '目标名称（如：阶段净值目标）'}
                  className="otl-input flex-1 min-w-[160px]"
                />
                <input
                  type="number"
                  value={goalAmount}
                  onChange={e => setGoalAmount(e.target.value)}
                  placeholder={t('set_goal_target')}
                  className="otl-input w-44"
                />
                <button
                  type="button"
                  disabled={savingGoal}
                  onClick={() => void handleSaveGoal()}
                  className="otl-btn otl-btn-primary h-[34px]"
                >
                  {t('save_config')}
                </button>
                <button
                  type="button"
                  onClick={() => setEditingGoal(false)}
                  className="otl-btn h-[34px]"
                >
                  {t('cancel')}
                </button>
              </div>
            </AnimatedCollapse>
          </div>
        )
      )}

      {/* Flush 4-Column Horizontal Ledger Strip */}
      <section className="otl-ledger-strip otl-kpi-grid">
        <KpiCard
          label={t('kpi_whole_pnl')}
          value={cur.signedMoney(activeWholePnl)}
          valueClass={pnlTone(activeWholePnl)}
          sub={fmtSignedPct(activeWholeRoi)}
        />
        <KpiCard
          label={t('kpi_futures_pnl')}
          value={cur.signedMoney(s.futures_net_pnl_usd)}
          valueClass={pnlTone(s.futures_net_pnl_usd)}
          sub={
            s.trade_count > 0
              ? `${s.trade_count} · ${t('kpi_winrate')} ${s.win_rate_pct.toFixed(1)}%`
              : `${s.trade_count} · ${t('kpi_winrate')} —`
          }
        />
        <KpiCard
          label={locale === 'en' ? 'Max Drawdown' : '区间最大回撤'}
          value={`-${cur.money(s.max_drawdown_usd)}`}
          valueClass="txt-neg"
          sub={locale === 'en' ? 'Peak-to-trough cumulative' : '按累计净值峰值回落计算'}
        />
        <KpiCard
          label={t('kpi_fee_saved')}
          value={cur.signedMoney(s.total_rebate_usd + s.total_discount_saved_usd)}
          valueClass="txt-pos"
          sub={`${s.combined_relief_pct.toFixed(1)}%`}
        />
      </section>

      {/* 7:5 Hairline Split: Equity Curve × Monthly PnL Calendar */}
      <section className="grid grid-cols-1 lg:grid-cols-12 border-b border-[var(--border)]">
        <div className="lg:col-span-7 py-6 lg:pr-7 lg:border-r border-[var(--border)] flex flex-col justify-between gap-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h2 className="t-h2">{t('an_curve_title')}</h2>
              <p className="m-0 text-[13px] text-[var(--fg-3)] mt-0.5">{t('ov_curve_sub')}</p>
            </div>
            <span className={`t-h1 sensitive whitespace-nowrap ${pnlTone(activeWholePnl)}`}>
              {cur.signedMoney(activeWholePnl)}
            </span>
          </div>
          <EquityChart points={data.equity_curve} height={238} />
        </div>
        <div className="lg:col-span-5 py-6 lg:pl-7 border-t lg:border-t-0 border-[var(--border)]">
          <MonthlyCalendar
            days={data.calendar_days}
            availableMonths={data.available_months}
            selectedMonth={calMonth || (/^\d{4}-\d{2}$/.test(rangeKey) ? rangeKey : '')}
            onSelectMonth={setCalMonth}
          />
        </div>
      </section>

      {/* Flush Attribution Table */}
      <section className="py-6 space-y-3">
        <div className="border-b border-[var(--border)] flex flex-wrap items-center justify-between gap-3">
          <h2 className="t-h2 py-2">{t('an_attr_title')}</h2>
          <UnderlineTabs
            value={attrMode}
            onChange={setAttrMode}
            options={[
              { value: 'symbol', label: t('an_attr_symbol') },
              { value: 'business', label: t('an_attr_business') },
            ]}
          />
        </div>
        <div key={attrMode} className="otl-panel-swap overflow-x-auto">
          {attrMode === 'symbol' ? (
            <table className="otl-table">
              <thead>
                <tr>
                  <th>{t('th_symbol')}</th>
                  <th>{t('th_venue')}</th>
                  <th className="text-right">{t('th_trades')}</th>
                  <th className="text-right">{t('th_winrate')}</th>
                  <th className="text-right">{t('th_fee_rebate')}</th>
                  <th className="text-right">{t('th_net_pnl')}</th>
                </tr>
              </thead>
              <tbody>
                {data.symbol_attribution.map(row => (
                  <tr
                    key={`${row.venue_id}-${row.symbol}`}
                    className={onInspectSymbol ? 'clickable' : undefined}
                    onClick={() => onInspectSymbol?.(row.venue_id, row.symbol, row.venue_name)}
                  >
                    <td className="whitespace-nowrap">
                      <span className="inline-flex items-center gap-2 font-semibold">
                        <InstrumentMark instrument={row.symbol} exchange={row.venue_id.toUpperCase()} />
                        {row.symbol}
                      </span>
                    </td>
                    <td>
                      <VenueBadge
                        venueId={row.venue_id}
                        name={locale === 'en' ? row.venue_name : row.venue_name_zh || row.venue_name}
                      />
                    </td>
                    <td className="text-right whitespace-nowrap">{row.trade_count}</td>
                    <td className="text-right whitespace-nowrap">{row.win_rate_pct.toFixed(1)}%</td>
                    <td className="text-right whitespace-nowrap text-[var(--fg-2)] sensitive">
                      -{cur.plainMoney(row.gross_fee_usd)} / <span className="txt-pos">+{cur.plainMoney(row.rebate_usd)}</span>
                    </td>
                    <td className={`text-right font-semibold whitespace-nowrap sensitive ${pnlTone(row.net_pnl_usd)}`}>
                      {cur.signedMoney(row.net_pnl_usd)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <table className="otl-table">
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
                {activeBusinessItems.map(it => (
                  <tr key={it.key}>
                    <td className="font-medium whitespace-nowrap">
                      {locale === 'en' ? it.label : it.label_zh || it.label}
                    </td>
                    <td className="text-[var(--fg-2)] sensitive">
                      {cur.convertText(locale === 'en' ? it.detail : it.detail_zh || it.detail)}
                    </td>
                    <td className={`text-right font-semibold whitespace-nowrap sensitive ${pnlTone(it.amount_usd)}`}>
                      {cur.signedMoney(it.amount_usd)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  )
}
