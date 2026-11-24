import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from '../App'
import type { DashboardData, ExchangeAdapterInfo, UserSettings } from '../lib/types'

// ResizeObserver polyfill for Recharts inside jsdom
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
;(globalThis as unknown as { ResizeObserver: typeof ResizeObserverMock }).ResizeObserver = ResizeObserverMock

const mockDashboard: DashboardData = {
  demo_mode: true,
  read_only: true,
  last_sync_ms: 1732291200000,
  syncing: false,
  range_key: 'all',
  available_months: ['2026-11'],
  available_symbols: ['ETH_USDT', 'BTC_USDT'],
  summary: {
    total_equity_usd: 24680.5,
    net_principal_usd: 22000.0,
    whole_net_pnl_usd: 2680.5,
    whole_roi_pct: 12.18,
    futures_net_pnl_usd: 2145.2,
    futures_book_pnl_usd: 1900.0,
    trade_count: 2,
    all_time_trade_count: 2,
    win_rate_pct: 100,
    total_fee_usd: 15.0,
    total_rebate_usd: 9.0,
    total_discount_saved_usd: 1.5,
    net_fee_usd: 6.0,
    combined_relief_pct: 63.6,
    potential_maker_savings_usd: 120.5,
    max_drawdown_usd: 148.6,
  },
  exchanges: [
    {
      venue_id: 'okx',
      display_name: 'OKX',
      display_name_zh: 'OKX',
      color_token: 'okx',
      connected: true,
      equity_usd: 14000,
      net_principal_usd: 12000,
      net_pnl_usd: 2000,
      available_margin_usd: 11000,
      trade_count: 1,
      rebate_rate_pct: 35,
      discount_rate_pct: 0,
      capabilities: { futures: true },
    },
    {
      venue_id: 'bybit',
      display_name: 'Bybit',
      display_name_zh: 'Bybit',
      color_token: 'bybit',
      connected: true,
      equity_usd: 10680.5,
      net_principal_usd: 10000,
      net_pnl_usd: 680.5,
      available_margin_usd: 9500,
      trade_count: 1,
      rebate_rate_pct: 25,
      discount_rate_pct: 0,
      capabilities: { futures: true },
    },
  ],
  open_positions: [],
  trades: [
    {
      trade_id: 'okx_1',
      venue_id: 'okx',
      venue_name: 'OKX',
      color_token: 'okx',
      symbol: 'ETH_USDT',
      side: 'SHORT',
      qty: 5,
      open_time_ms: 1732280000000,
      close_time_ms: 1732290000000,
      open_time_str: '2026-11-22 15:00',
      close_time_str: '2026-11-22 17:46',
      close_date: '2026-11-22',
      holding_sec: 9960,
      is_partial_close: false,
      gross_pnl_usd: 200,
      funding_pnl_usd: 0,
      book_pnl_usd: 192,
      total_fee_usd: 8,
      total_rebate_usd: 2.8,
      total_discount_saved_usd: 0,
      net_fee_usd: 5.2,
      net_pnl_usd: 194.8,
      effective_relief_pct: 35,
      rebate_status: 'SETTLED',
      entry_leg: {
        role: 'MAKER',
        mode_code: 'LIMIT_MAKER',
        fee_bps: 2.0,
        time_ms: 1732280000000,
        time_str: '11-22 15:00',
        avg_price: 2700,
        notional_usd: 13500,
        gross_fee_usd: 2.5,
        discount_saved_usd: 0,
        discount_asset: '',
        rebate_usd: 0.875,
        rebate_pct: 35,
        net_fee_usd: 1.625,
        rebate_status: 'SETTLED',
        settled_at: '11-22 18:00',
      },
      exit_leg: {
        role: 'TAKER',
        mode_code: 'MARKET_TAKER',
        fee_bps: 5.0,
        time_ms: 1732290000000,
        time_str: '11-22 17:46',
        avg_price: 2660,
        notional_usd: 13300,
        gross_fee_usd: 5.5,
        discount_saved_usd: 0,
        discount_asset: '',
        rebate_usd: 1.925,
        rebate_pct: 35,
        net_fee_usd: 3.575,
        rebate_status: 'SETTLED',
        settled_at: '11-22 18:00',
      },
      tags: ['planned'],
      note: '',
    },
  ],
  reconciliation: {
    net_principal_usd: 22000,
    total_equity_usd: 24680.5,
    net_pnl_usd: 2680.5,
    residual_usd: 0,
    items: [
      { key: 'fut', label: 'Futures Net', label_zh: '合约实收净盈亏', detail: 'OKX + Bybit', detail_zh: 'OKX + Bybit', amount_usd: 2680.5 },
    ],
  },
  equity_curve: [{ seq: 1, date: '2026-11-22', label: '11-22', delta_usd: 194.8, cum_net_usd: 194.8, cum_book_usd: 192 }],
  calendar_days: [{ date: '2026-11-22', net_pnl_usd: 194.8, trade_count: 1 }],
  symbol_attribution: [
    { venue_id: 'okx', venue_name: 'OKX', color_token: 'okx', symbol: 'ETH_USDT', trade_count: 1, win_rate_pct: 100, gross_fee_usd: 8, rebate_usd: 2.8, discount_saved_usd: 0, book_pnl_usd: 192, net_pnl_usd: 194.8 },
  ],
  fee_matrix: [
    { venue_id: 'okx', display_name: 'OKX', color_token: 'okx', trade_count: 1, maker_gross_bps: 2, maker_net_bps: 1.3, taker_gross_bps: 5, taker_net_bps: 3.25, gross_fee_usd: 8, discount_saved_usd: 0, discount_label: '', rebate_usd: 2.8, rebate_pct: 35, net_fee_usd: 5.2, combined_relief_pct: 35, potential_maker_savings_usd: 1.5 },
  ],
  goal: null,
  ai_review: {
    generated_at: '2026-11-22 18:00:00',
    generated_ms: 1732291200000,
    model: 'local-actuarial',
    equity_snapshot_usd: 24680.5,
    stale: true,
    highlights: [{ title: 'Fee Relief', title_zh: '费率减免', metric: '35%', body: 'OKX rebates active', body_zh: 'OKX 返佣生效' }],
    markdown_commentary: '1. **Use limit exits** on *major pairs*.',
  },
  extensions: {
    copy_trading: {
      enabled: true,
      total_invested_usd: 1000,
      active_equity_usd: 1100,
      book_pnl_usd: 100,
      rebate_total_usd: 12,
      combined_pnl_usd: 112,
      combined_roi_pct: 11.2,
      platforms: [],
      transfers: [],
      rebates: [{ venue_id: 'okx', venue_name: 'OKX', time_str: '2026-11-22 10:00', destination: 'Main', est_gross_fee_usd: 1.0, rebate_usd: 0.35 }],
    },
    stocks: {
      enabled: true,
      equity_usd: 500,
      unrealized_pnl_usd: 25,
      non_perp_fees_usd: 0.5,
      fee_rate_range: '0% ~ 0.1%',
      venue_fee_rates: [{ venue_id: 'binance', venue_name: 'Binance', summary: '0.1%' }],
      holdings: [],
      cash_ledger: [],
      perpetuals: [{ venue_id: 'binance', venue_name: 'Binance', symbol: 'SNDK_USDT', category: 'US Perp', trade_count: 3, qty: 10, maker_bps: 0, taker_bps: 3.6, gross_fee_usd: 1.2, rebate_usd: 0.24, net_fee_usd: 0.96, net_pnl_usd: 45.2 }],
    },
  },
}

const mockSettings: UserSettings = {
  locale: 'zh-CN',
  theme: 'obsidian',
  polarity: 'cn',
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
    webhook_url: '',
    telegram_bot_token: '',
    telegram_chat_id: '',
    bark_server_url: 'https://api.day.app',
    bark_push_key: '',
  },
}

const mockAdapters: ExchangeAdapterInfo[] = [
  { venue_id: 'okx', display_name: 'OKX', display_name_zh: 'OKX', color_token: 'okx', requires_passphrase: true, capabilities: {}, docs_url: '', api_base_url: 'https://www.okx.com', configured: false },
  { venue_id: 'bybit', display_name: 'Bybit', display_name_zh: 'Bybit', color_token: 'bybit', requires_passphrase: false, capabilities: {}, docs_url: '', api_base_url: 'https://api.bybit.com', configured: false },
]

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('OpenTradeLedger Full 6-Page Integration', () => {
  it('navigates all 6 pages, shows explicit entry & exit fee cards, supports i18n/privacy/polarity, and exposes OKX & Bybit adapters', async () => {
    let curSettings = { ...mockSettings }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.startsWith('/api/v1/dashboard')) {
        return new Response(JSON.stringify(mockDashboard), { status: 200 })
      }
      if (url.startsWith('/api/v1/settings')) {
        if (init?.method === 'PUT' && init.body) {
          curSettings = { ...curSettings, ...JSON.parse(String(init.body)) }
        }
        return new Response(JSON.stringify(curSettings), { status: 200 })
      }
      if (url.startsWith('/api/v1/exchanges')) {
        return new Response(JSON.stringify({ adapters: mockAdapters }), { status: 200 })
      }
      if (url.startsWith('/api/v1/fx-rates')) {
        return new Response(
          JSON.stringify({
            base: 'USD',
            source: 'mock-fx',
            updated_at: '2026-11-23 21:00',
            rates: { USDT: 1, USD: 1, CNY: 7.0, GBP: 0.8, EUR: 0.9, HKD: 7.8, JPY: 150 },
          }),
          { status: 200 },
        )
      }
      if (url.startsWith('/api/v1/version-check')) {
        return new Response(
          JSON.stringify({
            current_version: '1.2.0',
            latest_version: '1.3.0',
            update_available: true,
            check_enabled: true,
            github_repo: 'KoalaPro/OpenTradeLedger',
            release_url: 'https://github.com/KoalaPro/OpenTradeLedger/releases/tag/v1.3.0',
            release_name: 'v1.3.0 — NAS Auto Update',
            release_notes: '- GHCR multi-arch images\n- In-app version alert',
            published_at: '2026-11-24 12:00',
            checked_sec: 1795492800,
            checked_at: '2026-11-24 12:05',
            stale: false,
            error: '',
            error_zh: '',
          }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 })
    })

    render(<App />)

    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeTruthy())
    expect(screen.getByTestId('brand-title').textContent).toBe('交易账本')

    // Clicking KPI card opens standalone Velvet floating modal cleanly (zero helper button clutter)
    fireEvent.click(screen.getByTestId('kpi-whole-pnl'))
    expect(screen.getByTestId('reconcile-drawer').textContent).toContain('合约实收净盈亏')

    // Trades page displays explicit Entry Fee and Exit Fee cards
    fireEvent.click(screen.getByTestId('nav-trades'))
    await waitFor(() => expect(screen.getByTestId('trades-page')).toBeTruthy())
    expect(screen.getByTestId('entry-leg-fee-card').textContent).toContain('-2.500 U')
    expect(screen.getByTestId('entry-leg-fee-card').textContent).toContain('-1.625 U')
    expect(screen.getByTestId('exit-leg-fee-card').textContent).toContain('-5.500 U')
    expect(screen.getByTestId('exit-leg-fee-card').textContent).toContain('-3.575 U')

    // Fees & AI page renders comparison matrix and clean Markdown without raw asterisks
    fireEvent.click(screen.getByTestId('nav-fees'))
    await waitFor(() => expect(screen.getByTestId('fees-ai-page')).toBeTruthy())
    expect(screen.getByTestId('fee-matrix-section').textContent).toContain('OKX')
    expect(screen.getByTestId('ai-stale-banner')).toBeTruthy()
    expect(screen.getByTestId('fees-ai-page').textContent).not.toContain('*major pairs*')

    // Extensions page renders both full-width tables
    fireEvent.click(screen.getByTestId('nav-extensions'))
    await waitFor(() => expect(screen.getByTestId('copy-rebates-fullwidth')).toBeTruthy())
    fireEvent.click(screen.getByTestId('ext-tab-stock'))
    expect(screen.getByTestId('stock-perps-fullwidth').textContent).toContain('SNDK_USDT')

    // Settings page shows OKX & Bybit adapters and toggles locale & privacy
    fireEvent.click(screen.getByTestId('nav-settings'))
    await waitFor(() => expect(screen.getByTestId('settings-page')).toBeTruthy())
    expect(screen.getByTestId('adapter-card-okx')).toBeTruthy()
    expect(screen.getByTestId('adapter-card-bybit')).toBeTruthy()

    fireEvent.click(screen.getByTestId('privacy-toggle-btn'))
    expect(document.documentElement.dataset.privacy).toBe('on')

    fireEvent.click(screen.getByTestId('locale-toggle-btn'))
    await waitFor(() => expect(screen.getByTestId('brand-title').textContent).toBe('OpenTradeLedger'))

    // Currency switch converts values using live FX rates (CNY x7.0)
    fireEvent.click(screen.getByTestId('nav-overview'))
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeTruthy())
    fireEvent.change(screen.getByTestId('currency-selector'), { target: { value: 'CNY' } })
    await waitFor(() => expect(screen.getByTestId('kpi-equity').textContent).toContain('¥172,763.50'))
    // Switch to GBP (x0.8)
    fireEvent.change(screen.getByTestId('currency-selector'), { target: { value: 'GBP' } })
    await waitFor(() => expect(screen.getByTestId('kpi-equity').textContent).toContain('£19,744.40'))
  })

  it('never shows Connected when an exchange API errors, displays the error reason, and supports NAS password setup + Ceremony C', async () => {
    let setupDone = false
    const liveDash = { ...mockDashboard, demo_mode: false }
    const adaptersWithError: ExchangeAdapterInfo[] = [
      {
        venue_id: 'binance',
        display_name: 'Binance',
        display_name_zh: '币安',
        color_token: 'binance',
        requires_passphrase: false,
        capabilities: {},
        docs_url: '',
        api_base_url: 'https://api.binance.com',
        configured: true,
        credential: {
          venue_id: 'binance',
          label: 'Binance Main',
          masked_api_key: 'abcd…9876',
          has_passphrase: false,
          enabled: true,
        },
        health: {
          status: 'error',
          last_attempt_ms: 1732291200000,
          last_ok_ms: 0,
          error_code: 'AUTH_OR_IP_REJECTED',
          error_reason: 'Invalid API key or IP not whitelisted',
          error_reason_zh: 'API Key 无效或当前出口 IP 未加入白名单',
          raw_error: 'HTTP 401: -2015 Invalid API-key, IP, or permissions',
        },
      },
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
          label: 'Gate Live',
          masked_api_key: 'efgh…4321',
          has_passphrase: false,
          enabled: true,
        },
        health: {
          status: 'connected',
          last_attempt_ms: 1732291200000,
          last_ok_ms: 1732291200000,
          error_code: '',
          error_reason: '',
          error_reason_zh: '',
          raw_error: '',
        },
      },
    ]

    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
      const url = String(input)
      if (url.startsWith('/api/v1/auth/status')) {
        return new Response(
          JSON.stringify(
            setupDone
              ? { initialized: true, enabled: true, authenticated: true, require_setup: false, can_skip: true }
              : { initialized: false, enabled: false, authenticated: false, require_setup: true, can_skip: true },
          ),
          { status: 200 },
        )
      }
      if (url.startsWith('/api/v1/auth/setup')) {
        setupDone = true
        return new Response(
          JSON.stringify({ ok: true, initialized: true, enabled: true, authenticated: true, require_setup: false, can_skip: true }),
          { status: 200 },
        )
      }
      if (url.startsWith('/api/v1/dashboard')) {
        return new Response(JSON.stringify(liveDash), { status: 200 })
      }
      if (url.startsWith('/api/v1/settings')) {
        return new Response(JSON.stringify(mockSettings), { status: 200 })
      }
      if (url.startsWith('/api/v1/exchanges')) {
        return new Response(JSON.stringify({ adapters: adaptersWithError, error_count: 1 }), { status: 200 })
      }
      if (url.startsWith('/api/v1/fx-rates')) {
        return new Response(
          JSON.stringify({
            base: 'USD',
            source: 'mock-fx',
            updated_at: '2026-11-23 21:00',
            rates: { USDT: 1, USD: 1, CNY: 7.0, GBP: 0.8, EUR: 0.9, HKD: 7.8, JPY: 150 },
          }),
          { status: 200 },
        )
      }
      if (url.startsWith('/api/v1/version-check')) {
        return new Response(
          JSON.stringify({
            current_version: '1.2.0',
            latest_version: '1.3.0',
            update_available: true,
            check_enabled: true,
            github_repo: 'KoalaPro/OpenTradeLedger',
            release_url: 'https://github.com/KoalaPro/OpenTradeLedger/releases/tag/v1.3.0',
            release_name: 'v1.3.0 — NAS Auto Update',
            release_notes: '- GHCR multi-arch images\n- In-app version alert',
            published_at: '2026-11-24 12:00',
            checked_sec: 1795492800,
            checked_at: '2026-11-24 12:05',
            stale: false,
            error: '',
            error_zh: '',
          }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 })
    })

    render(<App />)

    // 1. First-time NAS setup card appears before dashboard
    await waitFor(() => expect(screen.getByTestId('auth-setup-card')).toBeTruthy())
    fireEvent.change(screen.getByTestId('auth-password-input'), { target: { value: 'naspass123' } })
    fireEvent.change(screen.getByTestId('auth-confirm-input'), { target: { value: 'naspass123' } })
    fireEvent.click(screen.getByTestId('auth-submit-btn'))

    // 2. Ceremony C overlay renders and dashboard unlocks underneath
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeTruthy())
    expect(screen.getByTestId('sidebar-lock-btn')).toBeTruthy()
    await waitFor(() => expect(screen.getByTestId('sidebar-update-badge').textContent).toContain('v1.3.0'))

    // 3. TopBar displays clickable API error badge & clicking it navigates to Settings
    const errPill = screen.getByTestId('topbar-exchange-error-badge')
    expect(errPill.textContent).toContain('1 个交易所 API 报错')
    fireEvent.click(errPill)

    await waitFor(() => expect(screen.getByTestId('settings-page')).toBeTruthy())
    // Binance errored -> shows 连接报错 (NOT 已连接!) + diagnostic reason + raw API error
    expect(screen.getByTestId('adapter-status-binance').textContent).toContain('连接报错')
    expect(screen.getByTestId('adapter-status-binance').textContent).not.toContain('已连接')
    expect(screen.getByTestId('adapter-error-box-binance').textContent).toContain('未加入白名单')
    expect(screen.getByTestId('adapter-error-box-binance').textContent).toContain('-2015')
    // Gate succeeded -> shows 已连接
    expect(screen.getByTestId('adapter-status-gate').textContent).toContain('已连接')
    // Security password management card & Version Update card are rendered
    expect(screen.getByTestId('security-password-card')).toBeTruthy()
    expect(screen.getByTestId('version-update-card')).toBeTruthy()
    expect(screen.getByTestId('version-status-badge').textContent).toContain('v1.3.0')
    expect(screen.getByTestId('version-release-box').textContent).toContain('GHCR multi-arch images')
    expect(screen.getByTestId('copy-nas-upgrade-cmd')).toBeTruthy()
  })
})
