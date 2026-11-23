import { AlertTriangle, Eye, EyeOff, Languages } from 'lucide-react'
import { useI18n } from '../i18n'
import { CURRENCY_OPTIONS } from '../lib/currency'
import type { CurrencyCode, Locale, NavPage } from '../lib/types'

interface TopBarProps {
  page: NavPage
  demoMode: boolean
  privacyMode: boolean
  onTogglePrivacy: () => void
  locale: Locale
  onToggleLocale: () => void
  currency?: CurrencyCode
  onChangeCurrency?: (next: CurrencyCode) => void
  rangeKey: string
  onChangeRange: (rk: string) => void
  exchangeErrorCount?: number
  onInspectErrors?: () => void
}

const pageTitleKeys: Record<NavPage, 'nav_overview' | 'nav_trades' | 'nav_analytics' | 'nav_fees' | 'nav_extensions' | 'nav_settings'> = {
  overview: 'nav_overview',
  trades: 'nav_trades',
  analytics: 'nav_analytics',
  fees: 'nav_fees',
  extensions: 'nav_extensions',
  settings: 'nav_settings',
}

export function TopBar({
  page,
  demoMode,
  privacyMode,
  onTogglePrivacy,
  locale,
  onToggleLocale,
  currency = 'USDT',
  onChangeCurrency,
  rangeKey,
  onChangeRange,
  exchangeErrorCount = 0,
  onInspectErrors,
}: TopBarProps) {
  const { t } = useI18n()

  return (
    <header className="h-14 px-6 border-b border-[var(--border)] bg-[var(--bg)] flex items-center justify-between gap-3 shrink-0">
      <div className="flex items-center gap-2.5 min-w-0">
        <h1 className="m-0 text-[17px] font-semibold tracking-tight text-[var(--fg)] truncate">
          {t(pageTitleKeys[page])}
        </h1>
        {demoMode && (
          <span
            data-testid="demo-mode-badge"
            className="px-2 py-0.5 rounded-md text-[11px] font-medium border border-[var(--border)] bg-[var(--surface-2)] text-[var(--fg-2)] whitespace-nowrap"
          >
            {t('demo_badge')}
          </span>
        )}
        {!demoMode && exchangeErrorCount > 0 && (
          <button
            type="button"
            data-testid="topbar-exchange-error-badge"
            onClick={onInspectErrors}
            className="otl-status-err inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-md text-[11.5px] font-medium cursor-pointer whitespace-nowrap"
          >
            <AlertTriangle size={12} />
            <span>
              {locale === 'en'
                ? `${exchangeErrorCount} Exchange API ${exchangeErrorCount > 1 ? 'Errors' : 'Error'} · View`
                : `${exchangeErrorCount} 个交易所 API 报错 · 查看原因`}
            </span>
          </button>
        )}
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        {onChangeCurrency && (
          <select
            aria-label={t('set_currency')}
            data-testid="currency-selector"
            value={currency}
            onChange={e => onChangeCurrency(e.target.value as CurrencyCode)}
            className="h-8 px-2 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer"
          >
            {CURRENCY_OPTIONS.map(opt => (
              <option key={opt.code} value={opt.code}>
                {opt.symbol ? `${opt.code} ${opt.symbol}` : opt.code}
              </option>
            ))}
          </select>
        )}

        <button
          type="button"
          data-testid="privacy-toggle-btn"
          onClick={onTogglePrivacy}
          className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer whitespace-nowrap"
          style={privacyMode ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : undefined}
        >
          {privacyMode ? <EyeOff size={14} /> : <Eye size={14} />}
          <span>{privacyMode ? t('privacy_show') : t('privacy_hide')}</span>
        </button>

        <button
          type="button"
          data-testid="locale-toggle-btn"
          onClick={onToggleLocale}
          className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer whitespace-nowrap"
        >
          <Languages size={14} />
          <span>{locale === 'zh-CN' ? 'EN' : '中文'}</span>
        </button>

        {page !== 'settings' && (
          <div className="inline-flex p-0.5 rounded-lg border border-[var(--border)] bg-[var(--surface-2)]">
            {(
              [
                ['all', t('range_all')],
                ['30d', t('range_30d')],
                ['7d', t('range_7d')],
              ] as const
            ).map(([rk, label]) => {
              const active = rangeKey === rk
              return (
                <button
                  key={rk}
                  type="button"
                  onClick={() => onChangeRange(rk)}
                  className="px-2.5 py-1 rounded-md text-[12px] font-medium transition-colors cursor-pointer whitespace-nowrap"
                  style={{
                    background: active ? 'var(--surface)' : 'transparent',
                    color: active ? 'var(--fg)' : 'var(--fg-2)',
                  }}
                >
                  {label}
                </button>
              )
            })}
          </div>
        )}
      </div>
    </header>
  )
}
