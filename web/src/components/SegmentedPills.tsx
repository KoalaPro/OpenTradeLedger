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
  wrap?: boolean
}

export function SegmentedPills<T extends string>({
  options,
  value,
  onChange,
  size = 'md',
  wrap = false,
}: SegmentedPillsProps<T>) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const btnRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const [glider, setGlider] = useState<{
    left: number
    top: number
    width: number
    height: number
    ready: boolean
  }>({
    left: 2,
    top: 2,
    width: 0,
    height: 0,
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
      const h = btn.offsetHeight
      const l = btn.offsetLeft
      const t = btn.offsetTop
      if (w > 0 && h > 0) {
        setGlider({ left: l, top: t, width: w, height: h, ready: true })
      }
    }
    sync()
    if (typeof ResizeObserver !== 'undefined' && containerRef.current) {
      const ro = new ResizeObserver(() => sync())
      ro.observe(containerRef.current)
      return () => ro.disconnect()
    }
  }, [value, options])

  const padClass = size === 'sm' ? 'px-2.5 py-1 text-[13px]' : 'px-3 py-1 text-[13px]'

  return (
    <div
      ref={containerRef}
      className={`otl-seg ${wrap ? 'flex-wrap' : 'flex-nowrap'}`}
    >
      {glider.ready && (
        <div
          aria-hidden="true"
          className="otl-glider absolute left-0 top-0 rounded-[5px] bg-[var(--surface)] border border-[var(--border-strong)] pointer-events-none"
          style={{
            width: `${glider.width}px`,
            height: `${glider.height}px`,
            transform: `translate3d(${glider.left}px, ${glider.top}px, 0)`,
            boxShadow: '0 1px 2px rgba(0, 0, 0, 0.10)',
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
            data-no-ripple="true"
            data-testid={opt.testId}
            onClick={() => onChange(opt.value)}
            className={`relative z-10 rounded-[5px] whitespace-nowrap cursor-pointer ${
              active ? 'font-semibold' : 'font-medium'
            } ${padClass}`}
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
