import { useEffect, useState } from 'react'
import { Target } from 'lucide-react'
import { AnimatedCollapse } from '../components/AnimatedCollapse'
import { EquityChart } from '../components/EquityChart'
import { KpiCard } from '../components/KpiCard'
import { MonthlyCalendar } from '../components/MonthlyCalendar'
import { SegmentedPills } from '../components/SegmentedPills'
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
  onUpdateSettings?: (patch: Record<string, unknown>) => Promise<void>
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
      await onUpdateSettings({
        goal_enabled: true,
        goal_label: goalLabel.trim(),
        goal_target_usd: num,
      })
      setEditingGoal(false)
    } finally {
      setSavingGoal(false)
    }
  }

  const handleDisableGoal = async () => {
    if (!onUpdateSettings) return
    setSavingGoal(true)
    try {
      await onUpdateSettings({ goal_enabled: false })
      setEditingGoal(false)
    } finally {
      setSavingGoal(false)
    }
  }

  return (
    <div data-testid="analytics-page" className="otl-stagger space-y-4">
      {/* Custom Equity Goal Card or Quick Setup Trigger */}
      {data.goal && data.goal.enabled ? (
        <section
          data-testid="custom-goal-banner"
          className="otl-card p-5"
        >
          <div className="grid grid-cols-1 md:grid-cols-12 gap-5 items-center">
            <div className="md:col-span-5">
              <div className="flex items-center gap-2 text-[12px] text-[var(--fg-3)]">
                <Target size={13} className="text-[var(--accent)]" />
                <span>{data.goal.label || t('an_goal_default')}</span>
              </div>
              <div className="flex items-baseline gap-3 mt-1 flex-wrap">
                <span className="text-[26px] font-bold tracking-tight sensitive">
                  {data.goal.progress_pct.toFixed(1)}%
                </span>
                <span className="text-[12.5px] text-[var(--fg-2)] whitespace-nowrap sensitive">
                  {cur.plainMoney(data.goal.current_equity_usd)} / {cur.money(data.goal.target_usd)}
                </span>
              </div>
            </div>
            <div className="md:col-span-7 flex flex-col gap-2">
              <div className="flex items-center justify-between gap-2">
                <div className="h-2.5 flex-1 rounded-full bg-[var(--track)] overflow-hidden">
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
                    data-testid="edit-goal-btn"
                    onClick={() => setEditingGoal(v => !v)}
                    className="h-7 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[11.5px] font-medium text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer whitespace-nowrap"
                  >
                    {locale === 'en' ? 'Edit Goal' : '调整目标'}
                  </button>
                )}
              </div>
              <div className="flex items-center justify-between gap-3 text-[12px] text-[var(--fg-2)] flex-wrap">
                <span className="whitespace-nowrap">
                  {data.goal.is_reached ? t('an_goal_surplus') : t('an_goal_remaining')}:{' '}
                  <b className="text-[var(--fg)] sensitive">
                    {cur.money(data.goal.is_reached ? data.goal.surplus_usd : data.goal.remaining_usd)}
                  </b>
                </span>
                <span className="whitespace-nowrap">
                  {t('an_goal_today')}:{' '}
                  <b className={`sensitive ${pnlTone(data.goal.today_delta_usd)}`}>
                    {cur.signedMoney(data.goal.today_delta_usd)}
                  </b>
                </span>
              </div>
            </div>
          </div>

          {onUpdateSettings && (
            <AnimatedCollapse
              open={editingGoal}
              innerClassName="pt-4 mt-4 border-t border-[var(--border)] flex flex-wrap items-center gap-2.5"
            >
              <input
                type="text"
                value={goalLabel}
                onChange={e => setGoalLabel(e.target.value)}
                placeholder={locale === 'en' ? 'Goal name (e.g. 2026 Equity Target)' : '目标名称（如：阶段净值目标）'}
                className="h-8 flex-1 min-w-[160px] px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
              />
              <input
                type="number"
                value={goalAmount}
                onChange={e => setGoalAmount(e.target.value)}
                placeholder={t('set_goal_target')}
                className="h-8 w-40 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
              />
              <button
                type="button"
                disabled={savingGoal}
                onClick={() => void handleSaveGoal()}
                className="h-8 px-3 rounded-lg text-[12px] font-semibold cursor-pointer"
                style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
              >
                {t('save_config')}
              </button>
              <button
                type="button"
                disabled={savingGoal}
                onClick={() => void handleDisableGoal()}
                className="h-8 px-3 rounded-lg border border-[var(--border)] text-[12px] text-[var(--fg-3)] hover:text-[var(--fg)] cursor-pointer"
              >
                {locale === 'en' ? 'Hide Goal' : '关闭进度条'}
              </button>
            </AnimatedCollapse>
          )}
        </section>
      ) : (
        onUpdateSettings && (
          <div>
            <div className="flex justify-end">
              <button
                type="button"
                data-testid="setup-goal-btn"
                onClick={() => setEditingGoal(v => !v)}
                className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] font-medium text-[var(--fg-2)] hover:text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer"
              >
                <Target size={13} />
                <span>{locale === 'en' ? 'Set Custom Equity Goal' : '设置自定义净值目标'}</span>
              </button>
            </div>
            <AnimatedCollapse open={editingGoal} innerClassName="pt-2.5">
              <section className="otl-card p-4 flex flex-wrap items-center gap-2.5">
                <input
                  type="text"
                  value={goalLabel}
                  onChange={e => setGoalLabel(e.target.value)}
                  placeholder={locale === 'en' ? 'Goal name (e.g. Milestone 50k)' : '目标名称（如：阶段净值目标）'}
                  className="h-8 flex-1 min-w-[160px] px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                />
                <input
                  type="number"
                  value={goalAmount}
                  onChange={e => setGoalAmount(e.target.value)}
                  placeholder={t('set_goal_target')}
                  className="h-8 w-44 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                />
                <button
                  type="button"
                  disabled={savingGoal}
                  onClick={() => void handleSaveGoal()}
                  className="h-8 px-3.5 rounded-lg text-[12px] font-semibold cursor-pointer"
                  style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
                >
                  {t('save_config')}
                </button>
                <button
                  type="button"
                  onClick={() => setEditingGoal(false)}
                  className="h-8 px-3 rounded-lg border border-[var(--border)] text-[12px] text-[var(--fg-2)] cursor-pointer"
                >
                  {t('cancel')}
                </button>
              </section>
            </AnimatedCollapse>
          </div>
        )
      )}

      {/* 4 Aligned KPIs */}
      <div className="otl-kpi-grid grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        <KpiCard
          label={t('kpi_whole_pnl')}
          value={cur.signedPlainMoney(activeWholePnl)}
          valueClass={pnlTone(activeWholePnl)}
          sub={fmtSignedPct(activeWholeRoi)}
        />
        <KpiCard
          label={t('kpi_futures_pnl')}
          value={cur.signedPlainMoney(s.futures_net_pnl_usd)}
          valueClass={pnlTone(s.futures_net_pnl_usd)}
          sub={`${s.trade_count} · ${t('kpi_winrate')} ${s.win_rate_pct.toFixed(1)}%`}
        />
        <KpiCard
          label={locale === 'en' ? 'Max Drawdown' : '区间最大回撤'}
          value={`-${cur.money(s.max_drawdown_usd)}`}
          valueClass="txt-neg"
          sub={locale === 'en' ? 'Peak-to-trough cumulative' : '按累计净值峰值回落计算'}
        />
        <KpiCard
          label={t('kpi_fee_saved')}
          value={cur.signedPlainMoney(s.total_rebate_usd + s.total_discount_saved_usd)}
          valueClass="txt-pos"
          sub={`${s.combined_relief_pct.toFixed(1)}%`}
        />
      </div>

      {/* Balanced 2-Col: Equity Curve × Monthly Calendar */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-stretch">
        <div className="lg:col-span-7 otl-card p-5 flex flex-col gap-3">
          <h2 className="m-0 text-[14px] font-semibold text-[var(--fg)]">{t('an_curve_title')}</h2>
          <EquityChart points={data.equity_curve} height={240} />
        </div>
        <div className="lg:col-span-5 otl-card p-5">
          <MonthlyCalendar
            days={data.calendar_days}
            availableMonths={data.available_months}
            selectedMonth={calMonth || (/^\d{4}-\d{2}$/.test(rangeKey) ? rangeKey : '')}
            onSelectMonth={setCalMonth}
          />
        </div>
      </div>

      {/* Full-Width Attribution Table — symmetric animated panel swap on both tabs! */}
      <section className="otl-card overflow-hidden">
        <div className="px-5 py-3.5 border-b border-[var(--border)] flex flex-wrap items-center justify-between gap-2">
          <h2 className="m-0 text-[14px] font-semibold text-[var(--fg)]">{t('an_attr_title')}</h2>
          <SegmentedPills
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
                    <td className="font-semibold whitespace-nowrap">{row.symbol}</td>
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
