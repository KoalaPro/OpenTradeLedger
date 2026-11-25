import { useLayoutEffect, useRef, useState } from 'react'

export interface UnderlineTabOption<T extends string> {
  value: T
  label: string
  testId?: string
}

interface UnderlineTabsProps<T extends string> {
  options: Array<UnderlineTabOption<T>>
  value: T
  onChange: (next: T) => void
}

export function UnderlineTabs<T extends string>({
  options,
  value,
  onChange,
}: UnderlineTabsProps<T>) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const btnRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const [glider, setGlider] = useState<{ left: number; width: number; ready: boolean }>({
    left: 0,
    width: 0,
    ready: false,
  })

  useLayoutEffect(() => {
    const sync = () => {
      const btn = btnRefs.current[value]
      if (!btn || btn.offsetWidth === 0) {
        setGlider(g => (g.ready ? { ...g, ready: false } : g))
        return
      }
      setGlider({
        left: btn.offsetLeft,
        width: btn.offsetWidth,
        ready: true,
      })
    }
    sync()
    if (typeof ResizeObserver !== 'undefined' && containerRef.current) {
      const ro = new ResizeObserver(() => sync())
      ro.observe(containerRef.current)
      return () => ro.disconnect()
    }
  }, [value, options])

  return (
    <div ref={containerRef} className="otl-tabs">
      <div
        aria-hidden="true"
        className="otl-tab-glider"
        style={{
          opacity: glider.ready ? 1 : 0,
          width: `${glider.width}px`,
          transform: `translate3d(${glider.left}px, 0, 0)`,
        }}
      />
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
            data-no-press="true"
            data-testid={opt.testId}
            onClick={() => onChange(opt.value)}
            className={active ? 'active' : undefined}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
