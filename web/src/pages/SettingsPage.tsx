import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  Check,
  ExternalLink,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import { AnimatedCollapse } from '../components/AnimatedCollapse'
import { FloatingModal } from '../components/FloatingModal'
import { FloatingToastViewport } from '../components/FloatingToast'
import { SegmentedPills } from '../components/SegmentedPills'
import { VenueBadge } from '../components/VenueBadge'
import { AccessPasswordCard } from '../components/settings/AccessPasswordCard'
import { AiConfigCard } from '../components/settings/AiConfigCard'
import { BarkNotifyCard } from '../components/settings/BarkNotifyCard'
import { VersionUpgradeCard } from '../components/settings/VersionUpgradeCard'
import { useI18n } from '../i18n'
import { ApiError } from '../lib/api'
import { CURRENCY_OPTIONS, useCurrency } from '../lib/currency'
import type {
  AuthStatusPayload,
  CurrencyCode,
  ExchangeAdapterInfo,
  Locale,
  Polarity,
  Theme,
  UserSettings,
  VersionCheckPayload,
} from '../lib/types'

interface SettingsPageProps {
  settings: UserSettings
  adapters: ExchangeAdapterInfo[]
  demoMode: boolean
  previewFallback?: boolean
  authStatus?: AuthStatusPayload
  onAuthChanged?: (next: AuthStatusPayload) => void
  onLockNow?: () => Promise<void>
  versionStatus?: VersionCheckPayload | null
  onCheckVersion?: (force?: boolean) => Promise<VersionCheckPayload | null>
  onUpdateSettings: (patch: Record<string, unknown>) => Promise<void | string>
  onUpsertExchange: (
    body: {
      venue_id: string
      label: string
      api_key: string
      api_secret: string
      passphrase?: string
    },
    onStep?: (step: 'verifying' | 'syncing' | 'done') => void,
  ) => Promise<void>
  onDeleteExchange: (venueId: string, keepData?: boolean) => Promise<void>
  onTestExchange: (venueId: string, isRetry?: boolean) => Promise<void>
  onToggleDemo: (enabled: boolean) => Promise<void>
}

function getAdapterVisualStatus(
  ad: ExchangeAdapterInfo,
): 'not_configured' | 'archived' | 'disabled' | 'connected' | 'error' | 'unverified' {
  if (!ad.configured) return ad.has_archived_data ? 'archived' : 'not_configured'
  if (ad.credential && ad.credential.enabled === false) return 'disabled'
  const st = ad.health?.status
  if (st === 'error') return 'error'
  if (st === 'connected') return 'connected'
  return 'unverified'
}

export function SettingsPage({
  settings,
  adapters,
  demoMode,
  previewFallback = false,
  authStatus,
  onAuthChanged,
  onLockNow,
  versionStatus,
  onCheckVersion,
  onUpdateSettings: persistSettings,
  onUpsertExchange,
  onDeleteExchange,
  onTestExchange,
  onToggleDemo,
}: SettingsPageProps) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const isZh = locale === 'zh-CN'

  const [addingVenue, setAddingVenue] = useState<string>('')
  const [deletingVenue, setDeletingVenue] = useState<string>('')
  const [deleteMode, setDeleteMode] = useState<'choice' | 'purge_archived'>('choice')
  const [keepHistoryOnDelete, setKeepHistoryOnDelete] = useState<boolean>(true)
  const [modalError, setModalError] = useState('')
  const [modalShake, setModalShake] = useState(false)
  const [submitStep, setSubmitStep] = useState<'' | 'verifying' | 'syncing' | 'done'>('')
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const [label, setLabel] = useState('')
  const [busyVenue, setBusyVenue] = useState('')
  const statusTimerRef = useRef<number | null>(null)
  const closeCeremonyTimerRef = useRef<number | null>(null)

  const [goalLabel, setGoalLabel] = useState(settings.goal_label || '')
  const [goalInput, setGoalInput] = useState(String(settings.goal_target_usd ?? ''))
  const [statusMsg, setStatusMsg] = useState('')
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    setGoalLabel(settings.goal_label || '')
    setGoalInput(String(settings.goal_target_usd ?? ''))
  }, [settings.goal_label, settings.goal_target_usd])

  const selectedAdapter = adapters.find(a => a.venue_id === addingVenue)
  const firstLiveConnection = (demoMode || previewFallback) && !adapters.some(adapter => adapter.configured)
  const deletingAdapter = adapters.find(a => a.venue_id === deletingVenue)

  const flashOk = (msg: string, ms = 3200) => {
    if (statusTimerRef.current !== null) {
      window.clearTimeout(statusTimerRef.current)
    }
    setErrorMsg('')
    setStatusMsg(msg)
    statusTimerRef.current = window.setTimeout(() => {
      setStatusMsg('')
      statusTimerRef.current = null
    }, ms)
  }

  useEffect(() => {
    return () => {
      if (statusTimerRef.current !== null) window.clearTimeout(statusTimerRef.current)
      if (closeCeremonyTimerRef.current !== null) window.clearTimeout(closeCeremonyTimerRef.current)
    }
  }, [])

  const onUpdateSettings = async (patch: Record<string, unknown>) => {
    setStatusMsg('')
    setErrorMsg('')
    try {
      const failure = await persistSettings(patch)
      if (!failure) return true
      setErrorMsg(failure)
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : isZh ? '设置保存失败' : 'Failed to save settings')
    }
    return false
  }

  const handleAddSubmit = async () => {
    if (!addingVenue || !apiKey.trim() || !apiSecret.trim()) return
    if (selectedAdapter?.requires_passphrase && !passphrase.trim()) return
    setBusyVenue(addingVenue)
    setSubmitStep('verifying')
    setStatusMsg('')
    setModalError('')
    setErrorMsg('')
    try {
      await onUpsertExchange(
        {
          venue_id: addingVenue,
          label: label.trim(),
          api_key: apiKey.trim(),
          api_secret: apiSecret.trim(),
          passphrase: passphrase.trim(),
        },
        step => setSubmitStep(step),
      )
      setSubmitStep('done')
      flashOk(
        isZh ? 'API 验证通过，已完成加密保存与数据同步' : 'API verified, encrypted & synced successfully',
        4000,
      )
      const reduced =
        typeof window !== 'undefined' &&
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches
      const finishClose = () => {
        setAddingVenue('')
        setApiKey('')
        setApiSecret('')
        setPassphrase('')
        setLabel('')
        setSubmitStep('')
        setBusyVenue('')
        closeCeremonyTimerRef.current = null
      }
      if (reduced) {
        finishClose()
      } else {
        closeCeremonyTimerRef.current = window.setTimeout(finishClose, 320)
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : isZh ? '连接失败' : 'Connection failed'
      const rolledBack = err instanceof ApiError && err.committed === false
      const updateHint = selectedAdapter?.configured
        ? isZh ? '原账户凭据和记录已保留。' : 'The previous credentials and records were kept.'
        : isZh ? '未保存新账户。' : 'The new account was not saved.'
      const historyFailed = rolledBack && err.errorCode !== 'LOCAL_SAVE_FAILED'
      const phase = historyFailed ? isZh ? '历史同步失败。' : 'Historical sync failed.' : ''
      const message = rolledBack ? [phase, detail, updateHint].filter(Boolean).join(' ') : detail
      setModalError(message)
      setErrorMsg(message)
      setSubmitStep('')
      setBusyVenue('')
      setModalShake(true)
      window.setTimeout(() => setModalShake(false), 320)
    }
  }

  const handleTest = async (vid: string, isRetry = false) => {
    setBusyVenue(vid)
    setErrorMsg('')
    try {
      await onTestExchange(vid, isRetry)
      flashOk(
        isRetry
          ? isZh
            ? `${vid.toUpperCase()} 连接与数据同步已恢复正常`
            : `${vid.toUpperCase()} connection & sync restored`
          : isZh
            ? `${vid.toUpperCase()} 只读 API 连接验证正常`
            : `${vid.toUpperCase()} read-only API verified`,
      )
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Connection test failed')
    } finally {
      setBusyVenue('')
    }
  }

  const formReady = Boolean(
    selectedAdapter &&
      apiKey.trim() &&
      apiSecret.trim() &&
      (!selectedAdapter.requires_passphrase || passphrase.trim()),
  )
  const ceremonyStage: 'credentials' | 'verifying' | 'syncing' | 'done' = submitStep || 'credentials'

  return (
    <div data-testid="settings-page" className="otl-stagger">
      <FloatingToastViewport
        statusMsg={statusMsg}
        errorMsg={errorMsg}
        onClearStatus={() => setStatusMsg('')}
        onClearError={() => setErrorMsg('')}
      />

      <div className="grid grid-cols-1 xl:grid-cols-12 items-start">
        {/* Left 7 Columns: Read-Only Exchange Adapters */}
        <section className="xl:col-span-7 py-6 xl:pr-7 xl:border-r border-[var(--border)] space-y-4">
          <div>
            <h2 className="t-h2">{t('set_ex_title')}</h2>
            <p className="m-0 text-[13px] text-[var(--fg-3)] mt-0.5">{t('set_ex_sub')}</p>
          </div>

          <div className="divide-y divide-[var(--border)] border-t border-[var(--border)]">
            {adapters.map(ad => {
              const vStatus = getAdapterVisualStatus(ad)
              const errReason = isZh
                ? ad.health?.error_reason_zh || ad.health?.error_reason || ''
                : ad.health?.error_reason || ad.health?.error_reason_zh || ''
              const rawErr = ad.health?.raw_error || ''

              return (
                <div
                  key={ad.venue_id}
                  data-testid={`adapter-card-${ad.venue_id}`}
                  className="py-4 space-y-2.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2.5">
                    <div className="flex items-center gap-3 min-w-0">
                      <VenueBadge
                        venueId={ad.venue_id}
                        name={locale === 'en' ? ad.display_name : ad.display_name_zh || ad.display_name}
                      />
                      <div className="min-w-0">
                        <div
                          className={`text-[14px] text-[var(--fg)] truncate ${
                            ad.configured && ad.credential ? 'font-mono text-[13px]' : 'font-medium'
                          }`}
                        >
                          {ad.configured && ad.credential
                            ? ad.credential.label &&
                              ad.credential.label.toLowerCase() !== ad.display_name.toLowerCase() &&
                              ad.credential.label.toLowerCase() !== ad.venue_id.toLowerCase()
                              ? `${ad.credential.label} · ${ad.credential.masked_api_key}`
                              : ad.credential.masked_api_key
                            : vStatus === 'archived'
                              ? isZh
                                ? '本地历史记录已封存（API 已解绑）'
                                : 'Local history archived (API unbound)'
                              : ad.api_base_url}
                        </div>
                        <div className="text-[13px] text-[var(--fg-3)]">
                          {ad.requires_passphrase ? t('set_passphrase_req') : 'GET-Only Signed API'}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2 flex-wrap">
                      {ad.configured ? (
                        <>
                          {vStatus === 'connected' && (
                            <span
                              data-testid={`adapter-status-${ad.venue_id}`}
                              className="inline-flex items-center gap-1.5 text-[13px] font-medium text-[var(--ok)] mr-1"
                            >
                              <span className="h-1.5 w-1.5 rounded-full bg-current" />
                              {t('set_connected')}
                            </span>
                          )}
                          {vStatus === 'error' && (
                            <span
                              data-testid={`adapter-status-${ad.venue_id}`}
                              className="inline-flex items-center gap-1 text-[13px] font-medium text-[var(--err)] mr-1"
                            >
                              <AlertTriangle size={13} />
                              <span>{isZh ? '连接报错' : 'API Error'}</span>
                            </span>
                          )}
                          {vStatus === 'unverified' && (
                            <span
                              data-testid={`adapter-status-${ad.venue_id}`}
                              className="text-[13px] font-medium text-[var(--warn)] mr-1"
                            >
                              {isZh ? '待验证' : 'Unverified'}
                            </span>
                          )}
                          {vStatus === 'disabled' && (
                            <span
                              data-testid={`adapter-status-${ad.venue_id}`}
                              className="text-[13px] text-[var(--fg-3)] mr-1"
                            >
                              {isZh ? '已停用' : 'Disabled'}
                            </span>
                          )}

                          <button
                            type="button"
                            data-testid={`test-adapter-${ad.venue_id}`}
                            disabled={busyVenue === ad.venue_id}
                            onClick={() => void handleTest(ad.venue_id, vStatus === 'error')}
                            className="otl-btn h-7 px-2.5"
                          >
                            {busyVenue === ad.venue_id
                              ? isZh
                                ? '检测中…'
                                : 'Testing…'
                              : vStatus === 'error'
                                ? isZh
                                  ? '重试连接'
                                  : 'Retry'
                                : t('set_test')}
                          </button>

                          <button
                            type="button"
                            data-testid={`edit-adapter-${ad.venue_id}`}
                            onClick={() => {
                              setAddingVenue(ad.venue_id)
                              setModalError('')
                              setLabel(ad.credential?.label || ad.display_name)
                              setApiKey('')
                              setApiSecret('')
                              setPassphrase('')
                            }}
                            title={isZh ? '修改或更新 API 密钥' : 'Update API credentials'}
                            className="otl-btn h-7 px-2.5 text-[var(--fg-2)]"
                          >
                            <Pencil size={12} />
                            <span>{isZh ? '改密钥' : 'Edit'}</span>
                          </button>

                          <button
                            type="button"
                            data-testid={`remove-adapter-${ad.venue_id}`}
                            onClick={() => {
                              setDeleteMode('choice')
                              setKeepHistoryOnDelete(true)
                              setDeletingVenue(ad.venue_id)
                            }}
                            className="otl-btn h-7 w-7 px-0 text-[var(--fg-3)] hover:text-[var(--err)]"
                            aria-label={t('set_remove')}
                          >
                            <Trash2 size={13} />
                          </button>
                        </>
                      ) : vStatus === 'archived' ? (
                        <>
                          <span
                            data-testid={`adapter-status-${ad.venue_id}`}
                            className="text-[13px] text-[var(--fg-3)] mr-1"
                          >
                            {isZh ? '已封存' : 'Archived'}
                          </span>
                          <button
                            type="button"
                            data-testid={`configure-adapter-${ad.venue_id}`}
                            onClick={() => {
                              setAddingVenue(ad.venue_id)
                              setModalError('')
                              setLabel(ad.display_name)
                              setApiKey('')
                              setApiSecret('')
                              setPassphrase('')
                            }}
                            className="otl-btn h-7 px-2.5"
                          >
                            <Plus size={13} />
                            <span>{isZh ? '重新绑定 API' : 'Re-bind API'}</span>
                          </button>
                          <button
                            type="button"
                            data-testid={`purge-archived-${ad.venue_id}`}
                            title={isZh ? '彻底清空该平台封存的本地历史数据' : 'Permanently clear archived local data'}
                            onClick={() => {
                              setDeleteMode('purge_archived')
                              setKeepHistoryOnDelete(false)
                              setDeletingVenue(ad.venue_id)
                            }}
                            className="otl-btn h-7 w-7 px-0 text-[var(--fg-3)] hover:text-[var(--err)]"
                            aria-label={isZh ? '清空封存数据' : 'Purge archived data'}
                          >
                            <Trash2 size={13} />
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          data-testid={`configure-adapter-${ad.venue_id}`}
                          onClick={() => {
                            setAddingVenue(ad.venue_id)
                            setModalError('')
                            setLabel(ad.display_name)
                            setApiKey('')
                            setApiSecret('')
                            setPassphrase('')
                          }}
                          className="otl-btn h-7 px-2.5"
                        >
                          <Plus size={13} />
                          <span>{t('set_add_btn')}</span>
                        </button>
                      )}
                    </div>
                  </div>

                  {vStatus === 'error' && errReason && (
                    <div
                      data-testid={`adapter-error-box-${ad.venue_id}`}
                      className="otl-status-err rounded-md px-3 py-2.5 text-[13px] space-y-1"
                    >
                      <div className="font-semibold flex items-start gap-1.5">
                        <AlertTriangle size={13} className="shrink-0 mt-0.5" />
                        <span>{errReason}</span>
                      </div>
                      {rawErr && !errReason.includes(rawErr) && (
                        <div className="opacity-85 font-mono break-all pl-5">
                          {rawErr}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </section>

        {/* Right 5 Columns: Security, Version OTA, Preferences, AI & Bark */}
        <section className="xl:col-span-5 xl:pl-7 border-t xl:border-t-0 border-[var(--border)] divide-y divide-[var(--border)]">
          {!demoMode && (
            <AccessPasswordCard
              authStatus={authStatus}
              onAuthChanged={onAuthChanged}
              onLockNow={onLockNow}
              flashOk={flashOk}
              setErrorMsg={setErrorMsg}
            />
          )}

          <VersionUpgradeCard
            settings={settings}
            versionStatus={versionStatus}
            onCheckVersion={onCheckVersion}
            onUpdateSettings={onUpdateSettings}
            flashOk={flashOk}
            setErrorMsg={setErrorMsg}
          />

          <div className="py-6 space-y-4">
            <h2 className="t-h2">{t('set_pref_title')}</h2>

            {/* Language */}
            <div className="flex items-center justify-between gap-2 text-[14px]">
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
            <div className="flex flex-wrap items-center justify-between gap-2 text-[14px] pt-3 border-t border-[var(--border)]">
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
            <div className="flex items-center justify-between gap-2 text-[14px] pt-3 border-t border-[var(--border)]">
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
            <div className="space-y-1.5 pt-3 border-t border-[var(--border)] text-[14px]">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[var(--fg-2)]">{t('set_currency')}</span>
                <select
                  aria-label={t('set_currency')}
                  data-testid="settings-currency-select"
                  value={settings.display_currency || 'USDT'}
                  onChange={e =>
                    void onUpdateSettings({ display_currency: e.target.value as CurrencyCode })
                  }
                  className="otl-select h-8 px-2.5 text-[13px] cursor-pointer"
                >
                  {CURRENCY_OPTIONS.map(opt => (
                    <option key={opt.code} value={opt.code}>
                      {locale === 'en' ? opt.labelEn : opt.labelZh}
                    </option>
                  ))}
                </select>
              </div>
              {cur.code !== 'USDT' && cur.code !== 'USD' && (
                <div className="text-[13px] text-[var(--fg-3)] text-right">
                  {t('set_fx_live')}: 1 USDT ≈ {cur.symbol}
                  {cur.rate.toFixed(cur.code === 'JPY' ? 2 : 4)}
                  {cur.fx.updated_at ? ` · ${cur.fx.updated_at}` : ''}
                </div>
              )}
            </div>

            {/* Optional Modules & Privacy Default */}
            <div className="space-y-3 pt-3 border-t border-[var(--border)] text-[14px]">
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
              <p className="m-0 text-[13px] text-[var(--fg-3)]">{t('set_mod_note')}</p>
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
                innerClassName="space-y-2 pt-2"
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
                    value={goalInput}
                    onChange={e => setGoalInput(e.target.value)}
                    placeholder={t('set_goal_target')}
                    className="otl-input flex-1"
                  />
                  <button
                    type="button"
                    onClick={async () => {
                      const target = Number(goalInput)
                      if (!Number.isFinite(target) || target <= 0) {
                        setStatusMsg('')
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
            <div className="pt-3 border-t border-[var(--border)] flex items-center justify-between gap-3">
              <div>
                <div className="text-[14px] font-medium text-[var(--fg)]">{t('set_demo_title')}</div>
                <div className="text-[13px] text-[var(--fg-3)]">
                  {previewFallback
                    ? isZh
                      ? '已切换为实盘模式（接入首个只读交易所 API 后，示例预览将自动替换为真实账单）'
                      : 'Live mode active (sample preview shown until your first read-only exchange API is added)'
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
          </div>

          <AiConfigCard
            settings={settings}
            onUpdateSettings={onUpdateSettings}
            flashOk={flashOk}
            setErrorMsg={setErrorMsg}
          />

          <BarkNotifyCard
            settings={settings}
            onUpdateSettings={onUpdateSettings}
            flashOk={flashOk}
            setErrorMsg={setErrorMsg}
          />
        </section>
      </div>

      {/* Guided Modal for Adding / Updating Exchange Credentials */}
      <FloatingModal
        open={Boolean(selectedAdapter)}
        onClose={() => {
          if (!busyVenue) {
            setAddingVenue('')
            setModalError('')
          }
        }}
        testId="add-exchange-modal"
        maxWidthClass="max-w-[520px]"
        title={
          selectedAdapter ? (
            <span className="inline-flex items-center gap-2">
              <VenueBadge
                venueId={selectedAdapter.venue_id}
                name={isZh ? selectedAdapter.display_name_zh || selectedAdapter.display_name : selectedAdapter.display_name}
              />
              <span>
                {selectedAdapter.configured
                  ? isZh
                    ? '更新只读 API 密钥'
                    : 'Update Read-Only API Key'
                  : isZh
                    ? '接入只读交易所账户'
                    : 'Connect Read-Only Exchange'}
              </span>
            </span>
          ) : null
        }
        subtitle={selectedAdapter?.api_base_url}
        headerActions={
          selectedAdapter?.docs_url ? (
            <a
              href={selectedAdapter.docs_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-[13px] font-medium text-[var(--accent)] hover:underline"
            >
              <span>{isZh ? 'API 文档' : 'API Docs'}</span>
              <ExternalLink size={12} />
            </a>
          ) : undefined
        }
      >
        {selectedAdapter && (
          <div className={`p-5 space-y-3.5 text-[13px] ${modalShake ? 'otl-shake' : ''}`}>
            <div
              data-testid="exchange-ceremony-stepper"
              data-stage={ceremonyStage}
              data-ready={String(formReady)}
              className="grid grid-cols-3 gap-1.5"
            >
              {[
                {
                  id: 'step1',
                  num: '1',
                  title: isZh ? '填写只读密钥' : 'Read-Only Keys',
                  done: formReady || ceremonyStage !== 'credentials',
                  active: ceremonyStage === 'credentials',
                  spinning: false,
                },
                {
                  id: 'step2',
                  num: '2',
                  title: isZh ? '权限握手验活' : 'Handshake Check',
                  done: ceremonyStage === 'syncing' || ceremonyStage === 'done',
                  active: ceremonyStage === 'verifying',
                  spinning: ceremonyStage === 'verifying',
                },
                {
                  id: 'step3',
                  num: '3',
                  title: isZh ? '加密入库同步' : 'Encrypt & Sync',
                  done: ceremonyStage === 'done',
                  active: ceremonyStage === 'syncing',
                  spinning: ceremonyStage === 'syncing',
                },
              ].map(st => (
                <div
                  key={st.id}
                  className="rounded-md border px-2.5 py-2 flex items-center gap-2 text-[13px] font-medium"
                  style={{
                    background: st.done
                      ? 'rgba(47, 196, 149, 0.10)'
                      : st.active
                        ? 'var(--accent-soft)'
                        : 'var(--surface-2)',
                    borderColor: st.done
                      ? 'rgba(47, 196, 149, 0.30)'
                      : st.active
                        ? 'var(--accent)'
                        : 'var(--border)',
                    color: st.done ? 'var(--ok)' : st.active ? 'var(--fg)' : 'var(--fg-3)',
                  }}
                >
                  <span
                    className="h-4 w-4 rounded-full inline-flex items-center justify-center text-[11px] font-semibold shrink-0"
                    style={{
                      background: st.done
                        ? 'var(--ok)'
                        : st.active
                          ? 'var(--accent)'
                          : 'var(--track)',
                      color: st.done || st.active ? 'var(--accent-fg)' : 'var(--fg-3)',
                    }}
                  >
                    {st.done ? (
                      <Check size={10} strokeWidth={3} />
                    ) : st.spinning ? (
                      <RefreshCw size={10} className="animate-spin" />
                    ) : (
                      st.num
                    )}
                  </span>
                  <span className="truncate">{st.title}</span>
                </div>
              ))}
            </div>

            <div className="otl-surface-2 px-3.5 py-2.5 text-[13px] text-[var(--fg-2)] space-y-1">
              <div className="font-semibold text-[var(--fg)] flex items-center gap-1.5">
                <ShieldCheck size={14} style={{ color: 'var(--ok)' }} className="shrink-0" />
                <span>{isZh ? '安全接入指引（先验活、再加密入库）' : 'Read-Only Safety Checklist'}</span>
              </div>
              <p className="m-0 leading-relaxed">
                {isZh
                  ? '请在交易所后台创建仅勾选「只读 / 查询」权限的 API Key，切勿勾选下单或提币；若绑定 IP 白名单，请填入当前运行设备的公网出口 IP。'
                  : 'Create an API key with Read-Only permissions only (never enable trading or withdrawals). Whitelist this host public IP if IP restriction is enabled.'}
              </p>
            </div>

            <AnimatedCollapse open={Boolean(modalError)}>
              <div className="otl-status-err rounded-md px-3 py-2 text-[13px] flex items-start gap-2">
                <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                <span>{modalError}</span>
              </div>
            </AnimatedCollapse>

            <div className="space-y-2.5">
              <label className="block space-y-1">
                <span className="text-[13px] font-medium text-[var(--fg-2)]">
                  {isZh ? '账户备注名称（选填）' : 'Account Label (Optional)'}
                </span>
                <input
                  type="text"
                  data-testid="exchange-label-input"
                  value={label}
                  onChange={e => setLabel(e.target.value)}
                  placeholder={isZh ? `如：${selectedAdapter.display_name_zh || selectedAdapter.display_name} 主账户` : `e.g. ${selectedAdapter.display_name} Main`}
                  className="otl-input h-9 w-full"
                />
              </label>

              <label className="block space-y-1">
                <span className="text-[13px] font-medium text-[var(--fg-2)]">
                  {isZh ? '只读 API Key *' : 'Read-Only API Key *'}
                </span>
                <input
                  type="text"
                  data-testid="exchange-key-input"
                  autoComplete="off"
                  value={apiKey}
                  onChange={e => setApiKey(e.target.value)}
                  placeholder={isZh ? '粘贴交易所生成的 API Key' : 'Paste Read-Only API Key'}
                  className="otl-input h-9 w-full font-mono"
                />
              </label>

              <label className="block space-y-1">
                <span className="text-[13px] font-medium text-[var(--fg-2)]">
                  {isZh ? 'API Secret 密钥 *' : 'API Secret *'}
                </span>
                <input
                  type="password"
                  data-testid="exchange-secret-input"
                  autoComplete="new-password"
                  value={apiSecret}
                  onChange={e => setApiSecret(e.target.value)}
                  placeholder={isZh ? '粘贴对应的 API Secret' : 'Paste API Secret'}
                  className="otl-input h-9 w-full font-mono"
                />
              </label>

              {selectedAdapter.requires_passphrase && (
                <label className="block space-y-1">
                  <span className="text-[13px] font-medium text-[var(--fg-2)]">
                    {isZh ? 'API 口令 (Passphrase) *' : 'API Passphrase *'}
                  </span>
                  <input
                    type="password"
                    data-testid="exchange-passphrase-input"
                    autoComplete="new-password"
                    value={passphrase}
                    onChange={e => setPassphrase(e.target.value)}
                    placeholder={isZh ? '创建该 API Key 时设置的口令' : 'Passphrase set when creating this API key'}
                    className="otl-input h-9 w-full font-mono"
                  />
                </label>
              )}
            </div>

            <AnimatedCollapse open={busyVenue === selectedAdapter.venue_id || submitStep === 'done'}>
              <div
                className={`rounded-md px-3 py-2.5 text-[13px] space-y-2 ${
                  submitStep === 'done'
                    ? 'otl-status-ok'
                    : 'otl-surface-2 text-[var(--fg-2)]'
                }`}
              >
                <div className="flex items-center gap-2">
                  {submitStep === 'done' ? (
                    <Check size={14} className="shrink-0" />
                  ) : (
                    <RefreshCw size={13} className="animate-spin text-[var(--accent)] shrink-0" />
                  )}
                  <span className="font-medium">
                    {submitStep === 'done'
                      ? isZh
                        ? '连接已就绪 · 已完成加密入库与首轮对账同步'
                        : 'Connected · Encrypted & synced successfully'
                      : submitStep === 'verifying'
                        ? isZh
                          ? '正在握手验证只读签名与 API 权限边界…'
                          : 'Verifying read-only signature & API permissions…'
                        : isZh
                          ? '验权通过 · 正在本地加密保存并回溯全历史账单（约 10～25 秒）…'
                          : 'Verified · Encrypting locally & syncing historical ledger (10–25s)…'}
                  </span>
                </div>
                {submitStep !== 'done' && (
                  <div className="h-1 w-full rounded-full bg-[var(--track)] overflow-hidden">
                    <div className="otl-ceremony-indeterminate" />
                  </div>
                )}
              </div>
            </AnimatedCollapse>

            <div className="flex justify-end gap-2 pt-2 border-t border-[var(--border)]">
              <button
                type="button"
                data-testid="cancel-exchange-modal-btn"
                disabled={busyVenue === selectedAdapter.venue_id}
                onClick={() => {
                  setAddingVenue('')
                  setModalError('')
                }}
                className="otl-btn"
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                data-testid="save-exchange-btn"
                disabled={busyVenue === selectedAdapter.venue_id || !formReady}
                onClick={() => void handleAddSubmit()}
                className="otl-btn otl-btn-primary px-4"
                style={
                  submitStep === 'done'
                    ? { background: 'var(--ok)', color: '#ffffff' }
                    : undefined
                }
              >
                {submitStep === 'done' ? (
                  <>
                    <Check size={13} />
                    <span>{isZh ? '连接成功' : 'Connected'}</span>
                  </>
                ) : busyVenue === selectedAdapter.venue_id ? (
                  <>
                    <RefreshCw size={12} className="animate-spin" />
                    <span>{isZh ? '验证并同步中…' : 'Verifying & Syncing…'}</span>
                  </>
                ) : firstLiveConnection ? (
                  <span>{isZh ? '连接并进入我的账本' : 'Connect and open my ledger'}</span>
                ) : (
                  <span>{isZh ? '验证并保存连接' : t('confirm_save')}</span>
                )}
              </button>
            </div>
          </div>
        )}
      </FloatingModal>

      {/* Safe Confirmation Modal Before Unbinding API or Purging Cached Ledger */}
      <FloatingModal
        open={Boolean(deletingAdapter)}
        onClose={() => setDeletingVenue('')}
        testId="delete-exchange-modal"
        maxWidthClass="max-w-[460px]"
        title={
          deleteMode === 'purge_archived'
            ? isZh
              ? '确认清空本地封存数据'
              : 'Purge Archived Local Data'
            : isZh
              ? '移除交易所 API 连接'
              : 'Remove Exchange API Connection'
        }
      >
        {deletingAdapter && (
          <div className="p-5 space-y-4 text-[13px]">
            {deleteMode === 'choice' ? (
              <>
                <p className="m-0 text-[var(--fg-2)] leading-relaxed">
                  {isZh
                    ? `请选择移除「${deletingAdapter.display_name_zh || deletingAdapter.display_name}」时的本地历史数据处理方式：`
                    : `Choose how to handle local historical data when removing ${deletingAdapter.display_name}:`}
                </p>

                <div className="space-y-2.5">
                  <button
                    type="button"
                    data-testid="delete-opt-keep"
                    onClick={() => setKeepHistoryOnDelete(true)}
                    className="w-full text-left rounded-md border p-3.5 cursor-pointer space-y-1"
                    style={{
                      borderColor: keepHistoryOnDelete ? 'var(--accent)' : 'var(--border)',
                      background: keepHistoryOnDelete ? 'var(--accent-soft)' : 'var(--surface-2)',
                    }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-[var(--fg)]">
                        {isZh ? '仅解绑 API，保留本地历史记录（推荐）' : 'Unbind API Key, Keep Local History (Recommended)'}
                      </span>
                      <span
                        className="h-4 w-4 rounded-full border inline-flex items-center justify-center shrink-0"
                        style={{
                          borderColor: keepHistoryOnDelete ? 'var(--accent)' : 'var(--border-strong)',
                          background: keepHistoryOnDelete ? 'var(--accent)' : 'transparent',
                          color: 'var(--accent-fg)',
                        }}
                      >
                        {keepHistoryOnDelete && <Check size={10} strokeWidth={3} />}
                      </span>
                    </div>
                    <p className="m-0 text-[var(--fg-2)] leading-relaxed">
                      {isZh
                        ? '销毁本地加密密钥并停止后台轮询；已同步的平仓交割、充提本金与闭环对账记录继续保留在本地（标为「已封存」）。'
                        : 'Deletes the encrypted API key and stops polling; synced trades, principal, and reconciliation stay archived locally.'}
                    </p>
                  </button>

                  <button
                    type="button"
                    data-testid="delete-opt-purge"
                    onClick={() => setKeepHistoryOnDelete(false)}
                    className="w-full text-left rounded-md border p-3.5 cursor-pointer space-y-1"
                    style={{
                      borderColor: !keepHistoryOnDelete ? 'var(--err)' : 'var(--border)',
                      background: !keepHistoryOnDelete ? 'rgba(244, 104, 118, 0.08)' : 'var(--surface-2)',
                    }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-[var(--fg)]">
                        {isZh ? '彻底删除 API 及全部本地数据' : 'Delete API Key & Purge All Local Data'}
                      </span>
                      <span
                        className="h-4 w-4 rounded-full border inline-flex items-center justify-center shrink-0"
                        style={{
                          borderColor: !keepHistoryOnDelete ? 'var(--err)' : 'var(--border-strong)',
                          background: !keepHistoryOnDelete ? 'var(--err)' : 'transparent',
                          color: '#ffffff',
                        }}
                      >
                        {!keepHistoryOnDelete && <Check size={10} strokeWidth={3} />}
                      </span>
                    </div>
                    <p className="m-0 text-[var(--fg-2)] leading-relaxed">
                      {isZh
                        ? '同时销毁本地 API 密钥并清空该平台分区内的全部资产与历史交易流水（不影响其他交易所，手写复盘笔记保留）。'
                        : 'Deletes the API key and wipes all cached balances and trades in this venue partition (other exchanges and journal notes are untouched).'}
                    </p>
                  </button>
                </div>
              </>
            ) : (
              <p className="m-0 text-[var(--fg-2)] leading-relaxed">
                {isZh
                  ? `确认彻底清空「${deletingAdapter.display_name_zh || deletingAdapter.display_name}」在本地封存的全部历史资产与交割流水吗？若超出交易所官方 API 保留期，清空后将无法重新拉回（其他交易所不受影响，复盘笔记保留）。`
                  : `Permanently purge all archived local balances and trades for ${deletingAdapter.display_name}? Records older than the exchange API retention window cannot be re-fetched (other exchanges and journal notes are preserved).`}
              </p>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setDeletingVenue('')}
                className="otl-btn"
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                data-testid="confirm-delete-exchange-btn"
                onClick={async () => {
                  const vid = deletingAdapter.venue_id
                  const shouldKeep = deleteMode === 'choice' ? keepHistoryOnDelete : false
                  setDeletingVenue('')
                  await onDeleteExchange(vid, shouldKeep)
                  flashOk(
                    shouldKeep
                      ? isZh
                        ? `已解绑 ${vid.toUpperCase()} 密钥，本地历史记录已封存保留`
                        : `Unbound ${vid.toUpperCase()} key · local history archived`
                      : isZh
                        ? `已彻底清空 ${vid.toUpperCase()} 本地数据`
                        : `Purged ${vid.toUpperCase()} local data`,
                  )
                }}
                className={`otl-btn ${
                  deleteMode === 'choice' && keepHistoryOnDelete ? 'otl-btn-primary' : 'otl-status-err'
                }`}
              >
                {deleteMode === 'choice' && keepHistoryOnDelete
                  ? isZh
                    ? '解绑并保留记录'
                    : 'Unbind & Keep History'
                  : isZh
                    ? '确认彻底清空'
                    : 'Delete & Purge'}
              </button>
            </div>
          </div>
        )}
      </FloatingModal>
    </div>
  )
}
