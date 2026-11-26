import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { I18nProvider } from '../i18n'
import { api } from '../lib/api'
import { CurrencyProvider, DEFAULT_FX } from '../lib/currency'
import type { DashboardData, UserSettings } from '../lib/types'
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
  window.location.hash = ''
})

describe('State Sync, Bootstrap Recovery, Scope Isolation & Journal Concurrency', () => {
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

  it('renders an explicit Retry button instead of staying stuck on Loading when initial dashboard 503s', async () => {
    const emptyDash = makeDash({
      available_months: [],
      available_symbols: [],
      exchanges: [],
      trades: [],
      calendar_days: [],
      goal: null,
    })

    vi.spyOn(api, 'authStatus').mockResolvedValue({
      initialized: true,
      enabled: false,
      authenticated: true,
      require_setup: false,
    })
    let failDash = true
    vi.spyOn(api, 'getDashboard').mockImplementation(async () => {
      if (failDash) throw new Error('Dashboard HTTP 503')
      return emptyDash
    })
    vi.spyOn(api, 'getSettings').mockResolvedValue(qaBaseSettings)
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

    const retryBtn = await screen.findByTestId('dashboard-retry-btn')
    expect(retryBtn.textContent).toBe('重试')
    expect(screen.queryByText('加载中…')).toBeNull()

    failDash = false
    fireEvent.click(retryBtn)
    await waitFor(() => expect(screen.queryByTestId('dashboard-retry-btn')).toBeNull())
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

  it('5. preserves in-flight typing during journal save and isolates late ack/error per trade_id across venue/trade switches', async () => {
    let resolveFirstSave!: () => void
    let rejectOkxSave!: (err: Error) => void
    let resolveOkxSuccessSave!: () => void
    const savedNotes: Record<string, { tags: string[]; note: string }> = {}

    vi.spyOn(api, 'authStatus').mockResolvedValue({
      initialized: true,
      enabled: false,
      authenticated: true,
      require_setup: false,
    })
    vi.spyOn(api, 'getDashboard').mockImplementation(async (range, venue = 'ALL', symbol = 'ALL') => {
      const d = makeDash({
        range_key: range,
        venue_filter: venue === 'ALL' ? 'ALL' : venue.toLowerCase(),
        symbol_filter: symbol === 'ALL' ? 'ALL' : symbol.toUpperCase(),
      })
      d.trades = d.trades
        .map(tr =>
          savedNotes[tr.trade_id]
            ? { ...tr, tags: savedNotes[tr.trade_id].tags, note: savedNotes[tr.trade_id].note }
            : tr,
        )
        .filter(tr => (venue === 'ALL' ? true : tr.venue_id.toLowerCase() === venue.toLowerCase()))
      return d
    })
    vi.spyOn(api, 'getSettings').mockResolvedValue({ ...baseSettings, privacy_mode: false })
    vi.spyOn(api, 'listExchanges').mockResolvedValue({ adapters: [], error_count: 0 })
    vi.spyOn(api, 'getFxRates').mockResolvedValue(DEFAULT_FX)
    vi.spyOn(api, 'getVersionCheck').mockRejectedValue(new Error('offline'))

    let callIdx = 0
    const saveSpy = vi.spyOn(api, 'saveJournal').mockImplementation((tradeId, tags, note) => {
      callIdx += 1
      if (callIdx === 1) {
        return new Promise<{ ok: boolean }>(resolve => {
          resolveFirstSave = () => {
            savedNotes[tradeId] = { tags: [...tags], note }
            resolve({ ok: true })
          }
        })
      }
      if (callIdx === 2) {
        savedNotes[tradeId] = { tags: [...tags], note }
        return Promise.resolve({ ok: true })
      }
      if (callIdx === 3) {
        return new Promise<{ ok: boolean }>((_, reject) => {
          rejectOkxSave = reject
        })
      }
      return new Promise<{ ok: boolean }>(resolve => {
        resolveOkxSuccessSave = () => {
          savedNotes[tradeId] = { tags: [...tags], note }
          resolve({ ok: true })
        }
      })
    })

    window.location.hash = '#/trades'
    render(<App />)
    await screen.findByTestId('trades-page')
    expect(screen.getByTestId('trade-row-gate_btc_1')).toBeTruthy()

    const textarea = screen.getByPlaceholderText(/记录|笔记|Note/i) as HTMLTextAreaElement

    // Case A: Type "第一版笔记" -> click Save -> continue typing while request is in flight!
    fireEvent.change(textarea, { target: { value: '第一版笔记' } })
    fireEvent.click(screen.getByTestId('journal-save-btn'))
    expect(saveSpy).toHaveBeenLastCalledWith('gate_btc_1', [], '第一版笔记', false)

    // User keeps typing BEFORE first save resolves
    fireEvent.change(textarea, { target: { value: '第一版笔记 + 等待保存时追加的内容' } })
    expect(textarea.value).toBe('第一版笔记 + 等待保存时追加的内容')

    // First save resolves -> MUST NOT wipe appended text and MUST NOT show "已保存复盘笔记"
    await act(async () => {
      resolveFirstSave()
    })
    expect(textarea.value).toBe('第一版笔记 + 等待保存时追加的内容')
    expect(screen.getByTestId('journal-save-btn').textContent).toBe('保存笔记')
    expect((screen.getByTestId('journal-save-btn') as HTMLButtonElement).disabled).toBe(false)

    // Clicking Save again submits the full updated text and shows "已保存复盘笔记"
    fireEvent.click(screen.getByTestId('journal-save-btn'))
    await waitFor(() =>
      expect(screen.getByTestId('journal-save-btn').textContent).toContain('已保存复盘笔记'),
    )
    expect(saveSpy).toHaveBeenLastCalledWith(
      'gate_btc_1',
      [],
      '第一版笔记 + 等待保存时追加的内容',
      false,
    )
    expect(textarea.value).toBe('第一版笔记 + 等待保存时追加的内容')

    // Case B1: Switch to OKX trade, click Save (slow failing request), then switch back to Gate BEFORE it rejects
    fireEvent.click(screen.getByTestId('trade-row-okx_eth_1'))
    fireEvent.change(textarea, { target: { value: 'OKX 慢请求笔记' } })
    fireEvent.click(screen.getByTestId('journal-save-btn'))

    // Immediately switch to Gate trade while OKX save is in-flight
    fireEvent.click(screen.getByTestId('trade-row-gate_btc_1'))
    expect((screen.getByTestId('journal-save-btn') as HTMLButtonElement).disabled).toBe(false)

    await act(async () => {
      rejectOkxSave(new Error('OKX 笔记写入超时 504'))
    })
    // Gate panel MUST NOT show OKX's error!
    expect(screen.queryByTestId('journal-save-error')).toBeNull()

    // Switching back to OKX shows OKX's isolated error
    fireEvent.click(screen.getByTestId('trade-row-okx_eth_1'))
    expect(screen.getByTestId('journal-save-error').textContent).toContain('OKX 笔记写入超时 504')

    // Case B2: Retry OKX save (slow succeeding request), switch to Gate BEFORE it resolves -> Gate must NOT show "已保存复盘笔记"
    fireEvent.click(screen.getByTestId('journal-save-btn'))
    fireEvent.click(screen.getByTestId('trade-row-gate_btc_1'))
    fireEvent.change(textarea, { target: { value: '第一版笔记 + 等待保存时追加的内容！' } })
    expect(screen.getByTestId('journal-save-btn').textContent).toBe('保存笔记')

    await act(async () => {
      resolveOkxSuccessSave()
    })
    // Gate panel MUST STILL show "保存笔记", NOT OKX's late "已保存复盘笔记"!
    expect(screen.getByTestId('journal-save-btn').textContent).toBe('保存笔记')
  })
})
