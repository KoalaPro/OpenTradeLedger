import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from '../App'
import { VersionUpgradeCard } from '../components/settings/VersionUpgradeCard'
import { I18nProvider } from '../i18n'
import { api } from '../lib/api'
import { CurrencyProvider, DEFAULT_FX } from '../lib/currency'
import type { DashboardData, ExchangeAdapterInfo, UserSettings } from '../lib/types'
import { FeesAiPage } from '../pages/FeesAiPage'
import { SettingsPage } from '../pages/SettingsPage'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const baseSettings: UserSettings = {
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

const emptyDash: DashboardData = {
  demo_mode: false,
  read_only: true,
  last_sync_ms: 1732200000000,
  syncing: false,
  range_key: 'all',
  available_months: [],
  available_symbols: [],
  summary: {
    total_equity_usd: 1000,
    net_principal_usd: 1000,
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
  open_positions: [],
  trades: [],
  reconciliation: {
    net_principal_usd: 1000,
    total_equity_usd: 1000,
    net_pnl_usd: 0,
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

describe('v1.4.0 Full-Stack QA UI Regressions', () => {
  it('shows Check Error instead of Up to Date when a cached version check fails on refresh', () => {
    render(
      <I18nProvider locale="zh-CN">
        <VersionUpgradeCard
          settings={baseSettings}
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

  it('distinguishes missing API Key when AI Base URL is already configured', () => {
    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <FeesAiPage
            data={emptyDash}
            settings={baseSettings}
            generating={false}
            onGenerate={async () => {}}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    fireEvent.click(screen.getByTestId('generate-ai-btn'))
    const modal = screen.getByTestId('ai-unconfigured-modal')
    expect(modal.textContent).toContain('尚未配置 AI API Key')
    expect(modal.textContent).toContain('https://api.openai.com/v1')
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
            settings={baseSettings}
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

  it('renders an explicit Retry button instead of staying stuck on Loading when initial dashboard 503s', async () => {
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
    vi.spyOn(api, 'getSettings').mockResolvedValue(baseSettings)
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
})
