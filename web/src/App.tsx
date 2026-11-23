import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { TradeDrilldownDrawer } from './components/TradeDrilldownDrawer'
import { I18nProvider } from './i18n'
import { api } from './lib/api'
import { CurrencyProvider, DEFAULT_FX } from './lib/currency'
import type { CurrencyCode, DashboardData, ExchangeAdapterInfo, FxRatesPayload, NavPage, UserSettings } from './lib/types'
import { AnalyticsPage } from './pages/AnalyticsPage'
import { ExtensionsPage } from './pages/ExtensionsPage'
import { FeesAiPage } from './pages/FeesAiPage'
import { OverviewPage } from './pages/OverviewPage'
import { SettingsPage } from './pages/SettingsPage'
import { TradesPage } from './pages/TradesPage'

const DEFAULT_SETTINGS: UserSettings = {
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
    webhook_url: '',
    telegram_bot_token: '',
    telegram_chat_id: '',
    bark_server_url: 'https://api.day.app',
    bark_push_key: '',
  },
}

export default function App() {
  const [page, setPage] = useState<NavPage>('overview')
  const [settings, setSettings] = useState<UserSettings>(DEFAULT_SETTINGS)
  const [privacyMode, setPrivacyMode] = useState(false)
  const initializedPrivacyRef = useRef(false)
  const [rangeKey, setRangeKey] = useState('all')
  const [venueFilter, setVenueFilter] = useState('ALL')
  const [symbolFilter, setSymbolFilter] = useState('ALL')
  const [data, setData] = useState<DashboardData | null>(null)
  const [adapters, setAdapters] = useState<ExchangeAdapterInfo[]>([])
  const [fx, setFx] = useState<FxRatesPayload>(DEFAULT_FX)
  const [manualSyncing, setManualSyncing] = useState(false)
  const [generatingAi, setGeneratingAi] = useState(false)
  const [error, setError] = useState('')
  const [drilldown, setDrilldown] = useState<{ venueId: string; venueName?: string; symbol: string } | null>(null)

  useLayoutEffect(() => {
    document.documentElement.lang = settings.locale
    document.documentElement.dataset.theme = settings.theme
    document.documentElement.dataset.polarity = settings.polarity
    document.documentElement.dataset.privacy = privacyMode ? 'on' : 'off'
    document.title = settings.locale === 'en' ? 'OpenTradeLedger' : '交易账本 · OpenTradeLedger'
  }, [settings.theme, settings.polarity, settings.locale, privacyMode])

  const loadAll = useCallback(async () => {
    try {
      const [dash, pref, ex] = await Promise.all([
        api.getDashboard(rangeKey, page === 'trades' ? venueFilter : 'ALL', page === 'trades' ? symbolFilter : 'ALL'),
        api.getSettings(),
        api.listExchanges(),
      ])
      setData(dash)
      setSettings(pref)
      if (!initializedPrivacyRef.current) {
        initializedPrivacyRef.current = true
        setPrivacyMode(Boolean(pref.privacy_mode))
      }
      setAdapters(ex.adapters)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    }
  }, [rangeKey, venueFilter, symbolFilter, page])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  useEffect(() => {
    api
      .getFxRates()
      .then(res => setFx(res))
      .catch(() => {})
  }, [])

  const handleUpdateSettings = async (patch: Record<string, unknown>) => {
    const next = await api.updateSettings(patch)
    setSettings(next)
    if (typeof patch.privacy_mode === 'boolean') setPrivacyMode(patch.privacy_mode)
    const dash = await api.getDashboard(rangeKey, page === 'trades' ? venueFilter : 'ALL', page === 'trades' ? symbolFilter : 'ALL')
    setData(dash)
  }

  const handleSync = async () => {
    setManualSyncing(true)
    try {
      await api.triggerSync()
      await loadAll()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sync failed')
    } finally {
      setManualSyncing(false)
    }
  }

  const handleSaveJournal = async (tradeId: string, tags: string[], note: string) => {
    await api.saveJournal(tradeId, tags, note)
    await loadAll()
  }

  const handleGenerateAi = async () => {
    setGeneratingAi(true)
    try {
      await api.generateAiReview(rangeKey)
      await loadAll()
    } finally {
      setGeneratingAi(false)
    }
  }

  const handleInspectSymbol = (venueId: string, symbol: string, venueName?: string) => {
    setDrilldown({ venueId: venueId.toLowerCase(), venueName, symbol })
  }

  const handleOpenInTrades = (venueId: string, symbol: string) => {
    setDrilldown(null)
    setVenueFilter(venueId.toLowerCase())
    setSymbolFilter(symbol)
    setPage('trades')
    if (typeof window !== 'undefined' && window.scrollY) window.scrollTo(0, 0)
  }

  const showExtensions = settings.modules.copy_trading || settings.modules.stocks
  const activeCurrency: CurrencyCode = settings.display_currency || 'USDT'

  return (
    <I18nProvider locale={settings.locale}>
      <CurrencyProvider currency={activeCurrency} fx={fx}>
        <div
          data-testid="otl-root"
          className="min-h-screen flex flex-col md:flex-row bg-[var(--bg)] text-[var(--fg)]"
        >
          <Sidebar
            page={page}
            onNavigate={next => {
              setPage(next)
              if (typeof window !== 'undefined' && window.scrollY) window.scrollTo(0, 0)
            }}
            showExtensions={showExtensions}
            lastSyncMs={data?.last_sync_ms ?? 0}
            manualSyncing={manualSyncing}
            onSync={() => void handleSync()}
          />

          <div className="flex-1 flex flex-col min-w-0">
            <TopBar
              page={page}
              demoMode={Boolean(data?.demo_mode)}
              privacyMode={privacyMode}
              onTogglePrivacy={() => setPrivacyMode(v => !v)}
              locale={settings.locale}
              onToggleLocale={() =>
                void handleUpdateSettings({ locale: settings.locale === 'zh-CN' ? 'en' : 'zh-CN' })
              }
              currency={activeCurrency}
              onChangeCurrency={next => void handleUpdateSettings({ display_currency: next })}
              rangeKey={rangeKey}
              onChangeRange={setRangeKey}
            />

          <main className="flex-1 px-6 py-5 max-w-[1440px] w-full mx-auto">
            {error && (
              <div
                className="mb-4 px-4 py-2.5 rounded-lg text-[12.5px]"
                style={{ background: 'var(--neg-soft)', border: '1px solid var(--neg-bd)', color: 'var(--neg)' }}
              >
                {error}
              </div>
            )}

            {!data ? (
              <div className="py-20 text-center text-[13px] text-[var(--fg-3)]">Loading…</div>
            ) : (
              <div key={page} className="otl-page-enter">
                {page === 'overview' && <OverviewPage data={data} onNavigate={setPage} />}
                {page === 'trades' && (
                  <TradesPage
                    data={data}
                    venueFilter={venueFilter}
                    onVenueChange={setVenueFilter}
                    symbolFilter={symbolFilter}
                    onSymbolChange={setSymbolFilter}
                    onSaveJournal={handleSaveJournal}
                  />
                )}
                {page === 'analytics' && (
                  <AnalyticsPage
                    data={data}
                    rangeKey={rangeKey}
                    onRangeChange={setRangeKey}
                    onInspectSymbol={handleInspectSymbol}
                    settings={settings}
                    onUpdateSettings={handleUpdateSettings}
                  />
                )}
                {page === 'fees' && (
                  <FeesAiPage data={data} generating={generatingAi} onGenerate={handleGenerateAi} />
                )}
                {page === 'extensions' && showExtensions && (
                  <ExtensionsPage
                    data={data}
                    enableCopy={settings.modules.copy_trading}
                    enableStocks={settings.modules.stocks}
                    onInspectSymbol={handleInspectSymbol}
                  />
                )}
                {page === 'settings' && (
                  <SettingsPage
                    settings={settings}
                    adapters={adapters}
                    demoMode={Boolean(data.demo_mode)}
                    onUpdateSettings={handleUpdateSettings}
                    onUpsertExchange={async body => {
                      await api.upsertExchange(body)
                      const syncRes = await api.triggerSync(body.venue_id).catch(err => ({
                        ok: false,
                        venues: {
                          [body.venue_id]: {
                            ok: false,
                            error: err instanceof Error ? err.message : 'Sync failed',
                          },
                        },
                      }))
                      await loadAll()
                      const venueRes = syncRes.venues?.[body.venue_id]
                      if (venueRes && !venueRes.ok) {
                        throw new Error(venueRes.error || 'Exchange sync failed')
                      }
                    }}
                    onDeleteExchange={async vid => {
                      await api.deleteExchange(vid)
                      await loadAll()
                    }}
                    onTestExchange={async vid => {
                      await api.testExchange(vid)
                    }}
                    onToggleDemo={async enabled => {
                      await api.setDemoMode(enabled)
                      await loadAll()
                    }}
                  />
                )}
              </div>
            )}
          </main>
        </div>
      </div>
      {drilldown && (
        <TradeDrilldownDrawer
          venueId={drilldown.venueId}
          venueName={drilldown.venueName}
          symbol={drilldown.symbol}
          rangeKey={rangeKey}
          onClose={() => setDrilldown(null)}
          onOpenInTrades={handleOpenInTrades}
        />
      )}
      </CurrencyProvider>
    </I18nProvider>
  )
}
