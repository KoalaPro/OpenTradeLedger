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
      onClick={onClick}
      className={`otl-card p-4 grid gap-2 min-w-0 transition-colors ${
        onClick ? 'cursor-pointer hover:bg-[var(--surface-hover)]' : ''
      }`}
      style={{
        gridTemplateRows: '20px 32px 1fr',
        ...(active ? { borderColor: 'var(--accent)' } : {}),
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
