interface VenueBadgeProps {
  venueId: string
  name?: string
}

const KNOWN_TOKENS = new Set(['binance', 'okx', 'bybit', 'gate', 'bitget', 'mexc', 'easicoin'])

export function VenueBadge({ venueId, name }: VenueBadgeProps) {
  const key = (venueId || 'gate').toLowerCase()
  const known = KNOWN_TOKENS.has(key)
  const bg = known ? `var(--v-${key}-bg)` : 'var(--surface-2)'
  const fg = known ? `var(--v-${key}-fg)` : 'var(--fg-2)'

  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-semibold whitespace-nowrap"
      style={{ background: bg, color: fg }}
    >
      {name || venueId.toUpperCase()}
    </span>
  )
}
