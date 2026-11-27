import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { VersionUpgradeCard } from '../components/settings/VersionUpgradeCard'
import { I18nProvider } from '../i18n'
import { api } from '../lib/api'
import { CurrencyProvider, DEFAULT_FX } from '../lib/currency'
import type { DashboardData, ExchangeAdapterInfo, UserSettings } from '../lib/types'
import { SettingsPage } from '../pages/SettingsPage'

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

const uxBaseSettings: UserSettings = {
  locale: 'zh-CN',
  theme: 'obsidian',
  polarity: 'cn',
  display_currency: 'USDT',
  privacy_mode: false,
  goal_enabled: false,
  goal_target_usd: null,
  goal_label: '',
  ai_base_url: '',
  ai_api_key: '',
  ai_model: '',
  copy_watcher_targets: [],
  modules: { copy_trading: true, stocks: true },
  notifications: {
    bark_server_url: 'https://api.day.app',
    bark_push_key: '',
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
    ],
    reconciliation: {
      net_principal_usd: 7500,
      total_equity_usd: 8035,
      net_pnl_usd: 535,
      residual_usd: 12.45,
      items: [],
    },
    equity_curve: [],
    calendar_days: [],
    symbol_attribution: [],
    fee_matrix: [],
    goal: null,
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
    window.sessionStorage?.removeItem?.('otl_onboarding_warning_v1')
  } catch { /* ignore */ }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.location.hash = ''
})

describe('Warnings, Partial Sync Banners, Sub-Account Onboarding & AuthGate Preferences', () => {
  const unconfiguredGate: ExchangeAdapterInfo = {
    venue_id: 'gate', display_name: 'Gate', display_name_zh: 'Gate', color_token: 'gate',
    requires_passphrase: false, capabilities: {}, docs_url: '', api_base_url: '', configured: false,
  }

  function renderLedger(dash: DashboardData, adapter = unconfiguredGate) {
    vi.spyOn(api, 'authStatus').mockResolvedValue({
      initialized: true, enabled: false, authenticated: true, require_setup: false,
    })
    vi.spyOn(api, 'getDashboard').mockResolvedValue(dash)
    vi.spyOn(api, 'getSettings').mockResolvedValue(uxBaseSettings)
    vi.spyOn(api, 'listExchanges').mockResolvedValue({ adapters: [adapter], error_count: 0 })
    vi.spyOn(api, 'getFxRates').mockResolvedValue(DEFAULT_FX)
    vi.spyOn(api, 'getVersionCheck').mockRejectedValue(new Error('offline'))
    render(<App />)
  }

  it('opens first-account settings from an empty live ledger and explains an empty sync', async () => {
    vi.spyOn(api, 'triggerSync').mockResolvedValue({ ok: false, status: 'no_accounts', venues: {} })
    const dash = makeDash({ demo_mode: false, has_live_data: false, exchanges: [], trades: [], last_sync_ms: 0 })
    dash.reconciliation.residual_usd = 0
    renderLedger(dash)
    expect(await screen.findByRole('heading', { name: '尚未连接交易所' })).toBeTruthy()
    expect(screen.queryByText('闭环对账 · 0.00 误差')).toBeNull()
    expect(screen.queryByTestId('overview-page')).toBeNull()
    expect(screen.queryByTestId('demo-mode-badge')).toBeNull()
    fireEvent.click(screen.getByTestId('sync-now-btn'))
    await waitFor(() => expect(screen.getByTestId('sync-now-btn').textContent).toContain('没有可同步的账户'))
    fireEvent.click(screen.getByRole('button', { name: '添加第一个账户' }))
    expect(await screen.findByTestId('configure-adapter-gate')).toBeTruthy()
  })

  it('shows a connection check for an existing account awaiting its first readable snapshot', async () => {
    vi.spyOn(api, 'testExchange').mockResolvedValue({ ok: true })
    vi.spyOn(api, 'triggerSync').mockResolvedValue({ ok: false, status: 'no_accounts', venues: {} })
    renderLedger(makeDash({ demo_mode: false, has_live_data: true, exchanges: [], trades: [] }), {
      ...unconfiguredGate,
      configured: true,
      health: {
        status: 'error', last_attempt_ms: 1, last_ok_ms: 0, error_code: 'AUTH_OR_IP_REJECTED',
        error_reason: 'Invalid key', error_reason_zh: '密钥无效', raw_error: '',
      },
    })
    fireEvent.click(await screen.findByRole('button', { name: '检查账户连接' }))
    expect(await screen.findByTestId('settings-page')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '添加第一个账户' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '重试连接' }))
    expect(await screen.findByText('没有可同步的账户')).toBeTruthy()
    expect(screen.queryByText(/连接与数据同步已恢复正常/)).toBeNull()
  })

  it('keeps the explicit demo dashboard and reports a demo refresh without a live sync', async () => {
    vi.spyOn(api, 'triggerSync').mockResolvedValue({ ok: true, demo_mode: true, venues: {} })
    renderLedger(makeDash({ demo_mode: true, explicit_demo_mode: true, has_live_data: false }))
    expect(await screen.findByTestId('overview-page')).toBeTruthy()
    expect(screen.getByTestId('demo-mode-badge')).toBeTruthy()
    fireEvent.click(screen.getByTestId('sync-now-btn'))
    await waitFor(() => expect(screen.getByTestId('sync-now-btn').textContent).toContain('演示数据'))
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

  it('retains failed imports when the onboarding tab is closed, and clears them on dismissal', async () => {
    const storage = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
    })
    let requireSetup = false
    let authenticated = true
    const masterAddr = '0x' + 'a'.repeat(40)
    const failSubAddr = '0x' + 'e'.repeat(40)

    const setupMocks = () => {
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
      vi.spyOn(api, 'getDashboard').mockResolvedValue(makeDash({ demo_mode: false, explicit_demo_mode: false }))
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
        subaccounts: [{ name: 'SubVaultB', address: failSubAddr, equity_usd: '1200' }],
      })
      vi.spyOn(api, 'triggerSync').mockImplementation(async () => {
        requireSetup = true
        authenticated = false
        return {
          ok: true,
          committed: true,
          account_id: 'hyperliquid',
          failed_subaccounts: [{ address: failSubAddr, name: 'SubVaultB', error_zh: '节点超时 502' }],
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
      vi.spyOn(api, 'setDemoMode').mockResolvedValue({ ok: true, demo_mode: true })
      vi.spyOn(api, 'getFxRates').mockResolvedValue(DEFAULT_FX)
      vi.spyOn(api, 'getVersionCheck').mockRejectedValue(new Error('offline'))
    }

    setupMocks()
    window.location.hash = '#/settings'
    const firstMount = render(<App />)
    await screen.findByTestId('settings-page')

    fireEvent.click(screen.getByTestId('configure-adapter-hyperliquid'))
    fireEvent.change(screen.getByTestId('exchange-wallet-input'), { target: { value: masterAddr } })
    fireEvent.click(screen.getByTestId('probe-subaccounts-btn'))
    await screen.findByTestId('discovered-subaccounts-box')
    fireEvent.click(screen.getByTestId('save-exchange-btn'))

    // AuthGate pops up
    await screen.findByTestId('auth-setup-card')
    expect(window.localStorage.getItem('otl_onboarding_warning_v1')).toContain('SubVaultB')

    // Close the onboarding tab. The replacement tab only shares localStorage.
    firstMount.unmount()
    window.sessionStorage.removeItem('otl_onboarding_warning_v1')
    const secondMount = render(<App />)
    await screen.findByTestId('auth-setup-card')

    // Complete / skip AuthGate after refresh -> warning banner MUST still render!
    fireEvent.click(screen.getByTestId('auth-skip-btn'))
    const banner = await screen.findByTestId('onboarding-subaccount-warning-banner')
    expect(banner.textContent).toContain('SubVaultB')
    expect(banner.textContent).toContain('节点超时 502')

    // Dismissing removes from sessionStorage so subsequent refresh won't resurrect it
    fireEvent.click(screen.getByTestId('dismiss-onboarding-warning-btn'))
    expect(screen.queryByTestId('onboarding-subaccount-warning-banner')).toBeNull()
    expect(window.localStorage.getItem('otl_onboarding_warning_v1')).toBeNull()
    secondMount.unmount()
  })

  it('8. distinguishes already-connected Hyperliquid sub-wallets on re-probe and blurs masked UIDs inside conflict error banners', async () => {
    const masterAddr = '0x' + 'a'.repeat(40)
    const connectedSubAddr = '0x' + '1'.repeat(40)
    const missingSubAddr = '0x1111' + '2'.repeat(32) + '1111'

    vi.spyOn(api, 'verifyExchange').mockResolvedValue({
      ok: true,
      verified: true,
      account_id: 'hyperliquid',
      subaccounts: [
        { name: 'AlreadyInSub', address: connectedSubAddr, equity_usd: '800', already_connected: true },
        { name: 'MissedSub', address: missingSubAddr, equity_usd: '1500', already_connected: false },
      ],
    })

    const upsertCalls: unknown[] = []
    const onUpsert = vi.fn().mockImplementation(async (body: { venue_id: string }) => {
      upsertCalls.push(body)
      if (body.venue_id === 'gate') {
        throw new Error(
          '该 Gate 账号（770***1111）与钱包（0xaaaa...aaaa）已在账本中接入，请勿重复添加同一账号。',
        )
      }
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
        configured: true,
        accounts: [
          {
            account_id: 'hyperliquid',
            adapter_id: 'hyperliquid',
            configured: true,
            masked_uid: '0xaaaa...aaaa',
            account_role: 'wallet',
            credential: {
              venue_id: 'hyperliquid',
              label: 'HL 主钱包',
              masked_api_key: '0xaaaa...aaaa',
              masked_uid: '0xaaaa...aaaa',
              has_passphrase: false,
              enabled: true,
            },
          },
          {
            account_id: 'hyperliquid__sub1',
            adapter_id: 'hyperliquid',
            configured: true,
            masked_uid: '0x1111...1111',
            account_role: 'subaccount',
            credential: {
              venue_id: 'hyperliquid',
              label: 'HL · AlreadyInSub',
              masked_api_key: '0x1111...1111',
              masked_uid: '0x1111...1111',
              has_passphrase: false,
              enabled: true,
            },
          },
        ],
      },
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
          masked_api_key: 'GKEY...1111',
          masked_uid: '770***1111',
          has_passphrase: false,
          enabled: true,
        },
      },
    ]

    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <SettingsPage
            settings={{ ...baseSettings, privacy_mode: true }}
            adapters={adapters}
            demoMode={false}
            onUpdateSettings={async () => {}}
            onUpsertExchange={onUpsert}
            onDeleteExchange={async () => {}}
            onTestExchange={async () => {}}
            onToggleDemo={async () => {}}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    // Part A: Edit Hyperliquid main wallet -> Probe Subaccounts
    fireEvent.click(screen.getByTestId('edit-adapter-hyperliquid'))
    fireEvent.change(screen.getByTestId('exchange-wallet-input'), { target: { value: masterAddr } })
    fireEvent.click(screen.getByTestId('probe-subaccounts-btn'))

    await screen.findByTestId('discovered-subaccounts-box')
    const connectedRow = screen.getByTestId(`discovered-sub-row-${connectedSubAddr}`)
    const missingRow = screen.getByTestId(`discovered-sub-row-${missingSubAddr}`)

    // Already connected sub-wallet is unchecked by default and shows "已接入" badge
    expect((connectedRow.querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(false)
    expect(screen.getByTestId(`discovered-sub-connected-${connectedSubAddr}`).textContent).toBe('已接入')

    // Unconnected sub-wallet is checked by default with no "已接入" badge
    expect((missingRow.querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(true)
    expect(screen.queryByTestId(`discovered-sub-connected-${missingSubAddr}`)).toBeNull()

    fireEvent.click(screen.getByTestId('save-exchange-btn'))
    await waitFor(() => expect(upsertCalls.length).toBe(1))
    expect(upsertCalls[0]).toMatchObject({
      venue_id: 'hyperliquid',
      include_subaccounts: [{ name: 'MissedSub', address: missingSubAddr }],
    })

    // Close modal if still closing, then open Gate "+ 加账号" to trigger duplicate UID error
    await waitFor(() => expect(screen.queryByTestId('add-exchange-modal')).toBeNull())
    fireEvent.click(screen.getByTestId('add-account-gate'))
    fireEvent.change(screen.getByTestId('exchange-key-input'), { target: { value: 'DUP_GATE_KEY' } })
    fireEvent.change(screen.getByTestId('exchange-secret-input'), { target: { value: 'DUP_GATE_SEC' } })
    fireEvent.click(screen.getByTestId('save-exchange-btn'))

    // Part B: Verify both modal error banner and toast error banner wrap masked UIDs in .sensitive while keeping prose readable
    const modalErr = await screen.findByTestId('exchange-modal-error')
    expect(modalErr.className).not.toContain('sensitive')
    expect(modalErr.textContent).toContain('请勿重复添加同一账号')
    const modalMaskedSpans = Array.from(modalErr.querySelectorAll('.sensitive')).map(el => el.textContent)
    expect(modalMaskedSpans).toEqual(['770***1111', '0xaaaa...aaaa'])

    const toastErr = await screen.findByTestId('settings-error-banner')
    expect(toastErr.className).not.toContain('sensitive')
    const toastMaskedSpans = Array.from(toastErr.querySelectorAll('.sensitive')).map(el => el.textContent)
    expect(toastMaskedSpans).toEqual(['770***1111', '0xaaaa...aaaa'])
  })

  it('shows Check Error instead of Up to Date when a cached version check fails on refresh', () => {
    render(
      <I18nProvider locale="zh-CN">
        <VersionUpgradeCard
          settings={qaBaseSettings}
          versionStatus={{
            current_version: '1.4.0',
            latest_version: '1.4.0',
            update_available: false,
            check_enabled: true,
            github_repo: 'KoalaPro/OpenTradeLedger',
            release_url: '',
            release_name: '',
            release_notes: '',
            published_at: '',
            checked_sec: 1732200000,
            checked_at: '2026-11-26 11:00',
            stale: true,
            error: 'GitHub API HTTP 503',
            error_zh: 'GitHub 接口返回异常状态码 HTTP 503',
          }}
          onUpdateSettings={async () => true}
          flashOk={() => {}}
          setErrorMsg={() => {}}
        />
      </I18nProvider>,
    )

    expect(screen.getByTestId('version-status-badge').textContent).toBe('检测失败')
    expect(screen.getByTestId('version-error-box').textContent).toContain('HTTP 503')
  })

  it('preserves the selected English language when a locked page is reloaded', async () => {
    const storage = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
    })
    let locked = false
    let settings = { ...uxBaseSettings, goal_enabled: true, goal_target_usd: 12345 } as UserSettings
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const path = String(input).split('?')[0]
      if (path === '/api/v1/auth/status') return Response.json({ authenticated: !locked, enabled: true })
      if (path === '/api/v1/auth/logout') {
        locked = true
        return Response.json({ authenticated: false, enabled: true })
      }
      if (path === '/api/v1/settings') {
        if (init?.method === 'PUT') {
          const patch = JSON.parse(String(init.body))
          settings = { ...settings, ...patch }
        }
        return Response.json(settings)
      }
      if (path === '/api/v1/dashboard') return Response.json(makeDash())
      if (path === '/api/v1/exchanges') return Response.json({ adapters: [] })
      return Response.json({ ok: true, rates: DEFAULT_FX.rates })
    })
    window.location.hash = '#/settings'

    const app = render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'English' }))
    await waitFor(() => expect(document.documentElement.lang).toBe('en'))
    fireEvent.click(screen.getByTestId('sec-lock-now-btn'))
    await screen.findByTestId('auth-login-card')
    app.unmount()
    render(<App />)
    await screen.findByTestId('auth-login-card')
    expect(document.documentElement.lang).toBe('en')
    vi.unstubAllGlobals()
  })
})
