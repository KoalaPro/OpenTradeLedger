import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowUpCircle,
  Bell,
  Check,
  ChevronDown,
  Copy,
  ExternalLink,
  KeyRound,
  Lock,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  Smartphone,
  Trash2,
} from 'lucide-react'
import { AnimatedCollapse } from '../components/AnimatedCollapse'
import { FloatingModal } from '../components/FloatingModal'
import { FloatingToastViewport } from '../components/FloatingToast'
import { SegmentedPills } from '../components/SegmentedPills'
import { VenueBadge } from '../components/VenueBadge'
import { useI18n } from '../i18n'
import { api } from '../lib/api'
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
  authStatus?: AuthStatusPayload
  onAuthChanged?: (next: AuthStatusPayload) => void
  onLockNow?: () => Promise<void>
  versionStatus?: VersionCheckPayload | null
  onCheckVersion?: (force?: boolean) => Promise<VersionCheckPayload | null>
  onUpdateSettings: (patch: Record<string, unknown>) => Promise<void>
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
  authStatus,
  onAuthChanged,
  onLockNow,
  versionStatus,
  onCheckVersion,
  onUpdateSettings,
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
  const [aiUrl, setAiUrl] = useState(settings.ai_base_url || '')
  const [aiModel, setAiModel] = useState(settings.ai_model || '')
  const [aiKey, setAiKey] = useState('')
  const [aiModels, setAiModels] = useState<string[]>([])
  const [fetchingModels, setFetchingModels] = useState(false)

  const [webhookUrl, setWebhookUrl] = useState(settings.notifications?.webhook_url || '')
  const [tgToken, setTgToken] = useState('')
  const [tgChat, setTgChat] = useState(settings.notifications?.telegram_chat_id || '')
  const [barkServerUrl, setBarkServerUrl] = useState(
    settings.notifications?.bark_server_url || 'https://api.day.app',
  )
  const [barkKey, setBarkKey] = useState('')
  const [outboundProxyUrl, setOutboundProxyUrl] = useState(settings.outbound_proxy_url || '')
  const [testingNotify, setTestingNotify] = useState(false)

  // Local NAS password management state
  const [curPwd, setCurPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmNewPwd, setConfirmNewPwd] = useState('')
  const [pwdBusy, setPwdBusy] = useState(false)

  // Version check, one-click OTA self-update & manual guide state
  const [checkingVersion, setCheckingVersion] = useState(false)
  const [upgradingStep, setUpgradingStep] = useState<'' | 'downloading' | 'restarting' | 'done'>('')
  const [manualGuideOpen, setManualGuideOpen] = useState(false)
  const [copiedKey, setCopiedKey] = useState<string>('')

  const handleSelfUpgrade = async () => {
    if (upgradingStep) return
    setUpgradingStep('downloading')
    setErrorMsg('')
    try {
      const res = await api.triggerSelfUpdate()
      const targetVer = res.updated_to || versionStatus?.latest_version || ''
      setUpgradingStep('restarting')
      const deadline = Date.now() + 25000
      while (Date.now() < deadline) {
        try {
          const h = await api.getHealth()
          if (h?.status === 'ok' && (!targetVer || h.version === targetVer)) {
            setUpgradingStep('done')
            flashOk(
              isZh
                ? `已成功升级至 v${h.version}，正在自动加载新版界面…`
                : `Upgraded to v${h.version} — reloading…`,
              3000,
            )
            window.setTimeout(() => {
              if (typeof window !== 'undefined' && typeof window.location?.reload === 'function') {
                window.location.reload()
              }
            }, 600)
            return
          }
        } catch {
          // Server is restarting; keep polling cleanly
        }
        await new Promise(resolve => window.setTimeout(resolve, 900))
      }
      setUpgradingStep('done')
      if (typeof window !== 'undefined' && typeof window.location?.reload === 'function') {
        window.location.reload()
      }
    } catch (err) {
      setUpgradingStep('')
      setErrorMsg(err instanceof Error ? err.message : isZh ? '在线升级失败' : 'Self-update failed')
    }
  }

  const copyTextSafe = async (key: string, text: string) => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text)
      } else {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
      }
      setCopiedKey(key)
      window.setTimeout(() => setCopiedKey(prev => (prev === key ? '' : prev)), 1800)
    } catch {
      // Ignore clipboard permission errors silently
    }
  }

  const [statusMsg, setStatusMsg] = useState('')
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    setGoalLabel(settings.goal_label || '')
    setGoalInput(String(settings.goal_target_usd ?? ''))
    setAiUrl(settings.ai_base_url || '')
    setAiModel(settings.ai_model || '')
    setWebhookUrl(settings.notifications?.webhook_url || '')
    setTgChat(settings.notifications?.telegram_chat_id || '')
    setBarkServerUrl(settings.notifications?.bark_server_url || 'https://api.day.app')
    setOutboundProxyUrl(settings.outbound_proxy_url || '')
  }, [
    settings.goal_label,
    settings.goal_target_usd,
    settings.ai_base_url,
    settings.ai_model,
    settings.outbound_proxy_url,
    settings.notifications?.webhook_url,
    settings.notifications?.telegram_chat_id,
    settings.notifications?.bark_server_url,
  ])

  const selectedAdapter = adapters.find(a => a.venue_id === addingVenue)
  const deletingAdapter = adapters.find(a => a.venue_id === deletingVenue)
  const authEnabled = Boolean(authStatus?.enabled)

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

  const handleAddSubmit = async () => {
    if (!addingVenue || !apiKey.trim() || !apiSecret.trim()) return
    if (selectedAdapter?.requires_passphrase && !passphrase.trim()) return
    setBusyVenue(addingVenue)
    setSubmitStep('verifying')
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
        closeCeremonyTimerRef.current = window.setTimeout(finishClose, 380)
      }
    } catch (err) {
      const m = err instanceof Error ? err.message : 'Connection failed'
      setModalError(m)
      setErrorMsg(m)
      setSubmitStep('')
      setBusyVenue('')
      setModalShake(true)
      window.setTimeout(() => setModalShake(false), 380)
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

  const handleSavePassword = async () => {
    if (pwdBusy) return
    const np = newPwd.trim()
    if (np.length < 4) {
      setErrorMsg(isZh ? '新密码长度至少需要 4 位字符' : 'New password must be at least 4 characters')
      return
    }
    if (!authEnabled && np !== confirmNewPwd.trim()) {
      setErrorMsg(isZh ? '两次输入的密码不一致' : 'New passwords do not match')
      return
    }
    setPwdBusy(true)
    setErrorMsg('')
    try {
      const res = await api.authChangePassword(curPwd, np, true)
      onAuthChanged?.(res)
      setCurPwd('')
      setNewPwd('')
      setConfirmNewPwd('')
      flashOk(
        authEnabled
          ? isZh
            ? '访问密码已修改（其他旧会话已自动失效）'
            : 'Password updated (older sessions invalidated)'
          : isZh
            ? '已开启本地访问密码保护'
            : 'Local access password enabled',
      )
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to update password')
    } finally {
      setPwdBusy(false)
    }
  }

  const handleDisablePassword = async () => {
    if (pwdBusy) return
    if (!curPwd.trim()) {
      setErrorMsg(isZh ? '关闭密码锁前请先输入当前访问密码' : 'Enter current password to disable lock')
      return
    }
    setPwdBusy(true)
    setErrorMsg('')
    try {
      const res = await api.authDisablePassword(curPwd)
      onAuthChanged?.(res)
      setCurPwd('')
      setNewPwd('')
      setConfirmNewPwd('')
      flashOk(isZh ? '已关闭本地访问密码，当前为本机免密模式' : 'Password lock disabled')
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to disable password')
    } finally {
      setPwdBusy(false)
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
    <div data-testid="settings-page" className="otl-stagger space-y-4">
      <FloatingToastViewport
        statusMsg={statusMsg}
        errorMsg={errorMsg}
        onClearStatus={() => setStatusMsg('')}
        onClearError={() => setErrorMsg('')}
      />

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
        {/* Left: Pluggable Exchange Adapters (Binance / OKX / Bybit / Gate / Bitget / MEXC / EasiCoin) */}
        <section className="xl:col-span-7 otl-card p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="m-0 text-[15px] font-semibold text-[var(--fg)] flex items-center gap-2">
                <ShieldCheck size={16} className="text-emerald-500" />
                {t('set_ex_title')}
              </h2>
              <p className="m-0 text-[12px] text-[var(--fg-3)] mt-0.5">{t('set_ex_sub')}</p>
            </div>
          </div>

          <div className="space-y-2.5">
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
                  className="otl-surface-2 rounded-xl p-3.5 space-y-3"
                  style={
                    vStatus === 'error'
                      ? { borderColor: 'rgba(239, 68, 68, 0.38)' }
                      : undefined
                  }
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2.5 min-w-0">
                      <VenueBadge
                        venueId={ad.venue_id}
                        name={locale === 'en' ? ad.display_name : ad.display_name_zh || ad.display_name}
                      />
                      <div className="min-w-0">
                        <div className="text-[13px] font-medium text-[var(--fg)] truncate">
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
                        <div className="text-[11px] text-[var(--fg-3)]">
                          {ad.requires_passphrase ? t('set_passphrase_req') : 'GET-Only Signed API'}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-1.5 flex-wrap">
                      {ad.configured ? (
                        <>
                          {vStatus === 'connected' && (
                            <span
                              data-testid={`adapter-status-${ad.venue_id}`}
                              className="otl-status-ok px-2 py-0.5 rounded-md text-[11px] font-semibold"
                            >
                              {t('set_connected')}
                            </span>
                          )}
                          {vStatus === 'error' && (
                            <span
                              data-testid={`adapter-status-${ad.venue_id}`}
                              className="otl-status-err inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold"
                            >
                              <AlertTriangle size={11} />
                              <span>{isZh ? '连接报错' : 'API Error'}</span>
                            </span>
                          )}
                          {vStatus === 'unverified' && (
                            <span
                              data-testid={`adapter-status-${ad.venue_id}`}
                              className="otl-status-warn px-2 py-0.5 rounded-md text-[11px] font-semibold"
                            >
                              {isZh ? '待验证' : 'Unverified'}
                            </span>
                          )}
                          {vStatus === 'disabled' && (
                            <span
                              data-testid={`adapter-status-${ad.venue_id}`}
                              className="px-2 py-0.5 rounded-md text-[11px] font-medium border border-[var(--border)] text-[var(--fg-3)]"
                            >
                              {isZh ? '已停用' : 'Disabled'}
                            </span>
                          )}

                          <button
                            type="button"
                            data-testid={`test-adapter-${ad.venue_id}`}
                            disabled={busyVenue === ad.venue_id}
                            onClick={() => void handleTest(ad.venue_id, vStatus === 'error')}
                            className="h-7 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[11.5px] text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer disabled:opacity-50"
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
                            className="inline-flex items-center gap-1 h-7 px-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] text-[11.5px] text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer"
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
                            className="h-7 px-2 rounded-lg border border-[var(--border)] text-[var(--fg-3)] hover:text-[var(--fg)] cursor-pointer"
                            aria-label={t('set_remove')}
                          >
                            <Trash2 size={13} />
                          </button>
                        </>
                      ) : vStatus === 'archived' ? (
                        <>
                          <span
                            data-testid={`adapter-status-${ad.venue_id}`}
                            className="px-2 py-0.5 rounded-md text-[11px] font-medium border border-[var(--border-strong)] bg-[var(--surface)] text-[var(--fg-2)]"
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
                            className="inline-flex items-center gap-1 h-7 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[11.5px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer"
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
                            className="h-7 px-2 rounded-lg border border-[var(--border)] text-[var(--fg-3)] hover:text-[var(--fg)] cursor-pointer"
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
                          className="inline-flex items-center gap-1 h-7 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[11.5px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer"
                        >
                          <Plus size={13} />
                          <span>{t('set_add_btn')}</span>
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Persistent API Error Diagnostic Box — NEVER hides why an exchange failed */}
                  {vStatus === 'error' && errReason && (
                    <div
                      data-testid={`adapter-error-box-${ad.venue_id}`}
                      className="otl-status-err rounded-lg px-3 py-2.5 text-[12px] space-y-1"
                    >
                      <div className="font-semibold flex items-start gap-1.5">
                        <AlertTriangle size={13} className="shrink-0 mt-0.5" />
                        <span>{errReason}</span>
                      </div>
                      {rawErr && !errReason.includes(rawErr) && (
                        <div className="text-[11px] opacity-85 font-mono break-all pl-5">
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

        {/* Right: Security, Preferences, AI & Notifications */}
        <section className="xl:col-span-5 space-y-4">
          {/* NAS / Local Access Password Card */}
          {!demoMode && (
            <div data-testid="security-password-card" className="otl-card p-5 space-y-3">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h3 className="m-0 text-[14px] font-semibold text-[var(--fg)] flex items-center gap-1.5">
                    <KeyRound size={15} className="text-[var(--accent)]" />
                    <span>{isZh ? '安全与本地访问密码' : 'Security & Local Access Password'}</span>
                  </h3>
                  <p className="m-0 text-[11.5px] text-[var(--fg-3)] mt-0.5">
                    {isZh
                      ? '部署到 NAS 或局域网时建议开启，密码仅以 scrypt 加盐哈希保存在本地'
                      : 'Recommended on NAS / LAN — salted scrypt hash stored exclusively on disk'}
                  </p>
                </div>
                <span
                  className={`px-2 py-0.5 rounded-md text-[11px] font-semibold shrink-0 ${
                    authEnabled
                      ? 'otl-status-ok'
                      : 'border border-[var(--border)] bg-[var(--surface-2)] text-[var(--fg-3)]'
                  }`}
                >
                  {authEnabled
                    ? isZh
                      ? '密码已开启'
                      : 'Protected'
                    : isZh
                      ? '免密模式'
                      : 'Password Off'}
                </span>
              </div>

              <div className="space-y-2 pt-1">
                {authEnabled && (
                  <input
                    type="password"
                    data-testid="sec-current-pwd"
                    value={curPwd}
                    onChange={e => setCurPwd(e.target.value)}
                    placeholder={isZh ? '当前访问密码' : 'Current password'}
                    className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                  />
                )}
                <input
                  type="password"
                  data-testid="sec-new-pwd"
                  value={newPwd}
                  onChange={e => setNewPwd(e.target.value)}
                  placeholder={
                    authEnabled
                      ? isZh
                        ? '新密码（至少 4 位）'
                        : 'New password (min 4 chars)'
                      : isZh
                        ? '设置访问密码（至少 4 位）'
                        : 'Set access password (min 4 chars)'
                  }
                  className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                />
                {!authEnabled && (
                  <input
                    type="password"
                    data-testid="sec-confirm-pwd"
                    value={confirmNewPwd}
                    onChange={e => setConfirmNewPwd(e.target.value)}
                    placeholder={isZh ? '再次确认访问密码' : 'Confirm access password'}
                    className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                  />
                )}
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                <div className="flex items-center gap-2">
                  {authEnabled && onLockNow && (
                    <button
                      type="button"
                      data-testid="sec-lock-now-btn"
                      onClick={() => void onLockNow()}
                      className="inline-flex items-center gap-1 h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer"
                    >
                      <Lock size={12} />
                      <span>{isZh ? '立即锁定' : 'Lock Now'}</span>
                    </button>
                  )}
                  {authEnabled && authStatus?.can_skip !== false && (
                    <button
                      type="button"
                      data-testid="sec-disable-pwd-btn"
                      disabled={pwdBusy}
                      onClick={() => void handleDisablePassword()}
                      className="h-8 px-2.5 rounded-lg border border-[var(--border)] text-[12px] text-[var(--fg-3)] hover:text-[var(--fg)] cursor-pointer disabled:opacity-50"
                    >
                      {isZh ? '关闭密码锁' : 'Disable Lock'}
                    </button>
                  )}
                </div>
                <button
                  type="button"
                  data-testid="sec-save-pwd-btn"
                  disabled={pwdBusy || !newPwd.trim()}
                  onClick={() => void handleSavePassword()}
                  className="h-8 px-3 rounded-lg text-[12px] font-semibold cursor-pointer disabled:opacity-50"
                  style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
                >
                  {authEnabled
                    ? isZh
                      ? '修改密码'
                      : 'Update Password'
                    : isZh
                      ? '开启密码保护'
                      : 'Enable Password'}
                </button>
              </div>
            </div>
          )}

          {/* Version & NAS One-Click Upgrade Card */}
          <div data-testid="version-update-card" className="otl-card p-5 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <ArrowUpCircle size={15} className="text-[var(--accent)] shrink-0" />
                <h3 className="m-0 text-[14px] font-semibold text-[var(--fg)] truncate">
                  {isZh ? '版本与更新' : 'Version & Updates'}
                </h3>
                <span className="px-1.5 py-0.5 rounded bg-[var(--surface-2)] border border-[var(--border)] font-mono text-[11px] text-[var(--fg-2)]">
                  v{versionStatus?.current_version || '1.3.0'}
                </span>
              </div>

              {versionStatus?.update_available ? (
                <span
                  data-testid="version-status-badge"
                  className="otl-status-warn px-2 py-0.5 rounded-md text-[11px] font-semibold shrink-0"
                >
                  {isZh
                    ? `新版本 v${versionStatus.latest_version}`
                    : `New v${versionStatus.latest_version}`}
                </span>
              ) : versionStatus?.error && !versionStatus.checked_sec ? (
                <span
                  data-testid="version-status-badge"
                  className="otl-status-err px-2 py-0.5 rounded-md text-[11px] font-semibold shrink-0"
                >
                  {isZh ? '检测失败' : 'Check Error'}
                </span>
              ) : versionStatus?.checked_sec ? (
                <span
                  data-testid="version-status-badge"
                  className="otl-status-ok px-2 py-0.5 rounded-md text-[11px] font-semibold shrink-0"
                >
                  {isZh ? '已是最新' : 'Up to Date'}
                </span>
              ) : (
                <span
                  data-testid="version-status-badge"
                  className="px-2 py-0.5 rounded-md border border-[var(--border)] bg-[var(--surface-2)] text-[11px] text-[var(--fg-3)] shrink-0"
                >
                  {settings.update_check_enabled === false
                    ? isZh
                      ? '自动检测关闭'
                      : 'Auto-Check Off'
                    : isZh
                      ? '待检测'
                      : 'Pending'}
                </span>
              )}
            </div>

            {versionStatus?.error && (
              <div
                data-testid="version-error-box"
                className="otl-status-err rounded-lg px-3 py-2 text-[11.5px]"
              >
                {isZh ? versionStatus.error_zh || versionStatus.error : versionStatus.error}
              </div>
            )}

            {versionStatus?.update_available && (
              <div
                data-testid="version-release-box"
                className="rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] p-3.5 space-y-3"
              >
                <div className="flex items-center justify-between gap-2 text-[12px]">
                  <span className="font-semibold text-[var(--fg)] truncate">
                    {versionStatus.release_name || `v${versionStatus.latest_version}`}
                  </span>
                  <a
                    href={versionStatus.release_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[11.5px] font-medium text-[var(--accent)] hover:underline shrink-0"
                  >
                    <span>{isZh ? '更新日志' : 'Release Notes'}</span>
                    <ExternalLink size={11} />
                  </a>
                </div>
                {versionStatus.release_notes && (
                  <pre className="m-0 max-h-32 overflow-y-auto whitespace-pre-wrap font-sans text-[11.5px] leading-relaxed text-[var(--fg-2)]">
                    {versionStatus.release_notes}
                  </pre>
                )}

                <div className="pt-2 border-t border-[var(--border)] space-y-2">
                  <button
                    type="button"
                    data-testid="one-click-upgrade-btn"
                    disabled={Boolean(upgradingStep)}
                    onClick={() => void handleSelfUpgrade()}
                    className="w-full h-9 px-4 rounded-lg text-[12.5px] font-semibold inline-flex items-center justify-center gap-2 cursor-pointer disabled:opacity-70 transition-all"
                    style={{
                      background: upgradingStep === 'done' ? '#10b981' : 'var(--accent)',
                      color: upgradingStep === 'done' ? '#ffffff' : 'var(--accent-fg)',
                    }}
                  >
                    {upgradingStep === 'done' ? (
                      <>
                        <Check size={14} />
                        <span>
                          {isZh
                            ? `已完成升级至 v${versionStatus.latest_version} · 正在刷新…`
                            : `Upgraded to v${versionStatus.latest_version} · Reloading…`}
                        </span>
                      </>
                    ) : upgradingStep ? (
                      <>
                        <RefreshCw size={13} className="animate-spin" />
                        <span>
                          {upgradingStep === 'downloading'
                            ? isZh
                              ? `正在下载并 SHA-256 校验 v${versionStatus.latest_version} 更新包…`
                              : `Downloading & verifying v${versionStatus.latest_version}…`
                            : isZh
                              ? '校验通过 · 正在平滑热重启服务（约 2 秒）…'
                              : 'Verified · Restarting service (~2s)…'}
                        </span>
                      </>
                    ) : (
                      <>
                        <ArrowUpCircle size={14} />
                        <span>
                          {isZh
                            ? `一键在线升级至 v${versionStatus.latest_version}（免终端 · 自动保留全部数据）`
                            : `One-Click Upgrade to v${versionStatus.latest_version} (Preserves All Data)`}
                        </span>
                      </>
                    )}
                  </button>
                  {upgradingStep && upgradingStep !== 'done' && (
                    <div className="h-1 w-full rounded-full bg-[var(--track)] overflow-hidden">
                      <div className="otl-ceremony-indeterminate" />
                    </div>
                  )}
                  <p className="m-0 text-[11px] text-[var(--fg-3)] text-center">
                    {isZh
                      ? '兼容极空间、绿联、飞牛 fnOS、群晖、威联通及本地 Docker，无需敲命令或进 NAS 后台，密钥与历史账单 100% 保留'
                      : 'Works across all NAS platforms without SSH or Docker socket access; 100% of keys & ledger history are preserved.'}
                  </p>
                </div>
              </div>
            )}

            {/* Optional Collapsible NAS GUI / CLI Manual Upgrade Reference */}
            <div className="space-y-2">
              <button
                type="button"
                data-testid="manual-upgrade-guide-toggle"
                onClick={() => setManualGuideOpen(v => !v)}
                className="w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] text-[11.5px] font-medium text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer"
              >
                <span>
                  {isZh
                    ? '查看各品牌 NAS 图形界面重拉镜像 / 命令行手动更新说明（可选）'
                    : 'Manual NAS Docker GUI / CLI Image Refresh Guide (Optional)'}
                </span>
                <ChevronDown
                  size={13}
                  className={`shrink-0 transition-transform duration-200 ${manualGuideOpen ? 'rotate-180' : ''}`}
                />
              </button>

              <AnimatedCollapse open={manualGuideOpen}>
                <div className="rounded-lg border border-[var(--border)] bg-[var(--surface-2)] p-3 space-y-2.5 text-[11.5px] text-[var(--fg-2)]">
                  <div className="space-y-1 leading-relaxed">
                    <div className="font-semibold text-[var(--fg)]">
                      {isZh
                        ? '方式一：NAS 图形界面更新底层镜像（极空间 / 绿联 / 飞牛 / 群晖通用）'
                        : 'Option 1: NAS Docker Graphical UI (Zero Terminal)'}
                    </div>
                    <p className="m-0">
                      {isZh
                        ? '日常小版本直接点上方「一键在线升级」即可。若需更新底层基础镜像：打开 NAS 的「Docker」应用 → 若用 Compose/项目部署，进入项目点击「重新构建 / 更新」即可；若用单容器部署，先在「镜像」重新拉取 latest 标签，再对原容器执行「重置 / 重新创建」（保持 /app/data 挂载目录不变，数据零丢失）。'
                        : 'For routine updates, use One-Click Upgrade above. To refresh the base Docker image via NAS GUI: open Docker -> Compose -> Rebuild/Update (or pull :latest in Images and recreate container keeping /app/data volume).'}
                    </p>
                  </div>

                  <div className="space-y-1 pt-2 border-t border-[var(--border)]">
                    <div className="text-[11px] text-[var(--fg-3)]">
                      {isZh ? '方式二：SSH 终端命令（进阶可选）' : 'Option 2: SSH / Docker Compose CLI'}
                    </div>
                    <div className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5">
                      <code className="flex-1 font-mono text-[11.5px] text-[var(--fg)] truncate select-all">
                        docker compose pull &amp;&amp; docker compose up -d
                      </code>
                      <button
                        type="button"
                        data-testid="copy-nas-upgrade-cmd"
                        onClick={() =>
                          void copyTextSafe('nas_pull', 'docker compose pull && docker compose up -d')
                        }
                        title={isZh ? '复制命令' : 'Copy command'}
                        aria-label={isZh ? '复制命令' : 'Copy command'}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-[var(--border)] bg-[var(--surface-2)] text-[11px] font-medium text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer shrink-0 transition-colors"
                      >
                        {copiedKey === 'nas_pull' ? (
                          <>
                            <Check size={11} className="text-emerald-500" />
                            <span className="text-emerald-500">{isZh ? '已复制' : 'Copied'}</span>
                          </>
                        ) : (
                          <>
                            <Copy size={11} />
                            <span>{isZh ? '复制' : 'Copy'}</span>
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                </div>
              </AnimatedCollapse>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-[var(--border)] text-[12px]">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  role="switch"
                  aria-label={isZh ? '自动检查新版本' : 'Auto-check new versions'}
                  aria-checked={settings.update_check_enabled !== false}
                  data-testid="auto-update-check-switch"
                  onClick={() =>
                    void onUpdateSettings({
                      update_check_enabled: settings.update_check_enabled === false,
                    })
                  }
                  className="otl-switch"
                >
                  <span className="otl-switch-knob" />
                </button>
                <span className="text-[var(--fg-2)]">
                  {isZh ? '自动检查更新' : 'Auto-check'}
                </span>
                {versionStatus?.checked_at && (
                  <span className="text-[11px] text-[var(--fg-3)] hidden sm:inline">
                    · {versionStatus.checked_at}
                  </span>
                )}
              </div>

              <button
                type="button"
                data-testid="check-version-btn"
                disabled={checkingVersion || !onCheckVersion}
                onClick={async () => {
                  if (!onCheckVersion) return
                  setCheckingVersion(true)
                  setErrorMsg('')
                  try {
                    const res = await onCheckVersion(true)
                    if (res?.error) {
                      setErrorMsg(isZh ? res.error_zh || res.error : res.error)
                    } else if (res?.update_available) {
                      flashOk(
                        isZh
                          ? `检测到新版本 v${res.latest_version}`
                          : `New version v${res.latest_version} available`,
                        3000,
                      )
                    } else if (res) {
                      flashOk(
                        isZh
                          ? `当前 v${res.current_version} 已是最新版本`
                          : `v${res.current_version} is up to date`,
                        2500,
                      )
                    }
                  } finally {
                    setCheckingVersion(false)
                  }
                }}
                className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[11.5px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] disabled:opacity-50 cursor-pointer"
              >
                <RefreshCw size={12} className={checkingVersion ? 'animate-spin' : ''} />
                <span>
                  {checkingVersion
                    ? isZh
                      ? '检查中…'
                      : 'Checking…'
                    : isZh
                      ? '检查更新'
                      : 'Check Now'}
                </span>
              </button>
            </div>
          </div>

          <div className="otl-card p-5 space-y-4">
            <h2 className="m-0 text-[15px] font-semibold text-[var(--fg)]">{t('set_pref_title')}</h2>

            {/* Language */}
            <div className="flex items-center justify-between gap-2 text-[12.5px]">
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

            {/* Theme — Glider-backed SegmentedPills with smooth color crossfade */}
            <div className="flex flex-wrap items-center justify-between gap-2 text-[12.5px] pt-3 border-t border-[var(--border)]">
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
            <div className="flex items-center justify-between gap-2 text-[12.5px] pt-3 border-t border-[var(--border)]">
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

            {/* Display Currency with Live FX */}
            <div className="space-y-1.5 pt-3 border-t border-[var(--border)] text-[12.5px]">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[var(--fg-2)]">{t('set_currency')}</span>
                <select
                  aria-label={t('set_currency')}
                  data-testid="settings-currency-select"
                  value={settings.display_currency || 'USDT'}
                  onChange={e =>
                    void onUpdateSettings({ display_currency: e.target.value as CurrencyCode })
                  }
                  className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)] cursor-pointer"
                >
                  {CURRENCY_OPTIONS.map(opt => (
                    <option key={opt.code} value={opt.code}>
                      {locale === 'en' ? opt.labelEn : opt.labelZh}
                    </option>
                  ))}
                </select>
              </div>
              {cur.code !== 'USDT' && cur.code !== 'USD' && (
                <div className="text-[11px] text-[var(--fg-3)] text-right">
                  {t('set_fx_live')}: 1 USDT ≈ {cur.symbol}
                  {cur.rate.toFixed(cur.code === 'JPY' ? 2 : 4)}
                  {cur.fx.updated_at ? ` · ${cur.fx.updated_at}` : ''}
                </div>
              )}
            </div>

            {/* Optional Modules & Privacy Default — Velvet Tactile Switches */}
            <div className="space-y-3 pt-3 border-t border-[var(--border)] text-[12.5px]">
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
              <p className="m-0 text-[11px] text-[var(--fg-3)]">{t('set_mod_note')}</p>
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
                  className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                />
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    value={goalInput}
                    onChange={e => setGoalInput(e.target.value)}
                    placeholder={t('set_goal_target')}
                    className="h-8 flex-1 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                  />
                  <button
                    type="button"
                    onClick={async () => {
                      await onUpdateSettings({
                        goal_enabled: true,
                        goal_label: goalLabel.trim(),
                        goal_target_usd: Number(goalInput) > 0 ? Number(goalInput) : null,
                      })
                      flashOk(t('set_save_ok'), 2500)
                    }}
                    className="h-8 px-3 rounded-lg text-[12px] font-semibold cursor-pointer"
                    style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
                  >
                    {t('save_config')}
                  </button>
                </div>
              </AnimatedCollapse>
            </div>

            {/* Demo Mode Toggle */}
            <div className="pt-3 border-t border-[var(--border)] flex items-center justify-between gap-3">
              <div>
                <div className="text-[12.5px] font-medium text-[var(--fg)]">{t('set_demo_title')}</div>
                <div className="text-[11.5px] text-[var(--fg-3)]">{t('set_demo_sub')}</div>
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

          {/* OpenAI-Compatible AI Configuration */}
          <div className="otl-card p-5 space-y-3">
            <div>
              <h3 className="m-0 text-[14px] font-semibold text-[var(--fg)]">{t('set_ai_title')}</h3>
              <p className="m-0 text-[11.5px] text-[var(--fg-3)] mt-0.5">{t('set_ai_sub')}</p>
            </div>
            <input
              type="text"
              value={aiUrl}
              onChange={e => setAiUrl(e.target.value)}
              placeholder="Base URL (e.g. https://api.openai.com/v1)"
              className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
            />
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <input
                type="password"
                value={aiKey}
                onChange={e => setAiKey(e.target.value)}
                placeholder={settings.ai_api_key_set ? 'API Key (configured)' : 'API Key'}
                className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
              />
              <button
                type="button"
                data-testid="fetch-ai-models-btn"
                disabled={fetchingModels || !aiUrl.trim()}
                onClick={async () => {
                  setFetchingModels(true)
                  setErrorMsg('')
                  try {
                    const res = await api.listAiModels(aiUrl.trim(), aiKey.trim())
                    setAiModels(res.models)
                    if (res.models.length > 0 && !aiModel.trim()) {
                      setAiModel(res.models[0])
                    }
                    flashOk(
                      locale === 'en'
                        ? `Loaded ${res.count} available models`
                        : `已获取 ${res.count} 个可用模型`,
                      3000,
                    )
                  } catch (err) {
                    setErrorMsg(err instanceof Error ? err.message : 'Failed to fetch models')
                  } finally {
                    setFetchingModels(false)
                  }
                }}
                className="inline-flex items-center justify-center gap-1.5 h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] disabled:opacity-50 cursor-pointer whitespace-nowrap"
              >
                <RefreshCw size={12} className={fetchingModels ? 'animate-spin' : ''} />
                <span>{locale === 'en' ? 'Fetch Models' : '获取模型列表'}</span>
              </button>
            </div>
            {aiModels.length > 0 ? (
              <div className="space-y-1.5">
                <select
                  aria-label="AI Model"
                  data-testid="ai-model-select"
                  value={aiModels.includes(aiModel) ? aiModel : '__custom__'}
                  onChange={e => {
                    if (e.target.value !== '__custom__') setAiModel(e.target.value)
                  }}
                  className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)] cursor-pointer"
                >
                  {aiModels.map(m => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                  {!aiModels.includes(aiModel) && (
                    <option value="__custom__">
                      {aiModel || (locale === 'en' ? 'Custom model…' : '自定义模型…')}
                    </option>
                  )}
                </select>
                <input
                  type="text"
                  value={aiModel}
                  onChange={e => setAiModel(e.target.value)}
                  placeholder="Model ID (e.g. gpt-4o-mini)"
                  className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                />
              </div>
            ) : (
              <input
                type="text"
                value={aiModel}
                onChange={e => setAiModel(e.target.value)}
                placeholder="Model (e.g. gpt-4o-mini / deepseek-chat)"
                className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
              />
            )}
            <div className="flex justify-end">
              <button
                type="button"
                onClick={async () => {
                  await onUpdateSettings({ ai_base_url: aiUrl, ai_model: aiModel, ai_api_key: aiKey })
                  setAiKey('')
                  flashOk(t('set_save_ok'), 2500)
                }}
                className="h-8 px-3 rounded-lg text-[12px] font-semibold cursor-pointer"
                style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
              >
                {t('save_config')}
              </button>
            </div>
          </div>

          {/* Multi-Channel Notifications (Webhook / Telegram / Bark) */}
          <div data-testid="notifications-card" className="otl-card p-5 space-y-3.5">
            <div>
              <h3 className="m-0 text-[14px] font-semibold text-[var(--fg)]">{t('set_notify_title')}</h3>
              <p className="m-0 text-[11.5px] text-[var(--fg-3)] mt-0.5">{t('set_notify_sub')}</p>
            </div>

            {/* Beginner Step-by-Step Bark Guide */}
            <div
              data-testid="bark-onboarding-guide"
              className="otl-surface-2 rounded-xl p-3.5 space-y-1.5 text-[11.5px] text-[var(--fg-2)] border border-[var(--border)]"
            >
              <div className="font-semibold text-[12px] text-[var(--fg)] inline-flex items-center gap-1.5">
                <Smartphone size={13} className="text-[var(--accent)] shrink-0" />
                <span>
                  {isZh
                    ? 'iPhone / iPad 新用户如何获取 Bark 推送？'
                    : 'How to set up iOS Bark Push Notifications'}
                </span>
              </div>
              <ol className="m-0 pl-4 space-y-0.5 list-decimal leading-relaxed">
                <li>
                  {isZh
                    ? '在苹果 App Store 搜索并安装免费开源应用「Bark」；'
                    : 'Install the free open-source "Bark" app from the iOS App Store;'}
                </li>
                <li>
                  {isZh
                    ? '打开 Bark App，点击首页第一条示例链接右侧的「复制」，直接整条粘贴到下方右侧「Bark 链接或专属 Key」输入框即可（系统会自动识别拆出服务器与 Key）；'
                    : 'Open Bark, tap "Copy" next to the sample URL, and paste the full URL (or device key) into the right-hand Bark box below;'}
                </li>
                <li>
                  {isZh
                    ? '直接点击「测试推送」即可自动保存并听手机响铃！开启后将在：① 盯盘带单员空出名额、② 带单员新开/平仓、③ 个人账户新平仓入账、④ 交易所后台同步异常报错 时实时推送。'
                    : 'Click "Test Push" directly to auto-save & verify! Alerts fire on: open copy slots, lead-trader fills, closed trades, and background sync errors.'}
                </li>
              </ol>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              <div className="space-y-1">
                <label className="block text-[11px] font-medium text-[var(--fg-2)]">
                  {isZh ? 'Bark 服务器地址（默认官方即可）' : 'Bark Server URL (Default OK)'}
                </label>
                <input
                  type="text"
                  data-testid="bark-server-url-input"
                  value={barkServerUrl}
                  onChange={e => setBarkServerUrl(e.target.value)}
                  placeholder="https://api.day.app"
                  className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                />
              </div>
              <div className="space-y-1">
                <label className="block text-[11px] font-medium text-[var(--fg)]">
                  {isZh ? 'Bark 链接或专属 Key（直接粘贴处）' : 'Bark App URL or Push Key (Paste here)'}
                </label>
                <input
                  type="password"
                  data-testid="bark-push-key-input"
                  value={barkKey}
                  onChange={e => setBarkKey(e.target.value)}
                  placeholder={
                    settings.notifications?.bark_push_key_set
                      ? isZh
                        ? '已配置（粘贴新链接或 Key 可覆盖）'
                        : 'Configured (paste new URL/Key to replace)'
                      : isZh
                        ? '粘贴 Bark 完整链接或设备 Key'
                        : 'Paste full Bark URL or Push Key'
                  }
                  className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                />
              </div>
            </div>

            <div className="space-y-2 pt-1 border-t border-[var(--border)]">
              <div className="text-[11.5px] font-medium text-[var(--fg-3)]">
                {isZh ? '其他渠道（可选：Telegram 机器人 / Webhook）' : 'Other Channels (Optional: Telegram / Webhook)'}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <input
                  type="password"
                  value={tgToken}
                  onChange={e => setTgToken(e.target.value)}
                  placeholder={
                    settings.notifications?.telegram_bot_token_set
                      ? isZh
                        ? 'Telegram Token（已保存）'
                        : 'Bot Token (configured)'
                      : 'Telegram Bot Token'
                  }
                  className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                />
                <input
                  type="text"
                  value={tgChat}
                  onChange={e => setTgChat(e.target.value)}
                  placeholder="Telegram Chat ID"
                  className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
                />
              </div>
              <input
                type="text"
                value={webhookUrl}
                onChange={e => setWebhookUrl(e.target.value)}
                placeholder="Webhook URL (https://...)"
                className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
              />
            </div>

            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                data-testid="test-notify-btn"
                disabled={testingNotify}
                onClick={async () => {
                  setTestingNotify(true)
                  setErrorMsg('')
                  try {
                    const pendingNotifications = {
                      webhook_url: webhookUrl.trim(),
                      telegram_bot_token: tgToken.trim(),
                      telegram_chat_id: tgChat.trim(),
                      bark_server_url: barkServerUrl.trim() || 'https://api.day.app',
                      bark_push_key: barkKey.trim(),
                    }
                    const res = await api.testNotify({ notifications: pendingNotifications })
                    if (res.settings) {
                      setBarkServerUrl(res.settings.notifications?.bark_server_url || 'https://api.day.app')
                      await onUpdateSettings({})
                    }
                    setTgToken('')
                    setBarkKey('')
                    const sentList = (res.sent || []).join(', ')
                    flashOk(
                      isZh
                        ? `配置已自动保存，测试推送已送达（${sentList}）`
                        : `Saved & test alert delivered (${sentList})`,
                    )
                  } catch (err) {
                    setErrorMsg(err instanceof Error ? err.message : 'Test notification failed')
                  } finally {
                    setTestingNotify(false)
                  }
                }}
                className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] disabled:opacity-50 cursor-pointer"
              >
                <Bell size={12} />
                <span>{testingNotify ? (isZh ? '发送中…' : 'Sending…') : isZh ? '测试推送' : 'Test Push'}</span>
              </button>
              <button
                type="button"
                onClick={async () => {
                  await onUpdateSettings({
                    notifications: {
                      webhook_url: webhookUrl,
                      telegram_bot_token: tgToken,
                      telegram_chat_id: tgChat,
                      bark_server_url: barkServerUrl.trim() || 'https://api.day.app',
                      bark_push_key: barkKey,
                    },
                  })
                  setTgToken('')
                  setBarkKey('')
                  flashOk(t('set_save_ok'), 2500)
                }}
                className="h-8 px-3 rounded-lg text-[12px] font-semibold cursor-pointer"
                style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
              >
                {t('save_config')}
              </button>
            </div>
          </div>

          {/* Optional Outbound Network Proxy (for Mainland China NAS reaching Binance) */}
          <div data-testid="outbound-proxy-card" className="otl-card p-5 space-y-2.5">
            <div>
              <h3 className="m-0 text-[14px] font-semibold text-[var(--fg)]">
                {isZh ? '网络代理配置（国内 NAS 连币安可选）' : 'Outbound Network Proxy (Optional)'}
              </h3>
              <p className="m-0 text-[11.5px] text-[var(--fg-3)] mt-0.5">
                {isZh
                  ? '若 NAS 直连币安超时，可填入局域网代理地址（如 http://192.168.x.x:7890；注意 Docker 容器内请填宿主机/软路由局域网 IP，勿填 127.0.0.1），留空则直连。'
                  : 'Optional HTTP/HTTPS/SOCKS5 proxy for exchange APIs & public copy radar. In Docker on NAS, use LAN IP rather than 127.0.0.1.'}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="text"
                data-testid="outbound-proxy-input"
                value={outboundProxyUrl}
                onChange={e => setOutboundProxyUrl(e.target.value)}
                placeholder={
                  isZh
                    ? '例如 http://192.168.x.x:7890（NAS 容器勿填 127.0.0.1，留空直连）'
                    : 'e.g. http://192.168.x.x:7890 (leave blank for direct)'
                }
                className="h-8 flex-1 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
              />
              <button
                type="button"
                data-testid="save-proxy-btn"
                onClick={async () => {
                  try {
                    await onUpdateSettings({ outbound_proxy_url: outboundProxyUrl.trim() })
                    flashOk(
                      isZh
                        ? outboundProxyUrl.trim()
                          ? '网络代理已生效'
                          : '已清除网络代理，恢复直连模式'
                        : 'Proxy settings saved',
                      2500,
                    )
                  } catch (err) {
                    setErrorMsg(err instanceof Error ? err.message : 'Invalid proxy URL')
                  }
                }}
                className="h-8 px-3 rounded-lg text-[12px] font-semibold cursor-pointer whitespace-nowrap"
                style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
              >
                {t('save_config')}
              </button>
            </div>
          </div>
        </section>
      </div>

      {/* Guided Velvet Floating Modal for Adding / Updating Exchange Credentials */}
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
              className="inline-flex items-center gap-1 text-[11.5px] font-medium text-[var(--accent)] hover:underline"
            >
              <span>{isZh ? 'API 文档' : 'API Docs'}</span>
              <ExternalLink size={11} />
            </a>
          ) : undefined
        }
      >
        {selectedAdapter && (
          <div className={`p-5 space-y-3.5 text-[12.5px] ${modalShake ? 'otl-shake' : ''}`}>
            {/* 3-Stage Live Read-Only Connection Ceremony Stepper */}
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
                  className="rounded-lg border px-2.5 py-2 flex items-center gap-2 text-[11px] font-medium transition-all duration-200"
                  style={{
                    background: st.done
                      ? 'rgba(16, 185, 129, 0.1)'
                      : st.active
                        ? 'var(--accent-soft)'
                        : 'var(--surface-2)',
                    borderColor: st.done
                      ? 'rgba(16, 185, 129, 0.34)'
                      : st.active
                        ? 'var(--accent)'
                        : 'var(--border)',
                    color: st.done ? '#10b981' : st.active ? 'var(--fg)' : 'var(--fg-3)',
                  }}
                >
                  <span
                    className="h-4 w-4 rounded-full inline-flex items-center justify-center text-[10px] font-bold shrink-0"
                    style={{
                      background: st.done
                        ? '#10b981'
                        : st.active
                          ? 'var(--accent)'
                          : 'var(--track)',
                      color: st.done || st.active ? '#ffffff' : 'var(--fg-3)',
                    }}
                  >
                    {st.done ? (
                      <Check size={10} strokeWidth={3} />
                    ) : st.spinning ? (
                      <RefreshCw size={9} className="animate-spin" />
                    ) : (
                      st.num
                    )}
                  </span>
                  <span className="truncate">{st.title}</span>
                </div>
              ))}
            </div>

            <div className="otl-surface-2 rounded-lg px-3.5 py-2.5 text-[11.5px] text-[var(--fg-2)] space-y-1">
              <div className="font-semibold text-[var(--fg)] flex items-center gap-1.5">
                <ShieldCheck size={13} className="text-emerald-500 shrink-0" />
                <span>{isZh ? '安全接入指引（先验活、再加密入库）' : 'Read-Only Safety Checklist'}</span>
              </div>
              <p className="m-0 leading-relaxed">
                {isZh
                  ? '请在交易所后台创建仅勾选「只读 / 查询」权限的 API Key，切勿勾选下单或提币；若绑定 IP 白名单，请填入当前运行设备的公网出口 IP。'
                  : 'Create an API key with Read-Only permissions only (never enable trading or withdrawals). Whitelist this host public IP if IP restriction is enabled.'}
              </p>
            </div>

            <AnimatedCollapse open={Boolean(modalError)}>
              <div className="otl-status-err rounded-lg px-3 py-2 text-[12px] flex items-start gap-2">
                <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                <span>{modalError}</span>
              </div>
            </AnimatedCollapse>

            <div className="space-y-2.5">
              <label className="block space-y-1">
                <span className="text-[11.5px] font-medium text-[var(--fg-2)]">
                  {isZh ? '账户备注名称（选填）' : 'Account Label (Optional)'}
                </span>
                <input
                  type="text"
                  data-testid="exchange-label-input"
                  value={label}
                  onChange={e => setLabel(e.target.value)}
                  placeholder={isZh ? `如：${selectedAdapter.display_name_zh || selectedAdapter.display_name} 主账户` : `e.g. ${selectedAdapter.display_name} Main`}
                  className="h-9 w-full px-3 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12.5px] text-[var(--fg)]"
                />
              </label>

              <label className="block space-y-1">
                <span className="text-[11.5px] font-medium text-[var(--fg-2)]">
                  {isZh ? '只读 API Key *' : 'Read-Only API Key *'}
                </span>
                <input
                  type="text"
                  data-testid="exchange-key-input"
                  autoComplete="off"
                  value={apiKey}
                  onChange={e => setApiKey(e.target.value)}
                  placeholder={isZh ? '粘贴交易所生成的 API Key' : 'Paste Read-Only API Key'}
                  className="h-9 w-full px-3 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12.5px] text-[var(--fg)] font-mono"
                />
              </label>

              <label className="block space-y-1">
                <span className="text-[11.5px] font-medium text-[var(--fg-2)]">
                  {isZh ? 'API Secret 密钥 *' : 'API Secret *'}
                </span>
                <input
                  type="password"
                  data-testid="exchange-secret-input"
                  autoComplete="new-password"
                  value={apiSecret}
                  onChange={e => setApiSecret(e.target.value)}
                  placeholder={isZh ? '粘贴对应的 API Secret' : 'Paste API Secret'}
                  className="h-9 w-full px-3 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12.5px] text-[var(--fg)] font-mono"
                />
              </label>

              {selectedAdapter.requires_passphrase && (
                <label className="block space-y-1">
                  <span className="text-[11.5px] font-medium text-[var(--fg-2)]">
                    {isZh ? 'API 口令 (Passphrase) *' : 'API Passphrase *'}
                  </span>
                  <input
                    type="password"
                    data-testid="exchange-passphrase-input"
                    autoComplete="new-password"
                    value={passphrase}
                    onChange={e => setPassphrase(e.target.value)}
                    placeholder={isZh ? '创建该 API Key 时设置的口令' : 'Passphrase set when creating this API key'}
                    className="h-9 w-full px-3 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12.5px] text-[var(--fg)]"
                  />
                </label>
              )}
            </div>

            <AnimatedCollapse open={busyVenue === selectedAdapter.venue_id || submitStep === 'done'}>
              <div
                className={`rounded-lg px-3 py-2.5 text-[11.5px] space-y-2 ${
                  submitStep === 'done'
                    ? 'otl-status-ok'
                    : 'border border-[var(--border)] bg-[var(--surface-2)] text-[var(--fg-2)]'
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
                className="h-8 px-3.5 rounded-lg border border-[var(--border)] text-[12px] text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer disabled:opacity-50"
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                data-testid="save-exchange-btn"
                disabled={busyVenue === selectedAdapter.venue_id || !formReady}
                onClick={() => void handleAddSubmit()}
                className="inline-flex items-center gap-1.5 h-8 px-4 rounded-lg text-[12px] font-semibold cursor-pointer disabled:opacity-50"
                style={{
                  background: submitStep === 'done' ? '#10b981' : 'var(--accent)',
                  color: submitStep === 'done' ? '#ffffff' : 'var(--accent-fg)',
                }}
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
          <div className="p-5 space-y-4 text-[12.5px]">
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
                    className="w-full text-left rounded-xl border p-3.5 transition-colors cursor-pointer space-y-1"
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
                          color: '#ffffff',
                        }}
                      >
                        {keepHistoryOnDelete && <Check size={10} strokeWidth={3} />}
                      </span>
                    </div>
                    <p className="m-0 text-[11.5px] text-[var(--fg-2)] leading-relaxed">
                      {isZh
                        ? '销毁本地加密密钥并停止后台轮询；已同步的平仓交割、充提本金与闭环对账记录继续保留在本地（标为「已封存」）。'
                        : 'Deletes the encrypted API key and stops polling; synced trades, principal, and reconciliation stay archived locally.'}
                    </p>
                  </button>

                  <button
                    type="button"
                    data-testid="delete-opt-purge"
                    onClick={() => setKeepHistoryOnDelete(false)}
                    className="w-full text-left rounded-xl border p-3.5 transition-colors cursor-pointer space-y-1"
                    style={{
                      borderColor: !keepHistoryOnDelete ? 'rgba(239, 68, 68, 0.55)' : 'var(--border)',
                      background: !keepHistoryOnDelete ? 'rgba(239, 68, 68, 0.08)' : 'var(--surface-2)',
                    }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-[var(--fg)]">
                        {isZh ? '彻底删除 API 及全部本地数据' : 'Delete API Key & Purge All Local Data'}
                      </span>
                      <span
                        className="h-4 w-4 rounded-full border inline-flex items-center justify-center shrink-0"
                        style={{
                          borderColor: !keepHistoryOnDelete ? '#ef4444' : 'var(--border-strong)',
                          background: !keepHistoryOnDelete ? '#ef4444' : 'transparent',
                          color: '#ffffff',
                        }}
                      >
                        {!keepHistoryOnDelete && <Check size={10} strokeWidth={3} />}
                      </span>
                    </div>
                    <p className="m-0 text-[11.5px] text-[var(--fg-2)] leading-relaxed">
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
                className="h-8 px-3.5 rounded-lg border border-[var(--border)] text-[12px] text-[var(--fg-2)] cursor-pointer"
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
                className={`h-8 px-3.5 rounded-lg text-[12px] font-semibold cursor-pointer ${
                  deleteMode === 'choice' && keepHistoryOnDelete ? '' : 'otl-status-err'
                }`}
                style={
                  deleteMode === 'choice' && keepHistoryOnDelete
                    ? { background: 'var(--accent)', color: 'var(--accent-fg)' }
                    : undefined
                }
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
