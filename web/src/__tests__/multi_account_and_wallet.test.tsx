import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { VenueBadge } from '../components/VenueBadge'
import { I18nProvider } from '../i18n'
import { api } from '../lib/api'
import { CurrencyProvider, DEFAULT_FX } from '../lib/currency'
import type { ExchangeAdapterInfo, UserSettings } from '../lib/types'
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
  privacy_mode: false,
  goal_enabled: false,
  goal_target_usd: null,
  goal_label: '',
  ai_base_url: '',
  ai_api_key: '',
  ai_model: '',
  copy_watcher_targets: [],
  modules: { copy_watcher: true, copy_trading: true, stocks: true },
  notifications: { bark_server_url: 'https://api.day.app', bark_push_key: '' },
}

describe('Multi-Account Settings, Subaccount Role Badges & Hyperliquid Keyless Modal', () => {
  it('resolves brand dot colors for all 4 new venues and multi-account IDs', () => {
    const { container } = render(
      <div>
        <VenueBadge venueId="hyperliquid" name="Hyperliquid" />
        <VenueBadge venueId="kucoin" name="KuCoin" />
        <VenueBadge venueId="bingx" name="BingX" />
        <VenueBadge venueId="htx" name="火币 HTX" />
        <VenueBadge venueId="binance__sub_quant" name="币安 · 量化子号" />
      </div>,
    )
    const dots = container.querySelectorAll('.otl-venue-dot')
    expect(dots).toHaveLength(5)
    expect((dots[0] as HTMLElement).style.background).toBe('var(--dot-hyperliquid)')
    expect((dots[1] as HTMLElement).style.background).toBe('var(--dot-kucoin)')
    expect((dots[2] as HTMLElement).style.background).toBe('var(--dot-bingx)')
    expect((dots[3] as HTMLElement).style.background).toBe('var(--dot-htx)')
    expect((dots[4] as HTMLElement).style.background).toBe('var(--dot-binance)')
  })

  it('displays multiple accounts on the same exchange with Master/Sub badges and submits create_new=true', async () => {
    const upsertSpy = vi.fn().mockResolvedValue(undefined)
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
        configured: true,
        accounts: [
          {
            account_id: 'bybit',
            adapter_id: 'bybit',
            configured: true,
            masked_uid: '10***01',
            account_role: 'master',
            credential: {
              venue_id: 'bybit',
              label: '手工主号',
              masked_api_key: 'MAST...1111',
              masked_uid: '10***01',
              account_role: 'master',
              revision: 'rev-1',
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
          {
            account_id: 'bybit__sub01',
            adapter_id: 'bybit',
            configured: true,
            masked_uid: '10***02',
            account_role: 'subaccount',
            credential: {
              venue_id: 'bybit__sub01',
              label: '量化子号',
              masked_api_key: 'SUBK...2222',
              masked_uid: '10***02',
              account_role: 'subaccount',
              revision: 'rev-2',
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

    expect(screen.getByText('已管理 2 个账号')).toBeTruthy()
    expect(screen.getByText('主账户')).toBeTruthy()
    expect(screen.getByText('子账户')).toBeTruthy()
    expect(screen.getByTestId('account-row-bybit').textContent).toContain('UID 10***01')
    expect(screen.getByTestId('account-row-bybit__sub01').textContent).toContain('UID 10***02')

    fireEvent.click(screen.getByTestId('add-account-bybit'))
    fireEvent.change(screen.getByTestId('exchange-label-input'), { target: { value: '跟单子号2' } })
    fireEvent.change(screen.getByTestId('exchange-key-input'), { target: { value: 'sub2-key' } })
    fireEvent.change(screen.getByTestId('exchange-secret-input'), { target: { value: 'sub2-sec' } })
    fireEvent.click(screen.getByTestId('save-exchange-btn'))

    await waitFor(() => expect(upsertSpy).toHaveBeenCalledTimes(1))
    expect(upsertSpy.mock.calls[0][0]).toMatchObject({
      venue_id: 'bybit',
      create_new: true,
      label: '跟单子号2',
      api_key: 'sub2-key',
      api_secret: 'sub2-sec',
    })
  })

  it('supports Hyperliquid keyless 0x wallet connection and on-chain subaccount discovery', async () => {
    const masterAddr = '0x' + 'a'.repeat(40)
    const subAddr = '0x' + 'b'.repeat(40)
    vi.spyOn(api, 'verifyExchange').mockResolvedValue({
      ok: true,
      verified: true,
      account_id: 'hyperliquid',
      subaccounts: [{ name: 'HLBot1', address: subAddr, equity_usd: '1250.50' }],
    })
    const upsertSpy = vi.fn().mockResolvedValue(undefined)

    const adapters: ExchangeAdapterInfo[] = [
      {
        venue_id: 'hyperliquid',
        display_name: 'Hyperliquid',
        display_name_zh: 'Hyperliquid',
        color_token: 'hyperliquid',
        auth_mode: 'wallet_address',
        requires_passphrase: false,
        capabilities: { futures: true, rebates: true },
        docs_url: 'https://hyperliquid.gitbook.io',
        api_base_url: 'https://api.hyperliquid.xyz',
        configured: false,
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

    fireEvent.click(screen.getByTestId('configure-adapter-hyperliquid'))
    expect(screen.queryByTestId('exchange-secret-input')).toBeNull()

    fireEvent.change(screen.getByTestId('exchange-wallet-input'), { target: { value: masterAddr } })
    fireEvent.click(screen.getByTestId('probe-subaccounts-btn'))

    await screen.findByTestId('discovered-subaccounts-box')
    expect(screen.getByText('HLBot1')).toBeTruthy()
    expect(screen.getByText('1250.50 U')).toBeTruthy()

    fireEvent.click(screen.getByTestId('save-exchange-btn'))
    await waitFor(() => expect(upsertSpy).toHaveBeenCalledTimes(1))
    expect(upsertSpy.mock.calls[0][0]).toMatchObject({
      venue_id: 'hyperliquid',
      wallet_address: masterAddr,
      api_key: masterAddr,
      api_secret: '',
      include_subaccounts: [{ name: 'HLBot1', address: subAddr }],
    })
  })
})
