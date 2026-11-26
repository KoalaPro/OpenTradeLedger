import { useState } from 'react'
import { Check } from 'lucide-react'
import { FloatingModal } from '../FloatingModal'
import { useI18n } from '../../i18n'
import type { ExchangeAdapterInfo } from '../../lib/types'

export interface DeletingExchangeTarget {
  seq: number
  adapter: ExchangeAdapterInfo
  accountId: string
  accountLabel: string
  mode: 'choice' | 'purge_archived'
  formRevision: string | undefined
}

interface ExchangeDeleteModalProps {
  deletingTarget: DeletingExchangeTarget | null
  onClose: () => void
  onDeleteExchange: (
    venueId: string,
    keepData?: boolean,
    expectedRevision?: string,
  ) => Promise<void>
  onClearMessages: () => void
  flashOk: (msg: string, ms?: number) => void
  setErrorMsg: (msg: string) => void
}

export function ExchangeDeleteModal({
  deletingTarget,
  onClose,
  onDeleteExchange,
  onClearMessages,
  flashOk,
  setErrorMsg,
}: ExchangeDeleteModalProps) {
  const { locale, t } = useI18n()
  const isZh = locale === 'zh-CN'

  const [activeSeq, setActiveSeq] = useState<number>(0)
  const [keepHistoryOnDelete, setKeepHistoryOnDelete] = useState<boolean>(true)

  const targetSeq = deletingTarget?.seq ?? 0
  if (targetSeq !== activeSeq) {
    setActiveSeq(targetSeq)
    if (deletingTarget) {
      setKeepHistoryOnDelete(deletingTarget.mode === 'choice')
    }
  }

  const deleteMode = deletingTarget?.mode ?? 'choice'

  return (
    <FloatingModal
      open={Boolean(deletingTarget)}
      onClose={onClose}
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
                        borderColor: keepHistoryOnDelete
                          ? 'var(--accent)'
                          : 'var(--border-strong)',
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
                    background: !keepHistoryOnDelete
                      ? 'rgba(244, 104, 118, 0.08)'
                      : 'var(--surface-2)',
                  }}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-[var(--fg)]">
                      {isZh
                        ? '彻底删除凭证及该账户全部本地数据'
                        : 'Delete Credential & Purge Account Data'}
                    </span>
                    <span
                      className="h-4 w-4 rounded-full border inline-flex items-center justify-center shrink-0"
                      style={{
                        borderColor: !keepHistoryOnDelete
                          ? 'var(--err)'
                          : 'var(--border-strong)',
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
            <button type="button" onClick={onClose} className="otl-btn">
              {t('cancel')}
            </button>
            <button
              type="button"
              data-testid="confirm-delete-exchange-btn"
              onClick={async () => {
                const targetAcctId = deletingTarget.accountId
                const targetLabel = deletingTarget.accountLabel
                const targetRevision = deletingTarget.formRevision
                const shouldKeep = deleteMode === 'choice' ? keepHistoryOnDelete : false
                onClose()
                onClearMessages()
                try {
                  await onDeleteExchange(targetAcctId, shouldKeep, targetRevision)
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
  )
}
