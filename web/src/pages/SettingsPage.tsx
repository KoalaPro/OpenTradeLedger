import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  Check,
  ExternalLink,
  Plus,
  RefreshCw,
  ShieldCheck,
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
import { api, ApiError, type ExchangeUpsertPayload } from '../lib/api'
import { CURRENCY_OPTIONS, useCurrency } from '../lib/currency'
import type {
  AuthStatusPayload,
  CurrencyCode,
  DiscoveredSubaccount,
  ExchangeAccountInstance,
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
    body: ExchangeUpsertPayload,
    onStep?: (step: 'verifying' | 'syncing' | 'done') => void,
  ) => Promise<{
    failedSubaccounts?: Array<{ address: string; name: string; error?: string; error_zh?: string }>
  } | void>
  onDeleteExchange: (venueId: string, keepData?: boolean, expectedRevision?: string) => Promise<void>
  onTestExchange: (venueId: string, isRetry?: boolean) => Promise<void>
  onToggleDemo: (enabled: boolean) => Promise<void>
}

const EVM_ADDR_RE = /^0x[0-9a-fA-F]{40}$/

function normalizeAdapterAccounts(ad: ExchangeAdapterInfo): ExchangeAccountInstance[] {
  if (Array.isArray(ad.accounts) && ad.accounts.length > 0) {
    return ad.accounts
  }
  if (ad.configured || ad.has_archived_data) {
    return [
      {
        account_id: ad.venue_id,
        adapter_id: ad.venue_id,
        configured: ad.configured,
        has_archived_data: ad.has_archived_data,
        masked_uid: ad.credential?.masked_uid,
        account_role: ad.credential?.account_role,
        credential: ad.credential,
        poll_state: ad.poll_state,
        health: ad.health,
      },
    ]
  }
  return []
}

function getAccountVisualStatus(
  acct: ExchangeAccountInstance,
): 'not_configured' | 'archived' | 'disabled' | 'connected' | 'error' | 'unverified' {
  if (!acct.configured) return acct.has_archived_data ? 'archived' : 'not_configured'
  if (acct.credential && acct.credential.enabled === false) return 'disabled'
  const st = acct.health?.status
  if (st === 'error') return 'error'
  if (st === 'connected') return 'connected'
  return 'unverified'
}

function formatRoleLabel(role: string | undefined, isZh: boolean): string {
  const r = (role || '').toLowerCase()
  if (r === 'subaccount') return isZh ? '子账户' : 'Sub'
  if (r === 'master') return isZh ? '主账户' : 'Main'
  if (r === 'wallet') return isZh ? '链上钱包' : 'Wallet'
  return ''
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
  const [editingAccountId, setEditingAccountId] = useState<string>('')
  const [isCreateNew, setIsCreateNew] = useState<boolean>(false)
  const [editingWasConfigured, setEditingWasConfigured] = useState<boolean>(false)
  const [formRevision, setFormRevision] = useState<string>()
  const [deletingTarget, setDeletingTarget] = useState<{
    adapter: ExchangeAdapterInfo
    accountId: string
    accountLabel: string
  } | null>(null)
  const [deleteMode, setDeleteMode] = useState<'choice' | 'purge_archived'>('choice')
  const [keepHistoryOnDelete, setKeepHistoryOnDelete] = useState<boolean>(true)
  const [modalError, setModalError] = useState('')
  const [modalShake, setModalShake] = useState(false)
  const [submitStep, setSubmitStep] = useState<'' | 'verifying' | 'syncing' | 'done'>('')
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [walletAddress, setWalletAddress] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const [label, setLabel] = useState('')
  const [discoveredSubs, setDiscoveredSubs] = useState<DiscoveredSubaccount[]>([])
  const [selectedSubs, setSelectedSubs] = useState<Record<string, boolean>>({})
  const [probingSubs, setProbingSubs] = useState(false)
  const [subsProbedOnce, setSubsProbedOnce] = useState(false)
  const [busyVenue, setBusyVenue] = useState('')
  const statusTimerRef = useRef<number | null>(null)
  const shakeTimerRef = useRef<number | null>(null)
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
  const isWalletMode = selectedAdapter?.auth_mode === 'wallet_address'
  const firstLiveConnection =
    (demoMode || previewFallback) &&
    !adapters.some(adapter => normalizeAdapterAccounts(adapter).some(a => a.configured))

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
      if (shakeTimerRef.current !== null) window.clearTimeout(shakeTimerRef.current)
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

  const openAccountForm = (
    ad: ExchangeAdapterInfo,
    opts?: { account?: ExchangeAccountInstance; createNew?: boolean },
  ) => {
    const acct = opts?.account
    const creatingNew = Boolean(opts?.createNew)
    const existingCount = normalizeAdapterAccounts(ad).length
    setAddingVenue(ad.venue_id)
    setIsCreateNew(creatingNew)
    setEditingAccountId(creatingNew ? '' : acct?.account_id || ad.venue_id)
    setEditingWasConfigured(Boolean(!creatingNew && acct?.configured))
    setFormRevision(creatingNew ? 'absent' : acct?.configured ? acct.credential?.revision : 'absent')
    setModalError('')
    if (creatingNew) {
      const nextNum = existingCount + 1
      setLabel(
        isZh
          ? `${ad.display_name_zh || ad.display_name} 账号${nextNum}`
          : `${ad.display_name} #${nextNum}`,
      )
    } else {
      setLabel(acct?.credential?.label || acct?.label || ad.display_name)
    }
    setApiKey('')
    setApiSecret('')
    setWalletAddress('')
    setPassphrase('')
    setDiscoveredSubs([])
    setSelectedSubs({})
    setSubsProbedOnce(false)
  }

  const handleProbeSubaccounts = async () => {
    if (!selectedAdapter || !EVM_ADDR_RE.test(walletAddress.trim())) return
    setProbingSubs(true)
    setModalError('')
    try {
      const res = await api.verifyExchange({
        venue_id: selectedAdapter.venue_id,
        ...(isCreateNew ? { create_new: true } : { account_id: editingAccountId || selectedAdapter.venue_id }),
        label: label.trim(),
        api_key: walletAddress.trim(),
        api_secret: '',
        wallet_address: walletAddress.trim(),
        expected_revision: formRevision,
      })
      const subs = res.subaccounts || []
      setDiscoveredSubs(subs)
      const nextChecked: Record<string, boolean> = {}
      for (const s of subs) {
        nextChecked[s.address] = true
      }
      setSelectedSubs(nextChecked)
      setSubsProbedOnce(true)
    } catch (err) {
      setModalError(err instanceof Error ? err.message : isZh ? '子账户检测失败' : 'Sub-account check failed')
    } finally {
      setProbingSubs(false)
    }
  }

  const formReady = Boolean(
    selectedAdapter &&
      (isWalletMode
        ? EVM_ADDR_RE.test(walletAddress.trim())
        : apiKey.trim() &&
          apiSecret.trim() &&
          (!selectedAdapter.requires_passphrase || passphrase.trim())),
  )

  const handleAddSubmit = async () => {
    if (!addingVenue || !selectedAdapter || !formReady) return
    const busyKey = editingAccountId || addingVenue
    setBusyVenue(busyKey)
    setSubmitStep('verifying')
    setStatusMsg('')
    setModalError('')
    setErrorMsg('')
    try {
      const chosenSubs = isWalletMode
        ? discoveredSubs
            .filter(s => selectedSubs[s.address])
            .map(s => ({ name: s.name, address: s.address }))
        : undefined
      const upsertOut = await onUpsertExchange(
        {
          venue_id: addingVenue,
          ...(isCreateNew
            ? { create_new: true }
            : editingAccountId && editingAccountId !== addingVenue
              ? { account_id: editingAccountId }
              : {}),
          label: label.trim(),
          api_key: isWalletMode ? walletAddress.trim() : apiKey.trim(),
          api_secret: isWalletMode ? '' : apiSecret.trim(),
          ...(isWalletMode ? { wallet_address: walletAddress.trim() } : {}),
          passphrase: passphrase.trim(),
          expected_revision: formRevision,
          ...(chosenSubs && chosenSubs.length > 0 ? { include_subaccounts: chosenSubs } : {}),
        },
        step => setSubmitStep(step),
      )
      setSubmitStep('done')
      const failedSubs = upsertOut && typeof upsertOut === 'object' ? upsertOut.failedSubaccounts : undefined
      if (failedSubs && failedSubs.length > 0) {
        const failedSummary = failedSubs
          .map(f => {
            const shortA = `${f.address.slice(0, 6)}...${f.address.slice(-4)}`
            return `${f.name} (${shortA})`
          })
          .join(', ')
        setErrorMsg(
          isZh
            ? `主钱包已保存，但 ${failedSubs.length} 个子账户同步失败未能导入：${failedSummary}。请待接口恢复后通过「添加账号」单独接入。`
            : `Main wallet saved, but ${failedSubs.length} sub-account(s) failed to sync and were not imported: ${failedSummary}. Retry via Add Account once upstream recovers.`,
        )
      } else {
        flashOk(
          isWalletMode
            ? isZh
              ? '链上地址验证通过，已完成本地保存与账单同步'
              : 'Wallet address verified & ledger synced successfully'
            : isZh
              ? 'API 验证通过，已完成加密保存与数据同步'
              : 'API verified, encrypted & synced successfully',
          4000,
        )
      }
      const reduced =
        typeof window !== 'undefined' &&
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches
      const finishClose = () => {
        setAddingVenue('')
        setEditingAccountId('')
        setIsCreateNew(false)
        setApiKey('')
        setApiSecret('')
        setWalletAddress('')
        setPassphrase('')
        setLabel('')
        setDiscoveredSubs([])
        setSelectedSubs({})
        setSubsProbedOnce(false)
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
      const updateHint = editingWasConfigured
        ? isZh
          ? '原账户凭据和记录已保留。'
          : 'The previous credentials and records were kept.'
        : isZh
          ? '未保存新账户。'
          : 'The new account was not saved.'
      const editConflict = err instanceof ApiError && err.errorCode === 'CREDENTIAL_EDIT_CONFLICT'
      const identityConflict =
        err instanceof ApiError &&
        (err.errorCode === 'DUPLICATE_ACCOUNT_UID' ||
          err.errorCode === 'ARCHIVED_ACCOUNT_UID_EXISTS' ||
          err.errorCode === 'DUPLICATE_ACCOUNT_KEY' ||
          err.errorCode === 'ACCOUNT_UID_MISMATCH' ||
          err.errorCode === 'ACCOUNT_UID_UNAVAILABLE')
      const historyFailed =
        rolledBack && err.status !== 409 && err.errorCode !== 'LOCAL_SAVE_FAILED'
      const phase = historyFailed ? (isZh ? '历史同步失败。' : 'Historical sync failed.') : ''
      const message =
        rolledBack && !editConflict && !identityConflict
          ? [phase, detail, updateHint].filter(Boolean).join(' ')
          : detail
      setModalError(message)
      setErrorMsg(message)
      setSubmitStep('')
      setBusyVenue('')
      setModalShake(true)
      if (shakeTimerRef.current !== null) window.clearTimeout(shakeTimerRef.current)
      shakeTimerRef.current = window.setTimeout(() => setModalShake(false), 320)
    }
  }

  const handleTest = async (accountId: string, displayLabel: string, isRetry = false) => {
    setBusyVenue(accountId)
    setErrorMsg('')
    try {
      await onTestExchange(accountId, isRetry)
      flashOk(
        isRetry
          ? isZh
            ? `${displayLabel} 连接与数据同步已恢复正常`
            : `${displayLabel} connection & sync restored`
          : isZh
            ? `${displayLabel} 只读连接验证正常`
            : `${displayLabel} read-only connection verified`,
      )
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Connection test failed')
    } finally {
      setBusyVenue('')
    }
  }

  const modalBusy = Boolean(busyVenue && (busyVenue === addingVenue || busyVenue === editingAccountId))
  const ceremonyStage: 'credentials' | 'verifying' | 'syncing' | 'done' = submitStep || 'credentials'

  const renderStatusPill = (acctId: string, vStatus: ReturnType<typeof getAccountVisualStatus>) => (
    <>
      {vStatus === 'connected' && (
        <span
          data-testid={`adapter-status-${acctId}`}
          className="inline-flex items-center gap-1.5 text-[12px] text-[var(--ok)]"
        >
          <span className="otl-venue-dot" style={{ background: 'var(--ok)' }} />
          {t('set_connected')}
        </span>
      )}
      {vStatus === 'error' && (
        <span
          data-testid={`adapter-status-${acctId}`}
          className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-[var(--err)]"
        >
          <span className="otl-venue-dot" style={{ background: 'var(--err)' }} />
          <span>{isZh ? '连接报错' : 'API Error'}</span>
        </span>
      )}
      {vStatus === 'unverified' && (
        <span
          data-testid={`adapter-status-${acctId}`}
          className="text-[12px] font-medium text-[var(--warn)]"
        >
          · {isZh ? '待验证' : 'Unverified'}
        </span>
      )}
      {vStatus === 'disabled' && (
        <span data-testid={`adapter-status-${acctId}`} className="text-[12px] text-[var(--fg-3)]">
          · {isZh ? '已停用' : 'Disabled'}
        </span>
      )}
      {vStatus === 'archived' && (
        <span data-testid={`adapter-status-${acctId}`} className="text-[12px] text-[var(--fg-3)]">
          · {isZh ? '已封存' : 'Archived'}
        </span>
      )}
    </>
  )

  const renderAccountActions = (
    ad: ExchangeAdapterInfo,
    acct: ExchangeAccountInstance,
    vStatus: ReturnType<typeof getAccountVisualStatus>,
    acctDisplayLabel: string,
  ) => {
    const aid = acct.account_id
    const adIsWallet = ad.auth_mode === 'wallet_address'
    if (acct.configured) {
      return (
        <>
          <button
            type="button"
            data-testid={`test-adapter-${aid}`}
            disabled={busyVenue === aid}
            onClick={() => void handleTest(aid, acctDisplayLabel, vStatus === 'error')}
            className="otl-btn"
          >
            {busyVenue === aid
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
            data-testid={`edit-adapter-${aid}`}
            onClick={() => openAccountForm(ad, { account: acct, createNew: false })}
            title={
              adIsWallet
                ? isZh
                  ? '修改备注或更新钱包地址'
                  : 'Update wallet address or label'
                : isZh
                  ? '修改或更新 API 密钥'
                  : 'Update API credentials'
            }
            className="otl-btn"
          >
            {adIsWallet ? (isZh ? '改地址' : 'Edit') : isZh ? '改密钥' : 'Edit'}
          </button>

          <button
            type="button"
            data-testid={`remove-adapter-${aid}`}
            onClick={() => {
              setFormRevision(acct.credential?.revision)
              setDeleteMode('choice')
              setKeepHistoryOnDelete(true)
              setDeletingTarget({
                adapter: ad,
                accountId: aid,
                accountLabel: acctDisplayLabel,
              })
            }}
            className="otl-btn text-[var(--fg-3)] hover:text-[var(--err)]"
            aria-label={t('set_remove')}
          >
            {t('set_remove')}
          </button>
        </>
      )
    }

    if (vStatus === 'archived') {
      return (
        <>
          <button
            type="button"
            data-testid={`configure-adapter-${aid}`}
            onClick={() => openAccountForm(ad, { account: acct, createNew: false })}
            className="otl-btn"
          >
            {adIsWallet ? (isZh ? '重新绑定地址' : 'Rebind') : isZh ? '重新绑定 API' : 'Rebind API'}
          </button>
          <button
            type="button"
            data-testid={`purge-archived-${aid}`}
            title={isZh ? '彻底清空该账户封存的本地历史数据' : 'Permanently clear archived local data'}
            onClick={() => {
              setFormRevision('absent')
              setDeleteMode('purge_archived')
              setKeepHistoryOnDelete(false)
              setDeletingTarget({
                adapter: ad,
                accountId: aid,
                accountLabel: acctDisplayLabel,
              })
            }}
            className="otl-btn text-[var(--fg-3)] hover:text-[var(--err)]"
            aria-label={isZh ? '清空封存数据' : 'Purge archived data'}
          >
            {isZh ? '清空封存' : 'Purge'}
          </button>
        </>
      )
    }

    return null
  }

  return (
    <div data-testid="settings-page" className="otl-stagger">
      <FloatingToastViewport
        statusMsg={statusMsg}
        errorMsg={errorMsg}
        onClearStatus={() => setStatusMsg('')}
        onClearError={() => setErrorMsg('')}
      />

      {/* Chapter 1: 7:5 Balanced Top Split — Exchange Adapters × Display Preferences */}
      <div className="grid grid-cols-1 xl:grid-cols-12 border-b border-[var(--border)]">
        {/* Left 7 Columns: Read-Only Exchange Adapters (Multi-Account Capable) */}
        <section className="xl:col-span-7 py-6 px-5 xl:pl-5 xl:pr-8 xl:border-r border-[var(--border)]">
          <div className="pb-4 border-b border-[var(--border)]">
            <h2 className="t-h2">{t('set_ex_title')}</h2>
            <p className="m-0 text-[12px] text-[var(--fg-3)] mt-0.5">{t('set_ex_sub')}</p>
          </div>

          <div className="divide-y divide-[var(--border)]">
            {adapters.map(ad => {
              const accounts = normalizeAdapterAccounts(ad)
              const adName = locale === 'en' ? ad.display_name : ad.display_name_zh || ad.display_name
              const passLabel =
                ad.auth_mode === 'wallet_address'
                  ? isZh
                    ? '公开 /info 零密钥只读'
                    : 'Public /info Keyless Read-Only'
                  : ad.venue_id === 'htx'
                    ? isZh
                      ? '只读签名查询 API'
                      : 'Read-Only Signed API'
                    : ad.requires_passphrase
                      ? t('set_passphrase_req')
                      : isZh
                        ? '纯 GET 只读签名 API'
                        : 'GET-Only Signed API'

              if (accounts.length === 0) {
                return (
                  <div
                    key={ad.venue_id}
                    data-testid={`adapter-card-${ad.venue_id}`}
                    className="py-3.5 space-y-2"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2.5 flex-wrap">
                          <VenueBadge strong venueId={ad.venue_id} name={adName} />
                        </div>
                        <div className="text-[12px] text-[var(--fg-3)] mt-0.5 truncate">
                          {`${ad.api_base_url} · ${passLabel}`}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <button
                          type="button"
                          data-testid={`configure-adapter-${ad.venue_id}`}
                          onClick={() => openAccountForm(ad, { createNew: false })}
                          className="otl-btn"
                        >
                          {t('set_add_btn')}
                        </button>
                      </div>
                    </div>
                  </div>
                )
              }

              if (accounts.length === 1) {
                const acct = accounts[0]
                const vStatus = getAccountVisualStatus(acct)
                const roleBadge = formatRoleLabel(acct.account_role || acct.credential?.account_role, isZh)
                const uidTag = acct.masked_uid || acct.credential?.masked_uid || ''
                const rawSingleLbl = (acct.credential?.label || acct.label || '').trim()
                const customLbl =
                  rawSingleLbl &&
                  rawSingleLbl.toLowerCase() !== ad.display_name.toLowerCase() &&
                  rawSingleLbl.toLowerCase() !== (ad.display_name_zh || '').toLowerCase() &&
                  rawSingleLbl.toLowerCase() !== ad.venue_id.toLowerCase()
                    ? rawSingleLbl
                    : ''
                const maskedKey = acct.configured && acct.credential ? acct.credential.masked_api_key : ''
                const errReason = isZh
                  ? acct.health?.error_reason_zh || acct.health?.error_reason || ''
                  : acct.health?.error_reason || acct.health?.error_reason_zh || ''
                const rawErr = acct.health?.raw_error || ''
                const acctDisplayLabel = customLbl ? `${adName} · ${customLbl}` : adName

                return (
                  <div
                    key={ad.venue_id}
                    data-testid={`adapter-card-${ad.venue_id}`}
                    className="py-3.5 space-y-2"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <VenueBadge strong venueId={ad.venue_id} name={adName} />
                          {roleBadge && (
                            <span className="px-1.5 py-0.5 rounded border border-[var(--border)] bg-[var(--surface-2)] text-[11px] text-[var(--fg-2)]">
                              {roleBadge}
                            </span>
                          )}
                          {renderStatusPill(acct.account_id, vStatus)}
                        </div>

                        <div className="text-[12px] text-[var(--fg-3)] mt-0.5 truncate">
                          {maskedKey ? (
                            <>
                              {customLbl && <span className="text-[var(--fg-2)]">{customLbl} · </span>}
                              {uidTag && ad.auth_mode !== 'wallet_address' && (
                                <span className="font-mono text-[var(--fg-2)]">UID {uidTag} · </span>
                              )}
                              <span className="font-mono text-[var(--fg-2)]">{maskedKey}</span> · {passLabel}
                            </>
                          ) : (
                            <>
                              {uidTag && <span className="font-mono text-[var(--fg-2)]">UID {uidTag} · </span>}
                              {isZh ? '本地历史记录已封存（凭证已解绑）' : 'Local history archived (unbound)'}
                            </>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-2 flex-wrap">
                        {renderAccountActions(ad, acct, vStatus, acctDisplayLabel)}
                        <button
                          type="button"
                          data-testid={`add-account-${ad.venue_id}`}
                          onClick={() => openAccountForm(ad, { createNew: true })}
                          title={
                            isZh
                              ? `添加同一交易所的子账户或另一独立账号`
                              : `Connect another ${ad.display_name} main or sub-account`
                          }
                          className="otl-btn inline-flex items-center gap-1"
                        >
                          <Plus size={12} />
                          <span>{isZh ? '加账号' : 'Add Account'}</span>
                        </button>
                      </div>
                    </div>

                    {vStatus === 'error' && errReason && (
                      <div
                        data-testid={`adapter-error-box-${acct.account_id}`}
                        className="pl-3 border-l-2 border-[var(--err)] text-[12px] text-[var(--err)] space-y-0.5"
                      >
                        <div className="font-medium">{errReason}</div>
                        {rawErr && !errReason.includes(rawErr) && (
                          <div className="opacity-85 font-mono break-all">{rawErr}</div>
                        )}
                      </div>
                    )}
                  </div>
                )
              }

              // Multi-account (>= 2 accounts on this exchange)
              return (
                <div
                  key={ad.venue_id}
                  data-testid={`adapter-card-${ad.venue_id}`}
                  className="py-3.5 space-y-2.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2.5">
                      <VenueBadge strong venueId={ad.venue_id} name={adName} />
                      <span className="text-[12px] text-[var(--fg-3)]">
                        {isZh ? `已管理 ${accounts.length} 个账号` : `${accounts.length} accounts`}
                      </span>
                    </div>
                    <button
                      type="button"
                      data-testid={`add-account-${ad.venue_id}`}
                      onClick={() => openAccountForm(ad, { createNew: true })}
                      className="otl-btn inline-flex items-center gap-1"
                    >
                      <Plus size={12} />
                      <span>{isZh ? '添加账号' : 'Add Account'}</span>
                    </button>
                  </div>

                  <div className="pl-3.5 border-l border-[var(--border)] divide-y divide-[var(--border)]">
                    {accounts.map((acct, idx) => {
                      const vStatus = getAccountVisualStatus(acct)
                      const roleBadge = formatRoleLabel(acct.account_role || acct.credential?.account_role, isZh)
                      const uidTag = acct.masked_uid || acct.credential?.masked_uid || ''
                      const credLbl = (acct.credential?.label || acct.label || '').trim()
                      const isDefaultCredLbl =
                        !credLbl ||
                        credLbl.toLowerCase() === ad.venue_id.toLowerCase() ||
                        credLbl.toLowerCase() === ad.display_name.toLowerCase() ||
                        credLbl.toLowerCase() === (ad.display_name_zh || '').toLowerCase()
                      const rawLbl = isDefaultCredLbl ? `${adName} #${idx + 1}` : credLbl
                      const maskedKey = acct.configured && acct.credential ? acct.credential.masked_api_key : ''
                      const errReason = isZh
                        ? acct.health?.error_reason_zh || acct.health?.error_reason || ''
                        : acct.health?.error_reason || acct.health?.error_reason_zh || ''
                      const rawErr = acct.health?.raw_error || ''
                      const fullDisplayLabel = rawLbl.toLowerCase().startsWith(adName.toLowerCase())
                        ? rawLbl
                        : `${adName} · ${rawLbl}`

                      return (
                        <div
                          key={acct.account_id}
                          data-testid={`account-row-${acct.account_id}`}
                          className="py-2.5 first:pt-1 last:pb-0.5 space-y-1.5"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="min-w-0">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="text-[13px] font-medium text-[var(--fg)]">{rawLbl}</span>
                                {roleBadge && (
                                  <span className="px-1.5 py-0.5 rounded border border-[var(--border)] bg-[var(--surface-2)] text-[11px] text-[var(--fg-2)]">
                                    {roleBadge}
                                  </span>
                                )}
                                {renderStatusPill(acct.account_id, vStatus)}
                              </div>
                              <div className="text-[12px] text-[var(--fg-3)] mt-0.5 truncate">
                                {maskedKey ? (
                                  <>
                                    {uidTag && ad.auth_mode !== 'wallet_address' && (
                                      <span className="font-mono text-[var(--fg-2)]">UID {uidTag} · </span>
                                    )}
                                    <span className="font-mono text-[var(--fg-2)]">{maskedKey}</span>
                                  </>
                                ) : (
                                  <>
                                    {uidTag && <span className="font-mono text-[var(--fg-2)]">UID {uidTag} · </span>}
                                    {isZh ? '本地历史记录已封存' : 'Local history archived'}
                                  </>
                                )}
                              </div>
                            </div>

                            <div className="flex items-center gap-2 flex-wrap">
                              {renderAccountActions(ad, acct, vStatus, fullDisplayLabel)}
                            </div>
                          </div>

                          {vStatus === 'error' && errReason && (
                            <div
                              data-testid={`adapter-error-box-${acct.account_id}`}
                              className="pl-3 border-l-2 border-[var(--err)] text-[12px] text-[var(--err)] space-y-0.5"
                            >
                              <div className="font-medium">{errReason}</div>
                              {rawErr && !errReason.includes(rawErr) && (
                                <div className="opacity-85 font-mono break-all">{rawErr}</div>
                              )}
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
        </section>

        {/* Right 5 Columns: Display Preferences & Module Toggles */}
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
              <div className="text-[12px] text-[var(--fg-3)] text-right">
                {t('set_fx_live')}: 1 USDT ≈ {cur.symbol}
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
      </div>

      {/* Chapter 2: Balanced 50:50 Two-Column System Configuration */}
      <div className="grid grid-cols-1 lg:grid-cols-2 items-start">
        <div className="px-5 lg:pl-5 lg:pr-8 lg:border-r border-[var(--border)] divide-y divide-[var(--border)]">
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
        </div>

        <div className="px-5 lg:pl-8 lg:pr-5 border-t lg:border-t-0 border-[var(--border)] divide-y divide-[var(--border)]">
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
        </div>
      </div>

      {/* Guided Modal for Adding / Updating Exchange Credentials or Keyless 0x Wallets */}
      <FloatingModal
        open={Boolean(selectedAdapter)}
        onClose={() => {
          if (!modalBusy) {
            setAddingVenue('')
            setEditingAccountId('')
            setIsCreateNew(false)
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
                name={
                  isZh
                    ? selectedAdapter.display_name_zh || selectedAdapter.display_name
                    : selectedAdapter.display_name
                }
              />
              <span>
                {editingWasConfigured
                  ? isWalletMode
                    ? isZh
                      ? '更新链上钱包地址'
                      : 'Update Wallet Address'
                    : isZh
                      ? '更新只读 API 密钥'
                      : 'Update Read-Only API Key'
                  : isCreateNew
                    ? isZh
                      ? '新增子账户 / 独立账号'
                      : 'Add Sub-Account or Separate Account'
                    : isWalletMode
                      ? isZh
                        ? '免密钥接入链上钱包'
                        : 'Connect Public 0x Wallet'
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
              className="inline-flex items-center gap-1 text-[12px] font-medium text-[var(--accent)] hover:underline"
            >
              <span>{isZh ? 'API 文档' : 'API Docs'}</span>
              <ExternalLink size={12} />
            </a>
          ) : undefined
        }
      >
        {selectedAdapter && (
          <div className={`p-6 space-y-3.5 text-[12.5px] ${modalShake ? 'otl-shake' : ''}`}>
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
                  title: isWalletMode
                    ? isZh
                      ? '填写公开地址'
                      : '0x Address'
                    : isZh
                      ? '填写只读密钥'
                      : 'Read-Only Keys',
                  done: formReady || ceremonyStage !== 'credentials',
                  active: ceremonyStage === 'credentials',
                  spinning: false,
                },
                {
                  id: 'step2',
                  num: '2',
                  title: isZh ? '身份与只读验活' : 'Identity Check',
                  done: ceremonyStage === 'syncing' || ceremonyStage === 'done',
                  active: ceremonyStage === 'verifying',
                  spinning: ceremonyStage === 'verifying',
                },
                {
                  id: 'step3',
                  num: '3',
                  title: isZh ? '独立分区同步' : 'Partition Sync',
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
                <span>
                  {isWalletMode
                    ? isZh
                      ? '免私钥零风险接入（支持自动识别链上子账户）'
                      : 'Keyless Public-Address Read-Only Mode'
                    : isZh
                      ? '安全接入指引（自动识别 UID 与主/子账户，防重防串账）'
                      : 'Read-Only Safety & Multi-Account Isolation'}
                </span>
              </div>
              <p className="m-0 leading-relaxed">
                {isWalletMode
                  ? isZh
                    ? '仅需填入 0x 开头的 42 位公开钱包地址（无需任何私钥或 API 密钥），底层仅访问公开 /info 查询接口；填入主地址后可一键检测并勾选名下链上子账户。'
                    : 'Enter your public 42-character 0x wallet address only (never enter a private key). You can also auto-detect and include on-chain sub-accounts.'
                  : isZh
                    ? '请在交易所后台为该主账号或子账户创建仅勾选「只读 / 查询」权限的 API Key（切勿勾选下单或提币）。系统会自动识别账户 UID，防止重复添加或跨账号混账。'
                    : 'Create an API key with Read-Only permissions only (for either a main account or an isolated sub-account). Remote UID is verified automatically to prevent duplicate counting.'}
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
                  {isZh ? '账户备注名称（推荐区分主号 / 子号）' : 'Account Label (e.g. Main / Quant Sub)'}
                </span>
                <input
                  type="text"
                  data-testid="exchange-label-input"
                  value={label}
                  onChange={e => setLabel(e.target.value)}
                  placeholder={
                    isZh
                      ? `如：${selectedAdapter.display_name_zh || selectedAdapter.display_name} 主号 / 量化子号`
                      : `e.g. ${selectedAdapter.display_name} Main / Quant Sub`
                  }
                  className="otl-input h-9 w-full"
                />
              </label>

              {isWalletMode ? (
                <div className="space-y-2">
                  <label className="block space-y-1">
                    <span className="text-[13px] font-medium text-[var(--fg-2)]">
                      {isZh ? '公开钱包地址 (0x...) *' : 'Public Wallet Address (0x...) *'}
                    </span>
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        data-testid="exchange-wallet-input"
                        autoComplete="off"
                        value={walletAddress}
                        onChange={e => {
                          setWalletAddress(e.target.value)
                          setSubsProbedOnce(false)
                        }}
                        placeholder="0x..."
                        className="otl-input h-9 flex-1 font-mono"
                      />
                      <button
                        type="button"
                        data-testid="probe-subaccounts-btn"
                        disabled={!EVM_ADDR_RE.test(walletAddress.trim()) || probingSubs || modalBusy}
                        onClick={() => void handleProbeSubaccounts()}
                        className="otl-btn h-9 whitespace-nowrap"
                      >
                        {probingSubs
                          ? isZh
                            ? '检测中…'
                            : 'Checking…'
                          : isZh
                            ? '检测子账户'
                            : 'Detect Subs'}
                      </button>
                    </div>
                  </label>

                  {discoveredSubs.length > 0 && (
                    <div
                      data-testid="discovered-subaccounts-box"
                      className="rounded-md border border-[var(--border)] bg-[var(--surface-2)] p-3 space-y-2"
                    >
                      <div className="text-[12px] font-medium text-[var(--fg)]">
                        {isZh
                          ? `检测到 ${discoveredSubs.length} 个链上子账户（勾选后一并作为独立账户接入）：`
                          : `Found ${discoveredSubs.length} on-chain sub-account(s) — select to import together:`}
                      </div>
                      <div className="space-y-1.5">
                        {discoveredSubs.map(sub => {
                          const checked = Boolean(selectedSubs[sub.address])
                          const shortAddr = `${sub.address.slice(0, 6)}...${sub.address.slice(-4)}`
                          return (
                            <label
                              key={sub.address}
                              className="flex items-center justify-between gap-2 text-[12px] cursor-pointer py-0.5"
                            >
                              <span className="inline-flex items-center gap-2 min-w-0">
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  onChange={e =>
                                    setSelectedSubs(prev => ({
                                      ...prev,
                                      [sub.address]: e.target.checked,
                                    }))
                                  }
                                />
                                <span className="font-medium text-[var(--fg)] truncate">{sub.name}</span>
                                <span className="font-mono text-[var(--fg-3)]">{shortAddr}</span>
                              </span>
                              {sub.equity_usd && (
                                <span className="font-mono text-[var(--fg-2)] whitespace-nowrap sensitive">
                                  {Number(sub.equity_usd).toFixed(2)} U
                                </span>
                              )}
                            </label>
                          )
                        })}
                      </div>
                    </div>
                  )}

                  {subsProbedOnce && discoveredSubs.length === 0 && (
                    <div className="text-[12px] text-[var(--fg-3)]">
                      {isZh
                        ? '该地址下暂无链上子账户，可直接保存接入当前钱包。'
                        : 'No on-chain sub-accounts found under this address.'}
                    </div>
                  )}
                </div>
              ) : (
                <>
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
                        placeholder={
                          isZh
                            ? '创建该 API Key 时设置的口令'
                            : 'Passphrase set when creating this API key'
                        }
                        className="otl-input h-9 w-full font-mono"
                      />
                    </label>
                  )}
                </>
              )}
            </div>

            <AnimatedCollapse open={modalBusy || submitStep === 'done'}>
              <div
                className={`rounded-md px-3 py-2.5 text-[13px] space-y-2 ${
                  submitStep === 'done' ? 'otl-status-ok' : 'otl-surface-2 text-[var(--fg-2)]'
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
                          ? '正在核验账户 UID 身份与只读权限边界…'
                          : 'Verifying account UID identity & read-only access…'
                        : isZh
                          ? '身份核验通过 · 正在独立分区回溯历史账单（约 10～25 秒）…'
                          : 'Verified · Syncing historical ledger into isolated partition…'}
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
                disabled={modalBusy}
                onClick={() => {
                  setAddingVenue('')
                  setEditingAccountId('')
                  setIsCreateNew(false)
                  setModalError('')
                }}
                className="otl-btn"
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                data-testid="save-exchange-btn"
                disabled={modalBusy || !formReady}
                onClick={() => void handleAddSubmit()}
                className="otl-btn otl-btn-primary px-4"
                style={submitStep === 'done' ? { background: 'var(--ok)', color: '#ffffff' } : undefined}
              >
                {submitStep === 'done' ? (
                  <>
                    <Check size={13} />
                    <span>{isZh ? '连接成功' : 'Connected'}</span>
                  </>
                ) : modalBusy ? (
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
        open={Boolean(deletingTarget)}
        onClose={() => setDeletingTarget(null)}
        testId="delete-exchange-modal"
        maxWidthClass="max-w-[460px]"
        title={
          deleteMode === 'purge_archived'
            ? isZh
              ? '确认清空本地封存数据'
              : 'Purge Archived Local Data'
            : isZh
              ? '移除账户只读连接'
              : 'Remove Read-Only Account'
        }
      >
        {deletingTarget && (
          <div className="p-6 space-y-4 text-[12.5px]">
            {deleteMode === 'choice' ? (
              <>
                <p className="m-0 text-[var(--fg-2)] leading-relaxed">
                  {isZh
                    ? `请选择移除「${deletingTarget.accountLabel}」时的本地历史数据处理方式：`
                    : `Choose how to handle local historical data when removing ${deletingTarget.accountLabel}:`}
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
                        {isZh
                          ? '仅解绑凭证，保留本地历史记录（推荐）'
                          : 'Unbind Credential, Keep Local History (Recommended)'}
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
                        ? '销毁本地加密凭证并停止该账户后台轮询；已同步的平仓交割、充提本金与闭环对账记录继续保留在本地（标为「已封存」）。'
                        : 'Deletes the stored credential and stops polling; synced trades, principal, and reconciliation stay archived locally.'}
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
                        {isZh ? '彻底删除凭证及该账户全部本地数据' : 'Delete Credential & Purge Account Data'}
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
                        ? '同时销毁该账户凭证并清空其独立分区内的全部资产与历史交易流水（不影响同交易所其他账号或其他交易所，手写复盘笔记保留）。'
                        : 'Deletes this credential and wipes all cached balances and trades in this account partition (other accounts and journal notes are untouched).'}
                    </p>
                  </button>
                </div>
              </>
            ) : (
              <p className="m-0 text-[var(--fg-2)] leading-relaxed">
                {isZh
                  ? `确认彻底清空「${deletingTarget.accountLabel}」在本地封存的全部历史资产与交割流水吗？若超出交易所官方 API 保留期，清空后将无法重新拉回（其他账号不受影响，复盘笔记保留）。`
                  : `Permanently purge all archived local balances and trades for ${deletingTarget.accountLabel}? Records older than the exchange API retention window cannot be re-fetched.`}
              </p>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setDeletingTarget(null)} className="otl-btn">
                {t('cancel')}
              </button>
              <button
                type="button"
                data-testid="confirm-delete-exchange-btn"
                onClick={async () => {
                  const targetAcctId = deletingTarget.accountId
                  const targetLabel = deletingTarget.accountLabel
                  const shouldKeep = deleteMode === 'choice' ? keepHistoryOnDelete : false
                  setDeletingTarget(null)
                  setStatusMsg('')
                  setErrorMsg('')
                  try {
                    await onDeleteExchange(targetAcctId, shouldKeep, formRevision)
                  } catch (err) {
                    setErrorMsg(
                      err instanceof Error
                        ? err.message
                        : isZh
                          ? '操作失败，请重试。'
                          : 'Unable to remove the account. Try again.',
                    )
                    return
                  }
                  flashOk(
                    shouldKeep
                      ? isZh
                        ? `已解绑 ${targetLabel} 凭证，本地历史记录已封存保留`
                        : `Unbound ${targetLabel} · local history archived`
                      : isZh
                        ? `已彻底清空 ${targetLabel} 本地数据`
                        : `Purged ${targetLabel} local data`,
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
