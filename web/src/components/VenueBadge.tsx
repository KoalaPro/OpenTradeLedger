interface VenueBadgeProps {
  venueId: string
  name?: string
}

const KNOWN_TOKENS = new Set(['binance', 'okx', 'bybit', 'gate', 'bitget', 'mexc', 'easicoin'])

export function VenueBadge({ venueId, name }: VenueBadgeProps) {
  const key = (venueId || 'gate').toLowerCase()
  const dotColor = KNOWN_TOKENS.has(key) ? `var(--dot-${key})` : 'var(--fg-3)'

  return (
    <span className="otl-venue-inline">
      <span
        aria-hidden="true"
        className="otl-venue-dot"
        style={{ background: dotColor }}
      />
      <span>{name || venueId.toUpperCase()}</span>
    </span>
  )
}
