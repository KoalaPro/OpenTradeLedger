import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { OverviewPage } from '../pages/OverviewPage'
import { I18nProvider } from '../i18n'
import { CurrencyProvider, DEFAULT_FX } from '../lib/currency'
import type { DashboardData, UserSettings } from '../lib/types'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  window.location.hash = ''
})

const SETTINGS_ALL_EXT_OFF: UserSettings = {
  locale: 'zh-CN',
  polarity: 'cn',
  theme: 'obsidian',
  display_currency: 'USDT',
  privacy_mode: false,
  goal_enabled: false,
  goal_target_usd: null,
  goal_label: '',
  ai_base_url: '',
  ai_api_key: '',
  ai_model: '',
  copy_watcher_targets: [],
  modules: { copy_watcher: false, copy_trading: false, stocks: false },
  notifications: {
    bark_server_url: 'https://api.day.app',
    bark_push_key: '',
  },
}

const DASHBOARD_WITH_STOCK_AND_SPOT: DashboardData = {
  demo_mode: false,
  read_only: true,
  last_sync_ms: 1732500000000,
  syncing: false,
  range_key: 'all',
  available_months: ['2026-11'],
  available_symbols: ['QCOM_USDT'],
  summary: {
    total_equity_usd: 5732.54,
    net_principal_usd: 7809.78,
    whole_net_pnl_usd: -2077.24,
    whole_roi_pct: -26.6,
    futures_net_pnl_usd: 75.0,
    futures_book_pnl_usd: 75.0,
    trade_count: 1,
    all_time_trade_count: 1,
    win_rate_pct: 100,
    total_fee_usd: 1.0,
    total_rebate_usd: 0,
    total_discount_saved_usd: 0,
    net_fee_usd: 1.0,
    combined_relief_pct: 0,
    potential_maker_savings_usd: 0,
    max_drawdown_usd: 0,
  },
  equity_breakdown: {
    futures_margin_usd: 4734.21,
    copy_equity_usd: 942.53,
    stocks_equity_usd: 49.96,
    spot_and_other_usd: 5.84,
  },
  exchanges: [
    {
      venue_id: 'gate',
      display_name: 'Gate',
      display_name_zh: 'Gate',
      color_token: 'gate',
      connected: true,
      equity_usd: 4672.1,
      net_principal_usd: 4900,
      net_pnl_usd: -227.9,
      available_margin_usd: 3678.8,
      trade_count: 1,
      rebate_rate_pct: 40,
      discount_rate_pct: 0,
      capabilities: {},
    },
  ],
  open_positions: [],
  spot_holdings: [
    {
      venue_id: 'gate',
      venue_name: 'Gate',
      color_token: 'gate',
      asset: 'WLFI',
      qty: 60.570078,
      price_usd: 0.0401,
      market_value_usd: 2.43,
      wallet_type: 'UNIFIED',
    },
  ],
  trades: [],
  reconciliation: {
    net_principal_usd: 7809.78,
    total_equity_usd: 5732.54,
    net_pnl_usd: -2077.24,
    residual_usd: 0,
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
      enabled: false,
      total_invested_usd: 1000,
      active_equity_usd: 942.53,
      book_pnl_usd: -57.47,
      rebate_total_usd: 20.5,
      combined_pnl_usd: -36.97,
      combined_roi_pct: -3.7,
      platforms: [],
      transfers: [],
      rebates: [],
    },
    stocks: {
      enabled: false,
      equity_usd: 49.96,
      unrealized_pnl_usd: -0.67,
      non_perp_fees_usd: 0,
      fee_rate_range: '0%',
      venue_fee_rates: [{ venue_id: 'gate', venue_name: 'Gate', venue_name_zh: 'Gate', summary: '0%' }],
      holdings: [
        {
          venue_id: 'gate',
          venue_name: 'Gate',
          venue_name_zh: 'Gate',
          symbol: 'ANT',
          name: 'Anthropic',
          channel: 'US Equity',
          channel_zh: '美股正股',
          shares: 0.61,
          avg_cost_usd: 83.0,
          current_price_usd: 81.9,
          market_value_usd: 49.96,
          pnl_usd: -0.67,
          pnl_pct: -1.32,
        },
      ],
      cash_ledger: [
        {
          venue_id: 'gate',
          venue_name: 'Gate',
          venue_name_zh: 'Gate',
          time_str: 'IPO / Launchpad',
          type: 'IPO Subscription Net Debit',
          type_zh: '美股打新认购净扣款',
          symbol: 'USDT',
          change: '-53.16 USDT',
        },
      ],
      perpetuals: [
        {
          venue_id: 'gate',
          venue_name: 'Gate',
          symbol: 'QCOM_USDT',
          category: 'US Perp',
          trade_count: 1,
          qty: 10,
          maker_bps: 0,
          taker_bps: 5,
          gross_fee_usd: 1.0,
          rebate_usd: 0,
          net_fee_usd: 1.0,
          net_pnl_usd: 75.0,
        },
      ],
    },
  },
}

describe('Stocks & Spot Main-Page Detail Modals, Perp Separation & Blank-Page Prevention', () => {
  it('opens Stock and Spot detail modals directly on Overview even when Stocks/Extensions modules are turned off (never blank page), and omits Perps from Stock tab when enabled', async () => {
    let curSettings = { ...SETTINGS_ALL_EXT_OFF }

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.startsWith('/api/v1/auth/status')) {
        return new Response(JSON.stringify({ enabled: false, authenticated: true, require_setup: false }), { status: 200 })
      }
      if (url.startsWith('/api/v1/dashboard')) {
        return new Response(JSON.stringify(DASHBOARD_WITH_STOCK_AND_SPOT), { status: 200 })
      }
      if (url.startsWith('/api/v1/settings')) {
        return new Response(JSON.stringify(curSettings), { status: 200 })
      }
      if (url.startsWith('/api/v1/exchanges')) {
        return new Response(JSON.stringify({ adapters: [], error_count: 0 }), { status: 200 })
      }
      if (url.startsWith('/api/v1/fx-rates')) {
        return new Response(
          JSON.stringify({ base: 'USD', source: 'mock', updated_at: '', rates: DEFAULT_FX.rates }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 })
    })

    const { unmount } = render(<App />)
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeTruthy())

    expect(screen.queryByTestId('overview-open-watcher-btn')).toBeNull()

    // 1. Clicking ANT stock row when stocks module is OFF must NOT navigate to a blank page — opens detail modal!
    const stock = screen.getByRole('button', { name: 'ANT' })
    expect(stock.closest('tr')?.querySelector('td:nth-child(5) .sensitive')?.textContent).toContain('83')
    fireEvent.click(stock)
    expect(screen.getByTestId('overview-page')).toBeTruthy()
    const stockModal = await screen.findByTestId('stock-holding-detail-modal')
    expect(stockModal.textContent).toContain('Anthropic')
    expect(within(stockModal).getByText(/^83(?:\.0+)?$/).closest('.sensitive')).toBeTruthy()
    expect(stockModal.textContent).toContain('-53.16 USDT')
    expect(screen.queryByTestId('stock-detail-open-ext-btn')).toBeNull()

    // Close stock modal via Escape, then click WLFI spot row -> opens spot detail modal!
    fireEvent.keyDown(stockModal, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByTestId('stock-holding-detail-modal')).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'WLFI' }))
    const spotModal = await screen.findByTestId('spot-holding-detail-modal')
    expect(spotModal.textContent).toContain('WLFI')
    expect(spotModal.textContent).toContain('60.570078')
    expect(spotModal.textContent).toContain('统一账户')

    unmount()

    // 2. When stocks module IS enabled, Stock tab in ExtensionsPage does NOT render perpetuals (QCOM_USDT)
    curSettings = { ...SETTINGS_ALL_EXT_OFF, modules: { copy_watcher: false, copy_trading: false, stocks: true } }
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeTruthy())
    fireEvent.click(screen.getByTestId('nav-extensions'))
    const stockView = await screen.findByTestId('stock-module-view')
    expect(stockView.textContent).toContain('ANT')
    expect(within(stockView).getByText('ANT').closest('tr')?.querySelector('td:nth-child(4) .sensitive')?.textContent).toContain('83')
    expect(stockView.textContent).not.toContain('QCOM_USDT')
    expect(screen.queryByTestId('stock-perps-fullwidth')).toBeNull()
  })
})

it('opens itemized wallets from the equity allocation even when current holdings are empty', async () => {
  const sample = DASHBOARD_WITH_STOCK_AND_SPOT
  const data: DashboardData = {
    ...sample,
    open_positions: [],
    spot_holdings: [],
    extensions: { ...sample.extensions, stocks: { ...sample.extensions.stocks, equity_usd: 0, holdings: [] } },
    summary: { ...sample.summary, total_equity_usd: 100 },
    equity_breakdown: { spot_and_other_usd: 100, stocks_equity_usd: 0, futures_margin_usd: 0, copy_equity_usd: 0 },
    spot_wallet_balances: [
      { asset: 'USDT', venue_id: 'easicoin', venue_name: 'EasiCoin', color_token: 'easicoin', wallet_type: 'SPOT', qty: 65, market_value_usd: 65 },
      { asset: 'USDT', venue_id: 'easicoin', venue_name: 'EasiCoin', color_token: 'easicoin', wallet_type: 'FUNDING', qty: 35, market_value_usd: 35 },
    ],
  }
  const overview = (dashboard: DashboardData) => (
    <I18nProvider locale="zh-CN"><OverviewPage data={dashboard} onNavigate={() => {}} /></I18nProvider>
  )
  const { rerender } = render(overview(data))
  fireEvent.click(screen.getByTestId('kpi-equity'))
  fireEvent.click(within(await screen.findByTestId('equity-drawer')).getByRole('button', { name: /现货、理财与空投尾仓/ }))
  const detail = within(await screen.findByTestId('spot-holding-detail-modal'))
  expect(detail.getAllByText('USDT')).toHaveLength(2)
  expect(detail.getByText('现货账户')).toBeTruthy()
  expect(detail.getByText('资金账户')).toBeTruthy()
  expect(detail.getByText('65')).toBeTruthy()
  expect(detail.getByText('35')).toBeTruthy()
  // Older snapshots with only an aggregate value must never invent a coin or a wallet.
  rerender(overview({ ...data, spot_wallet_balances: undefined }))
  expect(detail.queryByText('USDT')).toBeNull()
  expect(detail.getByText('当前快照没有逐币钱包余额。')).toBeTruthy()
})

it('falls back to all holdings when the selected asset category disappears on refresh', () => {
  const sample = DASHBOARD_WITH_STOCK_AND_SPOT
  const view = (data: DashboardData) => <I18nProvider locale="zh-CN"><OverviewPage data={data} onNavigate={() => {}} /></I18nProvider>
  const { rerender } = render(view(sample))
  const holdings = within(screen.getByTestId('overview-holdings'))
  fireEvent.click(holdings.getByRole('button', { name: '美股' }))
  expect(holdings.queryByText('WLFI')).toBeNull()
  rerender(view({ ...sample, extensions: { ...sample.extensions, stocks: { ...sample.extensions.stocks, holdings: [] } } }))
  expect(holdings.getByText('WLFI')).toBeTruthy()
  rerender(view(sample))
  expect(holdings.getByText('WLFI')).toBeTruthy()
  expect(holdings.getByText('ANT')).toBeTruthy()
})

it('keeps the latest selected dashboard range when an earlier response finishes last', async () => {
  let finish30: ((response: Response) => void) | undefined
  const dash = (range: string, equity: number) => new Response(JSON.stringify({
    ...DASHBOARD_WITH_STOCK_AND_SPOT, range_key: range, summary: { ...DASHBOARD_WITH_STOCK_AND_SPOT.summary, total_equity_usd: equity },
  }))
  vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const u = new URL(String(input), 'http://localhost')
    if (u.pathname.endsWith('/dashboard')) {
      const range = u.searchParams.get('range') || 'all'
      if (range === '30d') return new Promise<Response>(resolve => { finish30 = resolve })
      return dash(range, range === '7d' ? 777 : 111)
    }
    if (u.pathname.endsWith('/settings')) return new Response(JSON.stringify(SETTINGS_ALL_EXT_OFF))
    if (u.pathname.endsWith('/exchanges')) return new Response(JSON.stringify({adapters: []}))
    if (u.pathname.endsWith('/fx-rates')) return new Response(JSON.stringify(DEFAULT_FX))
    return new Response(JSON.stringify({authenticated: true, require_setup: false}))
  })
  render(<App />)
  await screen.findByTestId('overview-page')
  const ranges = within(screen.getByTestId('topbar-range-slot'))
  fireEvent.click(ranges.getByRole('button', { name: '30天' }))
  await waitFor(() => expect(finish30).toBeTypeOf('function'))
  expect(screen.queryByRole('button', { name: /总净资产/ })).toBeNull()
  fireEvent.click(ranges.getByRole('button', { name: '7天' }))
  await waitFor(() => expect(screen.getByTestId('kpi-equity').textContent).toContain('777.00'))
  await act(async () => { finish30!(dash('30d', 3030)) })
  expect(screen.getByTestId('kpi-equity').textContent).toContain('777.00')
})

it('closes details from the previous ledger when a refresh discovers a mode change', async () => {
  const sample = DASHBOARD_WITH_STOCK_AND_SPOT
  let demo = false
  vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/dashboard')) return Response.json({
      ...sample, demo_mode: demo, range_key: url.searchParams.get('range') || 'all',
      spot_holdings: demo ? [] : sample.spot_holdings,
    })
    if (url.pathname.endsWith('/settings')) return Response.json(SETTINGS_ALL_EXT_OFF)
    if (url.pathname.endsWith('/exchanges')) return Response.json({ adapters: [] })
    if (url.pathname.endsWith('/fx-rates')) return Response.json(DEFAULT_FX)
    return Response.json({ authenticated: true, require_setup: false })
  })
  render(<App />)
  fireEvent.click(await screen.findByText('WLFI'))
  expect(await screen.findByTestId('spot-holding-detail-modal')).toBeTruthy()
  demo = true
  fireEvent.click(within(screen.getByTestId('topbar-range-slot')).getByRole('button', { name: '7天' }))
  await waitFor(() => expect(screen.queryByTestId('spot-holding-detail-modal')).toBeNull())
})

it.each(['zh-CN', 'en'] as const)('shows the quoted low-liquidity subtotal and its pair evidence in %s', locale => {
  const en = locale === 'en'
  const sample = DASHBOARD_WITH_STOCK_AND_SPOT
  const rub = { ...sample.spot_holdings![0], asset: 'RUB', venue_id: 'hyperliquid', venue_name: 'Hyperliquid',
    qty: 2, price_usd: 16438, market_value_usd: 32876, wallet_type: 'SPOT',
    spot_market: { pair: '@173', quote_asset: 'USDC', price: 16438, volume_24h_quote: 0,
      source: 'Hyperliquid spotMetaAndAssetCtxs', liquidity: 'zero_volume' as const } }
  const other = { ...rub, venue_id: 'gate', venue_name: 'Gate', spot_market: undefined }
  const data = { ...sample, summary: { ...sample.summary, total_equity_usd: 65752 },
    spot_holdings: [rub, other], spot_wallet_balances: [rub, other] }
  const view = (current: DashboardData) => <I18nProvider locale={locale}><CurrencyProvider currency="CNY"
    fx={{ ...DEFAULT_FX, rates: { ...DEFAULT_FX.rates, CNY: 7 } }}><OverviewPage data={current}
      onNavigate={() => {}} /></CurrencyProvider></I18nProvider>
  const { rerender } = render(view(data))
  const kpi = screen.getByTestId('kpi-equity')
  expect(kpi.textContent).toContain(en ? 'Low-liquidity estimate' : '低流动性估值')
  expect(within(kpi).getByText(/230,132/).closest('.sensitive')).toBeTruthy()
  fireEvent.click(kpi)
  const breakdown = within(screen.getByTestId('equity-drawer'))
  expect(breakdown.getByText(/RUB.*USDC/)).toBeTruthy()
  fireEvent.keyDown(screen.getByTestId('equity-drawer'), {key: 'Escape'})
  fireEvent.click(screen.getAllByRole('button', {name: 'RUB'})[0])
  const detail = screen.getByTestId('spot-holding-detail-modal')
  expect(detail.textContent).toContain('@173')
  expect(detail.textContent).toContain('16,438 USDC')
  expect(detail.textContent).toContain('0 USDC')
  expect(detail.textContent).toContain('spotMetaAndAssetCtxs')
  fireEvent.keyDown(detail, {key: 'Escape'})
  rerender(view({ ...data, spot_holdings: [other], spot_wallet_balances: [other] }))
  expect(kpi.textContent).not.toContain(en ? 'Low-liquidity estimate' : '低流动性估值')
})

it('shows the three largest low-liquidity holdings first and can expand the full breakdown', () => {
  const sample = DASHBOARD_WITH_STOCK_AND_SPOT
  const rows = [1, 5, 2, 4, 3].map(value => ({
    ...sample.spot_holdings![0], asset: 'COIN' + value, market_value_usd: value,
    spot_market: { pair: '@' + value, quote_asset: 'USDC', price: 1, volume_24h_quote: 0, liquidity: 'zero_volume' as const },
  }))
  render(<I18nProvider locale="en"><OverviewPage data={{ ...sample, spot_wallet_balances: rows }}
    onNavigate={() => {}} /></I18nProvider>)
  fireEvent.click(screen.getByTestId('kpi-equity'))
  const drawer = within(screen.getByTestId('equity-drawer'))
  const pairs = () => drawer.getAllByText(/COIN\d\/USDC/).map(el => el.textContent?.split('/')[0])
  expect(pairs()).toEqual(['COIN5', 'COIN4', 'COIN3'])
  fireEvent.click(drawer.getByRole('button', {name: 'Show 2 more holdings'}))
  expect(pairs()).toEqual(['COIN5', 'COIN4', 'COIN3', 'COIN2', 'COIN1'])
  fireEvent.click(drawer.getByRole('button', {name: 'Show fewer holdings'}))
  expect(pairs()).toHaveLength(3)
})

it('opens the selected token index when two spot balances share a name on the same venue', () => {
  const sample = DASHBOARD_WITH_STOCK_AND_SPOT
  const first = { ...sample.spot_holdings![0], asset: 'DUP', venue_id: 'hyperliquid', venue_name: 'Hyperliquid',
    spot_token_index: 12, spot_market: { pair: '@21', quote_asset: 'USDC', price: 1, volume_24h_quote: 0,
      liquidity: 'zero_volume' as const } }
  const second = { ...first, spot_token_index: 33, spot_market: { ...first.spot_market, pair: '@89' } }
  render(<I18nProvider locale="en"><OverviewPage data={{ ...sample, spot_holdings: [first, second],
    spot_wallet_balances: [first, second] }} onNavigate={() => {}} /></I18nProvider>)
  fireEvent.click(screen.getAllByRole('button', {name: 'DUP'})[1])
  expect(screen.getByTestId('spot-holding-detail-modal').textContent).toContain('@89')
})

it('counts a promoted stock token and wallet-only dust once, excluding unknown coverage', () => {
  const sample = DASHBOARD_WITH_STOCK_AND_SPOT
  const market = { pair: '@20', quote_asset: 'USDC', price: 1, volume_24h_quote: 0, liquidity: 'zero_volume' as const }
  const spot = { ...sample.spot_holdings![0], asset: 'COIN', market_value_usd: 2, spot_market: market }
  const dust = { ...spot, asset: 'DUST', market_value_usd: 0.006 }
  const unknown = { ...spot, asset: 'UNKNOWN', market_value_usd: 400, spot_market: { liquidity: 'unknown' as const } }
  const stock = { ...sample.extensions.stocks.holdings[0], market_value_usd: 200, spot_market: {
    ...market, volume_24h_quote: 12, liquidity: 'holding_exceeds_volume' as const } }
  const data = { ...sample, spot_wallet_balances: [spot, dust, unknown], spot_holdings: [spot, unknown],
    extensions: { ...sample.extensions, stocks: { ...sample.extensions.stocks, holdings: [stock] } } }
  render(<I18nProvider locale="en"><OverviewPage data={data} onNavigate={() => {}} /></I18nProvider>)
  const kpi = screen.getByTestId('kpi-equity')
  expect(kpi.textContent).toContain('202.01 U')
  fireEvent.click(kpi)
  const breakdown = within(screen.getByTestId('equity-drawer'))
  expect(breakdown.getByText(/DUST\/USDC/)).toBeTruthy()
  expect(breakdown.getByText(/ANT\/USDC/)).toBeTruthy()
  expect(breakdown.queryByText(/UNKNOWN/)).toBeNull()
})
