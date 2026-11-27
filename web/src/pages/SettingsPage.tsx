import { useCallback, useEffect, useRef, useState } from 'react'
import { Plus } from 'lucide-react'
import { FloatingToastViewport } from '../components/FloatingToast'
import { VenueBadge } from '../components/VenueBadge'
import { AccessPasswordCard } from '../components/settings/AccessPasswordCard'
import { AiConfigCard } from '../components/settings/AiConfigCard'
import { BarkNotifyCard } from '../components/settings/BarkNotifyCard'
import {
  ExchangeConnectModal,
  type ExchangeConnectSession,
} from '../components/settings/ExchangeConnectModal'
import {
  ExchangeDeleteModal,
  type DeletingExchangeTarget,
} from '../components/settings/ExchangeDeleteModal'
import { PreferencesAndGoalSection } from '../components/settings/PreferencesAndGoalSection'
import {
  AccountHealthAlerts,
  AccountStatusPill,
  type AccountVisualStatus,
  formatRoleLabel,
  getAccountVisualStatus,
  normalizeAdapterAccounts,
  renderMaskedErrorText,
  resolveAccountWarnings,
} from '../components/settings/settingsHelpers'
import { VersionUpgradeCard } from '../components/settings/VersionUpgradeCard'
import { useI18n } from '../i18n'
import type { ExchangeUpsertPayload } from '../lib/api'
import type {
  AuthStatusPayload,
  DashboardWarningItem,
  ExchangeAccountInstance,
  ExchangeAdapterInfo,
  UserSettings,
  VersionCheckPayload,
} from '../lib/types'

interface SettingsPageProps {
  settings: UserSettings
  adapters: ExchangeAdapterInfo[]
  dashboardWarnings?: DashboardWarningItem[]
  demoMode: boolean
  authStatus?: AuthStatusPayload
  onAuthChanged?: (next: AuthStatusPayload, receipt: string) => void
  passwordReceipt?: string
  onPasswordReceiptShown?: () => void
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

export function SettingsPage({
  settings,
  adapters,
  dashboardWarnings = [],
  demoMode,
  authStatus,
  onAuthChanged,
  passwordReceipt,
  onPasswordReceiptShown,
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
  const isZh = locale === 'zh-CN'

  const [connectSession, setConnectSession] = useState<ExchangeConnectSession | null>(null)
  const [deletingTarget, setDeletingTarget] = useState<DeletingExchangeTarget | null>(null)
  const [busyVenue, setBusyVenue] = useState('')
  const [statusMsg, setStatusMsg] = useState('')
  const [errorMsg, setErrorMsg] = useState('')

  const statusTimerRef = useRef<number | null>(null)
  const modalSeqRef = useRef<number>(0)

  const firstLiveConnection =
    demoMode &&
    !adapters.some(adapter => normalizeAdapterAccounts(adapter).some(a => a.configured))

  const flashOk = useCallback((msg: string, ms = 3200) => {
    if (statusTimerRef.current !== null) {
      window.clearTimeout(statusTimerRef.current)
    }
    setErrorMsg('')
    setStatusMsg(msg)
    statusTimerRef.current = window.setTimeout(() => {
      setStatusMsg('')
      statusTimerRef.current = null
    }, ms)
  }, [])

  useEffect(() => {
    if (passwordReceipt) {
      flashOk(passwordReceipt)
      onPasswordReceiptShown?.()
    }
  }, [passwordReceipt, flashOk, onPasswordReceiptShown])

  useEffect(() => {
    return () => {
      if (statusTimerRef.current !== null) window.clearTimeout(statusTimerRef.current)
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
      setErrorMsg(
        err instanceof Error ? err.message : isZh ? '设置保存失败' : 'Failed to save settings',
      )
    }
    return false
  }

  const openAccountForm = (
    ad: ExchangeAdapterInfo,
    opts?: { account?: ExchangeAccountInstance; createNew?: boolean },
  ) => {
    const acct = opts?.account
    const creatingNew = Boolean(opts?.createNew)
    const nextNum = normalizeAdapterAccounts(ad).length + 1
    const initialLabel = creatingNew
      ? isZh
        ? `${ad.display_name_zh || ad.display_name} 账号${nextNum}`
        : `${ad.display_name} #${nextNum}`
      : acct?.credential?.label || acct?.label || ad.display_name

    modalSeqRef.current += 1
    setConnectSession({
      seq: modalSeqRef.current,
      venueId: ad.venue_id,
      editingAccountId: creatingNew ? '' : acct?.account_id || ad.venue_id,
      isCreateNew: creatingNew,
      editingWasConfigured: Boolean(!creatingNew && acct?.configured),
      formRevision: creatingNew ? 'absent' : acct?.configured ? acct.credential?.revision : 'absent',
      initialLabel,
    })
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

  const renderAccountActions = (
    ad: ExchangeAdapterInfo,
    acct: ExchangeAccountInstance,
    vStatus: AccountVisualStatus,
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
              modalSeqRef.current += 1
              setDeletingTarget({
                seq: modalSeqRef.current,
                adapter: ad,
                accountId: aid,
                accountLabel: acctDisplayLabel,
                mode: 'choice',
                formRevision: acct.credential?.revision,
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
              modalSeqRef.current += 1
              setDeletingTarget({
                seq: modalSeqRef.current,
                adapter: ad,
                accountId: aid,
                accountLabel: acctDisplayLabel,
                mode: 'purge_archived',
                formRevision: 'absent',
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
        renderError={renderMaskedErrorText}
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
                const roleBadge = formatRoleLabel(
                  acct.account_role || acct.credential?.account_role,
                  isZh,
                )
                const uidTag = acct.masked_uid || acct.credential?.masked_uid || ''
                const rawSingleLbl = (acct.credential?.label || acct.label || '').trim()
                const customLbl =
                  rawSingleLbl &&
                  rawSingleLbl.toLowerCase() !== ad.display_name.toLowerCase() &&
                  rawSingleLbl.toLowerCase() !== (ad.display_name_zh || '').toLowerCase() &&
                  rawSingleLbl.toLowerCase() !== ad.venue_id.toLowerCase()
                    ? rawSingleLbl
                    : ''
                const maskedKey =
                  acct.configured && acct.credential ? acct.credential.masked_api_key : ''
                const errReason = isZh
                  ? acct.health?.error_reason_zh || acct.health?.error_reason || ''
                  : acct.health?.error_reason || acct.health?.error_reason_zh || ''
                const rawErr = acct.health?.raw_error || ''
                const acctWarnings = resolveAccountWarnings(acct, dashboardWarnings, isZh)
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
                          <AccountStatusPill
                            accountId={acct.account_id}
                            status={vStatus}
                            hasWarnings={acctWarnings.length > 0}
                            connectedLabel={t('set_connected')}
                            isZh={isZh}
                          />
                        </div>

                        <div className="text-[12px] text-[var(--fg-3)] mt-0.5 truncate">
                          {maskedKey ? (
                            <>
                              {customLbl && <span className="text-[var(--fg-2)]">{customLbl} · </span>}
                              {uidTag && ad.auth_mode !== 'wallet_address' && (
                                <>
                                  <span className="font-mono text-[var(--fg-2)] sensitive">
                                    UID {uidTag}
                                  </span>
                                  {' · '}
                                </>
                              )}
                              <span className="font-mono text-[var(--fg-2)] sensitive">
                                {maskedKey}
                              </span>{' '}
                              · {passLabel}
                            </>
                          ) : (
                            <>
                              {uidTag && (
                                <>
                                  <span className="font-mono text-[var(--fg-2)] sensitive">
                                    UID {uidTag}
                                  </span>
                                  {' · '}
                                </>
                              )}
                              {isZh
                                ? '本地历史记录已封存（凭证已解绑）'
                                : 'Local history archived (unbound)'}
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

                    <AccountHealthAlerts
                      accountId={acct.account_id}
                      status={vStatus}
                      errReason={errReason}
                      rawErr={rawErr}
                      warnings={acctWarnings}
                    />
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
                      const roleBadge = formatRoleLabel(
                        acct.account_role || acct.credential?.account_role,
                        isZh,
                      )
                      const uidTag = acct.masked_uid || acct.credential?.masked_uid || ''
                      const credLbl = (acct.credential?.label || acct.label || '').trim()
                      const isDefaultCredLbl =
                        !credLbl ||
                        credLbl.toLowerCase() === ad.venue_id.toLowerCase() ||
                        credLbl.toLowerCase() === ad.display_name.toLowerCase() ||
                        credLbl.toLowerCase() === (ad.display_name_zh || '').toLowerCase()
                      const rawLbl = isDefaultCredLbl ? `${adName} #${idx + 1}` : credLbl
                      const maskedKey =
                        acct.configured && acct.credential ? acct.credential.masked_api_key : ''
                      const errReason = isZh
                        ? acct.health?.error_reason_zh || acct.health?.error_reason || ''
                        : acct.health?.error_reason || acct.health?.error_reason_zh || ''
                      const rawErr = acct.health?.raw_error || ''
                      const acctWarnings = resolveAccountWarnings(acct, dashboardWarnings, isZh)
                      const fullDisplayLabel = rawLbl
                        .toLowerCase()
                        .startsWith(adName.toLowerCase())
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
                                <span className="text-[13px] font-medium text-[var(--fg)]">
                                  {rawLbl}
                                </span>
                                {roleBadge && (
                                  <span className="px-1.5 py-0.5 rounded border border-[var(--border)] bg-[var(--surface-2)] text-[11px] text-[var(--fg-2)]">
                                    {roleBadge}
                                  </span>
                                )}
                                <AccountStatusPill
                                  accountId={acct.account_id}
                                  status={vStatus}
                                  hasWarnings={acctWarnings.length > 0}
                                  connectedLabel={t('set_connected')}
                                  isZh={isZh}
                                />
                              </div>
                              <div className="text-[12px] text-[var(--fg-3)] mt-0.5 truncate">
                                {maskedKey ? (
                                  <>
                                    {uidTag && ad.auth_mode !== 'wallet_address' && (
                                      <>
                                        <span className="font-mono text-[var(--fg-2)] sensitive">
                                          UID {uidTag}
                                        </span>
                                        {' · '}
                                      </>
                                    )}
                                    <span className="font-mono text-[var(--fg-2)] sensitive">
                                      {maskedKey}
                                    </span>
                                  </>
                                ) : (
                                  <>
                                    {uidTag && (
                                      <>
                                        <span className="font-mono text-[var(--fg-2)] sensitive">
                                          UID {uidTag}
                                        </span>
                                        {' · '}
                                      </>
                                    )}
                                    {isZh ? '本地历史记录已封存' : 'Local history archived'}
                                  </>
                                )}
                              </div>
                            </div>

                            <div className="flex items-center gap-2 flex-wrap">
                              {renderAccountActions(ad, acct, vStatus, fullDisplayLabel)}
                            </div>
                          </div>

                          <AccountHealthAlerts
                            accountId={acct.account_id}
                            status={vStatus}
                            errReason={errReason}
                            rawErr={rawErr}
                            warnings={acctWarnings}
                          />
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
        <PreferencesAndGoalSection
          settings={settings}
          demoMode={demoMode}
          onUpdateSettings={onUpdateSettings}
          onToggleDemo={onToggleDemo}
          onClearStatus={() => setStatusMsg('')}
          flashOk={flashOk}
          setErrorMsg={setErrorMsg}
        />
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

      <ExchangeConnectModal
        session={connectSession}
        adapters={adapters}
        busyVenue={busyVenue}
        firstLiveConnection={firstLiveConnection}
        onBusyVenueChange={setBusyVenue}
        onClose={() => setConnectSession(null)}
        onClearMessages={() => {
          setStatusMsg('')
          setErrorMsg('')
        }}
        flashOk={flashOk}
        setErrorMsg={setErrorMsg}
        onUpsertExchange={onUpsertExchange}
      />

      <ExchangeDeleteModal
        deletingTarget={deletingTarget}
        onClose={() => setDeletingTarget(null)}
        onDeleteExchange={onDeleteExchange}
        onClearMessages={() => {
          setStatusMsg('')
          setErrorMsg('')
        }}
        flashOk={flashOk}
        setErrorMsg={setErrorMsg}
      />
    </div>
  )
}
