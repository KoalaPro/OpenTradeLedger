import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { MonthlyCalendar } from '../components/MonthlyCalendar'
import { PublicCopyWatcherPanel } from '../components/PublicCopyWatcherPanel'
import { TradeDrilldownDrawer } from '../components/TradeDrilldownDrawer'
import { AiConfigCard } from '../components/settings/AiConfigCard'
import { BarkNotifyCard } from '../components/settings/BarkNotifyCard'
import { VersionUpgradeCard } from '../components/settings/VersionUpgradeCard'
import { I18nProvider } from '../i18n'
import { api } from '../lib/api'
import { CurrencyProvider, DEFAULT_FX, useCurrency } from '../lib/currency'
import { fmtSignedPct, fmtSignedUsd, fmtUsd } from '../lib/format'
import type { DashboardData, ExchangeAdapterInfo, UserSettings } from '../lib/types'
import { AnalyticsPage } from '../pages/AnalyticsPage'
import { FeesAiPage } from '../pages/FeesAiPage'
import { OverviewPage } from '../pages/OverviewPage'
import { SettingsPage } from '../pages/SettingsPage'
import { TradesPage } from '../pages/TradesPage'

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
  try {
    window.localStorage?.removeItem?.('otl.locale')
    window.localStorage?.removeItem?.('otl.theme')
    window.localStorage?.removeItem?.('otl.polarity')
    window.localStorage?.removeItem?.('otl.logout-pending')
  } catch { /* ignore */ }
  try {
    window.sessionStorage?.setItem?.('otl.tab_boot_ceremony', '1')
  } catch { /* ignore */ }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  window.location.hash = ''
})

describe('Full Retest UI, State Sync, Warnings & Privacy Fixes', () => {
  it('1a. recovers from partial bootstrap failure (getSettings 503) and still renders SettingsPage when !data', async () => {
    vi.spyOn(api, 'authStatus').mockResolvedValue({
      initialized: true,
      enabled: false,
      authenticated: true,
      require_setup: false,
    })
    let failDash = false
    let failSettings = true
    vi.spyOn(api, 'getDashboard').mockImplementation(async () => {
      if (failDash) throw new Error('Dashboard 503')
      return makeDash()
    })
    vi.spyOn(api, 'getSettings').mockImplementation(async () => {
      if (failSettings) throw new Error('Settings HTTP 503')
      return baseSettings
    })
    vi.spyOn(api, 'listExchanges').mockResolvedValue({ adapters: [], error_count: 0 })
    vi.spyOn(api, 'getFxRates').mockResolvedValue(DEFAULT_FX)
    vi.spyOn(api, 'getVersionCheck').mockResolvedValue({
      current_version: '1.4.0',
      latest_version: '1.4.0',
      update_available: false,
      check_enabled: true,
      github_repo: '',
      release_url: '',
      release_name: '',
      release_notes: '',
      published_at: '',
      checked_sec: 1,
      checked_at: '',
      stale: false,
      error: '',
      error_zh: '',
    })

    render(<App />)

    // Dashboard succeeded, but getSettings 503'd -> must NOT lock forever on Loading… without Retry
    const retryBtn = await screen.findByTestId('dashboard-retry-btn')
    expect(screen.queryByText('加载中…')).toBeNull()

    // Even before retrying, user can navigate to SettingsPage to fix proxy/accounts!
    fireEvent.click(screen.getByTestId('nav-settings'))
    expect(await screen.findByTestId('settings-page')).toBeTruthy()
    expect(screen.getByTestId('outbound-proxy-input')).toBeTruthy()

    // Retry from Settings or Overview recovers full bootstrap
    failSettings = false
    fireEvent.click(screen.getByTestId('dashboard-retry-btn'))
    fireEvent.click(screen.getByTestId('nav-overview'))
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeTruthy())
    expect(retryBtn).toBeTruthy()

    // Also verify when !data (initial dashboard 503), switching to SettingsPage still renders SettingsPage
    cleanup()
    failDash = true
    failSettings = false
    render(<App />)
    await screen.findByTestId('dashboard-retry-btn')
    fireEvent.click(screen.getByTestId('nav-settings'))
    expect(await screen.findByTestId('settings-page')).toBeTruthy()
  })

  it('1b & 1c. shows error + retry on rangeKey/venueFilter 503 instead of stuck Loading or cross-venue journal write', async () => {
    vi.spyOn(api, 'authStatus').mockResolvedValue({
      initialized: true,
      enabled: false,
      authenticated: true,
      require_setup: false,
    })
    let fail7d = true
    let failOkx = true
    let failAllReturn = false
    const saveJournalSpy = vi.spyOn(api, 'saveJournal').mockResolvedValue({ ok: true })

    vi.spyOn(api, 'getDashboard').mockImplementation(async (range, venue = 'ALL', symbol = 'ALL') => {
      if (range === '7d' && fail7d) throw new Error('Range 7d HTTP 503')
      if (venue.toLowerCase() === 'okx' && failOkx) throw new Error('OKX filter HTTP 503')
      if (venue === 'ALL' && failAllReturn) throw new Error('Return ALL HTTP 503')
      const base = makeDash({
        range_key: range,
        venue_filter: venue === 'ALL' ? 'ALL' : venue.toLowerCase(),
        symbol_filter: symbol === 'ALL' ? 'ALL' : symbol.toUpperCase(),
      })
      if (venue.toLowerCase() === 'okx') {
        base.trades = base.trades.filter(t => t.venue_id === 'okx')
      }
      return base
    })
    vi.spyOn(api, 'getSettings').mockResolvedValue({ ...baseSettings, privacy_mode: false })
    vi.spyOn(api, 'listExchanges').mockResolvedValue({ adapters: [], error_count: 0 })
    vi.spyOn(api, 'getFxRates').mockResolvedValue(DEFAULT_FX)
    vi.spyOn(api, 'getVersionCheck').mockRejectedValue(new Error('offline'))

    render(<App />)
    await screen.findByTestId('overview-page')

    // Switch rangeKey to 7d -> 503 -> must render Retry button, NOT stuck Loading…
    fireEvent.click(screen.getByRole('button', { name: '7天' }))
    const rangeRetryBtn = await screen.findByTestId('dashboard-retry-btn')
    expect(screen.queryByText('加载中…')).toBeNull()
    expect(screen.queryByTestId('overview-page')).toBeNull()

    fail7d = false
    fireEvent.click(rangeRetryBtn)
    await screen.findByTestId('overview-page')
    fireEvent.click(screen.getByRole('button', { name: '全部' }))
    await screen.findByTestId('overview-page')

    // Go to TradesPage -> click OKX filter -> 503 -> MUST hide stale Gate BTC trade & note editor!
    fireEvent.click(screen.getByTestId('nav-trades'))
    await screen.findByTestId('trades-page')
    expect(screen.getByTestId('trade-row-gate_btc_1')).toBeTruthy()

    fireEvent.click(screen.getByTestId('venue-filter-okx'))
    const filterRetryBtn = await screen.findByTestId('dashboard-retry-btn')
    expect(screen.queryByTestId('trade-row-gate_btc_1')).toBeNull()
    expect(screen.queryByTestId('journal-save-btn')).toBeNull()
    expect(saveJournalSpy).not.toHaveBeenCalled()

    // Retry OKX filter succeeds -> shows only OKX trade
    failOkx = false
    fireEvent.click(filterRetryBtn)
    await screen.findByTestId('trade-row-okx_eth_1')
    expect(screen.queryByTestId('trade-row-gate_btc_1')).toBeNull()

    // Switch back from filtered TradesPage to OverviewPage when ALL 503s -> must NOT show OKX single-venue data as ALL
    failAllReturn = true
    fireEvent.click(screen.getByTestId('nav-overview'))
    const backRetry = await screen.findByTestId('dashboard-retry-btn')
    expect(screen.queryByTestId('overview-page')).toBeNull()
    failAllReturn = false
    fireEvent.click(backRetry)
    await screen.findByTestId('overview-page')
  })

  it('1d. catches saveJournal errors in TradesPage and resets saved badge when switching trades', async () => {
    let shouldFail = true
    const onSave = vi.fn().mockImplementation(async () => {
      if (shouldFail) throw new Error('写入数据库失败 500')
    })

    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <TradesPage
            data={makeDash()}
            venueFilter="ALL"
            onVenueChange={() => {}}
            symbolFilter="ALL"
            onSymbolChange={() => {}}
            onSaveJournal={onSave}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    fireEvent.click(screen.getByTestId('journal-save-btn'))
    const errAlert = await screen.findByTestId('journal-save-error')
    expect(errAlert.textContent).toContain('写入数据库失败 500')

    shouldFail = false
    fireEvent.click(screen.getByTestId('journal-save-btn'))
    await waitFor(() =>
      expect(screen.getByTestId('journal-save-btn').textContent).toContain('已保存复盘笔记'),
    )
    expect(screen.queryByTestId('journal-save-error')).toBeNull()

    // Switching to another trade immediately clears the stale "已保存复盘笔记" badge
    fireEvent.click(screen.getByTestId('trade-row-okx_eth_1'))
    expect(screen.getByTestId('journal-save-btn').textContent).toBe('保存笔记')

    // Double-lock local filtering on TradesPage when venueFilter='okx'
    cleanup()
    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <TradesPage
            data={makeDash({ venue_filter: 'okx' })}
            venueFilter="okx"
            onVenueChange={() => {}}
            symbolFilter="ALL"
            onSymbolChange={() => {}}
            onSaveJournal={onSave}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )
    expect(screen.queryByTestId('trade-row-gate_btc_1')).toBeNull()
    expect(screen.getByTestId('trade-row-okx_eth_1')).toBeTruthy()
  })

  it('2. surfaces Partial Sync badge in TopBar and warning banners across Overview, Trades & Settings', async () => {
    const dashWithWarn = makeDash({
      warnings: [
        {
          venue_id: 'gate',
          display_name: 'Gate',
          display_name_zh: 'Gate',
          warnings: ['现货账户缺少读取权限未计入', '历史成交已达分页上限截断'],
          summary: 'Spot permission missing; trade history truncated at pagination cap',
          summary_zh: '现货缺权限未计入；历史成交已达分页上限截断',
        },
      ],
    })
    const adaptersWithWarn: ExchangeAdapterInfo[] = [
      {
        venue_id: 'gate',
        display_name: 'Gate',
        display_name_zh: 'Gate',
        color_token: 'gate',
        requires_passphrase: false,
        capabilities: { futures: true },
        docs_url: '',
        api_base_url: 'https://api.gateio.ws',
        configured: true,
        credential: {
          venue_id: 'gate',
          label: 'Gate 主号',
          masked_api_key: 'GKEY...8899',
          masked_uid: '99***11',
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
          warnings: ['现货缺权限未计入', '历史成交已达分页上限截断'],
        },
      },
    ]

    vi.spyOn(api, 'authStatus').mockResolvedValue({
      initialized: true,
      enabled: false,
      authenticated: true,
      require_setup: false,
    })
    vi.spyOn(api, 'getDashboard').mockImplementation(async range => ({
      ...dashWithWarn,
      range_key: range,
    }))
    vi.spyOn(api, 'getSettings').mockResolvedValue({ ...baseSettings, privacy_mode: false })
    vi.spyOn(api, 'listExchanges').mockResolvedValue({ adapters: adaptersWithWarn, error_count: 0 })
    vi.spyOn(api, 'getFxRates').mockResolvedValue(DEFAULT_FX)
    vi.spyOn(api, 'getVersionCheck').mockRejectedValue(new Error('offline'))
    vi.spyOn(api, 'triggerSync').mockResolvedValue({ ok: true, venues: { gate: { ok: true } } })

    render(<App />)
    await screen.findByTestId('overview-page')

    // TopBar shows amber Partial Sync pill with title
    const partialPill = screen.getByTestId('topbar-partial-sync-badge')
    expect(partialPill.textContent).toContain('部分同步')
    expect(partialPill.getAttribute('title')).toContain('现货缺权限未计入')

    // Manual sync button finishes with Partial Sync rather than green "已同步"
    fireEvent.click(screen.getByTestId('sync-now-btn'))
    await waitFor(() => expect(screen.getByTestId('sync-now-btn').textContent).toContain('部分同步'))
    expect(screen.getByTestId('sync-now-btn').textContent).not.toContain('已同步')

    // Overview warning banner
    expect(screen.getByTestId('overview-warnings-banner').textContent).toContain('现货缺权限未计入')

    // Trades truncation warning banner
    fireEvent.click(screen.getByTestId('nav-trades'))
    await screen.findByTestId('trades-page')
    expect(screen.getByTestId('trades-warnings-banner').textContent).toContain('历史成交已达分页上限截断')

    // Settings per-account warning box
    fireEvent.click(screen.getByTestId('nav-settings'))
    await screen.findByTestId('settings-page')
    expect(screen.getByTestId('adapter-warning-box-gate').textContent).toContain('现货缺权限未计入')
  })

  it('3a & 4b. preserves onboarding failed_subaccounts warning across AuthGate and persists pre-unlock AuthGate locale switch', async () => {
    let requireSetup = false
    let authenticated = true
    const masterAddr = '0x' + 'a'.repeat(40)
    const failSubAddr = '0x' + 'f'.repeat(40)
    const updateSettingsSpy = vi
      .spyOn(api, 'updateSettings')
      .mockImplementation(async patch => ({ ...baseSettings, ...(patch as Partial<UserSettings>) }))

    vi.spyOn(api, 'authStatus').mockImplementation(async () => ({
      initialized: !requireSetup,
      enabled: false,
      authenticated,
      require_setup: requireSetup,
      can_skip: true,
    }))
    vi.spyOn(api, 'authSkip').mockImplementation(async () => {
      requireSetup = false
      authenticated = true
      return { ok: true, initialized: true, enabled: false, authenticated: true, require_setup: false }
    })
    vi.spyOn(api, 'getDashboard').mockResolvedValue(makeDash({ demo_mode: true, explicit_demo_mode: false }))
    vi.spyOn(api, 'getSettings').mockResolvedValue({ ...baseSettings, locale: 'zh-CN' })
    vi.spyOn(api, 'listExchanges').mockResolvedValue({
      error_count: 0,
      adapters: [
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
      ],
    })
    vi.spyOn(api, 'verifyExchange').mockResolvedValue({
      ok: true,
      verified: true,
      account_id: 'hyperliquid',
      subaccounts: [{ name: 'AlphaSub', address: failSubAddr, equity_usd: '500' }],
    })
    vi.spyOn(api, 'triggerSync').mockImplementation(async () => {
      requireSetup = true
      authenticated = false
      return {
        ok: true,
        committed: true,
        account_id: 'hyperliquid',
        failed_subaccounts: [{ address: failSubAddr, name: 'AlphaSub', error_zh: '上游限流 HTTP 429' }],
        auth_status: {
          initialized: false,
          enabled: false,
          authenticated: false,
          require_setup: true,
          can_skip: true,
        },
        venues: { hyperliquid: { ok: true } },
      }
    })
    vi.spyOn(api, 'getFxRates').mockResolvedValue(DEFAULT_FX)
    vi.spyOn(api, 'getVersionCheck').mockRejectedValue(new Error('offline'))

    window.location.hash = '#/settings'
    render(<App />)
    await screen.findByTestId('settings-page')

    fireEvent.click(screen.getByTestId('configure-adapter-hyperliquid'))
    fireEvent.change(screen.getByTestId('exchange-wallet-input'), { target: { value: masterAddr } })
    fireEvent.click(screen.getByTestId('probe-subaccounts-btn'))
    await screen.findByTestId('discovered-subaccounts-box')
    fireEvent.click(screen.getByTestId('save-exchange-btn'))

    // AuthGate pops up due to first live connection require_setup=true
    const setupCard = await screen.findByTestId('auth-setup-card')
    expect(setupCard).toBeTruthy()

    // Toggle locale to English on AuthGate before skipping/unlocking
    fireEvent.click(screen.getByRole('button', { name: /EN/ }))
    expect(screen.getByTestId('auth-skip-btn').textContent).toContain('Skip')

    // Skip password setup -> enters main UI
    fireEvent.click(screen.getByTestId('auth-skip-btn'))

    // 1) Locale stays English and syncs to backend via updateSettings({ locale: 'en' })
    await waitFor(() => expect(updateSettingsSpy).toHaveBeenCalledWith({ locale: 'en' }))
    expect(await screen.findByTestId('brand-title')).toHaveProperty('textContent', 'OpenTradeLedger')

    // 2) Onboarding failed subaccount warning banner is still visible after AuthGate unmount!
    const banner = await screen.findByTestId('onboarding-subaccount-warning-banner')
    expect(banner.textContent).toContain('AlphaSub')
    fireEvent.click(screen.getByTestId('dismiss-onboarding-warning-btn'))
    expect(screen.queryByTestId('onboarding-subaccount-warning-banner')).toBeNull()
  })

  it('3b. TradeDrilldownDrawer shows fallback notice when 7d is empty and passes "all" to onOpenInTrades', async () => {
    vi.spyOn(api, 'getDashboard').mockImplementation(async range => {
      const d = makeDash({ range_key: range })
      return range === 'all' ? d : { ...d, trades: [] }
    })

    const onOpenInTrades = vi.fn()
    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <TradeDrilldownDrawer
            venueId="gate"
            venueName="Gate"
            symbol="BTC_USDT"
            rangeKey="7d"
            open={true}
            onClose={() => {}}
            onOpenInTrades={onOpenInTrades}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    const fallbackBanner = await screen.findByTestId('drilldown-fallback-banner')
    expect(fallbackBanner.textContent).toContain('当前所选周期无交割记录，已为您展示全部历史记录')

    fireEvent.click(screen.getByRole('button', { name: '去合约页查看' }))
    expect(onOpenInTrades).toHaveBeenCalledWith('gate', 'BTC_USDT', 'all')
  })

  it('3c & 3d. supports clearing AI/Bark keys, prioritizes version check error, and handles 25s self-upgrade timeout without blind reload', async () => {
    const patches: Array<Record<string, unknown>> = []
    const onUpdate = vi.fn().mockImplementation(async (p: Record<string, unknown>) => {
      patches.push(p)
      return true
    })

    render(
      <I18nProvider locale="zh-CN">
        <AiConfigCard settings={baseSettings} onUpdateSettings={onUpdate} flashOk={() => {}} setErrorMsg={() => {}} />
        <BarkNotifyCard settings={baseSettings} onUpdateSettings={onUpdate} flashOk={() => {}} setErrorMsg={() => {}} />
      </I18nProvider>,
    )

    fireEvent.click(screen.getByTestId('clear-ai-key-btn'))
    await waitFor(() => expect(patches).toContainEqual({ clear_ai_api_key: true }))

    fireEvent.click(screen.getByTestId('clear-bark-key-btn'))
    await waitFor(() =>
      expect(patches).toContainEqual({ notifications: { clear_bark_push_key: true } }),
    )

    cleanup()

    // VersionUpgradeCard: error takes precedence even if cached update_available=true, and null check result shows error
    const setErrorMsg = vi.fn()
    const { rerender } = render(
      <I18nProvider locale="zh-CN">
        <VersionUpgradeCard
          settings={baseSettings}
          versionStatus={{
            current_version: '1.4.0',
            latest_version: '1.4.1',
            update_available: true,
            check_enabled: true,
            github_repo: '',
            release_url: '',
            release_name: 'v1.4.1',
            release_notes: '',
            published_at: '',
            checked_sec: 100,
            checked_at: '2026-11-26',
            stale: true,
            error: 'HTTP 503',
            error_zh: 'GitHub 接口 503',
          }}
          onCheckVersion={async () => null}
          onUpdateSettings={onUpdate}
          flashOk={() => {}}
          setErrorMsg={setErrorMsg}
        />
      </I18nProvider>,
    )

    expect(screen.getByTestId('version-status-badge').textContent).toBe('检测失败')

    // Now test manual check returning null when previously Up to Date -> flips badge to Check Error!
    rerender(
      <I18nProvider locale="zh-CN">
        <VersionUpgradeCard
          settings={baseSettings}
          versionStatus={{
            current_version: '1.4.0',
            latest_version: '1.4.0',
            update_available: false,
            check_enabled: true,
            github_repo: '',
            release_url: '',
            release_name: '',
            release_notes: '',
            published_at: '',
            checked_sec: 100,
            checked_at: '2026-11-26',
            stale: false,
            error: '',
            error_zh: '',
          }}
          onCheckVersion={async () => null}
          onUpdateSettings={onUpdate}
          flashOk={() => {}}
          setErrorMsg={setErrorMsg}
        />
      </I18nProvider>,
    )
    expect(screen.getByTestId('version-status-badge').textContent).toBe('已是最新')
    fireEvent.click(screen.getByTestId('check-version-btn'))
    await waitFor(() => expect(screen.getByTestId('version-status-badge').textContent).toBe('检测失败'))
    expect(setErrorMsg).toHaveBeenCalled()

    // Test 25s self-upgrade timeout -> must NOT reload or show 已完成升级
    vi.useFakeTimers()
    const origLocation = window.location
    const reloadSpy = vi.fn()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { reload: reloadSpy, hash: '', href: 'http://localhost/', pathname: '/' },
    })
    try {
      vi.spyOn(api, 'triggerSelfUpdate').mockResolvedValue({
        ok: true,
        updated_to: '1.4.2',
        restarting: true,
      })
      vi.spyOn(api, 'getHealth').mockRejectedValue(new Error('Container still restarting'))

      rerender(
        <I18nProvider locale="zh-CN">
          <VersionUpgradeCard
            settings={baseSettings}
            versionStatus={{
              current_version: '1.4.0',
              latest_version: '1.4.2',
              update_available: true,
              check_enabled: true,
              github_repo: '',
              release_url: '',
              release_name: 'v1.4.2',
              release_notes: '',
              published_at: '',
              checked_sec: 100,
              checked_at: '2026-11-26',
              stale: false,
              error: '',
              error_zh: '',
            }}
            onUpdateSettings={onUpdate}
            flashOk={() => {}}
            setErrorMsg={() => {}}
          />
        </I18nProvider>,
      )

      await act(async () => {
        fireEvent.click(screen.getByTestId('one-click-upgrade-btn'))
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(27000)
      })

      expect(reloadSpy).not.toHaveBeenCalled()
      expect(document.body.textContent).not.toContain('已完成升级至')
      expect(screen.getByTestId('upgrade-timeout-box')).toBeTruthy()
      expect(screen.getByTestId('upgrade-retry-health-btn')).toBeTruthy()
      expect(screen.getByTestId('upgrade-manual-reload-btn')).toBeTruthy()
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: origLocation })
    }
  })

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
})
