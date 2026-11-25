import { AlertTriangle, Eye, EyeOff, Languages } from 'lucide-react'
import { useI18n } from '../i18n'
import { CURRENCY_OPTIONS } from '../lib/currency'
import type { CurrencyCode, Locale, NavPage } from '../lib/types'
import { SegmentedPills } from './SegmentedPills'

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

const pageTitleKeys: Record<
  NavPage,
  'nav_overview' | 'nav_trades' | 'nav_analytics' | 'nav_fees' | 'nav_extensions' | 'nav_settings'
> = {
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
  const showRange = page !== 'settings' && page !== 'extensions'

  return (
    <header className="min-h-[54px] py-2 px-4 sm:px-7 border-b border-[var(--border)] bg-[var(--bg)] flex flex-wrap items-center justify-between gap-3 shrink-0">
      <div className="flex items-baseline gap-3 shrink-0">
        <h1 key={page} className="t-h1 otl-title-swap whitespace-nowrap">
          {t(pageTitleKeys[page])}
        </h1>
        {demoMode && (
          <span
            data-testid="demo-mode-badge"
            className="text-[13px] text-[var(--fg-3)] whitespace-nowrap"
          >
            · {t('demo_badge')}
          </span>
        )}
        {!demoMode && exchangeErrorCount > 0 && (
          <button
            type="button"
            data-testid="topbar-exchange-error-badge"
            onClick={onInspectErrors}
            className="otl-status-err inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-md text-[13px] font-medium cursor-pointer whitespace-nowrap self-center"
          >
            <AlertTriangle size={13} />
            <span>
              {locale === 'en'
                ? `${exchangeErrorCount} Exchange API ${exchangeErrorCount > 1 ? 'Errors' : 'Error'} · View`
                : `${exchangeErrorCount} 个交易所 API 报错 · 查看原因`}
            </span>
          </button>
        )}
      </div>

      {/* Range slot stays LEFT of utility controls so Currency / Privacy / Locale stay pinned */}
      <div className="flex items-center gap-2 flex-wrap justify-end">
        <div
          data-testid="topbar-range-slot"
          aria-hidden={!showRange}
          className={`otl-range-slot ${showRange ? '' : 'is-hidden'}`}
        >
          <SegmentedPills
            value={rangeKey}
            onChange={rk => {
              if (showRange) onChangeRange(rk)
            }}
            options={[
              { value: 'all', label: t('range_all') },
              { value: '30d', label: t('range_30d') },
              { value: '7d', label: t('range_7d') },
              ...(/^\d{4}-\d{2}$/.test(rangeKey) ? [{ value: rangeKey, label: rangeKey }] : []),
            ]}
          />
        </div>

        {onChangeCurrency && (
          <select
            aria-label={t('set_currency')}
            data-testid="currency-selector"
            value={currency}
            onChange={e => onChangeCurrency(e.target.value as CurrencyCode)}
            className="otl-select h-8 py-0 px-2.5 text-[13px] font-medium cursor-pointer"
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
          className="otl-btn"
          style={privacyMode ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : undefined}
        >
          {privacyMode ? <EyeOff size={14} /> : <Eye size={14} />}
          <span>{privacyMode ? t('privacy_show') : t('privacy_hide')}</span>
        </button>

        <button
          type="button"
          data-testid="locale-toggle-btn"
          onClick={onToggleLocale}
          className="otl-btn"
        >
          <Languages size={14} />
          <span>{locale === 'zh-CN' ? 'EN' : '中文'}</span>
        </button>
      </div>
    </header>
  )
}
