import type { DashboardData, DashboardWarningItem, ExchangeAdapterInfo } from './types'

export function normalizeZeroAfterRound(value: number, digits = 2): number {
  if (!Number.isFinite(value)) return 0
  const d = Math.max(0, Math.floor(digits))
  const factor = Math.pow(10, d)
  const rounded = Math.round((value + Math.sign(value) * Number.EPSILON) * factor) / factor
  const threshold = 0.5 * Math.pow(10, -d)
  if (Math.abs(rounded) < threshold || Object.is(rounded, -0)) {
    return 0
  }
  return rounded
}

export function fmtGoalProgressPct(pct: number): string {
  const safe = Number(pct)
  if (!Number.isFinite(safe)) return '0.0'
  return (Math.round((safe + Number.EPSILON) * 10) / 10).toFixed(1)
}

export function fmtUsd(value: number, decimals = 2): string {
  const safe = normalizeZeroAfterRound(value, decimals)
  return safe.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export function fmtSignedUsd(value: number, decimals = 2): string {
  const safe = normalizeZeroAfterRound(value, decimals)
  const sign = safe > 0 ? '+' : safe < 0 ? '-' : ''
  return `${sign}${Math.abs(safe).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`
}

export function fmtSignedPct(value: number, decimals = 2): string {
  const safe = normalizeZeroAfterRound(value, decimals)
  const sign = safe > 0 ? '+' : safe < 0 ? '-' : ''
  return `${sign}${Math.abs(safe).toFixed(decimals)}%`
}

export function fmtPrice(px: number): string {
  if (!Number.isFinite(px) || px === 0) return '—'
  if (px >= 1000) return px.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (px >= 1) return px.toFixed(3)
  return px.toFixed(5)
}

export function fmtDuration(seconds: number, locale: 'zh-CN' | 'en'): string {
  const s = Math.max(0, Math.floor(seconds || 0))
  if (s === 0) return locale === 'en' ? '<1s' : '<1秒'
  if (s < 60) return locale === 'en' ? `${s}s` : `${s}秒`
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (locale === 'en') {
    if (h >= 24) return `${Math.floor(h / 24)}d ${h % 24}h`
    if (h > 0) return `${h}h ${m}m`
    return `${m}m`
  }
  if (h >= 24) return `${Math.floor(h / 24)}天${h % 24}小时`
  if (h > 0) return `${h}小时${m}分`
  return `${m}分钟`
}

export function fmtSyncTime(ms: number): string {
  if (!ms) return '—'
  return new Date(ms).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })
}

export function pnlTone(value: number): string {
  if (value > 0.0001) return 'txt-pos'
  if (value < -0.0001) return 'txt-neg'
  return ''
}


export function fmtContractQty(
  row: { symbol: string; qty: number | null; contract_count?: number },
  locale: 'zh-CN' | 'en',
): string {
  const base = row.symbol.split(/[_/-]/)[0]
  const amount = row.qty == null ? '' : `${Number(row.qty.toPrecision(12))} ${base}`
  if (row.contract_count == null) return amount
  const count = `${row.contract_count} ${locale === 'en' ? 'contracts' : '张'}`
  return amount ? `${count} · ${amount}` : count
}

export function fmtLegRate(rate: number | null, locale: 'zh-CN' | 'en'): string {
  return rate == null ? (locale === 'en' ? 'Rate unknown' : '费率未知') : `${rate.toFixed(1)} bps`
}

export function collectActiveWarnings(
  data?: DashboardData | null,
  adapters?: ExchangeAdapterInfo[],
): DashboardWarningItem[] {
  const byVenue = new Map<string, DashboardWarningItem>()

  const upsert = (item: DashboardWarningItem) => {
    const key = item.venue_id.toLowerCase()
    const cleanWarnings = (item.warnings ?? []).map(w => w.trim()).filter(Boolean)
    const summary = (item.summary || cleanWarnings.join('; ')).trim()
    const summaryZh = (item.summary_zh || cleanWarnings.join('；') || summary).trim()
    if (!cleanWarnings.length && !summary && !summaryZh) return
    const existing = byVenue.get(key)
    if (!existing) {
      byVenue.set(key, {
        venue_id: item.venue_id,
        display_name: item.display_name || item.venue_id.toUpperCase(),
        display_name_zh: item.display_name_zh || item.display_name || item.venue_id.toUpperCase(),
        warnings: cleanWarnings,
        summary: summary || summaryZh,
        summary_zh: summaryZh || summary,
      })
      return
    }
    const mergedSet = new Set([...existing.warnings, ...cleanWarnings])
    existing.warnings = Array.from(mergedSet)
    if (!existing.summary && summary) existing.summary = summary
    if (!existing.summary_zh && summaryZh) existing.summary_zh = summaryZh
  }

  for (const w of data?.warnings ?? []) {
    upsert(w)
  }

  for (const ex of data?.exchanges ?? []) {
    if (ex.connected === false || ex.health_status === 'archived') continue
    if (ex.warnings && ex.warnings.length > 0) {
      upsert({
        venue_id: ex.venue_id,
        display_name: ex.display_name,
        display_name_zh: ex.display_name_zh || ex.display_name,
        warnings: ex.warnings,
        summary: ex.warnings.join('; '),
        summary_zh: ex.warnings.join('；'),
      })
    }
  }

  for (const ad of adapters ?? []) {
    const accounts =
      Array.isArray(ad.accounts) && ad.accounts.length > 0
        ? ad.accounts
        : ad.configured
          ? [
              {
                account_id: ad.venue_id,
                label: ad.credential?.label || ad.display_name,
                configured: ad.configured,
                credential: ad.credential,
                health: ad.health,
              },
            ]
          : []
    for (const acct of accounts) {
      if (!acct.configured || acct.credential?.enabled === false) continue
      const ws = acct.health?.warnings ?? []
      if (ws.length > 0) {
        const customLbl = (acct.credential?.label || acct.label || '').trim()
        const hasCustom =
          customLbl &&
          customLbl.toLowerCase() !== ad.display_name.toLowerCase() &&
          customLbl.toLowerCase() !== (ad.display_name_zh || '').toLowerCase() &&
          customLbl.toLowerCase() !== ad.venue_id.toLowerCase()
        upsert({
          venue_id: acct.account_id,
          display_name: hasCustom ? `${ad.display_name} · ${customLbl}` : ad.display_name,
          display_name_zh: hasCustom
            ? `${ad.display_name_zh || ad.display_name} · ${customLbl}`
            : ad.display_name_zh || ad.display_name,
          warnings: ws,
          summary: ws.join('; '),
          summary_zh: ws.join('；'),
        })
      }
    }
  }

  return Array.from(byVenue.values())
}
