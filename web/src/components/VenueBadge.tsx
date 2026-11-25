interface VenueBadgeProps {
  venueId: string
  name?: string
  strong?: boolean
}

const KNOWN_TOKENS = new Set(['binance', 'okx', 'bybit', 'gate', 'bitget', 'mexc', 'easicoin'])

export function VenueBadge({ venueId, name, strong = false }: VenueBadgeProps) {
  const key = (venueId || 'gate').toLowerCase()
  const dotColor = KNOWN_TOKENS.has(key) ? `var(--dot-${key})` : 'var(--fg-3)'

  return (
    <span
      className="otl-venue-inline"
      style={strong ? { color: 'var(--fg)', fontWeight: 600 } : undefined}
    >
      <span
        aria-hidden="true"
        className="otl-venue-dot"
        style={{ background: dotColor }}
      />
      <span>{name || venueId.toUpperCase()}</span>
    </span>
  )
}
