import { useEffect, useState } from 'react'
import { Check, Plus, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { VenueBadge } from '../components/VenueBadge'
import { useI18n } from '../i18n'
import { api } from '../lib/api'
import { CURRENCY_OPTIONS, useCurrency } from '../lib/currency'
import type { CurrencyCode, ExchangeAdapterInfo, Locale, Polarity, Theme, UserSettings } from '../lib/types'

interface SettingsPageProps {
  settings: UserSettings
  adapters: ExchangeAdapterInfo[]
  demoMode: boolean
  onUpdateSettings: (patch: Record<string, unknown>) => Promise<void>
  onUpsertExchange: (body: {
    venue_id: string
    label: string
    api_key: string
    api_secret: string
    passphrase?: string
  }) => Promise<void>
  onDeleteExchange: (venueId: string) => Promise<void>
  onTestExchange: (venueId: string) => Promise<void>
  onToggleDemo: (enabled: boolean) => Promise<void>
}

export function SettingsPage({
  settings,
  adapters,
  demoMode,
  onUpdateSettings,
  onUpsertExchange,
  onDeleteExchange,
  onTestExchange,
  onToggleDemo,
}: SettingsPageProps) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const [addingVenue, setAddingVenue] = useState<string>('')
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const [label, setLabel] = useState('')
  const [busyVenue, setBusyVenue] = useState('')
  const [goalLabel, setGoalLabel] = useState(settings.goal_label || '')
  const [goalInput, setGoalInput] = useState(String(settings.goal_target_usd ?? ''))
  const [aiUrl, setAiUrl] = useState(settings.ai_base_url || '')
  const [aiModel, setAiModel] = useState(settings.ai_model || '')
  const [aiKey, setAiKey] = useState('')
  const [aiModels, setAiModels] = useState<string[]>([])
  const [fetchingModels, setFetchingModels] = useState(false)
  const [webhookUrl, setWebhookUrl] = useState(settings.notifications?.webhook_url || '')
  const [tgToken, setTgToken] = useState(settings.notifications?.telegram_bot_token || '')
  const [tgChat, setTgChat] = useState(settings.notifications?.telegram_chat_id || '')
  const [barkKey, setBarkKey] = useState(settings.notifications?.bark_push_key || '')
  const [statusMsg, setStatusMsg] = useState('')
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    setGoalLabel(settings.goal_label || '')
    setGoalInput(String(settings.goal_target_usd ?? ''))
    setAiUrl(settings.ai_base_url || '')
    setAiModel(settings.ai_model || '')
    setWebhookUrl(settings.notifications?.webhook_url || '')
    setTgChat(settings.notifications?.telegram_chat_id || '')
  }, [
    settings.goal_label,
    settings.goal_target_usd,
    settings.ai_base_url,
    settings.ai_model,
    settings.notifications?.webhook_url,
    settings.notifications?.telegram_chat_id,
  ])

  const selectedAdapter = adapters.find(a => a.venue_id === addingVenue)

  const handleAddSubmit = async () => {
    if (!addingVenue || !apiKey.trim() || !apiSecret.trim()) return
    setBusyVenue(addingVenue)
    setErrorMsg('')
    try {
      await onUpsertExchange({
        venue_id: addingVenue,
        label: label.trim(),
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
        passphrase: passphrase.trim(),
      })
      setAddingVenue('')
      setApiKey('')
      setApiSecret('')
      setPassphrase('')
      setLabel('')
      setStatusMsg(
        locale === 'en'
          ? 'Connected & synced latest data'
          : '已加密保存并完成只读数据拉取',
      )
      setTimeout(() => setStatusMsg(''), 4000)
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Connection failed')
    } finally {
      setBusyVenue('')
    }
  }

  const handleTest = async (vid: string) => {
    setBusyVenue(vid)
    setErrorMsg('')
    try {
      await onTestExchange(vid)
      setStatusMsg(locale === 'en' ? `${vid.toUpperCase()} read-only connection OK` : `${vid.toUpperCase()} 只读 API 连接正常`)
      setTimeout(() => setStatusMsg(''), 3500)
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Connection test failed')
    } finally {
      setBusyVenue('')
    }
  }

  return (
    <div data-testid="settings-page" className="space-y-4">
      {statusMsg && (
        <div
          className="px-4 py-2.5 rounded-lg text-[12.5px] font-medium flex items-center gap-2"
          style={{ background: 'var(--pos-soft)', border: '1px solid var(--pos-bd)', color: 'var(--pos)' }}
        >
          <Check size={14} />
          <span>{statusMsg}</span>
        </div>
      )}
      {errorMsg && (
        <div
          className="px-4 py-2.5 rounded-lg text-[12.5px] font-medium"
          style={{ background: 'var(--warn-soft)', border: '1px solid var(--warn-bd)', color: 'var(--warn)' }}
        >
          {errorMsg}
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
        {/* Left: Pluggable Exchange Adapters (Binance / OKX / Bybit / Gate / Bitget / MEXC / EasiCoin) */}
        <section className="xl:col-span-7 otl-card p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="m-0 text-[15px] font-semibold text-[var(--fg)] flex items-center gap-2">
                <ShieldCheck size={16} className="txt-pos" />
                {t('set_ex_title')}
              </h2>
              <p className="m-0 text-[12px] text-[var(--fg-3)] mt-0.5">{t('set_ex_sub')}</p>
            </div>
          </div>

          <div className="space-y-2.5">
            {adapters.map(ad => (
              <div
                key={ad.venue_id}
                data-testid={`adapter-card-${ad.venue_id}`}
                className="otl-surface-2 rounded-xl p-3.5 space-y-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <VenueBadge
                      venueId={ad.venue_id}
                      name={locale === 'en' ? ad.display_name : ad.display_name_zh || ad.display_name}
                    />
                    <div className="min-w-0">
                      <div className="text-[13px] font-medium text-[var(--fg)]">
                        {ad.configured && ad.credential
                          ? `${ad.credential.label} · ${ad.credential.masked_api_key}`
                          : ad.api_base_url}
                      </div>
                      <div className="text-[11px] text-[var(--fg-3)]">
                        {ad.requires_passphrase ? t('set_passphrase_req') : 'GET-Only Signed API'}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    {ad.configured ? (
                      <>
                        <span
                          className="px-2 py-0.5 rounded-md text-[11px] font-semibold"
                          style={{ background: 'var(--pos-soft)', color: 'var(--pos)' }}
                        >
                          {t('set_connected')}
                        </span>
                        <button
                          type="button"
                          disabled={busyVenue === ad.venue_id}
                          onClick={() => void handleTest(ad.venue_id)}
                          className="h-7 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[11.5px] text-[var(--fg)] cursor-pointer disabled:opacity-50"
                        >
                          {t('set_test')}
                        </button>
                        <button
                          type="button"
                          onClick={() => void onDeleteExchange(ad.venue_id)}
                          className="h-7 px-2 rounded-lg border border-[var(--border)] text-[var(--fg-3)] hover:text-[var(--fg)] cursor-pointer"
                          aria-label={t('set_remove')}
                        >
                          <Trash2 size={13} />
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        data-testid={`configure-adapter-${ad.venue_id}`}
                        onClick={() => {
                          setAddingVenue(addingVenue === ad.venue_id ? '' : ad.venue_id)
                          setLabel(ad.display_name)
                        }}
                        className="inline-flex items-center gap-1 h-7 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[11.5px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer"
                      >
                        <Plus size={13} />
                        <span>{t('set_add_btn')}</span>
                      </button>
                    )}
                  </div>
                </div>

                {addingVenue === ad.venue_id && selectedAdapter && (
                  <div className="otl-drawer-expand pt-3 border-t border-[var(--border)] grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                    <input
                      type="text"
                      value={apiKey}
                      onChange={e => setApiKey(e.target.value)}
                      placeholder="Read-Only API Key"
                      className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] text-[var(--fg)]"
                    />
                    <input
                      type="password"
                      value={apiSecret}
                      onChange={e => setApiSecret(e.target.value)}
                      placeholder="API Secret"
                      className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] text-[var(--fg)]"
                    />
                    {selectedAdapter.requires_passphrase && (
                      <input
                        type="password"
                        value={passphrase}
                        onChange={e => setPassphrase(e.target.value)}
                        placeholder="API Passphrase"
                        className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] text-[var(--fg)] sm:col-span-2"
                      />
                    )}
                    <div className="sm:col-span-2 flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => setAddingVenue('')}
                        className="h-7 px-3 rounded-lg border border-[var(--border)] text-[12px] text-[var(--fg-2)] cursor-pointer"
                      >
                        {t('cancel')}
                      </button>
                      <button
                        type="button"
                        data-testid="save-exchange-btn"
                        disabled={busyVenue === ad.venue_id}
                        onClick={() => void handleAddSubmit()}
                        className="h-7 px-3 rounded-lg text-[12px] font-semibold cursor-pointer"
                        style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
                      >
                        {t('confirm_save')}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>

        {/* Right: Preferences, Polarity, Theme, Goal, Optional Modules, AI & Demo */}
        <section className="xl:col-span-5 space-y-4">
          <div className="otl-card p-5 space-y-4">
            <h2 className="m-0 text-[15px] font-semibold text-[var(--fg)]">{t('set_pref_title')}</h2>

            {/* Language */}
            <div className="flex items-center justify-between gap-2 text-[12.5px]">
              <span className="text-[var(--fg-2)]">{t('set_lang')}</span>
              <div className="inline-flex p-0.5 rounded-lg border border-[var(--border)] bg-[var(--surface-2)]">
                {(['zh-CN', 'en'] as Locale[]).map(l => (
                  <button
                    key={l}
                    type="button"
                    onClick={() => void onUpdateSettings({ locale: l })}
                    className="px-2.5 py-1 rounded-md text-[12px] font-medium cursor-pointer"
                    style={{
                      background: settings.locale === l ? 'var(--surface)' : 'transparent',
                      color: settings.locale === l ? 'var(--fg)' : 'var(--fg-2)',
                    }}
                  >
                    {l === 'zh-CN' ? '简体中文' : 'English'}
                  </button>
                ))}
              </div>
            </div>

            {/* Theme */}
            <div className="flex items-center justify-between gap-2 text-[12.5px] pt-3 border-t border-[var(--border)]">
              <span className="text-[var(--fg-2)]">{t('set_theme')}</span>
              <select
                aria-label={t('set_theme')}
                value={settings.theme}
                onChange={e => void onUpdateSettings({ theme: e.target.value as Theme })}
                className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)] cursor-pointer"
              >
                <option value="obsidian">{t('theme_obsidian')}</option>
                <option value="ocean">{t('theme_ocean')}</option>
                <option value="light">{t('theme_light')}</option>
              </select>
            </div>

            {/* Polarity */}
            <div className="flex items-center justify-between gap-2 text-[12.5px] pt-3 border-t border-[var(--border)]">
              <span className="text-[var(--fg-2)]">{t('set_polarity')}</span>
              <div className="inline-flex p-0.5 rounded-lg border border-[var(--border)] bg-[var(--surface-2)]">
                {(
                  [
                    ['cn', t('polarity_cn')],
                    ['global', t('polarity_global')],
                  ] as Array<[Polarity, string]>
                ).map(([p, lbl]) => (
                  <button
                    key={p}
                    type="button"
                    data-testid={`polarity-${p}`}
                    onClick={() => void onUpdateSettings({ polarity: p })}
                    className="px-2.5 py-1 rounded-md text-[12px] font-medium cursor-pointer whitespace-nowrap"
                    style={{
                      background: settings.polarity === p ? 'var(--surface)' : 'transparent',
                      color: settings.polarity === p ? 'var(--fg)' : 'var(--fg-2)',
                    }}
                  >
                    {lbl}
                  </button>
                ))}
              </div>
            </div>

            {/* Display Currency with Live FX */}
            <div className="space-y-1.5 pt-3 border-t border-[var(--border)] text-[12.5px]">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[var(--fg-2)]">{t('set_currency')}</span>
                <select
                  aria-label={t('set_currency')}
                  data-testid="settings-currency-select"
                  value={settings.display_currency || 'USDT'}
                  onChange={e =>
                    void onUpdateSettings({ display_currency: e.target.value as CurrencyCode })
                  }
                  className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)] cursor-pointer"
                >
                  {CURRENCY_OPTIONS.map(opt => (
                    <option key={opt.code} value={opt.code}>
                      {locale === 'en' ? opt.labelEn : opt.labelZh}
                    </option>
                  ))}
                </select>
              </div>
              {cur.code !== 'USDT' && cur.code !== 'USD' && (
                <div className="text-[11px] text-[var(--fg-3)] text-right">
                  {t('set_fx_live')}: 1 USDT ≈ {cur.symbol}
                  {cur.rate.toFixed(cur.code === 'JPY' ? 2 : 4)}
                  {cur.fx.updated_at ? ` · ${cur.fx.updated_at}` : ''}
                </div>
              )}
            </div>

            {/* Optional Modules & Privacy Default */}
            <div className="space-y-2.5 pt-3 border-t border-[var(--border)] text-[12.5px]">
              <label className="flex items-center justify-between gap-2 cursor-pointer">
                <span className="text-[var(--fg-2)]">{t('set_privacy_default')}</span>
                <input
                  type="checkbox"
                  data-testid="privacy-default-switch"
                  checked={Boolean(settings.privacy_mode)}
                  onChange={e => void onUpdateSettings({ privacy_mode: e.target.checked })}
                />
              </label>
              <label className="flex items-center justify-between gap-2 cursor-pointer">
                <span className="text-[var(--fg-2)]">{t('set_mod_copy')}</span>
                <input
                  type="checkbox"
                  checked={settings.modules.copy_trading}
                  onChange={e =>
                    void onUpdateSettings({ modules: { copy_trading: e.target.checked } })
                  }
                />
              </label>
              <label className="flex items-center justify-between gap-2 cursor-pointer">
                <span className="text-[var(--fg-2)]">{t('set_mod_stock')}</span>
                <input
                  type="checkbox"
                  checked={settings.modules.stocks}
                  onChange={e => void onUpdateSettings({ modules: { stocks: e.target.checked } })}
                />
              </label>
              <p className="m-0 text-[11px] text-[var(--fg-3)]">{t('set_mod_note')}</p>
              <label className="flex items-center justify-between gap-2 cursor-pointer">
                <span className="text-[var(--fg-2)]">{t('set_goal_title')}</span>
                <input
                  type="checkbox"
                  checked={settings.goal_enabled}
                  onChange={e =>
                    void onUpdateSettings({
                      goal_enabled: e.target.checked,
                      goal_target_usd: Number(goalInput) > 0 ? Number(goalInput) : 10000,
                    })
                  }
                />
              </label>
              {settings.goal_enabled && (
                <div className="otl-drawer-expand space-y-2 pt-1">
                  <input
                    type="text"
                    value={goalLabel}
                    onChange={e => setGoalLabel(e.target.value)}
                    placeholder={locale === 'en' ? 'Goal name (e.g. 2026 Target)' : '目标名称（如：阶段净值目标）'}
                    className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                  />
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      value={goalInput}
                      onChange={e => setGoalInput(e.target.value)}
                      placeholder={t('set_goal_target')}
                      className="h-8 flex-1 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                    />
                    <button
                      type="button"
                      onClick={async () => {
                        await onUpdateSettings({
                          goal_enabled: true,
                          goal_label: goalLabel.trim(),
                          goal_target_usd: Number(goalInput) > 0 ? Number(goalInput) : null,
                        })
                        setStatusMsg(t('set_save_ok'))
                        setTimeout(() => setStatusMsg(''), 2500)
                      }}
                      className="h-8 px-3 rounded-lg text-[12px] font-semibold cursor-pointer"
                      style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
                    >
                      {t('save_config')}
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* Demo Mode Toggle */}
            <div className="pt-3 border-t border-[var(--border)] flex items-center justify-between gap-3">
              <div>
                <div className="text-[12.5px] font-medium text-[var(--fg)]">{t('set_demo_title')}</div>
                <div className="text-[11.5px] text-[var(--fg-3)]">{t('set_demo_sub')}</div>
              </div>
              <input
                type="checkbox"
                data-testid="demo-mode-switch"
                checked={demoMode}
                onChange={e => void onToggleDemo(e.target.checked)}
              />
            </div>
          </div>

          {/* OpenAI-Compatible AI Configuration */}
          <div className="otl-card p-5 space-y-3">
            <div>
              <h3 className="m-0 text-[14px] font-semibold text-[var(--fg)]">{t('set_ai_title')}</h3>
              <p className="m-0 text-[11.5px] text-[var(--fg-3)] mt-0.5">{t('set_ai_sub')}</p>
            </div>
            <input
              type="text"
              value={aiUrl}
              onChange={e => setAiUrl(e.target.value)}
              placeholder="Base URL (e.g. https://api.openai.com/v1)"
              className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
            />
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <input
                type="password"
                value={aiKey}
                onChange={e => setAiKey(e.target.value)}
                placeholder={settings.ai_api_key_set ? 'API Key (configured)' : 'API Key'}
                className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
              />
              <button
                type="button"
                data-testid="fetch-ai-models-btn"
                disabled={fetchingModels || !aiUrl.trim()}
                onClick={async () => {
                  setFetchingModels(true)
                  setErrorMsg('')
                  try {
                    const res = await api.listAiModels(aiUrl.trim(), aiKey.trim())
                    setAiModels(res.models)
                    if (res.models.length > 0 && !aiModel.trim()) {
                      setAiModel(res.models[0])
                    }
                    setStatusMsg(
                      locale === 'en'
                        ? `Loaded ${res.count} available models`
                        : `已获取 ${res.count} 个可用模型`,
                    )
                    setTimeout(() => setStatusMsg(''), 3000)
                  } catch (err) {
                    setErrorMsg(err instanceof Error ? err.message : 'Failed to fetch models')
                  } finally {
                    setFetchingModels(false)
                  }
                }}
                className="inline-flex items-center justify-center gap-1.5 h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] disabled:opacity-50 cursor-pointer whitespace-nowrap"
              >
                <RefreshCw size={12} className={fetchingModels ? 'animate-spin' : ''} />
                <span>{locale === 'en' ? 'Fetch Models' : '获取模型列表'}</span>
              </button>
            </div>
            {aiModels.length > 0 ? (
              <div className="space-y-1.5">
                <select
                  aria-label="AI Model"
                  data-testid="ai-model-select"
                  value={aiModels.includes(aiModel) ? aiModel : '__custom__'}
                  onChange={e => {
                    if (e.target.value !== '__custom__') setAiModel(e.target.value)
                  }}
                  className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)] cursor-pointer"
                >
                  {aiModels.map(m => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                  {!aiModels.includes(aiModel) && (
                    <option value="__custom__">
                      {aiModel || (locale === 'en' ? 'Custom model…' : '自定义模型…')}
                    </option>
                  )}
                </select>
                <input
                  type="text"
                  value={aiModel}
                  onChange={e => setAiModel(e.target.value)}
                  placeholder="Model ID (e.g. gpt-4o-mini)"
                  className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                />
              </div>
            ) : (
              <input
                type="text"
                value={aiModel}
                onChange={e => setAiModel(e.target.value)}
                placeholder="Model (e.g. gpt-4o-mini / deepseek-chat)"
                className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
              />
            )}
            <div className="flex justify-end">
              <button
                type="button"
                onClick={async () => {
                  await onUpdateSettings({ ai_base_url: aiUrl, ai_model: aiModel, ai_api_key: aiKey })
                  setAiKey('')
                  setStatusMsg(t('set_save_ok'))
                  setTimeout(() => setStatusMsg(''), 2500)
                }}
                className="h-8 px-3 rounded-lg text-[12px] font-semibold cursor-pointer"
                style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
              >
                {t('save_config')}
              </button>
            </div>
          </div>

          {/* Multi-Channel Notifications (Webhook / Telegram / Bark) */}
          <div data-testid="notifications-card" className="otl-card p-5 space-y-3">
            <div>
              <h3 className="m-0 text-[14px] font-semibold text-[var(--fg)]">{t('set_notify_title')}</h3>
              <p className="m-0 text-[11.5px] text-[var(--fg-3)] mt-0.5">{t('set_notify_sub')}</p>
            </div>
            <input
              type="text"
              value={webhookUrl}
              onChange={e => setWebhookUrl(e.target.value)}
              placeholder="Webhook URL (https://...)"
              className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
            />
            <div className="grid grid-cols-2 gap-2">
              <input
                type="password"
                value={tgToken}
                onChange={e => setTgToken(e.target.value)}
                placeholder="Telegram Bot Token"
                className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
              />
              <input
                type="text"
                value={tgChat}
                onChange={e => setTgChat(e.target.value)}
                placeholder="Telegram Chat ID"
                className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
              />
            </div>
            <input
              type="password"
              value={barkKey}
              onChange={e => setBarkKey(e.target.value)}
              placeholder="Bark Push Key"
              className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
            />
            <div className="flex justify-end">
              <button
                type="button"
                onClick={async () => {
                  await onUpdateSettings({
                    notifications: {
                      webhook_url: webhookUrl,
                      telegram_bot_token: tgToken,
                      telegram_chat_id: tgChat,
                      bark_push_key: barkKey,
                    },
                  })
                  setStatusMsg(t('set_save_ok'))
                  setTimeout(() => setStatusMsg(''), 2500)
                }}
                className="h-8 px-3 rounded-lg text-[12px] font-semibold cursor-pointer"
                style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
              >
                {t('save_config')}
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
