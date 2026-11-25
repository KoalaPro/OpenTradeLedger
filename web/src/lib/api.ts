import type {
  AuthStatusPayload,
  CopyWatcherStatusPayload,
  DashboardData,
  ExchangeAdapterInfo,
  FxRatesPayload,
  UserSettings,
  VenueHealthState,
  VersionCheckPayload,
} from './types'

export class ApiError extends Error {
  status: number
  authRequired: boolean
  requireSetup: boolean
  retryAfterSec: number
  errorCode: string
  rawError: string
  committed?: boolean

  constructor(
    message: string,
    opts: {
      status: number
      authRequired?: boolean
      requireSetup?: boolean
      retryAfterSec?: number
      errorCode?: string
      rawError?: string
      committed?: boolean
    },
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = opts.status
    this.authRequired = Boolean(opts.authRequired)
    this.requireSetup = Boolean(opts.requireSetup)
    this.retryAfterSec = Number(opts.retryAfterSec || 0)
    this.errorCode = opts.errorCode || ''
    this.rawError = opts.rawError || ''
    this.committed = opts.committed
  }
}

export const AUTH_REQUIRED_EVENT = 'otl:auth-required'
let accessGeneration = 0
let accessController = new AbortController()

export function invalidateAccessRequests() {
  accessGeneration++
  accessController.abort()
  accessController = new AbortController()
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const generation = accessGeneration
  const res = await fetch(url, { credentials: 'same-origin', signal: accessController.signal, ...init })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const isZh =
      typeof document !== 'undefined' &&
      (document.documentElement.lang || 'zh-CN').toLowerCase().startsWith('zh')
    const msg =
      (isZh && typeof body.detail_zh === 'string' && body.detail_zh) ||
      (typeof body.detail === 'string' && body.detail) ||
      `Request failed (${res.status})`
    const error = new ApiError(msg, {
      status: res.status,
      authRequired: Boolean(body.auth_required),
      requireSetup: Boolean(body.require_setup),
      retryAfterSec: Number(body.retry_after_sec || 0),
      errorCode: String(body.error_code || ''),
      rawError: String(body.raw_error || ''),
      committed: typeof body.committed === 'boolean' ? body.committed : undefined,
    })
    if (generation === accessGeneration && error.status === 401 && error.authRequired) {
      window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT, { detail: error }))
    }
    throw error
  }
  const result = await res.json() as T
  if (generation !== accessGeneration) throw new DOMException('Ledger changed', 'AbortError')
  return result
}

export const api = {
  authStatus(signal?: AbortSignal): Promise<AuthStatusPayload> {
    return req('/api/v1/auth/status', { signal })
  },
  authSetup(password: string, remember = true): Promise<AuthStatusPayload & { ok: boolean }> {
    return req('/api/v1/auth/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, remember }),
    })
  },
  authSkip(): Promise<AuthStatusPayload & { ok: boolean }> {
    return req('/api/v1/auth/skip', { method: 'POST' })
  },
  authLogin(password: string, remember = true): Promise<AuthStatusPayload & { ok: boolean }> {
    return req('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, remember }),
    })
  },
  authLogout(signal?: AbortSignal): Promise<AuthStatusPayload & { ok: boolean }> {
    return req('/api/v1/auth/logout', { method: 'POST', signal })
  },
  authChangePassword(
    currentPassword: string,
    newPassword: string,
    remember = true,
  ): Promise<AuthStatusPayload & { ok: boolean }> {
    return req('/api/v1/auth/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
        remember,
      }),
    })
  },
  authDisablePassword(currentPassword: string): Promise<AuthStatusPayload & { ok: boolean }> {
    return req('/api/v1/auth/disable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: currentPassword }),
    })
  },
  getDashboard(range: string, venue = 'ALL', symbol = 'ALL'): Promise<DashboardData> {
    const qs = new URLSearchParams({ range, venue, symbol })
    return req(`/api/v1/dashboard?${qs.toString()}`)
  },
  getFxRates(refresh = false): Promise<FxRatesPayload> {
    return req(`/api/v1/fx-rates${refresh ? '?refresh=true' : ''}`)
  },
  getVersionCheck(refresh = false): Promise<VersionCheckPayload> {
    return req(`/api/v1/version-check${refresh ? '?refresh=true' : ''}`)
  },
  getHealth(): Promise<{ status: string; version: string; base_version?: string; ota_active?: boolean }> {
    return req('/api/v1/health')
  },
  triggerSelfUpdate(): Promise<{ ok: boolean; updated_to: string; restarting: boolean }> {
    return req('/api/v1/system/self-update', { method: 'POST' })
  },
  getSettings(): Promise<UserSettings> {
    return req('/api/v1/settings')
  },
  updateSettings(patch: Record<string, unknown>): Promise<UserSettings> {
    return req('/api/v1/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
  },
  listExchanges(): Promise<{ adapters: ExchangeAdapterInfo[]; error_count?: number }> {
    return req('/api/v1/exchanges')
  },
  verifyExchange(body: {
    venue_id: string
    label: string
    api_key: string
    api_secret: string
    passphrase?: string
    expected_revision?: string
  }): Promise<{ ok: boolean; verified?: boolean; permission_warning?: string; permission_warning_zh?: string }> {
    return req('/api/v1/exchanges/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },
  upsertExchange(body: {
    venue_id: string
    label: string
    api_key: string
    api_secret: string
    passphrase?: string
    expected_revision?: string
  }): Promise<{ ok: boolean; health?: VenueHealthState }> {
    return req('/api/v1/exchanges', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },
  deleteExchange(
    venueId: string,
    keepData = false,
    expectedRevision?: string,
  ): Promise<{ ok: boolean; kept_data?: boolean; has_archived_data?: boolean }> {
    const qs = new URLSearchParams({ keep_data: String(keepData) })
    if (expectedRevision !== undefined) qs.set('expected_revision', expectedRevision)
    return req(`/api/v1/exchanges/${encodeURIComponent(venueId)}?${qs}`, { method: 'DELETE' })
  },
  testExchange(
    venueId: string,
  ): Promise<{ ok: boolean; venue_id?: string; health?: VenueHealthState; [k: string]: unknown }> {
    return req(`/api/v1/exchanges/${encodeURIComponent(venueId)}/test`, { method: 'POST' })
  },
  triggerSync(
    venueId?: string,
    candidateCredential?: {
      venue_id: string
      label: string
      api_key: string
      api_secret: string
      passphrase?: string
      expected_revision?: string
    },
  ): Promise<{
    ok: boolean
    committed?: boolean
    auth_status?: AuthStatusPayload
    venues?: Record<
      string,
      {
        ok: boolean
        error?: string
        error_zh?: string
        error_code?: string
        raw_error?: string
        equity_usd?: number
      }
    >
  }> {
    return req('/api/v1/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        force_all: true,
        ...(venueId ? { venue_id: venueId } : {}),
        ...(candidateCredential ? { candidate_credential: candidateCredential } : {}),
      }),
    })
  },
  setDemoMode(enabled: boolean): Promise<{ ok: boolean; demo_mode: boolean }> {
    return req('/api/v1/demo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
  },
  saveJournal(tradeId: string, tags: string[], note: string, demoMode?: boolean): Promise<{ ok: boolean }> {
    return req(`/api/v1/journal/${encodeURIComponent(tradeId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags, note, demo_mode: demoMode }),
    })
  },
  generateAiReview(
    rangeKey: string,
    mode: 'auto' | 'ai' | 'local_rules' = 'auto',
    demoMode?: boolean,
  ): Promise<NonNullable<DashboardData['ai_review']>> {
    return req('/api/v1/ai-review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ range_key: rangeKey, mode, demo_mode: demoMode }),
    })
  },
  listAiModels(baseUrl: string, apiKey: string): Promise<{ ok: boolean; models: string[]; count: number }> {
    return req('/api/v1/ai-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: baseUrl, api_key: apiKey }),
    })
  },
  getCopyWatcher(refresh = false): Promise<CopyWatcherStatusPayload> {
    const qs = refresh ? '?refresh=true' : ''
    return req(`/api/v1/extensions/copy-watcher${qs}`)
  },
  addCopyWatcherTargets(
    rawInput: string,
    slotAlert = true,
    tradeAlert = true,
  ): Promise<{ ok: boolean } & CopyWatcherStatusPayload> {
    return req('/api/v1/extensions/copy-watcher/targets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        raw_input: rawInput,
        slot_alert: slotAlert,
        trade_alert: tradeAlert,
      }),
    })
  },
  patchCopyWatcherTarget(
    portfolioId: string,
    patch: { enabled?: boolean; slot_alert?: boolean; trade_alert?: boolean },
  ): Promise<{ ok: boolean } & CopyWatcherStatusPayload> {
    return req(`/api/v1/extensions/copy-watcher/targets/${encodeURIComponent(portfolioId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
  },
  deleteCopyWatcherTarget(portfolioId: string): Promise<{ ok: boolean } & CopyWatcherStatusPayload> {
    return req(`/api/v1/extensions/copy-watcher/targets/${encodeURIComponent(portfolioId)}`, {
      method: 'DELETE',
    })
  },
  updateCopyWatcherConfig(body: {
    interval_sec: number
    trade_interval_sec: number
    cooldown_sec: number
  }): Promise<{ ok: boolean } & CopyWatcherStatusPayload> {
    return req('/api/v1/extensions/copy-watcher/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },
  testNotify(options?: {
    portfolio_id?: string
    notifications?: Record<string, unknown>
  }): Promise<{ ok: boolean; sent: string[]; errors?: Record<string, string>; settings?: UserSettings }> {
    return req('/api/v1/extensions/copy-watcher/test-notify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(options ?? {}),
    })
  },
}
