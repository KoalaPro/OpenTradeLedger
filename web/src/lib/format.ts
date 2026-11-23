export function fmtUsd(value: number, decimals = 2): string {
  const safe = Number.isFinite(value) ? value : 0
  return safe.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export function fmtSignedUsd(value: number, decimals = 2): string {
  const safe = Number.isFinite(value) ? value : 0
  const sign = safe > 0 ? '+' : safe < 0 ? '-' : ''
  return `${sign}${Math.abs(safe).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`
}

export function fmtSignedPct(value: number, decimals = 2): string {
  const safe = Number.isFinite(value) ? value : 0
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
