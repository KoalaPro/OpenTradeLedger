import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { AnimatedCollapse } from '../components/AnimatedCollapse'
import { TradeDrilldownDrawer } from '../components/TradeDrilldownDrawer'
import { I18nProvider } from '../i18n'
import { CurrencyProvider, DEFAULT_FX } from '../lib/currency'
import type { DashboardData, ExchangeAdapterInfo, UserSettings } from '../lib/types'

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
;(globalThis as unknown as { ResizeObserver: typeof ResizeObserverMock }).ResizeObserver = ResizeObserverMock

const baseSettings: UserSettings = {
  locale: 'zh-CN',
  theme: 'light',
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

const sampleDash: DashboardData = {
  demo_mode: false,
  read_only: true,
  last_sync_ms: 1732291200000,
  syncing: false,
  range_key: 'all',
  available_months: ['2026-11'],
  available_symbols: ['BTC_USDT'],
  summary: {
    total_equity_usd: 5730.45,
    net_principal_usd: 5000,
    whole_net_pnl_usd: 730.45,
    whole_roi_pct: 14.61,
    futures_net_pnl_usd: 730.45,
    futures_book_pnl_usd: 680.45,
    trade_count: 1,
    all_time_trade_count: 1,
    win_rate_pct: 100,
    total_fee_usd: 10,
    total_rebate_usd: 5,
    total_discount_saved_usd: 0,
    net_fee_usd: 5,
    combined_relief_pct: 50,
    potential_maker_savings_usd: 12,
    max_drawdown_usd: 40,
  },
  exchanges: [
    {
      venue_id: 'gate',
      display_name: 'Gate',
      display_name_zh: 'Gate',
      color_token: 'gate',
      connected: true,
      equity_usd: 5730.45,
      net_principal_usd: 5000,
      net_pnl_usd: 730.45,
      available_margin_usd: 5200,
      trade_count: 1,
      rebate_rate_pct: 50,
      discount_rate_pct: 0,
      capabilities: { futures: true },
    },
  ],
  open_positions: [],
  trades: [
    {
      trade_id: 'tr_1',
      venue_id: 'gate',
      venue_name: 'Gate',
      venue_name_zh: 'Gate',
      color_token: 'gate',
      symbol: 'BTC_USDT',
      side: 'LONG',
      qty: 0.1,
      open_time_ms: 1732280000000,
      close_time_ms: 1732290000000,
      open_time_str: '2026-11-22 15:00',
      close_time_str: '2026-11-22 17:46',
      close_date: '2026-11-22',
      holding_sec: 9960,
      is_partial_close: false,
      gross_pnl_usd: 735.45,
      funding_pnl_usd: 0,
      book_pnl_usd: 725.45,
      total_fee_usd: 10,
      total_rebate_usd: 5,
      total_discount_saved_usd: 0,
      net_fee_usd: 5,
      net_pnl_usd: 730.45,
      effective_relief_pct: 50,
      rebate_status: 'SETTLED',
      entry_leg: {
        role: 'MAKER',
        mode_code: 'LIMIT_MAKER',
        fee_bps: 2,
        time_ms: 1732280000000,
        time_str: '11-22 15:00',
        avg_price: 90000,
        notional_usd: 9000,
        gross_fee_usd: 4,
        discount_saved_usd: 0,
        discount_asset: '',
        rebate_usd: 2,
        rebate_pct: 50,
        net_fee_usd: 2,
        rebate_status: 'SETTLED',
        settled_at: '',
      },
      exit_leg: {
        role: 'TAKER',
        mode_code: 'MARKET_TAKER',
        fee_bps: 5,
        time_ms: 1732290000000,
        time_str: '11-22 17:46',
        avg_price: 97354.5,
        notional_usd: 9735,
        gross_fee_usd: 6,
        discount_saved_usd: 0,
        discount_asset: '',
        rebate_usd: 3,
        rebate_pct: 50,
        net_fee_usd: 3,
        rebate_status: 'SETTLED',
        settled_at: '',
      },
      tags: [],
      note: '',
    },
  ],
  reconciliation: {
    net_principal_usd: 5000,
    total_equity_usd: 5730.45,
    net_pnl_usd: 730.45,
    residual_usd: 0,
    items: [],
  },
  equity_curve: [
    { seq: 1, date: '2026-11-21', label: '11-21', delta_usd: 200, cum_net_usd: 200, cum_book_usd: 190 },
    { seq: 2, date: '2026-11-22', label: '11-22', delta_usd: 530.45, cum_net_usd: 730.45, cum_book_usd: 680.45 },
  ],
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

describe('Motion Engine, Zero-Jump Layout & Add-Account Ceremony', () => {
  it('AnimatedCollapse mounts synchronously on open and plays closing transition before unmounting', () => {
    vi.useFakeTimers()
    const { rerender } = render(
      <AnimatedCollapse open={false} testId="demo-collapse" durationMs={220}>
        <div data-testid="collapse-payload">Expanded Inner Content</div>
      </AnimatedCollapse>,
    )

    expect(screen.queryByTestId('collapse-payload')).toBeNull()

    // Flipping open=true mounts payload synchronously
    rerender(
      <AnimatedCollapse open={true} testId="demo-collapse" durationMs={220}>
        <div data-testid="collapse-payload">Expanded Inner Content</div>
      </AnimatedCollapse>,
    )
    expect(screen.getByTestId('collapse-payload')).toBeTruthy()

    // Flipping open=false enters closing state instead of snapping out in 0ms
    rerender(
      <AnimatedCollapse open={false} testId="demo-collapse" durationMs={220}>
        <div data-testid="collapse-payload">Expanded Inner Content</div>
      </AnimatedCollapse>,
    )
    expect(screen.getByTestId('demo-collapse').getAttribute('data-state')).toBe('closing')
    expect(screen.getByTestId('collapse-payload')).toBeTruthy()

    // After durationMs, unmounts cleanly
    act(() => {
      vi.advanceTimersByTime(250)
    })
    expect(screen.queryByTestId('collapse-payload')).toBeNull()
  })

  it('TradeDrilldownDrawer renders as a right-edge slide-over sheet with smooth exit state', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(sampleDash), { status: 200 }),
    )

    const { rerender } = render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <TradeDrilldownDrawer
            venueId="gate"
            venueName="Gate"
            symbol="BTC_USDT"
            rangeKey="all"
            open={true}
            onClose={() => {}}
            onOpenInTrades={() => {}}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    const drawer = await waitFor(() => screen.getByTestId('trade-drilldown-drawer'))
    expect(drawer.className).toContain('otl-drawer-backdrop')
    expect(drawer.querySelector('.otl-drawer-panel')).toBeTruthy()

    rerender(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <TradeDrilldownDrawer
            venueId="gate"
            venueName="Gate"
            symbol="BTC_USDT"
            rangeKey="all"
            open={false}
            onClose={() => {}}
            onOpenInTrades={() => {}}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )
    expect(screen.getByTestId('trade-drilldown-drawer').className).toContain('closing')
  })

  it('keeps Sidebar glider active on Settings, anchors TopBar utility buttons right of range pills, and runs 3-stage add-exchange ceremony + floating toast', async () => {
    const adapters: ExchangeAdapterInfo[] = [
      {
        venue_id: 'bybit',
        display_name: 'Bybit',
        display_name_zh: 'Bybit',
        color_token: 'bybit',
        requires_passphrase: false,
        capabilities: { futures: true },
        docs_url: 'https://bybit-exchange.github.io/docs/v5/intro',
        api_base_url: 'https://api.bybit.com',
        configured: false,
      },
    ]

    let resolveSync!: () => void
    const syncGate = new Promise<void>(r => {
      resolveSync = r
    })

    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
      const url = String(input)
      if (url.startsWith('/api/v1/auth/status')) {
        return new Response(
          JSON.stringify({ initialized: true, enabled: false, authenticated: true, require_setup: false }),
          { status: 200 },
        )
      }
      if (url.startsWith('/api/v1/dashboard')) {
        return new Response(JSON.stringify(sampleDash), { status: 200 })
      }
      if (url.startsWith('/api/v1/settings')) {
        return new Response(JSON.stringify(baseSettings), { status: 200 })
      }
      if (url.startsWith('/api/v1/exchanges/verify')) {
        return new Response(JSON.stringify({ ok: true }), { status: 200 })
      }
      if (url.startsWith('/api/v1/exchanges/bybit/test')) {
        return new Response(JSON.stringify({ ok: true }), { status: 200 })
      }
      if (url.startsWith('/api/v1/sync')) {
        await syncGate
        return new Response(JSON.stringify({ ok: true, venues: { bybit: { ok: true } } }), { status: 200 })
      }
      if (url.startsWith('/api/v1/exchanges')) {
        return new Response(JSON.stringify({ adapters, error_count: 0 }), { status: 200 })
      }
      return new Response(JSON.stringify({ ok: true, rates: DEFAULT_FX.rates }), { status: 200 })
    })

    render(<App />)
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeTruthy())

    // TopBar places range slot BEFORE currency-selector so utility buttons stay pinned right (0px shift)
    const rangeSlot = screen.getByTestId('topbar-range-slot')
    const currencySelect = screen.getByTestId('currency-selector')
    expect(
      Boolean(rangeSlot.compareDocumentPosition(currencySelect) & Node.DOCUMENT_POSITION_FOLLOWING),
    ).toBe(true)

    // Sidebar glider tracks active page even when navigating to Settings
    fireEvent.click(screen.getByTestId('nav-settings'))
    await waitFor(() => expect(screen.getByTestId('settings-page')).toBeTruthy())
    expect(screen.getByTestId('sidebar-glider').getAttribute('data-active-page')).toBe('settings')

    // Open Bybit Add-Exchange modal -> verify 3-step ceremony stepper & body Portal mount (immune to transform ancestors!)
    fireEvent.click(screen.getByTestId('configure-adapter-bybit'))
    const addModal = await waitFor(() => screen.getByTestId('add-exchange-modal'))
    expect(addModal.parentElement).toBe(document.body)
    const stepper = screen.getByTestId('exchange-ceremony-stepper')
    expect(stepper.getAttribute('data-stage')).toBe('credentials')

    fireEvent.change(screen.getByTestId('exchange-key-input'), { target: { value: 'bybit_ro_key' } })
    fireEvent.change(screen.getByTestId('exchange-secret-input'), { target: { value: 'bybit_ro_secret' } })
    expect(stepper.getAttribute('data-ready')).toBe('true')

    fireEvent.click(screen.getByTestId('save-exchange-btn'))

    // Verify transitions to 'syncing' once /verify passes and /sync is in flight
    await waitFor(() => expect(stepper.getAttribute('data-stage')).toBe('syncing'))

    // Complete sync and verify floating toast portals directly to document.body without layout hijack
    resolveSync()
    await waitFor(() => expect(screen.getByTestId('settings-ok-banner')).toBeTruthy(), { timeout: 2500 })
    const toastViewport = screen.getByTestId('floating-toast-viewport')
    expect(toastViewport.parentElement).toBe(document.body)
    expect(toastViewport.contains(screen.getByTestId('settings-ok-banner'))).toBe(true)
  })
})
