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
      className={`otl-card p-4 grid gap-2 min-w-0 ${
        onClick ? 'otl-card-interactive hover:bg-[var(--surface-hover)]' : ''
      }`}
      style={{
        gridTemplateRows: '20px 32px 1fr',
        ...(active ? { borderColor: 'rgba(251, 191, 36, 0.45)' } : {}),
      }}
    >
      <div className="flex items-center justify-between gap-2 text-[12px] text-[var(--fg-2)] whitespace-nowrap">
        <span className="truncate">{label}</span>
        {badge}
      </div>
      <div
        className={`text-[24px] font-semibold tracking-tight leading-tight whitespace-nowrap flex items-baseline gap-1 ${valueClass} ${
          sensitive ? 'sensitive' : ''
        }`}
      >
        {value}
      </div>
      <div className="text-[12px] text-[var(--fg-3)] leading-snug">{sub}</div>
    </div>
  )
}
