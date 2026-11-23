import type { DashboardData, ExchangeAdapterInfo, FxRatesPayload, UserSettings } from './types'

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${res.status})`)
  }
  return (await res.json()) as T
}

export const api = {
  getDashboard(range: string, venue = 'ALL', symbol = 'ALL'): Promise<DashboardData> {
    const qs = new URLSearchParams({ range, venue, symbol })
    return req(`/api/v1/dashboard?${qs.toString()}`)
  },
  getFxRates(refresh = false): Promise<FxRatesPayload> {
    return req(`/api/v1/fx-rates${refresh ? '?refresh=true' : ''}`)
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
  listExchanges(): Promise<{ adapters: ExchangeAdapterInfo[] }> {
    return req('/api/v1/exchanges')
  },
  upsertExchange(body: {
    venue_id: string
    label: string
    api_key: string
    api_secret: string
    passphrase?: string
  }): Promise<{ ok: boolean }> {
    return req('/api/v1/exchanges', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },
  deleteExchange(venueId: string): Promise<{ ok: boolean }> {
    return req(`/api/v1/exchanges/${encodeURIComponent(venueId)}`, { method: 'DELETE' })
  },
  testExchange(venueId: string): Promise<{ ok: boolean; venue_id?: string; [k: string]: unknown }> {
    return req(`/api/v1/exchanges/${encodeURIComponent(venueId)}/test`, { method: 'POST' })
  },
  triggerSync(
    venueId?: string,
  ): Promise<{ ok: boolean; venues?: Record<string, { ok: boolean; error?: string; equity_usd?: number }> }> {
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
}
