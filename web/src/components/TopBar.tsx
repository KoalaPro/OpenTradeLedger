import { Eye, EyeOff } from 'lucide-react'
import { useI18n } from '../i18n'
import { CURRENCY_OPTIONS } from '../lib/currency'
import type { CurrencyCode, Locale, NavPage, Polarity, Theme } from '../lib/types'
import { SegmentedPills } from './SegmentedPills'

interface TopBarProps {
  page: NavPage
  demoMode: boolean
  reconciledZero?: boolean
  theme?: Theme
  onChangeTheme?: (next: Theme) => void
  polarity?: Polarity
  onTogglePolarity?: () => void
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
  reconciledZero = false,
  theme = 'obsidian',
  onChangeTheme,
  polarity = 'cn',
  onTogglePolarity,
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
    <header className="sticky top-0 z-20 min-h-[54px] py-2 px-3 sm:px-8 lg:px-10 border-b border-[var(--border)] bg-[var(--bg)]/95 backdrop-blur-sm shrink-0">
      <div className="max-w-[1400px] w-full mx-auto sm:px-5 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1 sm:flex sm:flex-wrap sm:justify-between sm:gap-3">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 sm:gap-3 min-w-0 sm:mr-auto">
          <h1 key={page} className="t-h1 otl-title-swap whitespace-nowrap">
            {t(pageTitleKeys[page])}
          </h1>
          {demoMode && (
            <span
              data-testid="demo-mode-badge"
              className="text-[12px] text-[var(--fg-3)] max-w-full sm:whitespace-nowrap"
            >
              {t('demo_badge')}
              {reconciledZero ? ` · ${t('kpi_reconciled_zero')}` : ''}
            </span>
          )}
          {!demoMode && exchangeErrorCount > 0 && (
            <button
              type="button"
              data-testid="topbar-exchange-error-badge"
              onClick={onInspectErrors}
              className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[var(--err)] hover:underline bg-transparent border-0 p-0 cursor-pointer max-w-full text-left sm:whitespace-nowrap self-center"
            >
              <span className="otl-venue-dot" style={{ background: 'var(--err)' }} />
              <span>
                {locale === 'en'
                  ? `${exchangeErrorCount} Exchange API ${exchangeErrorCount > 1 ? 'Errors' : 'Error'} · View →`
                  : `${exchangeErrorCount} 个交易所 API 报错 · 查看原因 →`}
              </span>
            </button>
          )}
          {!demoMode && exchangeErrorCount === 0 && reconciledZero && (
            <span className="text-[12px] text-[var(--fg-3)] hidden sm:inline whitespace-nowrap">
              {t('kpi_reconciled_zero')}
            </span>
          )}
        </div>

        <div
          data-testid="topbar-range-slot"
          aria-hidden={!showRange}
          inert={!showRange}
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
          <span className="h-4 w-px ml-2.5 mr-0.5 hidden sm:inline-block bg-[var(--border)]" />
        </div>

        <div className="col-span-2 flex flex-wrap items-center justify-end gap-1.5 sm:gap-2">
          {onChangeTheme && (
            <div className="otl-theme-swatches" role="group" aria-label={t('set_theme')}>
              {(
                [
                  ['obsidian', '#141820', t('theme_obsidian')],
                  ['ocean', '#0f283d', t('theme_ocean')],
                  ['light', '#eef2f7', t('theme_light')],
                ] as const
              ).map(([th, hex, lbl]) => (
                <button
                  key={th}
                  type="button"
                  data-no-press="true"
                  title={lbl}
                  aria-label={lbl}
                  onClick={() => onChangeTheme(th)}
                  className={`otl-swatch-dot ${theme === th ? 'active' : ''}`}
                  style={{ background: hex }}
                />
              ))}
            </div>
          )}

          {onTogglePolarity && (
            <button
              type="button"
              onClick={onTogglePolarity}
              title={t('set_polarity')}
              className="otl-btn-ghost hidden sm:inline-flex"
            >
              {polarity === 'global'
                ? locale === 'en'
                  ? 'Green Up'
                  : '绿盈红亏'
                : locale === 'en'
                  ? 'Red Up'
                  : '红盈绿亏'}
            </button>
          )}

          {onChangeCurrency && (
            <select
              aria-label={t('set_currency')}
              data-testid="currency-selector"
              value={currency}
              onChange={e => onChangeCurrency(e.target.value as CurrencyCode)}
              className="otl-btn-ghost cursor-pointer pr-1"
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
            aria-label={privacyMode ? t('privacy_show') : t('privacy_hide')}
            onClick={onTogglePrivacy}
            className="otl-btn-ghost"
            style={privacyMode ? { color: 'var(--accent)' } : undefined}
          >
            {privacyMode ? <EyeOff size={14} /> : <Eye size={14} />}
            <span className="hidden sm:inline">
              {privacyMode ? t('privacy_show') : t('privacy_hide')}
            </span>
          </button>

          <button
            type="button"
            data-testid="locale-toggle-btn"
            onClick={onToggleLocale}
            className="otl-btn-ghost"
          >
            <span>{locale === 'zh-CN' ? 'EN' : '中文'}</span>
          </button>
        </div>
      </div>
    </header>
  )
}
