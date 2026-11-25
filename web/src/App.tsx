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
    bark_server_url: 'https://api.day.app',
    bark_push_key: '',
  },
}

const VALID_PAGES: NavPage[] = ['overview', 'trades', 'analytics', 'fees', 'extensions', 'settings']

function enabledExtensionTabs(modules: UserSettings['modules']): ExtensionSubTab[] {
  const tabs: ExtensionSubTab[] = []
  if (modules.copy_watcher !== false) tabs.push('watcher')
  if (modules.copy_trading) tabs.push('copy')
  if (modules.stocks) tabs.push('stock')
  return tabs
}

function parseHashRoute(
  hash: string,
  enabledTabs: ExtensionSubTab[],
): { page: NavPage; extTab?: ExtensionSubTab; redirectToOverview?: boolean } {
  const cleaned = hash.replace(/^#\/?/, '').trim()
  if (!cleaned) return { page: 'overview' }
  const [seg0, ...rest] = cleaned.replace(/\/+$/, '').split('/')
  const matched = VALID_PAGES.find(p => p === seg0)
  if (!matched) return { page: 'overview' }
  if (matched === 'extensions') {
    const requestedTab = rest.join('/')
    const extTab = requestedTab ? enabledTabs.find(tab => tab === requestedTab) : enabledTabs[0]
    return extTab ? { page: matched, extTab } : { page: 'overview', redirectToOverview: true }
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
  const [hash, setHash] = useState(() => typeof window !== 'undefined' ? window.location.hash : '')
  const [settings, setSettings] = useState<UserSettings>(() => {
    try {
      const savedLocale = window.localStorage.getItem('otl.locale')
      const savedTheme = window.localStorage.getItem('otl.theme')
      const savedPolarity = window.localStorage.getItem('otl.polarity')
      return {
        ...DEFAULT_SETTINGS,
        ...(savedLocale === 'en' || savedLocale === 'zh-CN' ? { locale: savedLocale } : {}),
        ...(savedTheme === 'obsidian' || savedTheme === 'ocean' || savedTheme === 'light'
          ? { theme: savedTheme }
          : {}),
        ...(savedPolarity === 'cn' || savedPolarity === 'global' ? { polarity: savedPolarity } : {}),
      }
    } catch { /* Device storage can be unavailable in private browsing. */ }
    return DEFAULT_SETTINGS
  })
  const enabledTabs = enabledExtensionTabs(settings.modules)
  const { page, extTab, redirectToOverview } = parseHashRoute(hash, enabledTabs)
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
  const [dashboardError, setDashboardError] = useState('')
  const [drilldown, setDrilldown] = useState<{ venueId: string; venueName?: string; symbol: string; rangeKey?: string } | null>(null)
  const [drilldownOpen, setDrilldownOpen] = useState(false)

  const [authStatus, setAuthStatus] = useState<AuthStatusPayload>({ authenticated: true, require_setup: false })
  const [authChecked, setAuthChecked] = useState(false)
  const [stageRevealSeq, setStageRevealSeq] = useState(0)
  const bumpStageReveal = useCallback(() => setStageRevealSeq(s => s + 1), [])

  useLayoutEffect(() => {
    document.documentElement.lang = settings.locale
    document.documentElement.dataset.theme = settings.theme
    document.documentElement.dataset.polarity = settings.polarity
    document.documentElement.dataset.privacy = privacyMode ? 'on' : 'off'
    document.title = settings.locale === 'en' ? 'OpenTradeLedger' : '交易账本 · OpenTradeLedger'
  }, [settings.theme, settings.polarity, settings.locale, privacyMode])

  useEffect(() => {
    try {
      window.localStorage.setItem('otl.locale', settings.locale)
      window.localStorage.setItem('otl.theme', settings.theme)
      window.localStorage.setItem('otl.polarity', settings.polarity)
    } catch { /* Preferences remain available for the current page. */ }
  }, [settings.locale, settings.theme, settings.polarity])

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
    const onHashChange = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const navigateTo = useCallback(
    (nextPage: NavPage, nextExtTab?: ExtensionSubTab) => {
      const targetHash = buildHashRoute(nextPage, nextExtTab)
      setHash(targetHash)
      if (typeof window !== 'undefined') {
        if (window.location.hash !== targetHash) {
          window.location.hash = targetHash
        }
        if (window.scrollY) window.scrollTo(0, 0)
      }
    },
    [],
  )

  const effVenue = page === 'trades' ? venueFilter : 'ALL'
  const effSymbol = page === 'trades' ? symbolFilter : 'ALL'

  const currentQuery = useRef({ rangeKey, effVenue, effSymbol })
  const dashboardRequest = useRef(0)
  const ledgerEpoch = useRef(0)
  const switchingLedger = useRef(false)

  useLayoutEffect(() => {
    currentQuery.current = { rangeKey, effVenue, effSymbol }
    dashboardRequest.current++
  }, [rangeKey, effVenue, effSymbol])

  const invalidateDashboard = useCallback(() => {
    dashboardRequest.current++
    ledgerEpoch.current++
    setDashboardError('')
    setData(null)
    setDrilldown(null)
    setDrilldownOpen(false)
    setAdapters([])
    setExchangeErrorCount(0)
  }, [])

  useLayoutEffect(() => {
    if (isAuthLocked) invalidateDashboard()
  }, [isAuthLocked, invalidateDashboard])

  const loadDashboardOnly = useCallback(async () => {
    if (switchingLedger.current) return
    const id = ++dashboardRequest.current
    const query = currentQuery.current
    try {
      const dash = await api.getDashboard(query.rangeKey, query.effVenue, query.effSymbol)
      if (id !== dashboardRequest.current) return
      setData(dash)
      setDashboardError('')
    } catch (e) {
      if (id !== dashboardRequest.current || handlePossibleAuthError(e)) return
      setDashboardError(e instanceof Error ? e.message : 'Failed to load data')
    }
  }, [handlePossibleAuthError])

  const loadAll = useCallback(async () => {
    if (switchingLedger.current) return
    const epoch = ledgerEpoch.current
    try {
      const [, pref, ex] = await Promise.all([loadDashboardOnly(), api.getSettings(), api.listExchanges()])
      if (epoch !== ledgerEpoch.current) return
      setSettings(pref)
      if (!initializedPrivacyRef.current) {
        initializedPrivacyRef.current = true
        setPrivacyMode(Boolean(pref.privacy_mode))
      }
      applyExchangeList(ex)
    } catch (e) {
      if (epoch !== ledgerEpoch.current || handlePossibleAuthError(e)) return
      setError(e instanceof Error ? e.message : 'Failed to load settings')
    }
  }, [loadDashboardOnly, handlePossibleAuthError])

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

  const handleUpdateSettings = async (patch: Record<string, unknown>, showPageError = true) => {
    setError('')
    try {
      const next = await api.updateSettings(patch)
      setSettings(next)
      if (typeof patch.privacy_mode === 'boolean') setPrivacyMode(patch.privacy_mode)
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to update settings'
      if (!handlePossibleAuthError(e) && showPageError) setError(message)
      return message
    }
    await loadDashboardOnly()
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
    await api.saveJournal(tradeId, tags, note, data?.demo_mode)
    await loadAll()
  }

  const handleGenerateAi = async (mode: 'auto' | 'ai' | 'local_rules' = 'auto') => {
    setGeneratingAi(true)
    setError('')
    try {
      await api.generateAiReview(rangeKey, mode, data?.demo_mode)
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

  const handleInspectSymbol = (venueId: string, symbol: string, venueName?: string, drilldownRange?: string) => {
    setDrilldown({ venueId: venueId.toLowerCase(), venueName, symbol, rangeKey: drilldownRange })
    setDrilldownOpen(true)
  }

  const handleOpenInTrades = (venueId: string, symbol: string) => {
    setDrilldownOpen(false)
    if (drilldown?.rangeKey) setRangeKey(drilldown.rangeKey)
    setVenueFilter(venueId.toLowerCase())
    setSymbolFilter(symbol)
    navigateTo('trades')
  }

  useEffect(() => {
    if (!data || !redirectToOverview || window.location.hash !== hash) return
    window.history.replaceState(window.history.state, '', '#/overview')
    setHash('#/overview')
  }, [data, hash, redirectToOverview])

  useLayoutEffect(() => {
    setDrilldown(null)
    setDrilldownOpen(false)
  }, [data?.demo_mode])

  const visibleError = error || dashboardError
  const activeCurrency: CurrencyCode = settings.display_currency || 'USDT'

  return (
    <I18nProvider locale={settings.locale}>
      <CurrencyProvider currency={activeCurrency} fx={fx}>
        {authChecked && (
          <AuthGate
            auth={authStatus}
            authChecked={authChecked}
            locale={settings.locale}
            onToggleLocale={() =>
              setSettings(prev => ({ ...prev, locale: prev.locale === 'zh-CN' ? 'en' : 'zh-CN' }))
            }
            onUnlocked={nextAuth => {
              setAuthStatus(nextAuth)
            }}
            onCeremonyLiftStart={bumpStageReveal}
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
              showExtensions={enabledTabs.length > 0}
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
                reconciledZero={Boolean(data && Math.abs(data.reconciliation.residual_usd) < 0.01)}
                theme={settings.theme}
                onChangeTheme={next => void handleUpdateSettings({ theme: next })}
                polarity={settings.polarity}
                onTogglePolarity={() =>
                  void handleUpdateSettings({
                    polarity: settings.polarity === 'cn' ? 'global' : 'cn',
                  })
                }
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

              <main className="flex-1 px-6 sm:px-8 lg:px-10 pb-12 max-w-[1400px] w-full mx-auto">
                <AnimatedCollapse open={Boolean(visibleError)} innerClassName="pt-4">
                  <div
                    data-testid="global-error-banner"
                    className="otl-status-err px-4 py-2.5 rounded-lg text-[13px] font-medium"
                  >
                    {visibleError}
                  </div>
                </AnimatedCollapse>

                {(!data || data.range_key !== rangeKey) && (
                  <div role="status" className="py-20 text-center text-[13px] text-[var(--fg-3)]">
                    {settings.locale === 'en' ? 'Loading…' : '加载中…'}
                  </div>
                )}
                {data && (
                  <div
                    key={`${page}-${data.demo_mode ? 'demo' : 'live'}-${stageRevealSeq}`}
                    hidden={data.range_key !== rangeKey}
                    className="otl-page-enter"
                  >
                    {page === 'overview' && (
                      <OverviewPage
                        data={data}
                        enableStockTab={enabledTabs.includes('stock')}
                        enableWatcherTab={enabledTabs.includes('watcher')}
                        onNavigate={navigateTo}
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
                        onInspectSymbol={handleInspectSymbol}
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
                      <FeesAiPage
                        data={data}
                        settings={settings}
                        generating={generatingAi}
                        onGenerate={handleGenerateAi}
                        onGoConfigureAi={() => navigateTo('settings')}
                      />
                    )}
                    {page === 'extensions' && (
                      <ExtensionsPage
                        data={data}
                        enableWatcher={enabledTabs.includes('watcher')}
                        enableCopy={enabledTabs.includes('copy')}
                        enableStocks={enabledTabs.includes('stock')}
                        requestedTab={extTab}
                        onTabChange={nextTab => navigateTo('extensions', nextTab)}
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
                    {page === 'settings' && (
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
                          const failure = await handleUpdateSettings(patch, false)
                          if (failure) return failure
                          if (typeof patch.update_check_enabled === 'boolean') {
                            void handleCheckVersion(false)
                          }
                        }}
                        onUpsertExchange={async (body, onStep) => {
                          onStep?.('verifying')
                          await api.verifyExchange(body)
                          onStep?.('syncing')
                          const syncRes = await api.triggerSync(body.venue_id, body)
                          if (syncRes.auth_status) {
                            invalidateDashboard()
                            setAuthStatus(syncRes.auth_status)
                            if (syncRes.auth_status.require_setup || syncRes.auth_status.authenticated === false) return
                          }
                          const venueRes = syncRes.venues?.[body.venue_id]
                          if (syncRes.ok === false || syncRes.committed === false || venueRes?.ok === false) {
                            const isZh = settings.locale === 'zh-CN'
                            throw new Error(
                              (isZh ? venueRes?.error_zh : venueRes?.error) ||
                                venueRes?.error ||
                                (isZh ? '交易所同步失败' : 'Exchange sync failed'),
                            )
                          }
                          if (syncRes.committed === undefined) await api.upsertExchange(body)
                          await loadAll()
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
                          switchingLedger.current = true
                          invalidateDashboard()
                          try {
                            await api.setDemoMode(enabled)
                            const st = await api.authStatus()
                            setAuthStatus(st)
                            switchingLedger.current = false
                            if (!st.require_setup && st.authenticated !== false) await loadAll()
                          } catch (e) {
                            switchingLedger.current = false
                            await loadAll()
                            if (!handlePossibleAuthError(e)) setError(e instanceof Error ? e.message : 'Failed to switch ledger')
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
            rangeKey={drilldown.rangeKey ?? rangeKey}
            open={drilldownOpen}
            onClose={() => setDrilldownOpen(false)}
            onOpenInTrades={handleOpenInTrades}
          />
        )}
      </CurrencyProvider>
    </I18nProvider>
  )
}
