import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

interface FloatingModalProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  subtitle?: ReactNode
  headerActions?: ReactNode
  maxWidthClass?: string
  testId?: string
  children: ReactNode
}

export function FloatingModal({
  open,
  onClose,
  title,
  subtitle,
  headerActions,
  maxWidthClass = 'max-w-[720px]',
  testId,
  children,
}: FloatingModalProps) {
  const [rendered, setRendered] = useState(open)
  const [closing, setClosing] = useState(false)
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    if (open) {
      if (timerRef.current) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
      setClosing(false)
      setRendered(true)
    } else if (rendered) {
      const reduced =
        typeof window !== 'undefined' &&
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches
      if (reduced) {
        setRendered(false)
        setClosing(false)
        return
      }
      setClosing(true)
      timerRef.current = window.setTimeout(() => {
        setRendered(false)
        setClosing(false)
        timerRef.current = null
      }, 185)
    }
  }, [open, rendered])

  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current)
    }
  }, [])

  const requestClose = useCallback(() => {
    onClose()
  }, [onClose])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') requestClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, requestClose])

  if (!rendered) return null

  const isZh = typeof document !== 'undefined' && (document.documentElement.lang || '').startsWith('zh')

  const dialogNode = (
    <div
      data-testid={testId}
      role="dialog"
      aria-modal="true"
      onClick={e => {
        if (e.target === e.currentTarget) requestClose()
      }}
      className={`otl-modal-backdrop fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 ${
        closing ? 'closing' : ''
      }`}
    >
      <div
        className={`otl-velvet-window w-full ${maxWidthClass} max-h-[88vh] flex flex-col overflow-hidden`}
      >
        <div className="px-6 py-4 border-b border-[var(--border)] bg-[var(--surface)] flex items-center justify-between gap-3 shrink-0">
          <div className="min-w-0">
            <div className="t-h2 truncate">{title}</div>
            {subtitle && (
              <div className="text-[12.5px] text-[var(--fg-3)] mt-0.5 truncate">{subtitle}</div>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {headerActions}
            <button
              type="button"
              onClick={requestClose}
              aria-label={isZh ? '关闭' : 'Close'}
              title={isZh ? '关闭' : 'Close'}
              className="otl-btn h-7 w-7 px-0 text-[var(--fg-2)] hover:text-[var(--fg)]"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>
  )

  return typeof document !== 'undefined' ? createPortal(dialogNode, document.body) : dialogNode
}
