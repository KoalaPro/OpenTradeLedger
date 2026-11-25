import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { I18nProvider } from '../i18n'
import { CurrencyProvider, DEFAULT_FX } from '../lib/currency'
import { ExtensionsPage } from '../pages/ExtensionsPage'
import { SettingsPage } from '../pages/SettingsPage'
import type { DashboardData, UserSettings } from '../lib/types'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const mockSettings: UserSettings = {
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
  outbound_proxy_url: '',
  copy_watcher_targets: [],
  modules: { copy_watcher: true, copy_trading: true, stocks: true },
  notifications: {
    webhook_url: '',
    telegram_bot_token: '',
    telegram_chat_id: '',
    bark_server_url: 'https://api.day.app',
    bark_push_key: '',
  },
}

const emptyDash: DashboardData = {
  demo_mode: false,
  read_only: true,
  last_sync_ms: 0,
  syncing: false,
  range_key: 'all',
  available_months: [],
  available_symbols: [],
  summary: {
    total_equity_usd: 0,
    net_principal_usd: 0,
    whole_net_pnl_usd: 0,
    whole_roi_pct: 0,
    futures_net_pnl_usd: 0,
    futures_book_pnl_usd: 0,
    trade_count: 0,
    all_time_trade_count: 0,
    win_rate_pct: 0,
    total_fee_usd: 0,
    total_rebate_usd: 0,
    total_discount_saved_usd: 0,
    net_fee_usd: 0,
    combined_relief_pct: 0,
    potential_maker_savings_usd: 0,
    max_drawdown_usd: 0,
  },
  exchanges: [],
  reconciliation: { net_principal_usd: 0, total_equity_usd: 0, net_pnl_usd: 0, residual_usd: 0, items: [] },
  equity_curve: [],
  calendar_days: [],
  symbol_attribution: [],
  fee_matrix: [],
  open_positions: [],
  spot_holdings: [],
  trades: [],
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
      fee_rate_range: '—',
      venue_fee_rates: [],
      holdings: [],
      cash_ledger: [],
      perpetuals: [],
    },
  },
}

describe('v1.3.1 Public Copy Watcher & Bark Onboarding UX', () => {
  it('renders Public Radar sub-tab with beginner how-to guide and adds a lead trader from share URL', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.includes('/api/v1/extensions/copy-watcher/targets') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            ok: true,
            demo_mode: false,
            watcher_running: true,
            interval_sec: 10,
            trade_poll_sec: 30,
            cooldown_sec: 180,
            updated_at_ms: Date.now(),
            open_slot_count: 1,
            channels: { webhook: false, telegram: false, bark: true },
            configured_targets: ['3988776655443322110'],
            items: [
              {
                portfolio_id: '3988776655443322110',
                venue: 'binance',
                nickname: 'SteadyWhale',
                status: 'ACTIVE',
                portfolio_type: 'PUBLIC',
                enabled: true,
                slot_alert: true,
                trade_alert: true,
                current_copy_count: 399,
                max_copy_count: 400,
                available_slots: 1,
                has_open_slot: true,
                min_copy_usd: 100,
                profit_sharing_rate: '10',
                lead_url: 'https://www.binance.com/zh-CN/copy-trading/lead-details/3988776655443322110',
                app_deeplink: '',
                last_alert_ms: 0,
                inferred_positions: [],
                recent_orders: [],
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(
        JSON.stringify({
          demo_mode: false,
          watcher_running: true,
          interval_sec: 10,
          trade_poll_sec: 30,
          cooldown_sec: 180,
          updated_at_ms: 0,
          open_slot_count: 0,
          channels: { webhook: false, telegram: false, bark: false },
          configured_targets: [],
          items: [],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    })

    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <ExtensionsPage
            data={emptyDash}
            enableWatcher={true}
            enableCopy={true}
            enableStocks={true}
            requestedTab="watcher"
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    expect(await screen.findByTestId('ext-tab-watcher')).toBeTruthy()
    expect(screen.getByTestId('watcher-howto-guide')).toBeTruthy()

    const input = screen.getByTestId('watcher-add-input')
    fireEvent.change(input, {
      target: { value: 'https://www.binance.com/zh-CN/copy-trading/lead-details/3988776655443322110' },
    })
    fireEvent.click(screen.getByTestId('watcher-add-btn'))

    await waitFor(() => {
      expect(screen.getByText('SteadyWhale')).toBeTruthy()
    })
    expect(fetchMock).toHaveBeenCalled()
  })

  it('shows Bark onboarding guide in Settings and auto-saves pasted Bark URL when clicking Test Push directly', async () => {
    let capturedBody: { notifications?: { bark_push_key?: string } } | null = null
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.includes('/api/v1/extensions/copy-watcher/test-notify')) {
        capturedBody = init?.body
          ? (JSON.parse(String(init.body)) as { notifications?: { bark_push_key?: string } })
          : null
        return new Response(
          JSON.stringify({
            ok: true,
            sent: ['bark'],
            errors: {},
            settings: {
              ...mockSettings,
              notifications: {
                ...mockSettings.notifications,
                bark_server_url: 'https://api.day.app',
                bark_push_key_set: true,
              },
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 })
    })

    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <SettingsPage
            settings={mockSettings}
            adapters={[]}
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

    expect(screen.getByTestId('bark-onboarding-guide')).toBeTruthy()
    const barkKeyInput = screen.getByTestId('bark-push-key-input')
    fireEvent.change(barkKeyInput, {
      target: { value: 'https://api.day.app/NewUserKey777/这里改成你自己的推送内容' },
    })

    // User clicks Test Push directly WITHOUT clicking Save Config first!
    fireEvent.click(screen.getByTestId('test-notify-btn'))

    await waitFor(() => {
      expect(capturedBody).not.toBeNull()
    })
    const notifs = capturedBody!.notifications
    expect(notifs?.bark_push_key).toContain('NewUserKey777')
  })

  it('allows customizing watcher slot & position polling intervals inline', async () => {
    let savedIntervals: Record<string, unknown> | null = null
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.includes('/api/v1/extensions/copy-watcher/config') && init?.method === 'PUT') {
        savedIntervals = init.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null
        return new Response(
          JSON.stringify({
            ok: true,
            demo_mode: false,
            watcher_running: true,
            interval_sec: 5,
            trade_poll_sec: 15,
            cooldown_sec: 120,
            updated_at_ms: Date.now(),
            open_slot_count: 0,
            channels: { webhook: false, telegram: false, bark: true },
            configured_targets: [],
            items: [],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(
        JSON.stringify({
          demo_mode: false,
          watcher_running: true,
          interval_sec: 10,
          trade_poll_sec: 30,
          cooldown_sec: 180,
          updated_at_ms: 0,
          open_slot_count: 0,
          channels: { webhook: false, telegram: false, bark: true },
          configured_targets: [],
          items: [],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    })

    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <ExtensionsPage
            data={emptyDash}
            enableWatcher={true}
            enableCopy={true}
            enableStocks={true}
            requestedTab="watcher"
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    fireEvent.click(await screen.findByTestId('watcher-config-toggle-btn'))
    expect(screen.getByTestId('watcher-config-panel')).toBeTruthy()

    fireEvent.change(screen.getByTestId('watcher-interval-input'), { target: { value: '5' } })
    fireEvent.change(screen.getByTestId('watcher-trade-interval-input'), { target: { value: '15' } })
    fireEvent.change(screen.getByTestId('watcher-cooldown-input'), { target: { value: '120' } })
    fireEvent.click(screen.getByTestId('watcher-save-config-btn'))

    await waitFor(() => {
      expect(savedIntervals).toEqual({
        interval_sec: 5,
        trade_interval_sec: 15,
        cooldown_sec: 120,
      })
    })
  })
})
