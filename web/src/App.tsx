import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { AnimatedCollapse } from './components/AnimatedCollapse'
import { AuthGate } from './components/AuthGate'
import { Sidebar, type ManualSyncResult } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { TradeDrilldownDrawer } from './components/TradeDrilldownDrawer'
import { I18nProvider } from './i18n'
import { api, ApiError } from './lib/api'
import { collectActiveWarnings } from './lib/format'
import { useLedgerSession } from './lib/ledgerSession'
import { CurrencyProvider, DEFAULT_FX } from './lib/currency'
import { attachVelvetRippleListener } from './lib/velvetRipple'
import type {
  AuthStatusPayload,
  CurrencyCode,
  DashboardData,
  ExchangeAdapterInfo,
  ExtensionSubTab,
  FxRatesPayload,
  Locale,
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

function normVenue(v?: string): string {
  return !v || v.toUpperCase() === 'ALL' ? 'ALL' : v.toLowerCase()
}

function normSymbol(s?: string): string {
  return !s || s.toUpperCase() === 'ALL' ? 'ALL' : s.toUpperCase()
}

const ONBOARDING_WARNING_STORAGE_KEY = 'otl_onboarding_warning_v1'

type OnboardingFailedSubaccount = {
  address: string
  name: string
  error?: string
  error_zh?: string
}

function readStoredOnboardingWarning(): OnboardingFailedSubaccount[] {
  try {
    const local = window.localStorage
    const legacy = window.sessionStorage?.getItem(ONBOARDING_WARNING_STORAGE_KEY)
    const stored = local?.getItem(ONBOARDING_WARNING_STORAGE_KEY)
    if (legacy && local) {
      if (!stored) local.setItem(ONBOARDING_WARNING_STORAGE_KEY, legacy)
      window.sessionStorage.removeItem(ONBOARDING_WARNING_STORAGE_KEY)
    }
    const raw = stored || legacy
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(
      (item): item is OnboardingFailedSubaccount =>
        Boolean(
          item &&
            typeof item === 'object' &&
            typeof item.address === 'string' &&
            typeof item.name === 'string',
        ),
    )
  } catch {
    return []
  }
}

function writeStoredOnboardingWarning(items: OnboardingFailedSubaccount[]): void {
  try {
    const storage = window.localStorage ?? window.sessionStorage
    if (items.length > 0) {
      storage.setItem(ONBOARDING_WARNING_STORAGE_KEY, JSON.stringify(items))
    } else {
      storage.removeItem(ONBOARDING_WARNING_STORAGE_KEY)
      window.sessionStorage?.removeItem(ONBOARDING_WARNING_STORAGE_KEY)
    }
  } catch {
    /* Storage may be unavailable in restricted environments. */
  }
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
  const [passwordReceipt, setPasswordReceipt] = useState('')
  const clearPasswordReceipt = useCallback(() => setPasswordReceipt(''), [])
  const [privacyMode, setPrivacyMode] = useState(true)
  const [preferencesReady, setPreferencesReady] = useState(false)
  const initializedPrivacyRef = useRef(false)
  const settingsWrites = useRef(Promise.resolve())
  const settingsRevision = useRef(0)
  const pendingSettingsWrites = useRef(0)
  const currencyRequest = useRef(0)
  const [pendingCurrency, setPendingCurrency] = useState<CurrencyCode | null>(null)
  const bootstrappedRef = useRef(false)
  const observedLedger = useRef<{ accounts?: string; sync?: number } | null>(null)
  const bootstrapRequest = useRef(0)
  const lastRefreshCheck = useRef<AuthStatusPayload | null>(null)
  const [rangeKey, setRangeKey] = useState('all')
  const [venueFilter, setVenueFilter] = useState('ALL')
  const [symbolFilter, setSymbolFilter] = useState('ALL')
  const [data, setData] = useState<DashboardData | null>(null)
  const [appliedScope, setAppliedScope] = useState<{
    rangeKey: string
    venueFilter: string
    symbolFilter: string
  } | null>(null)
  const [dashboardLoading, setDashboardLoading] = useState(false)
  const [bootstrapLoading, setBootstrapLoading] = useState(false)
  const [onboardingWarning, setOnboardingWarningState] = useState<OnboardingFailedSubaccount[]>(
    () => (typeof window !== 'undefined' ? readStoredOnboardingWarning() : []),
  )
  const updateOnboardingWarning = useCallback((next: OnboardingFailedSubaccount[]) => {
    setOnboardingWarningState(next)
    writeStoredOnboardingWarning(next)
  }, [])
  useEffect(() => {
    const syncWarning = (event: StorageEvent) => {
      if (event.key === ONBOARDING_WARNING_STORAGE_KEY || event.key === null) {
        setOnboardingWarningState(readStoredOnboardingWarning())
      }
    }
    window.addEventListener('storage', syncWarning)
    return () => window.removeEventListener('storage', syncWarning)
  }, [])
  const authGateLocaleRef = useRef<Locale | null>(null)
  const [adapters, setAdapters] = useState<ExchangeAdapterInfo[]>([])
  const [exchangeErrorCount, setExchangeErrorCount] = useState(0)
  const [fx, setFx] = useState<FxRatesPayload>(DEFAULT_FX)
  const [versionStatus, setVersionStatus] = useState<VersionCheckPayload | null>(null)
  const [manualSyncing, setManualSyncing] = useState(false)
  const [lastSyncResult, setLastSyncResult] = useState<ManualSyncResult>()
  const [generatingAi, setGeneratingAi] = useState(false)
  const [error, setError] = useState('')
  const [dashboardError, setDashboardError] = useState('')
  const [bootstrapError, setBootstrapError] = useState('')
  const [drilldown, setDrilldown] = useState<{ venueId: string; venueName?: string; symbol: string; rangeKey?: string } | null>(null)
  const [drilldownOpen, setDrilldownOpen] = useState(false)

  const [stageRevealSeq, setStageRevealSeq] = useState(0)
  const bumpStageReveal = useCallback(() => setStageRevealSeq(s => s + 1), [])

  useLayoutEffect(() => {
    document.documentElement.lang = settings.locale
    document.documentElement.dataset.theme = settings.theme
    document.documentElement.dataset.polarity = settings.polarity
    document.documentElement.dataset.privacy = privacyMode ? 'on' : 'off'
    document.documentElement.classList.toggle('privacy-active', privacyMode)
    document.title = settings.locale === 'en' ? 'OpenTradeLedger' : '交易账本 · OpenTradeLedger'
  }, [settings.theme, settings.polarity, settings.locale, privacyMode])

  useEffect(() => {
    try {
      window.localStorage.setItem('otl.locale', settings.locale)
      window.localStorage.setItem('otl.theme', settings.theme)
      window.localStorage.setItem('otl.polarity', settings.polarity)
    } catch { /* Preferences remain available for the current page. */ }
  }, [settings.locale, settings.theme, settings.polarity])

  useEffect(() => {
    return attachVelvetRippleListener()
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

  const handlePossibleAuthError = useCallback((err: unknown): boolean =>
    err instanceof ApiError && err.status === 401 && err.authRequired, [])

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

  useLayoutEffect(() => {
    currentQuery.current = { rangeKey, effVenue, effSymbol }
    dashboardRequest.current++
    setDashboardError('')
  }, [rangeKey, effVenue, effSymbol])

  const invalidateDashboard = useCallback(() => {
    dashboardRequest.current++
    ledgerEpoch.current++
    observedLedger.current = null
    bootstrappedRef.current = false
    initializedPrivacyRef.current = false
    pendingSettingsWrites.current = 0
    setSettings(prev => ({
      ...DEFAULT_SETTINGS, locale: prev.locale, theme: prev.theme, polarity: prev.polarity,
    }))
    setPreferencesReady(false)
    setPrivacyMode(true)
    setPendingCurrency(null)
    setError('')
    setVenueFilter('ALL')
    setSymbolFilter('ALL')
    setLastSyncResult(undefined)
    setManualSyncing(false)
    setGeneratingAi(false)
    setDashboardError('')
    setDashboardLoading(false)
    setBootstrapLoading(false)
    setData(null)
    setBootstrapError('')
    setAppliedScope(null)
    setDrilldown(null)
    setDrilldownOpen(false)
    setAdapters([])
    setExchangeErrorCount(0)
  }, [])

  const session = useLedgerSession(invalidateDashboard)
  const { authStatus, authChecked, allowed, generation } = session
  const isAuthLocked = Boolean(authStatus.require_setup || authStatus.authenticated === false)

  useEffect(() => {
    if (isAuthLocked || page !== 'settings') clearPasswordReceipt()
  }, [isAuthLocked, page, clearPasswordReceipt])

  const loadDashboardOnly = useCallback(async () => {
    if (!allowed.current) return false
    const id = ++dashboardRequest.current
    const query = { ...currentQuery.current }
    setDashboardLoading(true)
    setDashboardError('')
    try {
      const dash = await api.getDashboard(query.rangeKey, query.effVenue, query.effSymbol)
      if (id !== dashboardRequest.current) return false
      setData(dash)
      setAppliedScope({
        rangeKey: dash.range_key || query.rangeKey,
        venueFilter: normVenue(dash.venue_filter ?? query.effVenue),
        symbolFilter: normSymbol(dash.symbol_filter ?? query.effSymbol),
      })
      setDashboardError('')
      return true
    } catch (e) {
      if (id !== dashboardRequest.current || handlePossibleAuthError(e)) return false
      setDashboardError(e instanceof Error ? e.message : 'Failed to load data')
      return false
    } finally {
      if (id === dashboardRequest.current) {
        setDashboardLoading(false)
      }
    }
  }, [allowed, handlePossibleAuthError])

  const loadAll = useCallback(async () => {
    if (!allowed.current) return false
    const epoch = ledgerEpoch.current
    const requestId = ++bootstrapRequest.current
    const revision = settingsRevision.current
    const writing = pendingSettingsWrites.current > 0
    setBootstrapLoading(true)
    try {
      const [loaded, pref, ex] = await Promise.all([loadDashboardOnly(), api.getSettings(), api.listExchanges()])
      if (epoch !== ledgerEpoch.current || requestId !== bootstrapRequest.current) return false
      const pendingAuthLocale = authGateLocaleRef.current
      const effectivePref = pendingAuthLocale ? { ...pref, locale: pendingAuthLocale } : pref
      if (pendingAuthLocale) {
        authGateLocaleRef.current = null
        void api.updateSettings({ locale: pendingAuthLocale }).catch(() => {})
      }
      if (!writing && revision === settingsRevision.current) setSettings(effectivePref)
      if (!initializedPrivacyRef.current) {
        initializedPrivacyRef.current = true
        setPrivacyMode(Boolean(effectivePref.privacy_mode))
        setPreferencesReady(true)
      }
      applyExchangeList(ex)
      setBootstrapError('')
      return loaded
    } catch (e) {
      if (epoch !== ledgerEpoch.current || requestId !== bootstrapRequest.current || handlePossibleAuthError(e)) return false
      if (!initializedPrivacyRef.current) {
        bootstrappedRef.current = false
      }
      setBootstrapError(e instanceof Error ? e.message : 'Failed to load settings')
      return false
    } finally {
      if (epoch === ledgerEpoch.current && requestId === bootstrapRequest.current) {
        setBootstrapLoading(false)
      }
    }
  }, [allowed, loadDashboardOnly, handlePossibleAuthError])

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
  }, [authChecked, isAuthLocked, generation, rangeKey, effVenue, effSymbol, loadAll, loadDashboardOnly])

  const { account_revision: accounts, last_sync_ms: sync } = authStatus
  useEffect(() => {
    if (!authChecked || isAuthLocked) return
    const previous = observedLedger.current
    if (!previous) {
      observedLedger.current = { accounts, sync }
      return
    }
    // A busy NAS can complete several syncs during a single dashboard read.
    // Consume only the newest revision once the current request has settled.
    if (bootstrapLoading || dashboardLoading || lastRefreshCheck.current === authStatus) return
    if (previous.accounts === accounts && previous.sync === sync) return
    lastRefreshCheck.current = authStatus
    const read = previous.accounts !== accounts ? loadAll : loadDashboardOnly
    void read().then(loaded => {
      if (loaded && observedLedger.current === previous) observedLedger.current = { accounts, sync }
    })
  }, [authStatus, authChecked, isAuthLocked, accounts, sync, bootstrapLoading, dashboardLoading, loadAll, loadDashboardOnly])

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
    const epoch = ledgerEpoch.current
    const revision = ++settingsRevision.current
    const currency = patch.display_currency as CurrencyCode | undefined
    if (currency) {
      currencyRequest.current = revision
      setPendingCurrency(currency)
    }
    setError('')
    pendingSettingsWrites.current++
    const save = settingsWrites.current.then(async () => {
      if (epoch !== ledgerEpoch.current || !allowed.current) return
      const next = await api.updateSettings(patch)
      if (epoch !== ledgerEpoch.current) return
      setSettings(next)
      if (typeof patch.privacy_mode === 'boolean') {
        setPrivacyMode(next.privacy_mode)
        session.notifyOtherTabs()
      }
    })
    settingsWrites.current = save.catch(() => {})
    try {
      await save
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to update settings'
      if (epoch === ledgerEpoch.current && !handlePossibleAuthError(e) && showPageError) setError(message)
      return message
    } finally {
      if (epoch === ledgerEpoch.current) pendingSettingsWrites.current--
      if (currency && currencyRequest.current === revision) setPendingCurrency(null)
    }
    if (epoch === ledgerEpoch.current && !pendingSettingsWrites.current) await loadDashboardOnly()
  }

  const handleSync = async () => {
    const epoch = ledgerEpoch.current
    setManualSyncing(true)
    setError('')
    try {
      const syncRes = await api.triggerSync()
      await loadAll()
      if (epoch !== ledgerEpoch.current) return
      const failedEntries = Object.entries(syncRes.venues || {}).filter(([, v]) => !v.ok)
      if (failedEntries.length > 0) {
        setLastSyncResult('failed')
        const isZh = settings.locale === 'zh-CN'
        const details = failedEntries
          .map(([vid, v]) => `${vid.toUpperCase()}: ${(isZh ? v.error_zh : v.error) || v.error || 'Error'}`)
          .join(' ； ')
        setError(
          isZh
            ? `部分交易所同步异常 — ${details}`
            : `Exchange sync errors — ${details}`,
        )
      } else if (syncRes.demo_mode) {
        setLastSyncResult('demo')
      } else if (syncRes.status === 'no_accounts' || !Object.keys(syncRes.venues ?? {}).length) {
        setLastSyncResult('no_accounts')
      } else {
        setLastSyncResult('synced')
      }
    } catch (e) {
      if (epoch !== ledgerEpoch.current) return
      setLastSyncResult('failed')
      if (handlePossibleAuthError(e)) return
      setError(e instanceof Error ? e.message : 'Sync failed')
    } finally {
      if (epoch === ledgerEpoch.current) setManualSyncing(false)
    }
  }

  const handleSaveJournal = async (tradeId: string, tags: string[], note: string) => {
    const nextTags = [...tags]
    try {
      await api.saveJournal(tradeId, tags, note, data?.demo_mode)
    } catch (e) {
      handlePossibleAuthError(e)
      throw e
    }
    setData(prev =>
      prev
        ? {
            ...prev,
            trades: prev.trades.map(tr =>
              tr.trade_id === tradeId ? { ...tr, tags: nextTags, note } : tr,
            ),
          }
        : prev,
    )
    if (!allowed.current) return
    const epoch = ledgerEpoch.current
    const id = ++dashboardRequest.current
    const query = { ...currentQuery.current }
    void api
      .getDashboard(query.rangeKey, query.effVenue, query.effSymbol)
      .then(dash => {
        if (epoch !== ledgerEpoch.current || id !== dashboardRequest.current || !allowed.current) return
        setData(
          dash.trades.some(tr => tr.trade_id === tradeId)
            ? {
                ...dash,
                trades: dash.trades.map(tr =>
                  tr.trade_id === tradeId &&
                  !tr.note &&
                  (!tr.tags || tr.tags.length === 0) &&
                  (note || nextTags.length > 0)
                    ? { ...tr, tags: nextTags, note }
                    : tr,
                ),
              }
            : dash,
        )
        setAppliedScope({
          rangeKey: dash.range_key || query.rangeKey,
          venueFilter: normVenue(dash.venue_filter ?? query.effVenue),
          symbolFilter: normSymbol(dash.symbol_filter ?? query.effSymbol),
        })
      })
      .catch(e => {
        handlePossibleAuthError(e)
      })
      .finally(() => {
        if (id === dashboardRequest.current) {
          setDashboardLoading(false)
        }
      })
  }

  const handleGenerateAi = async (mode: 'auto' | 'ai' | 'local_rules' = 'auto') => {
    const epoch = ledgerEpoch.current
    setGeneratingAi(true)
    setError('')
    try {
      await api.generateAiReview(rangeKey, mode, data?.demo_mode)
      await loadAll()
    } catch (e) {
      if (epoch !== ledgerEpoch.current || handlePossibleAuthError(e)) return
      setError(e instanceof Error ? e.message : 'AI review generation failed')
    } finally {
      if (epoch === ledgerEpoch.current) setGeneratingAi(false)
    }
  }

  const handleInspectSymbol = (venueId: string, symbol: string, venueName?: string, drilldownRange?: string) => {
    setDrilldown({ venueId: venueId.toLowerCase(), venueName, symbol, rangeKey: drilldownRange })
    setDrilldownOpen(true)
  }

  const handleOpenInTrades = (venueId: string, symbol: string, targetRangeKey?: string) => {
    setDrilldownOpen(false)
    if (targetRangeKey) {
      setRangeKey(targetRangeKey)
    } else if (drilldown?.rangeKey) {
      setRangeKey(drilldown.rangeKey)
    }
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

  const visibleError = error || dashboardError || bootstrapError
  const activeCurrency: CurrencyCode = pendingCurrency || settings.display_currency || 'USDT'
  const activeWarnings = useMemo(() => collectActiveWarnings(data, adapters), [data, adapters])
  const warningSummaryTitle = useMemo(
    () =>
      activeWarnings
        .map(w => {
          const name = settings.locale === 'en' ? w.display_name : w.display_name_zh || w.display_name
          const text = settings.locale === 'en' ? w.summary || w.summary_zh : w.summary_zh || w.summary
          return `${name}: ${text}`
        })
        .join(' | '),
    [activeWarnings, settings.locale],
  )

  const needsFirstAccount = !data?.demo_mode && data?.has_live_data === false
  const needsAccountSnapshot = data && !data.demo_mode && data.exchanges.length === 0 &&
    activeWarnings.length === 0 && adapters.some(adapter => adapter.configured)

  const isFilterScopeMismatch = Boolean(
    page !== 'settings' &&
      data &&
      (!appliedScope ||
        appliedScope.venueFilter !== normVenue(effVenue) ||
        appliedScope.symbolFilter !== normSymbol(effSymbol)),
  )

  const isRangeScopeMismatch = Boolean(
    page !== 'settings' &&
      data &&
      (!appliedScope || data.range_key !== rangeKey || appliedScope.rangeKey !== rangeKey),
  )

  const isScopeMismatch = isFilterScopeMismatch || isRangeScopeMismatch
  const scopeFailureMsg = dashboardError || (!preferencesReady ? bootstrapError || error : '')

  const isLoadingAny = dashboardLoading || bootstrapLoading
  const isWaitingForScope = Boolean(
    isLoadingAny ||
      (authChecked && !isAuthLocked && !bootstrappedRef.current && !scopeFailureMsg) ||
      (isScopeMismatch && !dashboardError),
  )

  const handleRetryDashboard = useCallback(() => {
    setError('')
    setBootstrapError('')
    setDashboardError('')
    if (!preferencesReady || !data) {
      bootstrappedRef.current = true
      void loadAll()
    } else {
      void loadDashboardOnly()
    }
  }, [preferencesReady, data, loadAll, loadDashboardOnly])

  const canRenderSettings = page === 'settings' && (preferencesReady || Boolean(visibleError))
  const showFullPageErrorState =
    page !== 'settings' &&
    !(page === 'trades' && preferencesReady && Boolean(data)) &&
    !isWaitingForScope &&
    Boolean(scopeFailureMsg) &&
    (!preferencesReady || !data || isScopeMismatch)
  const showFullPageLoadingState =
    page === 'settings'
      ? !canRenderSettings
      : !(page === 'trades' && preferencesReady && Boolean(data)) &&
        !showFullPageErrorState &&
        (!preferencesReady || !data || isScopeMismatch)
  const canMountDataPages =
    preferencesReady &&
    Boolean(data) &&
    (page === 'trades' || (!isFilterScopeMismatch && !showFullPageErrorState))

  return (
    <I18nProvider locale={settings.locale}>
      <CurrencyProvider currency={activeCurrency} fx={fx}>
        <div className="otl-session-check" role="status" data-testid="session-check-status">
          {settings.locale === 'en' ? 'Checking ledger access…' : '正在核对账本…'}
        </div>

        {authChecked && (
          <AuthGate
            auth={authStatus}
            authChecked={authChecked}
            locale={settings.locale}
            onToggleLocale={() =>
              setSettings(prev => {
                const nextLocale: Locale = prev.locale === 'zh-CN' ? 'en' : 'zh-CN'
                authGateLocaleRef.current = nextLocale
                return { ...prev, locale: nextLocale }
              })
            }
            lockUnconfirmed={session.lockUnconfirmed}
            onUnlocked={session.unlocked}
            onCeremonyLiftStart={bumpStageReveal}
          />
        )}

        {session.checkError && (
          <div className="min-h-screen grid place-content-center text-center gap-3" role="alert">
            <p>{settings.locale === 'en' ? 'Could not check ledger access.' : '暂时无法确认账本访问状态。'}</p>
            <button type="button" className="otl-btn otl-btn-primary justify-self-center" onClick={() => void session.check()}>
              {settings.locale === 'en' ? 'Retry' : '重试'}
            </button>
          </div>
        )}

        {authChecked && !isAuthLocked && (
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
              syncResult={lastSyncResult}
              syncPartialWarning={!data?.demo_mode && activeWarnings.length > 0}
              syncWarningTitle={warningSummaryTitle}
              onSync={() => void handleSync()}
              authEnabled={Boolean(authStatus.enabled)}
              onLock={() => void session.lock()}
              versionStatus={versionStatus}
            />

            <div className="flex-1 flex flex-col min-w-0">
              <TopBar
                page={page}
                demoMode={Boolean(data?.demo_mode)}
                reconciledZero={Boolean(data && !needsFirstAccount && !needsAccountSnapshot && Math.abs(data.reconciliation.residual_usd) < 0.01)}
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
                activeWarnings={data?.demo_mode ? [] : activeWarnings}
                onInspectErrors={() => navigateTo('settings')}
              />

              <main className="flex-1 px-6 sm:px-8 lg:px-10 pb-12 max-w-[1400px] w-full mx-auto">
                <AnimatedCollapse open={Boolean(visibleError)} innerClassName="pt-4">
                  <div
                    data-testid="global-error-banner"
                    className="otl-status-err px-4 py-2.5 rounded-lg text-[13px] font-medium flex flex-wrap items-center justify-between gap-2"
                  >
                    <span>{visibleError}</span>
                    {page === 'settings' && (!data || !preferencesReady) && !isLoadingAny && (
                      <button
                        type="button"
                        data-testid="dashboard-retry-btn"
                        onClick={handleRetryDashboard}
                        className="otl-btn h-7 px-2.5 shrink-0"
                      >
                        {settings.locale === 'en' ? 'Retry' : '重试'}
                      </button>
                    )}
                  </div>
                </AnimatedCollapse>

                {onboardingWarning.length > 0 && (
                  <div
                    role="alert"
                    data-testid="onboarding-subaccount-warning-banner"
                    className="otl-status-warn mt-4 px-4 py-3 rounded-lg text-[12.5px] flex items-start justify-between gap-3"
                  >
                    <div className="flex items-start gap-2 min-w-0">
                      <AlertTriangle size={15} className="shrink-0 mt-0.5" />
                      <div className="space-y-1">
                        <div className="font-semibold">
                          {settings.locale === 'en'
                            ? `Main wallet connected, but ${onboardingWarning.length} sub-account(s) failed to import:`
                            : `主钱包已接入，但以下 ${onboardingWarning.length} 个子钱包未能导入：`}
                        </div>
                        <ul className="m-0 pl-4 list-disc space-y-0.5">
                          {onboardingWarning.map(f => {
                            const shortAddr =
                              f.address.length > 12
                                ? `${f.address.slice(0, 6)}...${f.address.slice(-4)}`
                                : f.address
                            const reason =
                              settings.locale === 'en'
                                ? f.error || f.error_zh || 'Sync failed'
                                : f.error_zh || f.error || '同步失败'
                            return (
                              <li key={f.address}>
                                <strong>{f.name}</strong> (<span className="sensitive">{shortAddr}</span>)：{reason}
                              </li>
                            )
                          })}
                        </ul>
                      </div>
                    </div>
                    <button
                      type="button"
                      data-testid="dismiss-onboarding-warning-btn"
                      aria-label={settings.locale === 'en' ? 'Dismiss' : '关闭提示'}
                      onClick={() => updateOnboardingWarning([])}
                      className="otl-btn-ghost h-6 w-6 px-0 justify-center shrink-0"
                    >
                      <X size={14} />
                    </button>
                  </div>
                )}

                {showFullPageErrorState && (
                  <div
                    role="alert"
                    data-testid="dashboard-load-error-state"
                    className="py-16 text-center space-y-3"
                  >
                    <p className="m-0 text-[13px] text-[var(--fg-2)]">
                      {settings.locale === 'en'
                        ? 'Could not load ledger dashboard right now.'
                        : '当前暂时无法加载账本数据。'}
                    </p>
                    <button
                      type="button"
                      data-testid="dashboard-retry-btn"
                      onClick={handleRetryDashboard}
                      className="otl-btn otl-btn-primary"
                    >
                      {settings.locale === 'en' ? 'Retry' : '重试'}
                    </button>
                  </div>
                )}

                {showFullPageLoadingState && (
                  <div role="status" className="py-20 text-center text-[13px] text-[var(--fg-3)]">
                    {settings.locale === 'en' ? 'Loading…' : '加载中…'}
                  </div>
                )}

                {canMountDataPages && page !== 'settings' && (
                  <div
                    key={`${page}-${data?.demo_mode ? 'demo' : 'live'}-${stageRevealSeq}`}
                    hidden={page !== 'trades' && isRangeScopeMismatch}
                    className="otl-page-enter"
                  >
                    {page === 'overview' && (needsFirstAccount || needsAccountSnapshot) && (
                      <section className="py-16 px-5 text-center space-y-4">
                        <h2 className="t-h2">
                          {needsFirstAccount
                            ? (settings.locale === 'en' ? 'No exchange connected yet' : '尚未连接交易所')
                            : (settings.locale === 'en' ? 'Waiting for account data' : '尚未获取账户数据')}
                        </h2>
                        <button
                          type="button"
                          className="otl-btn otl-btn-primary"
                          onClick={() => navigateTo('settings')}
                        >
                          {needsFirstAccount
                            ? (settings.locale === 'en' ? 'Add your first account' : '添加第一个账户')
                            : (settings.locale === 'en' ? 'Check account connection' : '检查账户连接')}
                        </button>
                      </section>
                    )}
                    {page === 'overview' && data && !needsFirstAccount && !needsAccountSnapshot && (
                      <OverviewPage
                        data={data}
                        activeWarnings={activeWarnings}
                        enableStockTab={enabledTabs.includes('stock')}
                        enableWatcherTab={enabledTabs.includes('watcher')}
                        onNavigate={navigateTo}
                        onInspectSymbol={handleInspectSymbol}
                      />
                    )}
                    {page === 'trades' && data && (
                      <TradesPage
                        data={data}
                        venueFilter={venueFilter}
                        onVenueChange={setVenueFilter}
                        symbolFilter={symbolFilter}
                        onSymbolChange={setSymbolFilter}
                        onSaveJournal={handleSaveJournal}
                        onInspectSymbol={handleInspectSymbol}
                        scopeReady={!isScopeMismatch}
                        scopeLoading={isWaitingForScope}
                        scopeError={isScopeMismatch && !isWaitingForScope ? dashboardError : ''}
                        onRetryScope={handleRetryDashboard}
                        activeWarnings={activeWarnings}
                      />
                    )}
                    {page === 'analytics' && data && (
                      <AnalyticsPage
                        data={data}
                        rangeKey={rangeKey}
                        onRangeChange={setRangeKey}
                        onInspectSymbol={handleInspectSymbol}
                        settings={settings}
                        onUpdateSettings={handleUpdateSettings}
                      />
                    )}
                    {page === 'fees' && data && (
                      <FeesAiPage
                        data={data}
                        settings={settings}
                        generating={generatingAi}
                        onGenerate={handleGenerateAi}
                        onGoConfigureAi={() => navigateTo('settings')}
                      />
                    )}
                    {page === 'extensions' && data && (
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
                  </div>
                )}

                {page === 'settings' && canRenderSettings && (
                  <div
                    key={`settings-${data?.demo_mode ? 'demo' : 'live'}`}
                    className="otl-page-enter"
                  >
                      <SettingsPage
                        settings={pendingCurrency ? { ...settings, display_currency: pendingCurrency } : settings}
                        adapters={adapters}
                        dashboardWarnings={activeWarnings}
                        demoMode={Boolean(
                          data?.explicit_demo_mode ??
                            authStatus.explicit_demo_mode ??
                            data?.demo_mode,
                        )}
                        authStatus={authStatus}
                        passwordReceipt={passwordReceipt}
                        onPasswordReceiptShown={clearPasswordReceipt}
                        onAuthChanged={(next, receipt) => {
                          session.changed(next)
                          setPasswordReceipt(receipt)
                        }}
                        onLockNow={session.lock}
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
                          const verRes = await api.verifyExchange(body)
                          onStep?.('syncing')
                          const resolvedAccountId = verRes.account_id || body.account_id || body.venue_id
                          const syncBody = verRes.account_id
                            ? {
                                ...body,
                                account_id: verRes.account_id,
                                create_new: false,
                                expected_revision: body.create_new ? 'absent' : body.expected_revision,
                              }
                            : body
                          const syncRes = await api.triggerSync(resolvedAccountId, syncBody)
                          const finalAccountId = syncRes.account_id || resolvedAccountId
                          const venueRes = syncRes.venues?.[finalAccountId] ?? syncRes.venues?.[body.venue_id]
                          if (syncRes.ok === false || syncRes.committed === false || venueRes?.ok === false) {
                            const isZh = settings.locale === 'zh-CN'
                            throw new Error(
                              (isZh ? venueRes?.error_zh : venueRes?.error) ||
                                venueRes?.error ||
                                (isZh ? '交易所同步失败' : 'Exchange sync failed'),
                            )
                          }
                          if (body.include_subaccounts?.length) {
                            const attempted = new Set(body.include_subaccounts.map(sub => sub.address.toLowerCase()))
                            const remaining = readStoredOnboardingWarning().filter(sub => !attempted.has(sub.address.toLowerCase()))
                            updateOnboardingWarning([...remaining, ...(syncRes.failed_subaccounts ?? [])])
                          } else if (syncRes.failed_subaccounts?.length) {
                            updateOnboardingWarning(syncRes.failed_subaccounts)
                          }
                          if (syncRes.auth_status) {
                            session.changed(syncRes.auth_status)
                            if (syncRes.auth_status.require_setup || syncRes.auth_status.authenticated === false) {
                              return { failedSubaccounts: syncRes.failed_subaccounts }
                            }
                          }
                          if (syncRes.committed === undefined) await api.upsertExchange(syncBody)
                          await loadAll()
                          onStep?.('done')
                          if (syncRes.failed_subaccounts && syncRes.failed_subaccounts.length > 0) {
                            return { failedSubaccounts: syncRes.failed_subaccounts }
                          }
                        }}
                        onDeleteExchange={async (vid, keepData = false, expectedRevision) => {
                          await api.deleteExchange(vid, keepData, expectedRevision)
                          await loadAll()
                        }}
                        onTestExchange={async (vid, isRetry = false) => {
                          try {
                            await api.testExchange(vid)
                            if (isRetry) {
                              const syncRes = await api.triggerSync(vid)
                              const venueRes = syncRes.venues?.[vid]
                              const isZh = settings.locale === 'zh-CN'
                              if (!venueRes) {
                                throw new Error(isZh ? '没有可同步的账户' : 'No accounts to sync')
                              }
                              if (!venueRes.ok) {
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
                          session.beginChange()
                          try {
                            await api.setDemoMode(enabled)
                            if (enabled) {
                              updateOnboardingWarning([])
                            }
                            session.notifyOtherTabs()
                          } catch (e) {
                            if (!handlePossibleAuthError(e)) setError(e instanceof Error ? e.message : 'Failed to switch ledger')
                          } finally {
                            await session.recheckAfterChange()
                          }
                        }}
                      />
                  </div>
                )}
              </main>
            </div>
          </div>
        )}

        {drilldown && authChecked && preferencesReady && !isAuthLocked && (
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
