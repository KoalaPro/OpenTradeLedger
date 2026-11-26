import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, ExternalLink, RefreshCw, ShieldCheck } from 'lucide-react'
import { AnimatedCollapse } from '../AnimatedCollapse'
import { FloatingModal } from '../FloatingModal'
import { VenueBadge } from '../VenueBadge'
import { useI18n } from '../../i18n'
import { api, ApiError, type ExchangeUpsertPayload } from '../../lib/api'
import type { DiscoveredSubaccount, ExchangeAdapterInfo } from '../../lib/types'
import {
  EVM_ADDR_RE,
  renderMaskedErrorText,
} from './settingsHelpers'

export interface ExchangeConnectSession {
  seq: number
  venueId: string
  editingAccountId: string
  isCreateNew: boolean
  editingWasConfigured: boolean
  formRevision: string | undefined
  initialLabel: string
}

interface ExchangeConnectModalProps {
  session: ExchangeConnectSession | null
  adapters: ExchangeAdapterInfo[]
  busyVenue: string
  firstLiveConnection: boolean
  onBusyVenueChange: (venue: string) => void
  onClose: () => void
  onClearMessages: () => void
  flashOk: (msg: string, ms?: number) => void
  setErrorMsg: (msg: string) => void
  onUpsertExchange: (
    body: ExchangeUpsertPayload,
    onStep?: (step: 'verifying' | 'syncing' | 'done') => void,
  ) => Promise<{
    failedSubaccounts?: Array<{ address: string; name: string; error?: string; error_zh?: string }>
  } | void>
}

export function ExchangeConnectModal({
  session,
  adapters,
  busyVenue,
  firstLiveConnection,
  onBusyVenueChange,
  onClose,
  onClearMessages,
  flashOk,
  setErrorMsg,
  onUpsertExchange,
}: ExchangeConnectModalProps) {
  const { locale, t } = useI18n()
  const isZh = locale === 'zh-CN'

  const [activeSeq, setActiveSeq] = useState<number>(0)
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

  const shakeTimerRef = useRef<number | null>(null)
  const closeCeremonyTimerRef = useRef<number | null>(null)

  const targetSeq = session?.seq ?? 0
  if (targetSeq !== activeSeq) {
    setActiveSeq(targetSeq)
    if (session) {
      setLabel(session.initialLabel)
      setModalError('')
      setApiKey('')
      setApiSecret('')
      setWalletAddress('')
      setPassphrase('')
      setDiscoveredSubs([])
      setSelectedSubs({})
      setSubsProbedOnce(false)
      setSubmitStep('')
    }
  }

  useEffect(() => {
    return () => {
      if (closeCeremonyTimerRef.current !== null) window.clearTimeout(closeCeremonyTimerRef.current)
      if (shakeTimerRef.current !== null) window.clearTimeout(shakeTimerRef.current)
    }
  }, [])

  const selectedAdapter = session
    ? adapters.find(a => a.venue_id === session.venueId)
    : undefined
  const isWalletMode = selectedAdapter?.auth_mode === 'wallet_address'
  const modalBusy = Boolean(
    busyVenue &&
      session &&
      (busyVenue === session.venueId || busyVenue === session.editingAccountId),
  )
  const ceremonyStage: 'credentials' | 'verifying' | 'syncing' | 'done' =
    submitStep || 'credentials'

  const formReady = Boolean(
    selectedAdapter &&
      (isWalletMode
        ? EVM_ADDR_RE.test(walletAddress.trim())
        : apiKey.trim() &&
          apiSecret.trim() &&
          (!selectedAdapter.requires_passphrase || passphrase.trim())),
  )

  const handleProbeSubaccounts = async () => {
    if (!session || !selectedAdapter || !EVM_ADDR_RE.test(walletAddress.trim())) return
    setProbingSubs(true)
    setModalError('')
    try {
      const res = await api.verifyExchange({
        venue_id: selectedAdapter.venue_id,
        ...(session.isCreateNew
          ? { create_new: true }
          : { account_id: session.editingAccountId || selectedAdapter.venue_id }),
        label: label.trim(),
        api_key: walletAddress.trim(),
        api_secret: '',
        wallet_address: walletAddress.trim(),
        expected_revision: session.formRevision,
      })
      const subs = res.subaccounts || []
      setDiscoveredSubs(subs)
      const nextChecked: Record<string, boolean> = {}
      for (const s of subs) {
        nextChecked[s.address] = !s.already_connected
      }
      setSelectedSubs(nextChecked)
      setSubsProbedOnce(true)
    } catch (err) {
      setModalError(
        err instanceof Error ? err.message : isZh ? '子账户检测失败' : 'Sub-account check failed',
      )
    } finally {
      setProbingSubs(false)
    }
  }

  const handleAddSubmit = async () => {
    if (!session || !selectedAdapter || !formReady) return
    const busyKey = session.editingAccountId || session.venueId
    onBusyVenueChange(busyKey)
    setSubmitStep('verifying')
    onClearMessages()
    setModalError('')
    try {
      const chosenSubs = isWalletMode
        ? discoveredSubs
            .filter(s => selectedSubs[s.address])
            .map(s => ({ name: s.name, address: s.address }))
        : undefined
      const upsertOut = await onUpsertExchange(
        {
          venue_id: session.venueId,
          ...(session.isCreateNew
            ? { create_new: true }
            : session.editingAccountId && session.editingAccountId !== session.venueId
              ? { account_id: session.editingAccountId }
              : {}),
          label: label.trim(),
          api_key: isWalletMode ? walletAddress.trim() : apiKey.trim(),
          api_secret: isWalletMode ? '' : apiSecret.trim(),
          ...(isWalletMode ? { wallet_address: walletAddress.trim() } : {}),
          passphrase: passphrase.trim(),
          expected_revision: session.formRevision,
          ...(chosenSubs && chosenSubs.length > 0 ? { include_subaccounts: chosenSubs } : {}),
        },
        step => setSubmitStep(step),
      )
      setSubmitStep('done')
      const failedSubs =
        upsertOut && typeof upsertOut === 'object' ? upsertOut.failedSubaccounts : undefined
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
        onClose()
        setApiKey('')
        setApiSecret('')
        setWalletAddress('')
        setPassphrase('')
        setLabel('')
        setDiscoveredSubs([])
        setSelectedSubs({})
        setSubsProbedOnce(false)
        setSubmitStep('')
        onBusyVenueChange('')
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
      const updateHint = session.editingWasConfigured
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
      onBusyVenueChange('')
      setModalShake(true)
      if (shakeTimerRef.current !== null) window.clearTimeout(shakeTimerRef.current)
      shakeTimerRef.current = window.setTimeout(() => setModalShake(false), 320)
    }
  }

  return (
    <FloatingModal
      open={Boolean(selectedAdapter)}
      onClose={() => {
        if (!modalBusy) {
          setModalError('')
          onClose()
        }
      }}
      testId="add-exchange-modal"
      maxWidthClass="max-w-[520px]"
      title={
        selectedAdapter && session ? (
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
              {session.editingWasConfigured
                ? isWalletMode
                  ? isZh
                    ? '更新链上钱包地址'
                    : 'Update Wallet Address'
                  : isZh
                    ? '更新只读 API 密钥'
                    : 'Update Read-Only API Key'
                : session.isCreateNew
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
            <div
              data-testid="exchange-modal-error"
              className="otl-status-err rounded-md px-3 py-2 text-[13px] flex items-start gap-2"
            >
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>{renderMaskedErrorText(modalError)}</span>
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
                        const alreadyConnected = sub.already_connected
                        const checked = Boolean(selectedSubs[sub.address])
                        const shortAddr = `${sub.address.slice(0, 6)}...${sub.address.slice(-4)}`
                        return (
                          <label
                            key={sub.address}
                            data-testid={`discovered-sub-row-${sub.address}`}
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
                              <span className="font-mono text-[var(--fg-3)] sensitive">{shortAddr}</span>
                              {alreadyConnected && (
                                <span
                                  data-testid={`discovered-sub-connected-${sub.address}`}
                                  className="px-1.5 py-0.5 rounded text-[11px] font-medium whitespace-nowrap"
                                  style={{
                                    background: 'rgba(47, 196, 149, 0.12)',
                                    color: 'var(--ok)',
                                  }}
                                >
                                  {isZh ? '已接入' : 'Connected'}
                                </span>
                              )}
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
                setModalError('')
                onClose()
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
  )
}
