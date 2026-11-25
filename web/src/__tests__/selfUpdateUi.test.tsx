import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { I18nProvider } from '../i18n'
import { api } from '../lib/api'
import { CurrencyProvider, DEFAULT_FX } from '../lib/currency'
import type { UserSettings, VersionCheckPayload } from '../lib/types'
import { SettingsPage } from '../pages/SettingsPage'

const BASE_SETTINGS: UserSettings = {
  locale: 'zh-CN',
  theme: 'obsidian',
  polarity: 'cn',
  display_currency: 'USDT',
  privacy_mode: false,
  update_check_enabled: true,
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

describe('One-Click In-App OTA Upgrade UI', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders prominent one-click upgrade button when a new release is available and triggers OTA update', async () => {
    const triggerSpy = vi
      .spyOn(api, 'triggerSelfUpdate')
      .mockResolvedValue({ ok: true, updated_to: '1.3.4', restarting: true })
    const healthSpy = vi
      .spyOn(api, 'getHealth')
      .mockResolvedValue({ status: 'ok', version: '1.3.4' })

    const versionStatus: VersionCheckPayload = {
      current_version: '1.3.3',
      latest_version: '1.3.4',
      update_available: true,
      ota_ready: true,
      check_enabled: true,
      github_repo: 'KoalaPro/OpenTradeLedger',
      release_url: 'https://github.com/KoalaPro/OpenTradeLedger/releases/tag/v1.3.4',
      release_name: 'v1.3.4 — 全平台网页一键在线升级',
      release_notes: '- 新增跨 NAS 网页内一键在线升级按钮',
      published_at: '2026-11-25 10:00',
      checked_sec: 1795572000,
      checked_at: '2026-11-25 10:01',
      stale: false,
      error: '',
      error_zh: '',
    }

    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <SettingsPage
            settings={BASE_SETTINGS}
            adapters={[]}
            demoMode={false}
            versionStatus={versionStatus}
            onCheckVersion={async () => versionStatus}
            onUpdateSettings={async () => {}}
            onUpsertExchange={async () => {}}
            onDeleteExchange={async () => {}}
            onTestExchange={async () => {}}
            onToggleDemo={async () => {}}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    const upgradeBtn = screen.getByTestId('one-click-upgrade-btn')
    expect(upgradeBtn.textContent).toContain('1.3.4')

    // Terminal command should be tucked behind optional manual guide toggle, not forced upfront
    expect(screen.getByTestId('manual-upgrade-guide-toggle')).toBeTruthy()

    fireEvent.click(upgradeBtn)

    await waitFor(() => {
      expect(triggerSpy).toHaveBeenCalledTimes(1)
      expect(healthSpy).toHaveBeenCalled()
    })
  })
})
