/**
 * Lightweight global pointerdown listener for Style 2 Velvet Spotlight Ripples.
 * Spawns a restrained radial shimmer from the exact click coordinate on interactive
 * buttons and clickable KPI cards (skipping TradesPage table rows & reduced-motion).
 */
export function attachVelvetRippleListener(): () => void {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return () => {}
  }

  const onPointerDown = (e: PointerEvent) => {
    if (e.button !== 0) return
    if (
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ) {
      return
    }

    const target = e.target as HTMLElement | null
    if (!target) return

    const host = target.closest(
      '.otl-card-interactive, button:not([disabled]):not(.otl-switch)',
    ) as HTMLElement | null
    if (!host) return

    // Skip tiny icon-only buttons (< 26px) to keep micro-controls crisp
    const rect = host.getBoundingClientRect()
    if (rect.width < 26 || rect.height < 22) return

    const computed = window.getComputedStyle(host)
    if (computed.position === 'static') {
      host.style.position = 'relative'
    }
    const prevOverflow = host.style.overflow
    if (computed.overflow === 'visible') {
      host.style.overflow = 'hidden'
    }

    const ripple = document.createElement('span')
    ripple.className = 'otl-velvet-ripple'
    const diameter = Math.min(260, Math.max(rect.width, rect.height) * 1.15)
    ripple.style.width = `${diameter}px`
    ripple.style.height = `${diameter}px`
    ripple.style.left = `${e.clientX - rect.left}px`
    ripple.style.top = `${e.clientY - rect.top}px`
    host.appendChild(ripple)

    window.setTimeout(() => {
      ripple.remove()
      if (!host.querySelector('.otl-velvet-ripple') && prevOverflow === '') {
        host.style.overflow = ''
      }
    }, 540)
  }

  document.addEventListener('pointerdown', onPointerDown, { passive: true })
  return () => document.removeEventListener('pointerdown', onPointerDown)
}
