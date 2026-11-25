import type { ReactNode } from 'react'

interface KpiCardProps {
  label: string
  badge?: ReactNode
  value: ReactNode
  valueClass?: string
  sub?: ReactNode
  sensitive?: boolean
  testId?: string
  onClick?: () => void
  active?: boolean
}

export function KpiCard({
  label,
  badge,
  value,
  valueClass = '',
  sub,
  sensitive = true,
  testId,
  onClick,
  active = false,
}: KpiCardProps) {
  return (
    <div
      data-testid={testId}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={
        onClick
          ? e => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onClick()
              }
            }
          : undefined
      }
      className={`otl-ledger-cell ${onClick ? 'otl-card-interactive' : ''}`}
      style={active ? { boxShadow: 'inset 0 -2px 0 var(--accent)' } : undefined}
    >
      <div className="flex items-center justify-between gap-2 text-[13px] text-[var(--fg-2)] whitespace-nowrap">
        <span className="truncate">{label}</span>
        {badge}
      </div>
      <div
        className={`t-kpi whitespace-nowrap flex items-baseline gap-1 ${valueClass} ${
          sensitive ? 'sensitive' : ''
        }`}
      >
        {value}
      </div>
      <div className="text-[13px] text-[var(--fg-3)] leading-snug">{sub}</div>
    </div>
  )
}
