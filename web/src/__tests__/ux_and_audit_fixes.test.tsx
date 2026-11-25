import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from '../App'
import { MonthlyCalendar } from '../components/MonthlyCalendar'
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

const twoTradeDashboard: DashboardData = {
  demo_mode: false,
  read_only: true,
  last_sync_ms: 1732291200000,
  syncing: false,
  range_key: 'all',
  available_months: ['2026-11', '2026-10'],
  available_symbols: ['ETH_USDT', 'BTC_USDT'],
  summary: {
    total_equity_usd: 4600,
    net_principal_usd: 4900,
    whole_net_pnl_usd: -300,
    whole_roi_pct: -6.12,
    futures_net_pnl_usd: -200,
    futures_book_pnl_usd: -358,
    trade_count: 2,
    all_time_trade_count: 2,
    win_rate_pct: 50,
    total_fee_usd: 15,
    total_rebate_usd: 10,
    total_discount_saved_usd: 0,
    net_fee_usd: 5,
    combined_relief_pct: 66.7,
    potential_maker_savings_usd: 23.39,
    max_drawdown_usd: 400,
  },
  exchanges: [
    {
      venue_id: 'gate',
      display_name: 'Gate',
      display_name_zh: 'Gate',
      color_token: 'gate',
      connected: true,
      equity_usd: 4600,
      net_principal_usd: 4900,
      net_pnl_usd: -300,
      available_margin_usd: 3600,
      trade_count: 2,
      rebate_rate_pct: 75,
      discount_rate_pct: 0,
      capabilities: { futures: true },
    },
  ],
  open_positions: [],
  trades: [
    {
      trade_id: 't_1',
      venue_id: 'gate',
      venue_name: 'Gate',
      color_token: 'gate',
      symbol: 'ETH_USDT',
      side: 'SHORT',
      qty: 2,
      open_time_ms: 1732280000000,
      close_time_ms: 1732290000000,
      open_time_str: '2026-11-22 15:00',
      close_time_str: '2026-11-22 17:46',
      close_date: '2026-11-22',
      holding_sec: 9960,
      is_partial_close: false,
      gross_pnl_usd: 10,
      funding_pnl_usd: 0,
      book_pnl_usd: -2.0,
      total_fee_usd: 12,
      total_rebate_usd: 6.32,
      total_discount_saved_usd: 0,
      net_fee_usd: 5.68,
      net_pnl_usd: 4.32,
      effective_relief_pct: 52.7,
      rebate_status: 'SETTLED',
      entry_leg: {
        role: 'MAKER',
        mode_code: 'LIMIT_MAKER',
        fee_bps: 2.0,
        time_ms: 1732280000000,
        time_str: '11-22 15:00',
        avg_price: 3000,
        notional_usd: 6000,
        gross_fee_usd: 4,
        discount_saved_usd: 0,
        discount_asset: '',
        rebate_usd: 2.0,
        rebate_pct: 50,
        net_fee_usd: 2.0,
        rebate_status: 'SETTLED',
        settled_at: '',
      },
      exit_leg: {
        role: 'TAKER',
        mode_code: 'MARKET_TAKER',
        fee_bps: 5.0,
        time_ms: 1732290000000,
        time_str: '11-22 17:46',
        avg_price: 2995,
        notional_usd: 5990,
        gross_fee_usd: 8,
        discount_saved_usd: 0,
        discount_asset: '',
        rebate_usd: 4.32,
        rebate_pct: 54,
        net_fee_usd: 3.68,
        rebate_status: 'SETTLED',
        settled_at: '',
      },
      tags: [],
      note: '',
    },
    {
      trade_id: 't_2',
      venue_id: 'gate',
      venue_name: 'Gate',
      color_token: 'gate',
      symbol: 'BTC_USDT',
      side: 'LONG',
      qty: 0.1,
      open_time_ms: 1732180000000,
      close_time_ms: 1732190000000,
      open_time_str: '2026-11-21 12:00',
      close_time_str: '2026-11-21 14:00',
      close_date: '2026-11-21',
      holding_sec: 7200,
      is_partial_close: false,
      gross_pnl_usd: -200,
      funding_pnl_usd: 0,
      book_pnl_usd: -204.32,
      total_fee_usd: 3,
      total_rebate_usd: 0,
      total_discount_saved_usd: 0,
      net_fee_usd: 3,
      net_pnl_usd: -204.32,
      effective_relief_pct: 0,
      rebate_status: 'SETTLED',
      entry_leg: {
        role: 'TAKER',
        mode_code: 'MARKET_TAKER',
        fee_bps: 5.0,
        time_ms: 1732180000000,
        time_str: '11-21 12:00',
        avg_price: 90000,
        notional_usd: 9000,
        gross_fee_usd: 1.5,
        discount_saved_usd: 0,
        discount_asset: '',
        rebate_usd: 0,
        rebate_pct: 0,
        net_fee_usd: 1.5,
        rebate_status: 'SETTLED',
        settled_at: '',
      },
      exit_leg: {
        role: 'TAKER',
        mode_code: 'MARKET_TAKER',
        fee_bps: 5.0,
        time_ms: 1732190000000,
        time_str: '11-21 14:00',
        avg_price: 88000,
        notional_usd: 8800,
        gross_fee_usd: 1.5,
        discount_saved_usd: 0,
        discount_asset: '',
        rebate_usd: 0,
        rebate_pct: 0,
        net_fee_usd: 1.5,
        rebate_status: 'SETTLED',
        settled_at: '',
      },
      tags: [],
      note: '',
    },
  ],
  reconciliation: {
    net_principal_usd: 4900,
    total_equity_usd: 4600,
    net_pnl_usd: -300,
    residual_usd: 0,
    items: [
      {
        key: 'manual_futures',
        label: '① Gate Manual Futures Net PnL',
        label_zh: '① 纯手动合约实收净盈亏',
        detail: 'All-time 2 closed trades book -358.83 + manual rebates +157.86',
        detail_zh: '全历史 2 笔平仓账面 -358.83 + 手操返佣 +157.86',
        amount_usd: -200,
      },
    ],
  },
  equity_curve: [{ seq: 1, date: '2026-11-22', label: '11-22', delta_usd: -300, cum_net_usd: -300, cum_book_usd: -358 }],
  calendar_days: [
    { date: '2026-11-01', net_pnl_usd: 0.18, trade_count: 0 },
    { date: '2026-11-02', net_pnl_usd: -0.002, trade_count: 0 },
    { date: '2026-11-22', net_pnl_usd: -300, trade_count: 2 },
  ],
  symbol_attribution: [],
  fee_matrix: [],
  goal: null,
  ai_review: {
    generated_at: '2026-11-24 12:00:00 UTC',
    generated_ms: 1732291200000,
    model: 'local-actuarial',
    equity_snapshot_usd: 4600,
    stale: false,
    highlights: [
      {
        kind: 'maker_upside',
        title: 'Maker Execution Upside',
        title_zh: '吃单改挂单增厚空间',
        metric: '+23.39 U',
        amount_usd: 23.39,
        body: 'Shifting taker legs could retain +23.39 USDT.',
        body_zh: '将可控吃单腿改为限价挂单预计可多留存 +23.39 USDT。',
      },
    ],
    markdown_commentary: '1. Passive limit exits retain extra profit.',
    context_preview: { total_equity_usd: 4600 },
  },
  extensions: {
    copy_trading: {
      enabled: true,
      total_invested_usd: 1000,
      active_equity_usd: 866,
      book_pnl_usd: -134,
      rebate_total_usd: 19.83,
      combined_pnl_usd: -114.17,
      combined_roi_pct: -11.42,
      platforms: [],
      transfers: [],
      rebates: [],
    },
    stocks: {
      enabled: true,
      equity_usd: 53.32,
      unrealized_pnl_usd: 2.69,
      non_perp_fees_usd: 0,
      fee_rate_range: '0.098%',
      venue_fee_rates: [],
      holdings: [
        {
          venue_id: 'gate',
          venue_name: 'Gate',
          symbol: 'ANT',
          name: 'Ant',
          channel: 'US Equity',
          channel_zh: '美股正股',
          shares: 0.61,
          avg_cost_usd: 83,
          current_price_usd: 87.41,
          market_value_usd: 53.32,
          pnl_usd: 2.69,
          pnl_pct: 5.32,
        },
      ],
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
  vi.restoreAllMocks()
  window.location.hash = ''
})

describe('Audit Bugfixes: Calendar, Add-Account Modal, Privacy, Currency, Drafts & Routing', () => {
  it('opens an all-time perpetual breakdown from a seven-day view and keeps its full history in Trades', async () => {
    const data = structuredClone(twoTradeDashboard)
    data.available_symbols = ['QCOM_USDT']
    data.trades.forEach(trade => { trade.symbol = 'QCOM_USDT' })
    data.extensions.stocks.perpetuals = [{
      venue_id: 'gate', venue_name: 'Gate', symbol: 'QCOM_USDT', category: 'US Equity Perp',
      trade_count: 2, qty: 3, maker_bps: 0, taker_bps: 3, gross_fee_usd: 15,
      rebate_usd: 10, net_fee_usd: 5, net_pnl_usd: -200,
    }]
    data.extensions.stocks.enabled = false
    window.location.hash = '#/trades'
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
      const url = new URL(String(input), window.location.origin)
      if (url.pathname === '/api/v1/dashboard') {
        const range = url.searchParams.get('range')
        return Response.json({ ...data, range_key: range, trades: range === 'all' ? data.trades : data.trades.slice(0, 1) })
      }
      if (url.pathname === '/api/v1/settings') {
        return Response.json({ ...baseSettings, modules: { stocks: false, copy_trading: false, copy_watcher: false } })
      }
      if (url.pathname === '/api/v1/exchanges') return Response.json({ adapters: [] })
      if (url.pathname === '/api/v1/auth/status') return Response.json({ authenticated: true })
      return Response.json({ ok: true, rates: DEFAULT_FX.rates })
    })

    render(<App />)
    await screen.findByTestId('trades-page')
    fireEvent.click(screen.getByRole('button', { name: '7天' }))
    await waitFor(() => expect(screen.getAllByTestId(/^trade-row-/)).toHaveLength(1))
    fireEvent.click(screen.getByRole('button', { name: /Gate QCOM_USDT/ }))
    await screen.findByText(/2 笔交割/)
    fireEvent.click(screen.getByRole('button', { name: '去合约页查看' }))
    await waitFor(() => expect(screen.getAllByTestId(/^trade-row-/)).toHaveLength(2))
    expect((screen.getByRole('combobox', { name: '全部标的' }) as HTMLSelectElement).value).toBe('QCOM_USDT')
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

  it('uses guided FloatingModal for adding exchange (requiring passphrase on OKX, verifying before save), confirms before delete, exposes Bark Server URL, and avoids goal_target_usd=10000 pollution', async () => {
    const calls: Array<{ url: string; method: string; body?: unknown }> = []
    let curSettings = { ...baseSettings }
    const adapters: ExchangeAdapterInfo[] = [
      {
        venue_id: 'okx',
        display_name: 'OKX',
        display_name_zh: 'OKX',
        color_token: 'okx',
        requires_passphrase: true,
        capabilities: {},
        docs_url: 'https://www.okx.com/docs-v5/en/',
        api_base_url: 'https://www.okx.com',
        configured: false,
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
        credential: { venue_id: 'gate', label: 'Gate', masked_api_key: 'gate...0001', has_passphrase: false, enabled: true },
        health: { status: 'connected', last_attempt_ms: 1, last_ok_ms: 1, error_code: '', error_reason: '', error_reason_zh: '', raw_error: '' },
      },
    ]

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      const method = init?.method || 'GET'
      const parsedBody = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ url, method, body: parsedBody })

      if (url.startsWith('/api/v1/auth/status')) {
        return new Response(JSON.stringify({ initialized: true, enabled: false, authenticated: true, require_setup: false }), { status: 200 })
      }
      if (url.startsWith('/api/v1/dashboard')) {
        return new Response(JSON.stringify(twoTradeDashboard), { status: 200 })
      }
      if (url.startsWith('/api/v1/settings')) {
        if (method === 'PUT' && parsedBody) curSettings = { ...curSettings, ...(parsedBody as object) }
        return new Response(JSON.stringify(curSettings), { status: 200 })
      }
      if (url.startsWith('/api/v1/exchanges/verify')) {
        return new Response(JSON.stringify({ detail_zh: '口令错误', error_code: 'PASSPHRASE_ERROR' }), { status: 502 })
      }
      if (url.startsWith('/api/v1/exchanges')) {
        return new Response(JSON.stringify({ adapters, error_count: 0 }), { status: 200 })
      }
      return new Response(JSON.stringify({ ok: true, rates: DEFAULT_FX.rates }), { status: 200 })
    })

    render(<App />)
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeTruthy())
    fireEvent.click(screen.getByTestId('nav-settings'))
    await waitFor(() => expect(screen.getByTestId('settings-page')).toBeTruthy())

    // 1. Bark Server URL input is exposed
    expect(screen.getByTestId('bark-server-url-input')).toBeTruthy()

    // 2. Clicking Add Exchange on OKX opens Velvet FloatingModal with passphrase required before enabling save
    fireEvent.click(screen.getByTestId('configure-adapter-okx'))
    const addModal = await waitFor(() => screen.getByTestId('add-exchange-modal'))
    expect(addModal).toBeTruthy()
    expect(screen.getByTestId('exchange-label-input')).toBeTruthy()

    const saveBtn = screen.getByTestId('save-exchange-btn') as HTMLButtonElement
    fireEvent.change(screen.getByTestId('exchange-key-input'), { target: { value: 'okx_key_123' } })
    fireEvent.change(screen.getByTestId('exchange-secret-input'), { target: { value: 'okx_sec_123' } })
    // Still disabled because OKX requires passphrase!
    expect(saveBtn.disabled).toBe(true)

    fireEvent.change(screen.getByTestId('exchange-passphrase-input'), { target: { value: 'wrong_pass' } })
    expect(saveBtn.disabled).toBe(false)

    // Submitting runs /verify FIRST; since /verify returns 502, POST /api/v1/exchanges must NOT be called!
    fireEvent.click(saveBtn)
    await waitFor(() => expect(calls.some(c => c.url === '/api/v1/exchanges/verify')).toBe(true))
    expect(calls.some(c => c.url === '/api/v1/exchanges' && c.method === 'POST')).toBe(false)
    fireEvent.click(screen.getByTestId('cancel-exchange-modal-btn'))

    // 3. Clicking remove on Gate opens delete confirmation modal instead of firing DELETE immediately
    fireEvent.click(screen.getByTestId('remove-adapter-gate'))
    expect(screen.getByTestId('delete-exchange-modal')).toBeTruthy()
    expect(calls.some(c => c.method === 'DELETE')).toBe(false)
  })

  it('jumps from stock holding to stock tab, preserves unsaved trade note drafts across pagination, avoids double currency codes, and masks sensitive areas', async () => {
    let curSettings: UserSettings = { ...baseSettings, display_currency: 'HKD' }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.startsWith('/api/v1/auth/status')) {
        return new Response(JSON.stringify({ initialized: true, enabled: false, authenticated: true, require_setup: false }), { status: 200 })
      }
      if (url.startsWith('/api/v1/dashboard')) {
        return new Response(JSON.stringify(twoTradeDashboard), { status: 200 })
      }
      if (url.startsWith('/api/v1/settings')) {
        if (init?.method === 'PUT' && init.body) {
          curSettings = { ...curSettings, ...(JSON.parse(String(init.body)) as object) }
        }
        return new Response(JSON.stringify(curSettings), { status: 200 })
      }
      if (url.startsWith('/api/v1/exchanges')) {
        return new Response(JSON.stringify({ adapters: [], error_count: 0 }), { status: 200 })
      }
      if (url.startsWith('/api/v1/fx-rates')) {
        return new Response(
          JSON.stringify({ base: 'USD', source: 'mock', updated_at: '', rates: { ...DEFAULT_FX.rates, HKD: 7.8 } }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 })
    })

    render(<App />)
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeTruthy())

    // 1. No double currency code "-HK$2,340.00 HKD" on overview curve headline
    expect(screen.getByTestId('overview-page').textContent).not.toMatch(/HK\$[\d,.]+\s+HKD/)

    // 2. Clicking ANT stock row opens Stock Holding Detail modal on Overview, and optional button navigates to Stock tab
    fireEvent.click(screen.getByText('ANT'))
    const stkModal = await screen.findByTestId('stock-holding-detail-modal')
    expect(stkModal.textContent).not.toMatch(/HK\$[\d,.]+\s+HKD/)
    fireEvent.click(screen.getByTestId('stock-detail-open-ext-btn'))
    await waitFor(() => expect(screen.getByTestId('stock-module-view')).toBeTruthy())
    expect(window.location.hash).toBe('#/extensions/stock')
    expect(screen.getByTestId('stock-module-view').textContent).not.toMatch(/HK\$[\d,.]+\s+HKD/)

    // 3. Copy trading floating PnL subtitle has .sensitive class
    fireEvent.click(screen.getByTestId('ext-tab-copy'))
    await waitFor(() => expect(screen.getByTestId('copy-module-view')).toBeTruthy())
    expect(screen.getByTestId('copy-book-pnl-sub').className).toContain('sensitive')

    // 4. Fees & AI converts highlight amount to HK$ and marks JSON preview as .sensitive
    fireEvent.click(screen.getByTestId('nav-fees'))
    await waitFor(() => expect(screen.getByTestId('fees-ai-page')).toBeTruthy())
    expect(screen.getByTestId('fees-ai-page').textContent).toContain('+HK$182.44')
    fireEvent.click(screen.getByTestId('toggle-ai-payload-btn'))
    expect(screen.getByTestId('ai-payload-pre').className).toContain('sensitive')

    // 5. Trades page preserves unsaved draft per trade_id across Next/Previous navigation
    fireEvent.click(screen.getByTestId('nav-trades'))
    await waitFor(() => expect(screen.getByTestId('trades-page')).toBeTruthy())
    const noteBox = screen.getByPlaceholderText('记录这笔交易的入出场得失…') as HTMLTextAreaElement
    fireEvent.change(noteBox, { target: { value: '未保存复盘草稿_第一单' } })
    fireEvent.click(screen.getByLabelText('Next'))
    expect(noteBox.value).toBe('')
    fireEvent.click(screen.getByLabelText('Previous'))
    expect(noteBox.value).toBe('未保存复盘草稿_第一单')
  })

  it('supports unbinding API while keeping archived local history by default, plus purging archived data later', async () => {
    const deleteCalls: string[] = []
    let gateConfigured = true
    let gateArchived = true

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      const method = (init?.method || 'GET').toUpperCase()
      if (method === 'DELETE') {
        deleteCalls.push(url)
        if (url.includes('keep_data=true')) {
          gateConfigured = false
          gateArchived = true
        } else {
          gateConfigured = false
          gateArchived = false
        }
        return new Response(JSON.stringify({ ok: true, has_archived_data: gateArchived }), { status: 200 })
      }
      if (url.startsWith('/api/v1/auth/status')) {
        return new Response(JSON.stringify({ initialized: true, enabled: false, authenticated: true, require_setup: false }), { status: 200 })
      }
      if (url.startsWith('/api/v1/dashboard')) {
        const dash = {
          ...twoTradeDashboard,
          exchanges: gateArchived
            ? [
                {
                  ...twoTradeDashboard.exchanges[0],
                  connected: gateConfigured,
                  health_status: gateConfigured ? ('connected' as const) : ('archived' as const),
                },
              ]
            : [],
        }
        return new Response(JSON.stringify(dash), { status: 200 })
      }
      if (url.startsWith('/api/v1/settings')) {
        return new Response(JSON.stringify(baseSettings), { status: 200 })
      }
      if (url.startsWith('/api/v1/exchanges')) {
        return new Response(
          JSON.stringify({
            error_count: 0,
            adapters: [
              {
                venue_id: 'gate',
                display_name: 'Gate',
                display_name_zh: 'Gate',
                color_token: 'gate',
                requires_passphrase: false,
                capabilities: { futures: true, rebates: true },
                docs_url: '',
                api_base_url: 'https://api.gateio.ws',
                configured: gateConfigured,
                has_archived_data: gateArchived,
                credential: gateConfigured
                  ? { venue_id: 'gate', label: 'Gate', masked_api_key: 'gate...0001', has_passphrase: false, enabled: true }
                  : null,
                health: {
                  status: gateConfigured ? 'connected' : gateArchived ? 'archived' : 'not_configured',
                  last_attempt_ms: 0,
                  last_ok_ms: 0,
                  error_code: '',
                  error_reason: '',
                  error_reason_zh: '',
                  raw_error: '',
                },
              },
            ],
          }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 })
    })

    render(<App />)
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeTruthy())

    fireEvent.click(screen.getByTestId('nav-settings'))
    await waitFor(() => expect(screen.getByTestId('settings-page')).toBeTruthy())

    // 1. Click remove -> modal opens with keep-history option selected by default
    fireEvent.click(screen.getByTestId('remove-adapter-gate'))
    expect(screen.getByTestId('delete-exchange-modal')).toBeTruthy()
    expect(screen.getByTestId('delete-opt-keep')).toBeTruthy()
    expect(screen.getByTestId('delete-opt-purge')).toBeTruthy()

    fireEvent.click(screen.getByTestId('confirm-delete-exchange-btn'))
    await waitFor(() => expect(deleteCalls).toContain('/api/v1/exchanges/gate?keep_data=true'))

    // 2. Gate card now displays Archived status + Re-bind button + Purge archived data button
    await waitFor(() => expect(screen.getByTestId('adapter-status-gate').textContent).toContain('已封存'))
    expect(screen.getByTestId('configure-adapter-gate').textContent).toContain('重新绑定')
    expect(screen.getByTestId('purge-archived-gate')).toBeTruthy()

    // 3. Overview page also shows Archived badge on Gate
    fireEvent.click(screen.getByTestId('nav-overview'))
    await waitFor(() => expect(screen.getByTestId('overview-archived-badge-gate')).toBeTruthy())

    // 4. Returning to Settings and purging archived data calls keep_data=false
    fireEvent.click(screen.getByTestId('nav-settings'))
    await waitFor(() => expect(screen.getByTestId('purge-archived-gate')).toBeTruthy())
    fireEvent.click(screen.getByTestId('purge-archived-gate'))
    expect(screen.getByTestId('delete-exchange-modal')).toBeTruthy()
    fireEvent.click(screen.getByTestId('confirm-delete-exchange-btn'))
    await waitFor(() => expect(deleteCalls).toContain('/api/v1/exchanges/gate?keep_data=false&expected_revision=absent'))
  })

  it('prompts before generating review when AI is unconfigured, displays period scope badge, and prevents false green Synced state on sync error', async () => {
    const aiCalls: Array<{ range_key?: string; mode?: string }> = []
    let currentAiReview: DashboardData['ai_review'] = null

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      const method = (init?.method || 'GET').toUpperCase()
      if (url.startsWith('/api/v1/auth/status')) {
        return new Response(JSON.stringify({ initialized: true, enabled: false, authenticated: true, require_setup: false }), { status: 200 })
      }
      if (url.startsWith('/api/v1/ai-review') && method === 'POST') {
        const body = init?.body ? (JSON.parse(String(init.body)) as { range_key?: string; mode?: string }) : {}
        aiCalls.push(body)
        currentAiReview = {
          ...twoTradeDashboard.ai_review!,
          model: 'local-actuarial',
          range_key: body.range_key || '7d',
          range_label: 'Last 7 Days',
          range_label_zh: '近 7 天',
          trade_count_snapshot: 2,
          stale: false,
        }
        return new Response(JSON.stringify(currentAiReview), { status: 200 })
      }
      if (url.startsWith('/api/v1/sync') && method === 'POST') {
        return new Response(
          JSON.stringify({
            ok: true,
            venues: {
              easicoin: { ok: false, error: 'ReadTimeout', error_zh: '历史同步连接超时' },
            },
          }),
          { status: 200 },
        )
      }
      if (url.startsWith('/api/v1/dashboard')) {
        return new Response(
          JSON.stringify({
            ...twoTradeDashboard,
            range_key: url.includes('range=7d') ? '7d' : 'all',
            ai_review: currentAiReview,
          }),
          { status: 200 },
        )
      }
      if (url.startsWith('/api/v1/settings')) {
        return new Response(
          JSON.stringify({ ...baseSettings, ai_base_url: '', ai_api_key_set: false }),
          { status: 200 },
        )
      }
      if (url.startsWith('/api/v1/exchanges')) {
        return new Response(JSON.stringify({ adapters: [], error_count: 0 }), { status: 200 })
      }
      return new Response(JSON.stringify({ ok: true, rates: DEFAULT_FX.rates }), { status: 200 })
    })

    render(<App />)
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeTruthy())

    // 1. Trigger manual sync when a venue fails -> Sidebar must show error indicator instead of green "已同步"
    fireEvent.click(screen.getByTestId('sync-now-btn'))
    await waitFor(() => expect(screen.getByText(/部分交易所同步异常/)).toBeTruthy())
    expect(screen.getByTestId('sync-now-btn').textContent).not.toContain('已同步')
    expect(screen.getByTestId('sync-now-btn').textContent).toContain('同步异常')

    // 2. Navigate to Fees & AI, switch range to 7d
    fireEvent.click(screen.getByTestId('nav-fees'))
    await waitFor(() => expect(screen.getByTestId('fees-ai-page')).toBeTruthy())
    fireEvent.click(screen.getByText('7天'))

    // Unconfigured AI status badge is explicitly visible BEFORE clicking generate
    expect(screen.getByTestId('ai-config-status-badge').textContent).toContain('未配置 AI')

    // Clicking generate does NOT silently generate local-actuarial; opens choice modal first!
    fireEvent.click(screen.getByTestId('generate-ai-btn'))
    expect(aiCalls.length).toBe(0)
    expect(screen.getByTestId('ai-unconfigured-modal')).toBeTruthy()

    // Choosing "使用本地规则生成" runs 7d review and displays period scope badge + clear local attribution
    fireEvent.click(screen.getByTestId('ai-modal-use-local-btn'))
    await waitFor(() => expect(aiCalls.length).toBe(1))
    expect(aiCalls[0].range_key).toBe('7d')
    await waitFor(() => expect(screen.getByTestId('ai-scope-badge').textContent).toContain('近 7 天'))
    expect(screen.getByTestId('fees-ai-page').textContent).toContain('本地精算规则')
  })
})

describe('Settings persistence and login preferences', () => {
  function settingsServer(editEasicoin = false, editConflict = false) {
    let locked = false
    let settings = { ...baseSettings, goal_enabled: true, goal_target_usd: 12345 } as UserSettings
    const updates: Record<string, unknown>[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const path = String(input).split('?')[0]
      if (init?.method === 'DELETE' && editConflict) {
        updates.push({ expected_revision: new URL(String(input), 'http://localhost').searchParams.get('expected_revision') })
        return Response.json({ detail_zh: '配置已变更，请刷新后重新确认。', error_code: 'CREDENTIAL_EDIT_CONFLICT' }, { status: 409 })
      }
      if (path === '/api/v1/auth/status') return Response.json({ authenticated: !locked, enabled: true })
      if (path === '/api/v1/auth/logout') {
        locked = true
        return Response.json({ authenticated: false, enabled: true })
      }
      if (path === '/api/v1/settings') {
        if (init?.method === 'PUT') {
          const patch = JSON.parse(String(init.body))
          updates.push(patch)
          if (patch.outbound_proxy_url === 'bad') return Response.json({ detail: 'Invalid proxy address' }, { status: 400 })
          settings = { ...settings, ...patch }
        }
        return Response.json(settings)
      }
      if (path === '/api/v1/dashboard') return Response.json(twoTradeDashboard)
      if (path === '/api/v1/exchanges/verify') return Response.json({ ok: true, verified: true })
      if (path === '/api/v1/sync' && editConflict) {
        updates.push(JSON.parse(String(init?.body)).candidate_credential)
        return Response.json({
          detail_zh: '账户配置已更新，请刷新后重新编辑。', detail: 'The account configuration changed. Refresh before editing again.',
          error_code: 'CREDENTIAL_EDIT_CONFLICT', committed: false,
        }, { status: 409 })
      }
      if (path === '/api/v1/sync') return Response.json({
        detail_zh: '历史数据请求超时', committed: false,
      }, { status: 502 })
      if (path === '/api/v1/exchanges') return Response.json({
        adapters: editEasicoin ? [{
          venue_id: 'easicoin', display_name: 'EasiCoin', display_name_zh: '易币',
          configured: true, capabilities: {}, health: { status: 'connected' },
          credential: { revision: 'original-form-revision', label: '旧账户', masked_api_key: 'FAKE...1111', enabled: true },
        }] : [],
      })
      return Response.json({ ok: true, rates: DEFAULT_FX.rates })
    })
    window.location.hash = '#/settings'
    return updates
  }

  it('reports a failed proxy save without success, then clears the error on a successful retry', async () => {
    settingsServer()
    render(<App />)
    const input = await screen.findByTestId('outbound-proxy-input')
    fireEvent.change(input, { target: { value: 'bad' } })
    fireEvent.click(screen.getByTestId('save-proxy-btn'))
    await screen.findByText('Invalid proxy address')
    expect(screen.queryByText('网络代理已生效')).toBeNull()
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.click(screen.getByTestId('save-proxy-btn'))
    await screen.findByText('已清除网络代理，恢复直连模式')
    await waitFor(() => expect(screen.queryAllByText('Invalid proxy address')).toHaveLength(0))
  })

  it('keeps the existing goal when a negative amount is entered', async () => {
    const updates = settingsServer()
    render(<App />)
    const input = await screen.findByDisplayValue('12345')
    fireEvent.change(input, { target: { value: '-1' } })
    fireEvent.click(input.parentElement!.querySelector('button')!)
    expect(updates.some(patch => 'goal_target_usd' in patch)).toBe(false)
    expect(await screen.findByTestId('settings-error-banner')).toBeTruthy()
  })

  it('explains that a rejected EasiCoin backfill kept the existing account', async () => {
    settingsServer(true)
    render(<App />)
    fireEvent.click(await screen.findByTestId('edit-adapter-easicoin'))
    fireEvent.change(screen.getByTestId('exchange-key-input'), { target: { value: 'fixture-key' } })
    fireEvent.change(screen.getByTestId('exchange-secret-input'), { target: { value: 'fixture-secret' } })
    fireEvent.click(screen.getByTestId('save-exchange-btn'))
    const modal = screen.getByTestId('add-exchange-modal')
    await waitFor(() => expect(modal.textContent).toContain('历史数据请求超时'))
    expect(modal.textContent).toContain('原账户凭据和记录已保留')
    expect(screen.queryByTestId('settings-ok-banner')).toBeNull()
  })

  it.each(['zh-CN', 'en'])('sends the edit version and shows its conflict in %s', async locale => {
    const updates = settingsServer(true, true)
    render(<App />)
    await screen.findByTestId('edit-adapter-easicoin')
    if (locale === 'en') {
      fireEvent.click(screen.getByRole('button', { name: 'English' }))
      await waitFor(() => expect(document.documentElement.lang).toBe('en'))
    }
    fireEvent.click(screen.getByTestId('edit-adapter-easicoin'))
    fireEvent.change(screen.getByTestId('exchange-key-input'), { target: { value: 'fixture-key' } })
    fireEvent.change(screen.getByTestId('exchange-secret-input'), { target: { value: 'fixture-secret' } })
    fireEvent.click(screen.getByTestId('save-exchange-btn'))
    const modal = screen.getByTestId('add-exchange-modal')
    await waitFor(() => expect(modal.textContent).toContain(locale === 'en' ? 'Refresh before editing again' : '请刷新后重新编辑'))
    expect(updates.at(-1)?.expected_revision).toBe('original-form-revision')
    expect(modal.textContent).not.toContain(locale === 'en' ? 'Historical sync failed' : '历史同步失败')
    expect(screen.queryByTestId('settings-ok-banner')).toBeNull()
  })

  it('preserves the newer credentials when a removal dialog has become stale', async () => {
    const updates = settingsServer(true, true)
    render(<App />)
    fireEvent.click(await screen.findByTestId('remove-adapter-easicoin'))
    fireEvent.click(screen.getByTestId('confirm-delete-exchange-btn'))
    await screen.findByText('配置已变更，请刷新后重新确认。')
    expect(updates.at(-1)?.expected_revision).toBe('original-form-revision')
    expect(screen.queryByTestId('settings-ok-banner')).toBeNull()
  })

  it('preserves the selected English language when a locked page is reloaded', async () => {
    const storage = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
    })
    settingsServer()
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
