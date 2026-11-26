import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Check, X } from 'lucide-react'

interface ToastItemProps {
  message: string
  tone: 'ok' | 'err'
  testId: string
  onDismiss: () => void
  renderText?: (text: string) => ReactNode
}

function ToastItem({ message, tone, testId, onDismiss, renderText }: ToastItemProps) {
  const [rendered, setRendered] = useState(Boolean(message))
  const [displayedMsg, setDisplayedMsg] = useState(message)
  const [closing, setClosing] = useState(false)
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    if (message) {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
      setDisplayedMsg(message)
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
      }, 150)
    }
  }, [message, rendered])

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    }
  }, [])

  if (!message && !rendered) return null

  const activeText = message || displayedMsg

  return (
    <div
      data-testid={testId}
      role={tone === 'err' ? 'alert' : 'status'}
      aria-live="polite"
      className={`otl-toast-card ${closing ? 'closing' : ''} ${
        tone === 'ok' ? 'otl-status-ok' : 'otl-status-err'
      } pointer-events-auto px-4 py-3 rounded-lg text-[13px] font-medium flex items-start gap-2.5`}
    >
      {tone === 'ok' ? (
        <Check size={15} className="shrink-0 mt-0.5" style={{ color: 'var(--ok)' }} />
      ) : (
        <AlertTriangle size={15} className="shrink-0 mt-0.5" style={{ color: 'var(--err)' }} />
      )}
      <span className="flex-1 leading-snug">{renderText ? renderText(activeText) : activeText}</span>
      <button
        type="button"
        data-no-ripple="true"
        onClick={onDismiss}
        aria-label="Dismiss"
        className="opacity-70 hover:opacity-100 p-0.5 rounded cursor-pointer shrink-0"
      >
        <X size={13} />
      </button>
    </div>
  )
}

export function FloatingToastViewport({
  statusMsg,
  errorMsg,
  onClearStatus,
  onClearError,
  okTestId = 'settings-ok-banner',
  errTestId = 'settings-error-banner',
  renderError,
}: {
  statusMsg: string
  errorMsg: string
  onClearStatus: () => void
  onClearError: () => void
  okTestId?: string
  errTestId?: string
  renderError?: (text: string) => ReactNode
}) {
  const viewportNode = (
    <div
      data-testid="floating-toast-viewport"
      className="fixed bottom-5 right-5 z-[60] flex flex-col gap-2 max-w-[400px] w-[calc(100vw-2.5rem)] sm:w-auto pointer-events-none"
    >
      <ToastItem message={statusMsg} tone="ok" testId={okTestId} onDismiss={onClearStatus} />
      <ToastItem
        message={errorMsg}
        tone="err"
        testId={errTestId}
        onDismiss={onClearError}
        renderText={renderError}
      />
    </div>
  )

  return typeof document !== 'undefined' ? createPortal(viewportNode, document.body) : viewportNode
}
