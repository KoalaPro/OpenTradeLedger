/**
 * Lightweight global pointerdown listener for restrained desktop spotlight ripples.
 * Spawns a damped radial highlight using the active theme's `--accent-glow` token
 * on interactive ledger cells and buttons (skipping table rows, switches & reduced-motion).
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
      '.otl-card-interactive, button:not([disabled]):not(.otl-switch):not([data-no-ripple])',
    ) as HTMLElement | null
    if (!host || host.hasAttribute('data-no-ripple')) return

    const rect = host.getBoundingClientRect()
    if (rect.width < 28 || rect.height < 22) return

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
    const diameter = Math.min(220, Math.max(rect.width, rect.height) * 1.1)
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
    }, 500)
  }

  document.addEventListener('pointerdown', onPointerDown, { passive: true })
  return () => document.removeEventListener('pointerdown', onPointerDown)
}
