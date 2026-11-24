import { useLayoutEffect, useRef, useState } from 'react'

export interface PillOption<T extends string> {
  value: T
  label: string
  testId?: string
}

interface SegmentedPillsProps<T extends string> {
  options: Array<PillOption<T>>
  value: T
  onChange: (next: T) => void
  size?: 'sm' | 'md'
}

export function SegmentedPills<T extends string>({
  options,
  value,
  onChange,
  size = 'md',
}: SegmentedPillsProps<T>) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const btnRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const [glider, setGlider] = useState<{ left: number; width: number; ready: boolean }>({
    left: 2,
    width: 0,
    ready: false,
  })

  useLayoutEffect(() => {
    const sync = () => {
      const btn = btnRefs.current[value]
      if (!btn) {
        setGlider(g => (g.ready ? { ...g, ready: false } : g))
        return
      }
      const w = btn.offsetWidth
      const l = btn.offsetLeft
      if (w > 0) {
        setGlider({ left: l, width: w, ready: true })
      }
    }
    sync()
    if (typeof ResizeObserver !== 'undefined' && containerRef.current) {
      const ro = new ResizeObserver(() => sync())
      ro.observe(containerRef.current)
      return () => ro.disconnect()
    }
  }, [value, options])

  const padClass = size === 'sm' ? 'px-2.5 py-0.5 text-[11.5px]' : 'px-2.5 py-1 text-[12px]'

  return (
    <div
      ref={containerRef}
      className="relative inline-flex p-0.5 rounded-lg border border-[var(--border)] bg-[var(--surface-2)]"
    >
      {glider.ready && (
        <div
          aria-hidden="true"
          className="otl-glider absolute top-0.5 bottom-0.5 left-0 rounded-md bg-[var(--surface)] border border-[var(--border-strong)] pointer-events-none"
          style={{
            width: `${glider.width}px`,
            transform: `translateX(${glider.left}px)`,
            boxShadow: '0 1px 4px rgba(0, 0, 0, 0.18)',
          }}
        />
      )}
      {options.map(opt => {
        const active = opt.value === value
        return (
          <button
            key={opt.value}
            ref={el => {
              btnRefs.current[opt.value] = el
            }}
            type="button"
            data-testid={opt.testId}
            onClick={() => onChange(opt.value)}
            className={`relative z-10 rounded-md font-medium whitespace-nowrap cursor-pointer transition-colors ${padClass}`}
            style={{
              background: !glider.ready && active ? 'var(--surface)' : 'transparent',
              color: active ? 'var(--fg)' : 'var(--fg-2)',
            }}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
