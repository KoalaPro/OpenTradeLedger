import type {
  AuthStatusPayload,
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

  constructor(
    message: string,
    opts: {
      status: number
      authRequired?: boolean
      requireSetup?: boolean
      retryAfterSec?: number
      errorCode?: string
      rawError?: string
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
  }
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { credentials: 'same-origin', ...init })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const isZh =
      typeof document !== 'undefined' &&
      (document.documentElement.lang || 'zh-CN').toLowerCase().startsWith('zh')
    const msg =
      (isZh && typeof body.detail_zh === 'string' && body.detail_zh) ||
      (typeof body.detail === 'string' && body.detail) ||
      `Request failed (${res.status})`
    throw new ApiError(msg, {
      status: res.status,
      authRequired: Boolean(body.auth_required),
      requireSetup: Boolean(body.require_setup),
      retryAfterSec: Number(body.retry_after_sec || 0),
      errorCode: String(body.error_code || ''),
      rawError: String(body.raw_error || ''),
    })
  }
  return (await res.json()) as T
}

export const api = {
  authStatus(): Promise<AuthStatusPayload> {
    return req('/api/v1/auth/status')
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
  authLogout(): Promise<AuthStatusPayload & { ok: boolean }> {
    return req('/api/v1/auth/logout', { method: 'POST' })
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
  upsertExchange(body: {
    venue_id: string
    label: string
    api_key: string
    api_secret: string
    passphrase?: string
  }): Promise<{ ok: boolean; health?: VenueHealthState }> {
    return req('/api/v1/exchanges', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },
  deleteExchange(venueId: string): Promise<{ ok: boolean }> {
    return req(`/api/v1/exchanges/${encodeURIComponent(venueId)}`, { method: 'DELETE' })
  },
  testExchange(
    venueId: string,
  ): Promise<{ ok: boolean; venue_id?: string; health?: VenueHealthState; [k: string]: unknown }> {
    return req(`/api/v1/exchanges/${encodeURIComponent(venueId)}/test`, { method: 'POST' })
  },
  triggerSync(
    venueId?: string,
  ): Promise<{
    ok: boolean
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
      body: JSON.stringify({ force_all: true, ...(venueId ? { venue_id: venueId } : {}) }),
    })
  },
  setDemoMode(enabled: boolean): Promise<{ ok: boolean; demo_mode: boolean }> {
    return req('/api/v1/demo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
  },
  saveJournal(tradeId: string, tags: string[], note: string): Promise<{ ok: boolean }> {
    return req(`/api/v1/journal/${encodeURIComponent(tradeId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags, note }),
    })
  },
  generateAiReview(rangeKey: string): Promise<NonNullable<DashboardData['ai_review']>> {
    return req('/api/v1/ai-review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ range_key: rangeKey }),
    })
  },
  listAiModels(baseUrl: string, apiKey: string): Promise<{ ok: boolean; models: string[]; count: number }> {
    return req('/api/v1/ai-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: baseUrl, api_key: apiKey }),
    })
  },
  testNotify(): Promise<{ ok: boolean; sent: string[]; errors?: Record<string, string> }> {
    return req('/api/v1/extensions/copy-watcher/test-notify', { method: 'POST' })
  },
}
