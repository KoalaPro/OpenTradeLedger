import type { ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'
import type {
  DashboardWarningItem,
  ExchangeAccountInstance,
  ExchangeAdapterInfo,
} from '../../lib/types'

export type AccountVisualStatus =
  | 'not_configured'
  | 'archived'
  | 'disabled'
  | 'connected'
  | 'error'
  | 'unverified'

export const EVM_ADDR_RE = /^0x[0-9a-fA-F]{40}$/

const MASKED_IDENTITY_TOKEN_RE =
  /(0x[a-fA-F0-9]{2,8}(?:\.\.\.|…)[a-fA-F0-9]{2,8}|0x[a-fA-F0-9]{40}|[a-zA-Z0-9_-]{1,8}\*{2,}[a-zA-Z0-9_-]{1,8}|\*{2,}[a-zA-Z0-9_-]{2,8}|[a-zA-Z0-9_-]{3,8}\.\.\.[a-zA-Z0-9_-]{3,8})/g

export function renderMaskedErrorText(text: string): ReactNode {
  if (!text) return text
  const parts = text.split(MASKED_IDENTITY_TOKEN_RE)
  if (parts.length <= 1) return text
  return parts.map((part, idx) =>
    idx % 2 === 1 ? (
      <span key={idx} className="sensitive">
        {part}
      </span>
    ) : (
      part
    ),
  )
}

export function normalizeAdapterAccounts(ad: ExchangeAdapterInfo): ExchangeAccountInstance[] {
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

export function getAccountVisualStatus(acct: ExchangeAccountInstance): AccountVisualStatus {
  if (!acct.configured) return acct.has_archived_data ? 'archived' : 'not_configured'
  if (acct.credential && acct.credential.enabled === false) return 'disabled'
  const st = acct.health?.status
  if (st === 'error') return 'error'
  if (st === 'connected') return 'connected'
  return 'unverified'
}

export function formatRoleLabel(role: string | undefined, isZh: boolean): string {
  const r = (role || '').toLowerCase()
  if (r === 'subaccount') return isZh ? '子账户' : 'Sub'
  if (r === 'master') return isZh ? '主账户' : 'Main'
  if (r === 'wallet') return isZh ? '链上钱包' : 'Wallet'
  return ''
}

export function resolveAccountWarnings(
  acct: ExchangeAccountInstance,
  dashboardWarnings: DashboardWarningItem[],
  isZh: boolean,
): string[] {
  if (!acct.configured || acct.credential?.enabled === false) return []
  const direct = acct.health?.warnings ?? []
  const matchedDash = dashboardWarnings.find(
    w => w.venue_id.toLowerCase() === acct.account_id.toLowerCase(),
  )
  const dashList = matchedDash
    ? matchedDash.warnings.length > 0
      ? matchedDash.warnings
      : [
          isZh
            ? matchedDash.summary_zh || matchedDash.summary
            : matchedDash.summary || matchedDash.summary_zh,
        ]
    : []
  return Array.from(new Set([...direct, ...dashList].map(s => s.trim()).filter(Boolean)))
}

export function AccountStatusPill({
  accountId,
  status,
  hasWarnings = false,
  connectedLabel,
  isZh,
}: {
  accountId: string
  status: AccountVisualStatus
  hasWarnings?: boolean
  connectedLabel: string
  isZh: boolean
}) {
  return (
    <>
      {status === 'connected' && !hasWarnings && (
        <span
          data-testid={`adapter-status-${accountId}`}
          className="inline-flex items-center gap-1.5 text-[12px] text-[var(--ok)]"
        >
          <span className="otl-venue-dot" style={{ background: 'var(--ok)' }} />
          {connectedLabel}
        </span>
      )}
      {status === 'connected' && hasWarnings && (
        <span
          data-testid={`adapter-status-${accountId}`}
          className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[var(--warn)]"
        >
          <span className="otl-venue-dot" style={{ background: 'var(--warn)' }} />
          {isZh ? `${connectedLabel} · 部分同步` : `${connectedLabel} · Partial Sync`}
        </span>
      )}
      {status === 'error' && (
        <span
          data-testid={`adapter-status-${accountId}`}
          className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-[var(--err)]"
        >
          <span className="otl-venue-dot" style={{ background: 'var(--err)' }} />
          <span>{isZh ? '连接报错' : 'API Error'}</span>
        </span>
      )}
      {status === 'unverified' && (
        <span
          data-testid={`adapter-status-${accountId}`}
          className="text-[12px] font-medium text-[var(--warn)]"
        >
          · {isZh ? '待验证' : 'Unverified'}
        </span>
      )}
      {status === 'disabled' && (
        <span data-testid={`adapter-status-${accountId}`} className="text-[12px] text-[var(--fg-3)]">
          · {isZh ? '已停用' : 'Disabled'}
        </span>
      )}
      {status === 'archived' && (
        <span data-testid={`adapter-status-${accountId}`} className="text-[12px] text-[var(--fg-3)]">
          · {isZh ? '已封存' : 'Archived'}
        </span>
      )}
    </>
  )
}

export function AccountHealthAlerts({
  accountId,
  status,
  errReason,
  rawErr,
  warnings,
}: {
  accountId: string
  status: AccountVisualStatus
  errReason: string
  rawErr: string
  warnings: string[]
}) {
  return (
    <>
      {status === 'error' && errReason && (
        <div
          data-testid={`adapter-error-box-${accountId}`}
          className="pl-3 border-l-2 border-[var(--err)] text-[12px] text-[var(--err)] space-y-0.5"
        >
          <div className="font-medium">{renderMaskedErrorText(errReason)}</div>
          {rawErr && !errReason.includes(rawErr) && (
            <div className="opacity-85 font-mono break-all">{renderMaskedErrorText(rawErr)}</div>
          )}
        </div>
      )}

      {warnings.length > 0 && (
        <div
          data-testid={`adapter-warning-box-${accountId}`}
          className="otl-status-warn px-3 py-2 rounded-md text-[12px] space-y-0.5"
        >
          {warnings.map((w, wi) => (
            <div key={wi} className="flex items-start gap-1.5">
              <AlertTriangle size={13} className="shrink-0 mt-0.5" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
