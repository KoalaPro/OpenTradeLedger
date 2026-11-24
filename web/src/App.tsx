import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { AuthGate } from './components/AuthGate'
import { Sidebar } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { TradeDrilldownDrawer } from './components/TradeDrilldownDrawer'
import { I18nProvider } from './i18n'
import { api, ApiError } from './lib/api'
import { CurrencyProvider, DEFAULT_FX } from './lib/currency'
import { attachVelvetRippleListener } from './lib/velvetRipple'
import type {
  AuthStatusPayload,
  CurrencyCode,
  DashboardData,
  ExchangeAdapterInfo,
  FxRatesPayload,
  NavPage,
  UserSettings,
  VersionCheckPayload,
} from './lib/types'
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
  update_check_enabled: true,
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
  const [exchangeErrorCount, setExchangeErrorCount] = useState(0)
  const [fx, setFx] = useState<FxRatesPayload>(DEFAULT_FX)
  const [versionStatus, setVersionStatus] = useState<VersionCheckPayload | null>(null)
  const [manualSyncing, setManualSyncing] = useState(false)
  const [generatingAi, setGeneratingAi] = useState(false)
  const [error, setError] = useState('')
  const [drilldown, setDrilldown] = useState<{ venueId: string; venueName?: string; symbol: string } | null>(null)
  const [drilldownOpen, setDrilldownOpen] = useState(false)

  const [authStatus, setAuthStatus] = useState<AuthStatusPayload>({ authenticated: true, require_setup: false })
  const [authChecked, setAuthChecked] = useState(false)

  useLayoutEffect(() => {
    document.documentElement.lang = settings.locale
    document.documentElement.dataset.theme = settings.theme
    document.documentElement.dataset.polarity = settings.polarity
    document.documentElement.dataset.privacy = privacyMode ? 'on' : 'off'
    document.title = settings.locale === 'en' ? 'OpenTradeLedger' : '交易账本 · OpenTradeLedger'
  }, [settings.theme, settings.polarity, settings.locale, privacyMode])

  const isAuthLocked = Boolean(authStatus.require_setup || authStatus.authenticated === false)

  useEffect(() => {
    return attachVelvetRippleListener()
  }, [])

  useEffect(() => {
    api
      .authStatus()
      .then(st => {
        setAuthStatus(st)
        setAuthChecked(true)
      })
      .catch(() => {
        setAuthChecked(true)
      })
  }, [])

  const applyExchangeList = (ex: { adapters: ExchangeAdapterInfo[]; error_count?: number }) => {
    setAdapters(ex.adapters)
    const computedErr =
      typeof ex.error_count === 'number'
        ? ex.error_count
        : ex.adapters.filter(a => a.configured && a.credential?.enabled !== false && a.health?.status === 'error')
            .length
    setExchangeErrorCount(computedErr)
  }

  const handlePossibleAuthError = useCallback((err: unknown): boolean => {
    if (err instanceof ApiError && err.status === 401 && err.authRequired) {
      setAuthStatus(prev => ({
        ...prev,
        authenticated: false,
        require_setup: err.requireSetup,
      }))
      return true
    }
    return false
  }, [])

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
      applyExchangeList(ex)
      setError('')
    } catch (e) {
      if (handlePossibleAuthError(e)) return
      setError(e instanceof Error ? e.message : 'Failed to load data')
    }
  }, [rangeKey, venueFilter, symbolFilter, page, handlePossibleAuthError])

  useEffect(() => {
    if (!authChecked || isAuthLocked) return
    void loadAll()
  }, [authChecked, isAuthLocked, loadAll])

  useEffect(() => {
    if (!authChecked || isAuthLocked) return
    api
      .getFxRates()
      .then(res => setFx(res))
      .catch(() => {})
    api
      .getVersionCheck(false)
      .then(res => setVersionStatus(res))
      .catch(() => {})
  }, [authChecked, isAuthLocked])

  const handleCheckVersion = useCallback(
    async (force = true): Promise<VersionCheckPayload | null> => {
      try {
        const res = await api.getVersionCheck(force)
        setVersionStatus(res)
        return res
      } catch (e) {
        handlePossibleAuthError(e)
        return null
      }
    },
    [handlePossibleAuthError],
  )

  const handleUpdateSettings = async (patch: Record<string, unknown>) => {
    try {
      const next = await api.updateSettings(patch)
      setSettings(next)
      if (typeof patch.privacy_mode === 'boolean') setPrivacyMode(patch.privacy_mode)
      const dash = await api.getDashboard(
        rangeKey,
        page === 'trades' ? venueFilter : 'ALL',
        page === 'trades' ? symbolFilter : 'ALL',
      )
      setData(dash)
    } catch (e) {
      if (handlePossibleAuthError(e)) return
      setError(e instanceof Error ? e.message : 'Failed to update settings')
    }
  }

  const handleSync = async () => {
    setManualSyncing(true)
    setError('')
    try {
      const syncRes = await api.triggerSync()
      await loadAll()
      const failedEntries = Object.entries(syncRes.venues || {}).filter(([, v]) => !v.ok)
      if (failedEntries.length > 0) {
        const isZh = settings.locale === 'zh-CN'
        const details = failedEntries
          .map(([vid, v]) => `${vid.toUpperCase()}: ${(isZh ? v.error_zh : v.error) || v.error || 'Error'}`)
          .join(' ； ')
        setError(
          isZh
            ? `部分交易所同步异常 — ${details}`
            : `Exchange sync errors — ${details}`,
        )
      }
    } catch (e) {
      if (handlePossibleAuthError(e)) return
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
    setError('')
    try {
      await api.generateAiReview(rangeKey)
      await loadAll()
    } catch (e) {
      if (handlePossibleAuthError(e)) return
      setError(e instanceof Error ? e.message : 'AI review generation failed')
    } finally {
      setGeneratingAi(false)
    }
  }

  const handleLockLedger = async () => {
    try {
      const st = await api.authLogout()
      setAuthStatus({ ...st, authenticated: false })
    } catch {
      setAuthStatus(prev => ({ ...prev, authenticated: false }))
    }
  }

  const handleInspectSymbol = (venueId: string, symbol: string, venueName?: string) => {
    setDrilldown({ venueId: venueId.toLowerCase(), venueName, symbol })
    setDrilldownOpen(true)
  }

  const handleOpenInTrades = (venueId: string, symbol: string) => {
    setDrilldownOpen(false)
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
        {authChecked && (
          <AuthGate
            auth={authStatus}
            locale={settings.locale}
            onToggleLocale={() =>
              setSettings(prev => ({ ...prev, locale: prev.locale === 'zh-CN' ? 'en' : 'zh-CN' }))
            }
            onUnlocked={nextAuth => {
              setAuthStatus(nextAuth)
            }}
          />
        )}

        {!isAuthLocked && (
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
              authEnabled={Boolean(authStatus.enabled)}
              onLock={() => void handleLockLedger()}
              versionStatus={versionStatus}
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
                exchangeErrorCount={exchangeErrorCount}
                onInspectErrors={() => setPage('settings')}
              />

              <main className="flex-1 px-6 py-5 max-w-[1440px] w-full mx-auto">
                {error && (
                  <div
                    data-testid="global-error-banner"
                    className="otl-status-err mb-4 px-4 py-2.5 rounded-lg text-[12.5px] font-medium"
                  >
                    {error}
                  </div>
                )}

                {!data ? (
                  <div className="py-20 text-center text-[13px] text-[var(--fg-3)]">Loading…</div>
                ) : (
                  <div key={page} className="otl-page-enter">
                    {page === 'overview' && (
                      <OverviewPage
                        data={data}
                        onNavigate={setPage}
                        onInspectSymbol={handleInspectSymbol}
                      />
                    )}
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
                        authStatus={authStatus}
                        onAuthChanged={setAuthStatus}
                        onLockNow={handleLockLedger}
                        versionStatus={versionStatus}
                        onCheckVersion={handleCheckVersion}
                        onUpdateSettings={async patch => {
                          await handleUpdateSettings(patch)
                          if (typeof patch.update_check_enabled === 'boolean') {
                            void handleCheckVersion(false)
                          }
                        }}
                        onUpsertExchange={async body => {
                          await api.upsertExchange(body)
                          let verifyErr: Error | null = null
                          try {
                            await api.testExchange(body.venue_id)
                            const syncRes = await api.triggerSync(body.venue_id)
                            const venueRes = syncRes.venues?.[body.venue_id]
                            if (venueRes && !venueRes.ok) {
                              const isZh = settings.locale === 'zh-CN'
                              verifyErr = new Error(
                                (isZh ? venueRes.error_zh : venueRes.error) ||
                                  venueRes.error ||
                                  'Exchange sync failed',
                              )
                            }
                          } catch (err) {
                            verifyErr = err instanceof Error ? err : new Error('Connection verification failed')
                          } finally {
                            await loadAll()
                          }
                          if (verifyErr) {
                            throw verifyErr
                          }
                        }}
                        onDeleteExchange={async vid => {
                          await api.deleteExchange(vid)
                          await loadAll()
                        }}
                        onTestExchange={async vid => {
                          try {
                            await api.testExchange(vid)
                          } finally {
                            const ex = await api.listExchanges()
                            applyExchangeList(ex)
                          }
                        }}
                        onToggleDemo={async enabled => {
                          await api.setDemoMode(enabled)
                          const st = await api.authStatus().catch(() => authStatus)
                          setAuthStatus(st)
                          if (!st.require_setup && st.authenticated !== false) {
                            await loadAll()
                          }
                        }}
                      />
                    )}
                  </div>
                )}
              </main>
            </div>
          </div>
        )}

        {drilldown && !isAuthLocked && (
          <TradeDrilldownDrawer
            venueId={drilldown.venueId}
            venueName={drilldown.venueName}
            symbol={drilldown.symbol}
            rangeKey={rangeKey}
            open={drilldownOpen}
            onClose={() => setDrilldownOpen(false)}
            onOpenInTrades={handleOpenInTrades}
          />
        )}
      </CurrencyProvider>
    </I18nProvider>
  )
}
