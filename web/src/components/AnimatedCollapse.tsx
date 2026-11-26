import { useEffect, useRef, useState, type ReactNode } from 'react'

interface AnimatedCollapseProps {
  open: boolean
  children: ReactNode
  className?: string
  innerClassName?: string
  durationMs?: number
  testId?: string
}

/**
 * Two-way CSS Grid height collapse (`0fr <-> 1fr`) that mounts children
 * synchronously when `open` turns true and animates smoothly to `0fr` before
 * unmounting when `open` turns false.
 */
export function AnimatedCollapse({
  open,
  children,
  className = '',
  innerClassName = '',
  durationMs = 220,
  testId,
}: AnimatedCollapseProps) {
  const [rendered, setRendered] = useState(open)
  const [expanded, setExpanded] = useState(open)
  const [settled, setSettled] = useState(open)
  const timerRef = useRef<number | null>(null)
  const settleTimerRef = useRef<number | null>(null)
  const raf1Ref = useRef<number | null>(null)
  const raf2Ref = useRef<number | null>(null)

  useEffect(() => {
    const cancelRafs = () => {
      if (raf1Ref.current !== null && typeof window !== 'undefined' && window.cancelAnimationFrame) {
        window.cancelAnimationFrame(raf1Ref.current)
        raf1Ref.current = null
      }
      if (raf2Ref.current !== null && typeof window !== 'undefined' && window.cancelAnimationFrame) {
        window.cancelAnimationFrame(raf2Ref.current)
        raf2Ref.current = null
      }
    }

    const reduced =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches

    if (open) {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
      if (settleTimerRef.current !== null) {
        window.clearTimeout(settleTimerRef.current)
        settleTimerRef.current = null
      }
      setRendered(true)
      if (reduced || typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
        setExpanded(true)
        setSettled(true)
      } else {
        setSettled(false)
        cancelRafs()
        raf1Ref.current = window.requestAnimationFrame(() => {
          raf2Ref.current = window.requestAnimationFrame(() => {
            setExpanded(true)
            settleTimerRef.current = window.setTimeout(() => {
              setSettled(true)
              settleTimerRef.current = null
            }, durationMs)
          })
        })
      }
    } else {
      cancelRafs()
      if (settleTimerRef.current !== null) {
        window.clearTimeout(settleTimerRef.current)
        settleTimerRef.current = null
      }
      setSettled(false)
      setExpanded(false)
      if (rendered) {
        if (reduced) {
          setRendered(false)
        } else {
          timerRef.current = window.setTimeout(() => {
            setRendered(false)
            timerRef.current = null
          }, durationMs)
        }
      }
    }

    return () => {
      cancelRafs()
    }
  }, [open, rendered, durationMs])

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
      }
      if (settleTimerRef.current !== null) {
        window.clearTimeout(settleTimerRef.current)
      }
    }
  }, [])

  const shouldRender = open || rendered
  if (!shouldRender) return null

  const isOpenVisual = open && expanded
  const stateLabel = open ? (expanded ? 'open' : 'opening') : 'closing'

  return (
    <div
      data-testid={testId}
      data-state={stateLabel}
      className={`otl-collapse-grid ${isOpenVisual ? 'is-open' : 'is-collapsed'} ${
        isOpenVisual && settled ? 'is-settled' : ''
      } ${className}`}
    >
      <div className="otl-collapse-inner">
        <div className={innerClassName}>{children}</div>
      </div>
    </div>
  )
}
