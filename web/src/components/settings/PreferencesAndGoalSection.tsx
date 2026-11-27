import { useEffect, useState } from 'react'
import { AnimatedCollapse } from '../AnimatedCollapse'
import { SegmentedPills } from '../SegmentedPills'
import { useI18n } from '../../i18n'
import { CURRENCY_OPTIONS, useCurrency } from '../../lib/currency'
import type { CurrencyCode, Locale, Polarity, Theme, UserSettings } from '../../lib/types'

interface PreferencesAndGoalSectionProps {
  settings: UserSettings
  demoMode: boolean
  previewFallback: boolean
  onUpdateSettings: (patch: Record<string, unknown>) => Promise<boolean>
  onToggleDemo: (enabled: boolean) => Promise<void>
  onClearStatus: () => void
  flashOk: (msg: string, ms?: number) => void
  setErrorMsg: (msg: string) => void
}

export function PreferencesAndGoalSection({
  settings,
  demoMode,
  previewFallback,
  onUpdateSettings,
  onToggleDemo,
  onClearStatus,
  flashOk,
  setErrorMsg,
}: PreferencesAndGoalSectionProps) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const isZh = locale === 'zh-CN'
  const fallbackRate = cur.fx.source === 'default' || cur.fx.source === 'offline-fallback'
  const rateLabel = fallbackRate ? 'set_fx_offline' : cur.fx.stale ? 'set_fx_cached' : 'set_fx_live'

  const [goalLabel, setGoalLabel] = useState(settings.goal_label || '')
  const [goalInput, setGoalInput] = useState(String(settings.goal_target_usd ?? ''))

  useEffect(() => {
    setGoalLabel(settings.goal_label || '')
    setGoalInput(String(settings.goal_target_usd ?? ''))
  }, [settings.goal_label, settings.goal_target_usd])

  return (
    <section className="xl:col-span-5 py-6 px-5 xl:pl-8 xl:pr-5 border-t xl:border-t-0 border-[var(--border)] space-y-3.5">
      <h2 className="t-h2">{t('set_pref_title')}</h2>

      {/* Language */}
      <div className="flex items-center justify-between gap-2 text-[13px]">
        <span className="text-[var(--fg-2)]">{t('set_lang')}</span>
        <SegmentedPills
          value={settings.locale}
          onChange={(l: Locale) => void onUpdateSettings({ locale: l })}
          options={[
            { value: 'zh-CN', label: '简体中文' },
            { value: 'en', label: 'English' },
          ]}
        />
      </div>

      {/* Theme */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
        <span className="text-[var(--fg-2)]">{t('set_theme')}</span>
        <SegmentedPills
          size="sm"
          value={settings.theme}
          onChange={(th: Theme) => void onUpdateSettings({ theme: th })}
          options={[
            { value: 'obsidian', label: t('theme_obsidian'), testId: 'theme-obsidian' },
            { value: 'ocean', label: t('theme_ocean'), testId: 'theme-ocean' },
            { value: 'light', label: t('theme_light'), testId: 'theme-light' },
          ]}
        />
      </div>

      {/* Polarity */}
      <div className="flex items-center justify-between gap-2 text-[13px]">
        <span className="text-[var(--fg-2)]">{t('set_polarity')}</span>
        <SegmentedPills
          value={settings.polarity}
          onChange={(p: Polarity) => void onUpdateSettings({ polarity: p })}
          options={[
            { value: 'cn', label: t('polarity_cn'), testId: 'polarity-cn' },
            { value: 'global', label: t('polarity_global'), testId: 'polarity-global' },
          ]}
        />
      </div>

      {/* Display Currency */}
      <div className="space-y-1 text-[13px]">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--fg-2)]">{t('set_currency')}</span>
          <select
            aria-label={t('set_currency')}
            data-testid="settings-currency-select"
            value={settings.display_currency || 'USDT'}
            onChange={e =>
              void onUpdateSettings({ display_currency: e.target.value as CurrencyCode })
            }
            className="otl-select h-8 px-2.5 text-[12px] cursor-pointer"
          >
            {CURRENCY_OPTIONS.map(opt => (
              <option key={opt.code} value={opt.code}>
                {locale === 'en' ? opt.labelEn : opt.labelZh}
              </option>
            ))}
          </select>
        </div>
        {cur.code !== 'USDT' && cur.code !== 'USD' && (
          <div data-testid="settings-fx-note" className="text-[12px] text-[var(--fg-3)] text-right">
            {t(rateLabel)}: 1 USDT ≈{' '}
            {cur.symbol}
            {cur.rate.toFixed(cur.code === 'JPY' ? 2 : 4)}
            {cur.fx.updated_at ? ` · ${cur.fx.updated_at}` : ''}
          </div>
        )}
      </div>

      {/* Optional Modules & Privacy Default */}
      <div className="space-y-3 pt-1 text-[13px]">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--fg-2)]">{t('set_privacy_default')}</span>
          <button
            type="button"
            role="switch"
            aria-label={t('set_privacy_default')}
            aria-checked={Boolean(settings.privacy_mode)}
            data-testid="privacy-default-switch"
            onClick={() => void onUpdateSettings({ privacy_mode: !settings.privacy_mode })}
            className="otl-switch"
          >
            <span className="otl-switch-knob" />
          </button>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--fg-2)]">{t('set_mod_watcher')}</span>
          <button
            type="button"
            role="switch"
            aria-label={t('set_mod_watcher')}
            aria-checked={settings.modules.copy_watcher !== false}
            onClick={() =>
              void onUpdateSettings({
                modules: { copy_watcher: !(settings.modules.copy_watcher !== false) },
              })
            }
            className="otl-switch"
          >
            <span className="otl-switch-knob" />
          </button>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--fg-2)]">{t('set_mod_copy')}</span>
          <button
            type="button"
            role="switch"
            aria-label={t('set_mod_copy')}
            aria-checked={Boolean(settings.modules.copy_trading)}
            onClick={() =>
              void onUpdateSettings({ modules: { copy_trading: !settings.modules.copy_trading } })
            }
            className="otl-switch"
          >
            <span className="otl-switch-knob" />
          </button>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--fg-2)]">{t('set_mod_stock')}</span>
          <button
            type="button"
            role="switch"
            aria-label={t('set_mod_stock')}
            aria-checked={Boolean(settings.modules.stocks)}
            onClick={() =>
              void onUpdateSettings({ modules: { stocks: !settings.modules.stocks } })
            }
            className="otl-switch"
          >
            <span className="otl-switch-knob" />
          </button>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--fg-2)]">{t('set_goal_title')}</span>
          <button
            type="button"
            role="switch"
            aria-label={t('set_goal_title')}
            aria-checked={Boolean(settings.goal_enabled)}
            onClick={() => {
              const nextOn = !settings.goal_enabled
              const patch: Record<string, unknown> = { goal_enabled: nextOn }
              if (nextOn && Number(goalInput) > 0) {
                patch.goal_target_usd = Number(goalInput)
              }
              void onUpdateSettings(patch)
            }}
            className="otl-switch"
          >
            <span className="otl-switch-knob" />
          </button>
        </div>
        <AnimatedCollapse
          open={Boolean(settings.goal_enabled)}
          innerClassName="space-y-2 pt-1.5"
        >
          <input
            type="text"
            value={goalLabel}
            onChange={e => setGoalLabel(e.target.value)}
            placeholder={locale === 'en' ? 'Goal name (e.g. 2026 Target)' : '目标名称（如：阶段净值目标）'}
            className="otl-input w-full"
          />
          <div className="flex items-center gap-2">
            <input
              type="number"
              data-testid="settings-goal-target-input"
              aria-label={isZh ? '目标金额 (固定以 USDT 计价)' : 'Target Equity (Fixed in USDT)'}
              value={goalInput}
              onChange={e => setGoalInput(e.target.value)}
              placeholder={t('set_goal_target')}
              className="otl-input flex-1 sensitive"
            />
            <span className="text-[12px] font-semibold text-[var(--fg-3)] whitespace-nowrap shrink-0">
              USDT
            </span>
            <button
              type="button"
              onClick={async () => {
                const target = Number(goalInput)
                if (!Number.isFinite(target) || target <= 0) {
                  onClearStatus()
                  setErrorMsg(isZh ? '目标净值必须大于 0' : 'Target equity must be greater than zero')
                  return
                }
                if (
                  await onUpdateSettings({
                    goal_enabled: true,
                    goal_label: goalLabel.trim(),
                    goal_target_usd: target,
                  })
                )
                  flashOk(t('set_save_ok'), 2500)
              }}
              className="otl-btn otl-btn-primary h-[34px]"
            >
              {t('save_config')}
            </button>
          </div>
        </AnimatedCollapse>
      </div>

      {/* Demo Mode Toggle */}
      <div className="pt-3.5 border-t border-[var(--border)] flex items-center justify-between gap-3">
        <div>
          <div className="text-[13px] font-medium text-[var(--fg)]">{t('set_demo_title')}</div>
          <div className="text-[12px] text-[var(--fg-3)]">
            {previewFallback
              ? isZh
                ? '已切换为实盘模式（接入首个只读交易所 API 后自动替换为真实账单）'
                : 'Live mode active (sample preview shown until your first read-only API is added)'
              : t('set_demo_sub')}
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-label={t('set_demo_title')}
          aria-checked={demoMode}
          data-testid="demo-mode-switch"
          onClick={() => void onToggleDemo(!demoMode)}
          className="otl-switch"
        >
          <span className="otl-switch-knob" />
        </button>
      </div>
    </section>
  )
}
