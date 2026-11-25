import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { AnimatedCollapse } from './components/AnimatedCollapse'
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
  ExtensionSubTab,
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

const VALID_PAGES: NavPage[] = ['overview', 'trades', 'analytics', 'fees', 'extensions', 'settings']

function parseHashRoute(hash: string): { page: NavPage; extTab?: ExtensionSubTab } {
  const cleaned = hash.replace(/^#\/?/, '').trim()
  if (!cleaned) return { page: 'overview' }
  const [seg0, seg1] = cleaned.split('/')
  const matched = VALID_PAGES.find(p => p === seg0)
  if (!matched) return { page: 'overview' }
  if (matched === 'extensions' && (seg1 === 'watcher' || seg1 === 'copy' || seg1 === 'stock')) {
    return { page: 'extensions', extTab: seg1 }
  }
  return { page: matched }
}

function buildHashRoute(page: NavPage, extTab?: ExtensionSubTab): string {
  if (page === 'extensions' && extTab) {
    return `#/extensions/${extTab}`
  }
  return `#/${page}`
}

export default function App() {
  const initialRoute = parseHashRoute(typeof window !== 'undefined' ? window.location.hash : '')
  const [page, setPage] = useState<NavPage>(initialRoute.page)
  const [extTab, setExtTab] = useState<ExtensionSubTab | undefined>(initialRoute.extTab)
  const [settings, setSettings] = useState<UserSettings>(DEFAULT_SETTINGS)
  const [privacyMode, setPrivacyMode] = useState(false)
  const initializedPrivacyRef = useRef(false)
  const bootstrappedRef = useRef(false)
  const [rangeKey, setRangeKey] = useState('all')
  const [venueFilter, setVenueFilter] = useState('ALL')
  const [symbolFilter, setSymbolFilter] = useState('ALL')
  const [data, setData] = useState<DashboardData | null>(null)
  const [adapters, setAdapters] = useState<ExchangeAdapterInfo[]>([])
  const [exchangeErrorCount, setExchangeErrorCount] = useState(0)
  const [fx, setFx] = useState<FxRatesPayload>(DEFAULT_FX)
  const [versionStatus, setVersionStatus] = useState<VersionCheckPayload | null>(null)
  const [manualSyncing, setManualSyncing] = useState(false)
  const [lastSyncHadError, setLastSyncHadError] = useState(false)
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

  useEffect(() => {
    const onHashChange = () => {
      const parsed = parseHashRoute(window.location.hash)
      setPage(parsed.page)
      if (parsed.extTab) setExtTab(parsed.extTab)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const navigateTo = useCallback(
    (nextPage: NavPage, nextExtTab?: ExtensionSubTab) => {
      setPage(nextPage)
      if (nextPage === 'extensions' && nextExtTab) {
        setExtTab(nextExtTab)
      }
      if (typeof window !== 'undefined') {
        const targetHash = buildHashRoute(nextPage, nextPage === 'extensions' ? nextExtTab ?? extTab : undefined)
        if (window.location.hash !== targetHash) {
          window.location.hash = targetHash
        }
        if (window.scrollY) window.scrollTo(0, 0)
      }
    },
    [extTab],
  )

  const effVenue = page === 'trades' ? venueFilter : 'ALL'
  const effSymbol = page === 'trades' ? symbolFilter : 'ALL'

  const loadAll = useCallback(async () => {
    try {
      const [dash, pref, ex] = await Promise.all([
        api.getDashboard(rangeKey, effVenue, effSymbol),
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
  }, [rangeKey, effVenue, effSymbol, handlePossibleAuthError])

  const loadDashboardOnly = useCallback(async () => {
    try {
      const dash = await api.getDashboard(rangeKey, effVenue, effSymbol)
      setData(dash)
      setError('')
    } catch (e) {
      if (handlePossibleAuthError(e)) return
      setError(e instanceof Error ? e.message : 'Failed to load data')
    }
  }, [rangeKey, effVenue, effSymbol, handlePossibleAuthError])

  useEffect(() => {
    if (!authChecked || isAuthLocked) {
      bootstrappedRef.current = false
      return
    }
    if (!bootstrappedRef.current) {
      bootstrappedRef.current = true
      void loadAll()
      return
    }
    void loadDashboardOnly()
  }, [authChecked, isAuthLocked, rangeKey, effVenue, effSymbol, loadAll, loadDashboardOnly])

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
        setLastSyncHadError(true)
        const isZh = settings.locale === 'zh-CN'
        const details = failedEntries
          .map(([vid, v]) => `${vid.toUpperCase()}: ${(isZh ? v.error_zh : v.error) || v.error || 'Error'}`)
          .join(' ； ')
        setError(
          isZh
            ? `部分交易所同步异常 — ${details}`
            : `Exchange sync errors — ${details}`,
        )
      } else {
        setLastSyncHadError(false)
      }
    } catch (e) {
      setLastSyncHadError(true)
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

  const handleGenerateAi = async (mode: 'auto' | 'ai' | 'local_rules' = 'auto') => {
    setGeneratingAi(true)
    setError('')
    try {
      await api.generateAiReview(rangeKey, mode)
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
    navigateTo('trades')
  }

  const showExtensions =
    settings.modules.copy_watcher !== false || settings.modules.copy_trading || settings.modules.stocks
  const effectivePage: NavPage = page === 'extensions' && !showExtensions ? 'overview' : page

  useEffect(() => {
    if (page === 'extensions' && !showExtensions) {
      setPage('overview')
      if (typeof window !== 'undefined' && window.location.hash.startsWith('#/extensions')) {
        window.location.hash = '#/overview'
      }
    }
  }, [page, showExtensions])

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
            className="min-h-screen flex flex-col md:flex-row bg-[var(--bg)] text-[var(--fg)] transition-colors duration-200"
          >
            <Sidebar
              page={page}
              onNavigate={next => {
                if (next === 'trades') {
                  setVenueFilter('ALL')
                  setSymbolFilter('ALL')
                }
                navigateTo(next)
                if (next === 'settings' && versionStatus?.update_available) {
                  window.setTimeout(() => {
                    const el = document.querySelector('[data-testid="version-update-card"]')
                    if (el && typeof el.scrollIntoView === 'function') {
                      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
                    }
                  }, 120)
                }
              }}
              showExtensions={showExtensions}
              lastSyncMs={data?.last_sync_ms ?? 0}
              manualSyncing={manualSyncing}
              syncFailed={lastSyncHadError}
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
                onInspectErrors={() => navigateTo('settings')}
              />

              <main className="flex-1 px-6 py-5 max-w-[1440px] w-full mx-auto">
                <AnimatedCollapse open={Boolean(error)} innerClassName="pb-4">
                  <div
                    data-testid="global-error-banner"
                    className="otl-status-err px-4 py-2.5 rounded-lg text-[12.5px] font-medium"
                  >
                    {error}
                  </div>
                </AnimatedCollapse>

                {!data ? (
                  <div className="py-20 text-center text-[13px] text-[var(--fg-3)]">Loading…</div>
                ) : (
                  <div key={effectivePage} className="otl-page-enter">
                    {effectivePage === 'overview' && (
                      <OverviewPage
                        data={data}
                        enableStockTab={Boolean(settings.modules.stocks)}
                        onNavigate={navigateTo}
                        onInspectSymbol={handleInspectSymbol}
                      />
                    )}
                    {effectivePage === 'trades' && (
                      <TradesPage
                        data={data}
                        venueFilter={venueFilter}
                        onVenueChange={setVenueFilter}
                        symbolFilter={symbolFilter}
                        onSymbolChange={setSymbolFilter}
                        onSaveJournal={handleSaveJournal}
                      />
                    )}
                    {effectivePage === 'analytics' && (
                      <AnalyticsPage
                        data={data}
                        rangeKey={rangeKey}
                        onRangeChange={setRangeKey}
                        onInspectSymbol={handleInspectSymbol}
                        settings={settings}
                        onUpdateSettings={handleUpdateSettings}
                      />
                    )}
                    {effectivePage === 'fees' && (
                      <FeesAiPage
                        data={data}
                        settings={settings}
                        generating={generatingAi}
                        onGenerate={handleGenerateAi}
                        onGoConfigureAi={() => navigateTo('settings')}
                      />
                    )}
                    {effectivePage === 'extensions' && showExtensions && (
                      <ExtensionsPage
                        data={data}
                        enableWatcher={settings.modules.copy_watcher !== false}
                        enableCopy={settings.modules.copy_trading}
                        enableStocks={settings.modules.stocks}
                        requestedTab={extTab}
                        onTabChange={nextTab => {
                          setExtTab(nextTab)
                          if (typeof window !== 'undefined') {
                            const targetHash = buildHashRoute('extensions', nextTab)
                            if (window.location.hash !== targetHash) {
                              window.location.hash = targetHash
                            }
                          }
                        }}
                        onInspectSymbol={handleInspectSymbol}
                        onGoSettings={() => {
                          navigateTo('settings')
                          window.setTimeout(() => {
                            const el = document.querySelector('[data-testid="notifications-card"]')
                            if (el && typeof el.scrollIntoView === 'function') {
                              el.scrollIntoView({ behavior: 'smooth', block: 'center' })
                            }
                          }, 120)
                        }}
                      />
                    )}
                    {effectivePage === 'settings' && (
                      <SettingsPage
                        settings={settings}
                        adapters={adapters}
                        demoMode={Boolean(
                          (data as DashboardData & { explicit_demo_mode?: boolean }).explicit_demo_mode ??
                            data.demo_mode,
                        )}
                        previewFallback={Boolean(
                          data.demo_mode &&
                            (data as DashboardData & { explicit_demo_mode?: boolean }).explicit_demo_mode === false,
                        )}
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
                        onUpsertExchange={async (body, onStep) => {
                          onStep?.('verifying')
                          await api.verifyExchange(body)
                          onStep?.('syncing')
                          try {
                            const syncRes = await api.triggerSync(body.venue_id, body)
                            const venueRes = syncRes.venues?.[body.venue_id]
                            if (venueRes && !venueRes.ok) {
                              const isZh = settings.locale === 'zh-CN'
                              throw new Error(
                                (isZh ? venueRes.error_zh : venueRes.error) ||
                                  venueRes.error ||
                                  'Exchange sync failed',
                              )
                            }
                            if (syncRes.committed === undefined) {
                              await api.upsertExchange(body)
                            }
                          } finally {
                            await loadAll()
                          }
                          onStep?.('done')
                        }}
                        onDeleteExchange={async (vid, keepData = false) => {
                          await api.deleteExchange(vid, keepData)
                          await loadAll()
                        }}
                        onTestExchange={async (vid, isRetry = false) => {
                          try {
                            await api.testExchange(vid)
                            if (isRetry) {
                              const syncRes = await api.triggerSync(vid)
                              const venueRes = syncRes.venues?.[vid]
                              if (venueRes && !venueRes.ok) {
                                const isZh = settings.locale === 'zh-CN'
                                throw new Error(
                                  (isZh ? venueRes.error_zh : venueRes.error) ||
                                    venueRes.error ||
                                    'Exchange sync failed',
                                )
                              }
                            }
                          } finally {
                            await loadAll()
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
