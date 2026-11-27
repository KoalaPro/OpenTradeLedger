import { useEffect, useLayoutEffect, useRef } from 'react'

const focusable = 'button, a[href], input, select, textarea, summary, [tabindex]'

export function useDialogFocus(open: boolean, onClose: () => void) {
  const panel = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  useLayoutEffect(() => { close.current = onClose }, [onClose])

  useEffect(() => {
    const dialog = panel.current
    if (!open || !dialog) return
    const opener = document.activeElement
    const controls = () => Array.from(dialog.querySelectorAll<HTMLElement>(focusable))
      .filter(el => el.tabIndex >= 0 && !el.matches(':disabled') && !el.closest('[hidden], [inert]') &&
        (typeof el.checkVisibility !== 'function' || el.checkVisibility({ visibilityProperty: true })))
    const focusFirst = () => (controls()[0] || dialog).focus({ preventScroll: true })
    focusFirst()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        close.current()
      } else if (event.key === 'Tab') {
        const items = controls()
        const next = event.shiftKey ? items.at(-1) : items[0]
        const boundary = event.shiftKey ? items[0] : items.at(-1)
        if (!dialog.contains(document.activeElement) || document.activeElement === dialog || document.activeElement === boundary) {
          event.preventDefault()
          const destination = next || dialog
          destination.focus()
        }
      }
    }
    const onFocus = (event: FocusEvent) => {
      if (event.target instanceof Node && !dialog.contains(event.target)) focusFirst()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('focusin', onFocus)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('focusin', onFocus)
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus({ preventScroll: true })
    }
  }, [open])
  return panel
}
