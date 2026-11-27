import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PreferencesAndGoalSection } from '../components/settings/PreferencesAndGoalSection'
import { MonthlyCalendar } from '../components/MonthlyCalendar'
import { PublicCopyWatcherPanel } from '../components/PublicCopyWatcherPanel'
import { I18nProvider } from '../i18n'
import { api } from '../lib/api'
import { CurrencyProvider, DEFAULT_FX, useCurrency } from '../lib/currency'
import { fmtPrice, fmtSignedPct, fmtSignedUsd, fmtUsd } from '../lib/format'
import type { DashboardData, ExchangeAdapterInfo, UserSettings } from '../lib/types'
import { AnalyticsPage } from '../pages/AnalyticsPage'
import { FeesAiPage } from '../pages/FeesAiPage'
import { OverviewPage } from '../pages/OverviewPage'
import { SettingsPage } from '../pages/SettingsPage'
import { TradesPage } from '../pages/TradesPage'

it('displays a tiny positive spot price at a readable precision', () => {
  expect(fmtPrice(0.0000001)).toBe('0.0000001')
  expect(fmtPrice(0.00000010012345)).toBe('0.00000010012')
  expect(fmtPrice(0)).toBe('—')
})

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
;(globalThis as unknown as { ResizeObserver: typeof ResizeObserverMock }).ResizeObserver = ResizeObserverMock

const baseSettings: UserSettings = {
  locale: 'zh-CN',
  theme: 'obsidian',
  polarity: 'cn',
  display_currency: 'USDT',
  privacy_mode: true,
  update_check_enabled: true,
  goal_enabled: true,
  goal_target_usd: 10000,
  goal_label: '阶段净值目标',
  ai_base_url: 'https://api.openai.com/v1',
  ai_api_key: '',
  ai_api_key_set: true,
  ai_model: 'gpt-4o-mini',
  outbound_proxy_url: '',
  copy_watcher_targets: [],
  modules: { copy_watcher: true, copy_trading: true, stocks: true },
  notifications: {
    bark_server_url: 'https://api.day.app',
    bark_push_key: '',
    bark_push_key_set: true,
  },
}

const qaBaseSettings: UserSettings = {
  locale: 'zh-CN',
  theme: 'obsidian',
  polarity: 'cn',
  display_currency: 'USDT',
  privacy_mode: true,
  update_check_enabled: true,
  goal_enabled: false,
  goal_target_usd: null,
  goal_label: '',
  ai_base_url: 'https://api.openai.com/v1',
  ai_api_key: '',
  ai_api_key_set: false,
  ai_model: 'gpt-4o-mini',
  copy_watcher_targets: [],
  modules: { copy_watcher: true, copy_trading: true, stocks: true },
  notifications: { bark_server_url: 'https://api.day.app', bark_push_key: '' },
}

function makeDash(overrides?: Partial<DashboardData>): DashboardData {
  return {
    demo_mode: false,
    explicit_demo_mode: false,
    has_live_data: true,
    read_only: true,
    last_sync_ms: 1732500000000,
    syncing: false,
    range_key: 'all',
    venue_filter: 'ALL',
    symbol_filter: 'ALL',
    available_months: ['2026-11', '2026-10'],
    available_symbols: ['BTC_USDT', 'ETH_USDT'],
    summary: {
      total_equity_usd: 8035,
      net_principal_usd: 7500,
      whole_net_pnl_usd: 535,
      whole_roi_pct: 7.13,
      futures_net_pnl_usd: 535,
      futures_book_pnl_usd: 510,
      trade_count: 2,
      all_time_trade_count: 2,
      win_rate_pct: 50,
      total_fee_usd: 12,
      total_rebate_usd: 5,
      total_discount_saved_usd: 0,
      net_fee_usd: 7,
      combined_relief_pct: 41.7,
      potential_maker_savings_usd: 10,
      max_drawdown_usd: 80,
    },
    exchanges: [
      {
        venue_id: 'gate',
        display_name: 'Gate',
        display_name_zh: 'Gate',
        color_token: 'gate',
        connected: true,
        equity_usd: 5035,
        net_principal_usd: 4500,
        net_pnl_usd: 535,
        available_margin_usd: 4200,
        trade_count: 1,
        rebate_rate_pct: 50,
        discount_rate_pct: 0,
        capabilities: { futures: true },
      },
      {
        venue_id: 'okx',
        display_name: 'OKX',
        display_name_zh: 'OKX',
        color_token: 'okx',
        connected: true,
        equity_usd: 3000,
        net_principal_usd: 3000,
        net_pnl_usd: 0,
        available_margin_usd: 2800,
        trade_count: 1,
        rebate_rate_pct: 35,
        discount_rate_pct: 0,
        capabilities: { futures: true },
      },
    ],
    open_positions: [],
    trades: [
      {
        trade_id: 'gate_btc_1',
        venue_id: 'gate',
        venue_name: 'Gate',
        venue_name_zh: 'Gate',
        color_token: 'gate',
        symbol: 'BTC_USDT',
        side: 'LONG',
        qty: 0.25,
        open_time_ms: 1732280000000,
        close_time_ms: 1732290000000,
        open_time_str: '2026-11-22 15:00',
        close_time_str: '2026-11-22 17:46',
        close_date: '2026-11-22',
        holding_sec: 9960,
        is_partial_close: false,
        gross_pnl_usd: 540,
        funding_pnl_usd: 0,
        book_pnl_usd: 532,
        total_fee_usd: 8,
        total_rebate_usd: 3,
        total_discount_saved_usd: 0,
        net_fee_usd: 5,
        net_pnl_usd: 535,
        effective_relief_pct: 37.5,
        rebate_status: 'SETTLED',
        entry_leg: {
          role: 'MAKER',
          mode_code: 'LIMIT_MAKER',
          fee_bps: 2.0,
          time_ms: 1732280000000,
          time_str: '11-22 15:00',
          avg_price: 91234.5,
          notional_usd: 22808,
          gross_fee_usd: 3,
          discount_saved_usd: 0,
          discount_asset: '',
          rebate_usd: 1,
          rebate_pct: 33,
          net_fee_usd: 2,
          rebate_status: 'SETTLED',
          settled_at: '',
        },
        exit_leg: {
          role: 'TAKER',
          mode_code: 'MARKET_TAKER',
          fee_bps: 5.0,
          time_ms: 1732290000000,
          time_str: '11-22 17:46',
          avg_price: 93394.5,
          notional_usd: 23348,
          gross_fee_usd: 5,
          discount_saved_usd: 0,
          discount_asset: '',
          rebate_usd: 2,
          rebate_pct: 40,
          net_fee_usd: 3,
          rebate_status: 'SETTLED',
          settled_at: '',
        },
        tags: [],
        note: '',
      },
      {
        trade_id: 'okx_eth_1',
        venue_id: 'okx',
        venue_name: 'OKX',
        venue_name_zh: 'OKX',
        color_token: 'okx',
        symbol: 'ETH_USDT',
        side: 'SHORT',
        qty: 1.5,
        open_time_ms: 1732180000000,
        close_time_ms: 1732190000000,
        open_time_str: '2026-11-21 12:00',
        close_time_str: '2026-11-21 14:00',
        close_date: '2026-11-21',
        holding_sec: 7200,
        is_partial_close: false,
        gross_pnl_usd: 2,
        funding_pnl_usd: 0,
        book_pnl_usd: -2,
        total_fee_usd: 4,
        total_rebate_usd: 2,
        total_discount_saved_usd: 0,
        net_fee_usd: 2,
        net_pnl_usd: 0,
        effective_relief_pct: 50,
        rebate_status: 'SETTLED',
        entry_leg: {
          role: 'MAKER',
          mode_code: 'LIMIT_MAKER',
          fee_bps: 2.0,
          time_ms: 1732180000000,
          time_str: '11-21 12:00',
          avg_price: 3100,
          notional_usd: 4650,
          gross_fee_usd: 2,
          discount_saved_usd: 0,
          discount_asset: '',
          rebate_usd: 1,
          rebate_pct: 50,
          net_fee_usd: 1,
          rebate_status: 'SETTLED',
          settled_at: '',
        },
        exit_leg: {
          role: 'TAKER',
          mode_code: 'MARKET_TAKER',
          fee_bps: 5.0,
          time_ms: 1732190000000,
          time_str: '11-21 14:00',
          avg_price: 3098.6,
          notional_usd: 4648,
          gross_fee_usd: 2,
          discount_saved_usd: 0,
          discount_asset: '',
          rebate_usd: 1,
          rebate_pct: 50,
          net_fee_usd: 1,
          rebate_status: 'SETTLED',
          settled_at: '',
        },
        tags: [],
        note: '',
      },
    ],
    reconciliation: {
      net_principal_usd: 7500,
      total_equity_usd: 8035,
      net_pnl_usd: 535,
      residual_usd: 12.45,
      items: [],
    },
    equity_curve: [],
    calendar_days: [
      { date: '2026-10-31', net_pnl_usd: 88, trade_count: 1 },
      { date: '2026-11-22', net_pnl_usd: 535, trade_count: 1 },
    ],
    symbol_attribution: [],
    fee_matrix: [],
    goal: {
      enabled: true,
      label: '阶段净值目标',
      target_usd: 10000,
      current_equity_usd: 8035,
      remaining_usd: 1965,
      surplus_usd: 0,
      is_reached: false,
      progress_pct: 80.35,
      needed_gain_pct: 24.46,
      today_delta_usd: 50,
    },
    ai_review: null,
    extensions: {
      copy_trading: {
        enabled: true,
        total_invested_usd: 0,
        active_equity_usd: 0,
        book_pnl_usd: 0,
        rebate_total_usd: 0,
        combined_pnl_usd: 0,
        combined_roi_pct: 0,
        platforms: [],
        transfers: [],
        rebates: [],
      },
      stocks: {
        enabled: true,
        equity_usd: 0,
        unrealized_pnl_usd: 0,
        non_perp_fees_usd: 0,
        fee_rate_range: '0%',
        venue_fee_rates: [],
        holdings: [],
        cash_ledger: [],
        perpetuals: [],
      },
    },
    ...overrides,
  }
}

beforeEach(() => {
  window.location.hash = ''
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  window.location.hash = ''
})

describe('Privacy Masking, Number Precision, FX Conversion & Calendar Formatting', () => {
  it('4. masks all newly audited .sensitive elements, eliminates -0.00/+0.00%/-JP¥0, rounds 80.35%->80.4%, converts $1,250.00, resets Nov 31, and validates Analytics goal', async () => {
    // 1) Precision & negative-zero elimination
    expect(fmtUsd(-0.004, 2)).toBe('0.00')
    expect(fmtSignedUsd(-0.004, 2)).toBe('0.00')
    expect(fmtSignedUsd(0.004, 2)).toBe('0.00')
    expect(fmtSignedPct(-0.004, 2)).toBe('0.00%')
    expect(fmtSignedPct(0.001, 2)).toBe('0.00%')

    function FxProbe() {
      const c = useCurrency()
      return (
        <div>
          <span data-testid="jpy-neg-zero">{c.signedPlainMoney(-0.002)}</span>
          <span data-testid="jpy-plain-zero">{c.plainMoney(-0.002)}</span>
          <span data-testid="dollar-conv">{c.convertText('Remote LLM: net +$320.50 on $1,250.00 equity')}</span>
        </div>
      )
    }

    const { unmount: unmountJpy } = render(
      <CurrencyProvider currency="JPY" fx={DEFAULT_FX}>
        <FxProbe />
      </CurrencyProvider>,
    )
    expect(screen.getByTestId('jpy-neg-zero').textContent).toBe('JP¥0')
    expect(screen.getByTestId('jpy-plain-zero').textContent).toBe('JP¥0')
    unmountJpy()

    const { unmount: unmountCny } = render(
      <CurrencyProvider currency="CNY" fx={DEFAULT_FX}>
        <FxProbe />
      </CurrencyProvider>,
    )
    expect(screen.getByTestId('dollar-conv').textContent).toBe(
      'Remote LLM: net +¥2,307.60 on ¥9,000.00 equity',
    )
    unmountCny()

    // 2) Overview residual_usd .sensitive + 80.35 -> 80.4%
    const dash = makeDash({
      ai_review: {
        generated_at: '2026-11-26',
        generated_ms: 1,
        model: 'gpt-4o',
        equity_snapshot_usd: 8035,
        stale: true,
        stale_reason_zh: '上次快照净值 $1,250.00，当前已有新成交',
        highlights: [],
        markdown_commentary: '',
      },
    })

    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="CNY" fx={DEFAULT_FX}>
          <OverviewPage data={dash} onNavigate={() => {}} />
          <AnalyticsPage
            data={dash}
            rangeKey="all"
            settings={{ ...baseSettings, display_currency: 'CNY' }}
            onUpdateSettings={async () => {}}
          />
          <FeesAiPage data={dash} settings={baseSettings} generating={false} onGenerate={async () => {}} />
          <TradesPage
            data={dash}
            venueFilter="ALL"
            onVenueChange={() => {}}
            symbolFilter="ALL"
            onSymbolChange={() => {}}
            onSaveJournal={async () => {}}
          />
          <SettingsPage
            settings={{ ...baseSettings, display_currency: 'CNY' }}
            adapters={[
              {
                venue_id: 'gate',
                display_name: 'Gate',
                display_name_zh: 'Gate',
                color_token: 'gate',
                requires_passphrase: false,
                capabilities: {},
                docs_url: '',
                api_base_url: 'https://api.gateio.ws',
                configured: true,
                credential: {
                  venue_id: 'gate',
                  label: 'Gate',
                  masked_api_key: 'GKEY...9999',
                  masked_uid: '88***22',
                  has_passphrase: false,
                  enabled: true,
                },
                health: {
                  status: 'connected',
                  last_attempt_ms: 1,
                  last_ok_ms: 1,
                  error_code: '',
                  error_reason: '',
                  error_reason_zh: '',
                  raw_error: '',
                },
              },
            ]}
            demoMode={false}
            onUpdateSettings={async () => {}}
            onUpsertExchange={async () => {}}
            onDeleteExchange={async () => {}}
            onTestExchange={async () => {}}
            onToggleDemo={async () => {}}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    // Residual drift has .sensitive
    expect(screen.getByTestId('kpi-residual-drift').className).toContain('sensitive')
    // 80.35% rounds to 80.4% on both Overview & Analytics
    expect(screen.getByTestId('overview-page').textContent).toContain('(80.4%)')
    expect(screen.getByTestId('custom-goal-banner').textContent).toContain('(80.4%)')

    // Analytics trade count unit "笔交割", inline goal form USDT badge, input.sensitive & <=0 error
    expect(screen.getByTestId('analytics-page').textContent).toContain('2 笔交割')
    fireEvent.click(screen.getByTestId('edit-goal-btn'))
    const goalInput = screen.getByTestId('analytics-goal-amount-input')
    expect(goalInput.className).toContain('sensitive')
    expect(screen.getByTestId('analytics-goal-form').textContent).toContain('USDT')
    fireEvent.change(goalInput, { target: { value: '0' } })
    fireEvent.click(screen.getByTestId('analytics-save-goal-btn'))
    expect(await screen.findByTestId('analytics-goal-error')).toBeTruthy()

    // FeesAiPage stale banner has .sensitive and converts $1,250.00 -> ¥9,000.00
    const staleBanner = screen.getByTestId('ai-stale-banner')
    expect(staleBanner.className).toContain('sensitive')
    expect(staleBanner.textContent).toContain('¥9,000.00')

    // TradesPage entry/exit avg_price have .sensitive
    expect(screen.getByText('91,234.50').className).toContain('sensitive')
    expect(screen.getByText('93,394.50').className).toContain('sensitive')

    // SettingsPage masked_uid, masked_api_key, goal target input have .sensitive, and DEFAULT_FX shows 离线参考汇率
    expect(screen.getByText('UID 88***22').className).toContain('sensitive')
    expect(screen.getByText('GKEY...9999').className).toContain('sensitive')
    expect(screen.getByTestId('settings-goal-target-input').className).toContain('sensitive')
    expect(screen.getByTestId('settings-fx-note').textContent).toContain('离线参考汇率')

    cleanup()

    // 3) PublicCopyWatcherPanel .sensitive on position qty, recent fill qty & realized_pnl
    vi.spyOn(api, 'getCopyWatcher').mockResolvedValue({
      demo_mode: false,
      watcher_running: true,
      interval_sec: 10,
      trade_poll_sec: 30,
      cooldown_sec: 180,
      updated_at_ms: 1,
      open_slot_count: 0,
      channels: { bark: true },
      configured_targets: ['3988776655443322110'],
      items: [
        {
          portfolio_id: '3988776655443322110',
          venue: 'binance',
          nickname: 'WhaleA',
          status: 'ACTIVE',
          portfolio_type: 'PUBLIC',
          enabled: true,
          slot_alert: true,
          trade_alert: true,
          current_copy_count: 200,
          max_copy_count: 200,
          available_slots: 0,
          has_open_slot: false,
          min_copy_usd: 100,
          profit_sharing_rate: '10',
          lead_url: 'https://www.binance.com',
          app_deeplink: '',
          last_alert_ms: 0,
          inferred_positions: [
            {
              symbol: 'BTCUSDT',
              position_side: 'LONG',
              side_zh: '做多',
              qty: 3.75,
              avg_entry_price: 90000,
              notional_usd: 337500,
              mark_price: 91000,
              unrealized_pnl_usd: 3750,
              unrealized_roi_pct: 5.5,
              opened_at_bj: '',
              last_action_bj: '',
            },
          ],
          recent_orders: [
            {
              order_time_ms: 1,
              time_bj: '11-26 10:00',
              symbol: 'BTCUSDT',
              side: 'SELL',
              position_side: 'LONG',
              action_zh: '平多',
              order_type: 'MARKET',
              executed_qty: 1.25,
              avg_price: 91500,
              realized_pnl: 420.5,
            },
          ],
        },
      ],
    })

    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <PublicCopyWatcherPanel />
        </CurrencyProvider>
      </I18nProvider>,
    )
    expect((await screen.findByText('3.75')).className).toContain('sensitive')
    fireEvent.click(screen.getByRole('button', { name: /查看最近公开成交/ }))
    expect((await screen.findByText('1.25')).className).toContain('sensitive')
    expect(screen.getByText('+420.50 U').closest('td')?.className).toContain('sensitive')
    cleanup()

    // 4) MonthlyCalendar resets day 31 when selectedMonth changes from 2026-10 to 2026-11
    const { rerender: rerenderCal } = render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <MonthlyCalendar
            days={[
              { date: '2026-10-31', net_pnl_usd: 88, trade_count: 1 },
              { date: '2026-11-22', net_pnl_usd: 120, trade_count: 1 },
            ]}
            availableMonths={['2026-11', '2026-10']}
            selectedMonth="2026-10"
            onSelectMonth={() => {}}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: /^31/ }))
    expect(document.body.textContent).toContain('2026-10-31')

    rerenderCal(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <MonthlyCalendar
            days={[
              { date: '2026-10-31', net_pnl_usd: 88, trade_count: 1 },
              { date: '2026-11-22', net_pnl_usd: 120, trade_count: 1 },
            ]}
            availableMonths={['2026-11', '2026-10']}
            selectedMonth="2026-11"
            onSelectMonth={() => {}}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )
    expect(document.body.textContent).not.toContain('2026-11-31')

    // 5) CSS focus ring, input.sensitive blur & <=640px 390px mobile nav wrap rules
    const fsSpec = 'node:fs'
    const fsMod = (await import(/* @vite-ignore */ fsSpec)) as {
      readFileSync: (p: string, enc: string) => string
    }
    const cssText = fsMod.readFileSync('src/styles/theme.css', 'utf-8')
    expect(cssText).toContain('[role="button"]:focus-visible')
    expect(cssText).toContain('[tabindex="0"]:focus-visible')
    expect(cssText).toContain('.privacy-active input.sensitive')
    expect(cssText).toContain('@media (max-width: 640px)')
    expect(cssText).toContain('.otl-primary-nav')
    expect(cssText).toContain('flex-wrap: wrap')
  })

  it('6. renders zero max_drawdown_usd as unsigned 0.00 U / $0.00 / ¥0.00 / JP¥0 with txt-flat and non-zero with leading minus', () => {
    const zeroDash = makeDash({
      summary: {
        ...makeDash().summary,
        max_drawdown_usd: 0,
      },
    })
    const tinyFloatZeroDash = makeDash({
      summary: {
        ...makeDash().summary,
        max_drawdown_usd: 0.001,
      },
    })

    const cases: Array<{ code: UserSettings['display_currency']; expectedZero: string; expected80: string }> = [
      { code: 'USDT', expectedZero: '0.00 U', expected80: '-80.00 U' },
      { code: 'USD', expectedZero: '$0.00', expected80: '-$80.00' },
      { code: 'CNY', expectedZero: '¥0.00', expected80: '-¥576.00' },
      { code: 'JPY', expectedZero: 'JP¥0', expected80: '-JP¥12,400' },
    ]

    for (const c of cases) {
      const { rerender, unmount } = render(
        <I18nProvider locale="zh-CN">
          <CurrencyProvider currency={c.code!} fx={DEFAULT_FX}>
            <AnalyticsPage data={zeroDash} rangeKey="all" />
          </CurrencyProvider>
        </I18nProvider>,
      )
      const kpiZero = screen.getByTestId('kpi-max-drawdown')
      expect(kpiZero.textContent).toContain(c.expectedZero)
      expect(kpiZero.textContent).not.toContain(`-${c.expectedZero}`)
      expect(kpiZero.querySelector('.t-kpi')?.className).toContain('txt-flat')

      rerender(
        <I18nProvider locale="zh-CN">
          <CurrencyProvider currency={c.code!} fx={DEFAULT_FX}>
            <AnalyticsPage data={tinyFloatZeroDash} rangeKey="all" />
          </CurrencyProvider>
        </I18nProvider>,
      )
      expect(screen.getByTestId('kpi-max-drawdown').textContent).not.toContain(`-${c.expectedZero}`)

      rerender(
        <I18nProvider locale="zh-CN">
          <CurrencyProvider currency={c.code!} fx={DEFAULT_FX}>
            <AnalyticsPage data={makeDash()} rangeKey="all" />
          </CurrencyProvider>
        </I18nProvider>,
      )
      const kpiNonZero = screen.getByTestId('kpi-max-drawdown')
      expect(kpiNonZero.textContent).toContain(c.expected80)
      expect(kpiNonZero.querySelector('.t-kpi')?.className).toContain('txt-neg')
      unmount()
    }
  })

  it('MonthlyCalendar does not count passive sub-dollar interest as trade win days or display -0', () => {
    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <MonthlyCalendar
            days={[
              { date: '2026-11-01', net_pnl_usd: 0.18, trade_count: 0 },
              { date: '2026-11-02', net_pnl_usd: -0.002, trade_count: 0 },
              { date: '2026-11-09', net_pnl_usd: -254.2, trade_count: 3 },
              { date: '2026-11-21', net_pnl_usd: 108.4, trade_count: 2 },
            ]}
            availableMonths={['2026-11']}
            selectedMonth="2026-11"
            onSelectMonth={() => {}}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    // Only 1 real winning day (+108.4) and 1 real losing day (-254.2), NOT counting +0.18 passive interest as a trade win day!
    expect(screen.getByText(/1 盈 · 1 亏/)).toBeTruthy()
    expect(document.body.textContent).not.toContain('-0')
  })

  it('blurs Hyperliquid discovered subaccount equity in privacy mode, reports partial subaccount failure, and keeps archived custom labels', async () => {
    const masterAddr = '0x' + '1'.repeat(40)
    const okSub = '0x' + '2'.repeat(40)
    const failSub = '0x' + '3'.repeat(40)
    vi.spyOn(api, 'verifyExchange').mockResolvedValue({
      ok: true,
      verified: true,
      account_id: 'hyperliquid',
      subaccounts: [
        { name: 'Sub600', address: okSub, equity_usd: '600' },
        { name: 'Sub900', address: failSub, equity_usd: '900' },
      ],
    })
    const upsertSpy = vi.fn().mockResolvedValue({
      failedSubaccounts: [{ address: failSub, name: 'Sub900', error_zh: 'HTTP 503' }],
    })

    const adapters: ExchangeAdapterInfo[] = [
      {
        venue_id: 'hyperliquid',
        display_name: 'Hyperliquid',
        display_name_zh: 'Hyperliquid',
        color_token: 'hyperliquid',
        auth_mode: 'wallet_address',
        requires_passphrase: false,
        capabilities: { futures: true },
        docs_url: '',
        api_base_url: 'https://api.hyperliquid.xyz',
        configured: false,
      },
      {
        venue_id: 'htx',
        display_name: 'HTX',
        display_name_zh: '火币 HTX',
        color_token: 'htx',
        requires_passphrase: false,
        capabilities: { futures: true },
        docs_url: '',
        api_base_url: 'https://api.hbdm.com',
        configured: false,
      },
      {
        venue_id: 'bybit',
        display_name: 'Bybit',
        display_name_zh: 'Bybit',
        color_token: 'bybit',
        requires_passphrase: false,
        capabilities: { futures: true },
        docs_url: '',
        api_base_url: 'https://api.bybit.com',
        configured: true,
        accounts: [
          {
            account_id: 'bybit',
            adapter_id: 'bybit',
            label: '主号',
            configured: true,
            credential: {
              venue_id: 'bybit',
              label: '主号',
              masked_api_key: 'K1...11',
              has_passphrase: false,
              enabled: true,
            },
          },
          {
            account_id: 'bybit__arch01',
            adapter_id: 'bybit',
            label: '示例子账户',
            configured: false,
            has_archived_data: true,
            masked_uid: '770***0022',
            credential: null,
          },
        ],
      },
    ]

    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <SettingsPage
            settings={qaBaseSettings}
            adapters={adapters}
            demoMode={false}
            onUpdateSettings={async () => {}}
            onUpsertExchange={upsertSpy}
            onDeleteExchange={async () => {}}
            onTestExchange={async () => {}}
            onToggleDemo={async () => {}}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    // Protocol badges accurate for Hyperliquid & HTX; archived custom label preserved!
    expect(screen.getByTestId('adapter-card-hyperliquid').textContent).toContain('公开 /info 零密钥只读')
    expect(screen.getByTestId('adapter-card-htx').textContent).toContain('只读签名查询 API')
    expect(screen.getByTestId('account-row-bybit__arch01').textContent).toContain('示例子账户')

    fireEvent.click(screen.getByTestId('configure-adapter-hyperliquid'))
    fireEvent.change(screen.getByTestId('exchange-wallet-input'), { target: { value: masterAddr } })
    fireEvent.click(screen.getByTestId('probe-subaccounts-btn'))

    await screen.findByTestId('discovered-subaccounts-box')
    const eqSpan = screen.getByText('600.00 U')
    expect(eqSpan.className).toContain('sensitive')

    fireEvent.click(screen.getByTestId('save-exchange-btn'))
    await waitFor(() => expect(upsertSpy).toHaveBeenCalledTimes(1))
    await waitFor(() => {
      expect(document.body.textContent).toContain('Sub900')
      expect(document.body.textContent).toContain('未能导入')
    })
  })
})

it.each([
  ['offline-fallback', true, '', '离线参考汇率'],
  ['provider', true, '2026-11-26 14:00', '上次成功获取的汇率'],
  ['provider', false, '2026-11-27 14:00', '实时参考汇率'],
] as const)('reports the origin and freshness of %s rates (stale=%s)', (source, stale, updated_at, label) => {
  render(
    <I18nProvider locale="zh-CN"><CurrencyProvider currency="CNY" fx={{ ...DEFAULT_FX, source, stale, updated_at }}>
      <PreferencesAndGoalSection settings={baseSettings} demoMode={false}
        onUpdateSettings={async () => true} onToggleDemo={async () => {}} onClearStatus={() => {}}
        flashOk={() => {}} setErrorMsg={() => {}} />
    </CurrencyProvider></I18nProvider>,
  )
  const note = screen.getByTestId('settings-fx-note')
  expect(note.textContent).toContain(label)
  if (updated_at) expect(note.textContent).toContain(updated_at)
})
