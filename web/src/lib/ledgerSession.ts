import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, AUTH_REQUIRED_EVENT, invalidateAccessRequests, type ApiError } from './api'
import type { AuthStatusPayload } from './types'

const CHANGE_KEY = 'otl.ledger-changed'
const STATUS_TIMEOUT_MS = 8000
const LEDGER_CHECK_INTERVAL_MS = 15000
const PENDING_LOCK_KEY = 'otl.logout-pending'
let pendingWithoutStorage = false

function setPendingLock(pending: boolean) {
  pendingWithoutStorage = pending
  try {
    localStorage.setItem(PENDING_LOCK_KEY, pending ? '1' : '')
  } catch { /* This tab retains the lock when storage is unavailable. */ }
}

function hasPendingLock() {
  try {
    return localStorage.getItem(PENDING_LOCK_KEY) === '1'
  } catch {
    return pendingWithoutStorage
  }
}

function notifyOtherTabs() {
  try {
    // Each mutation must change the value, including consecutive logouts.
    localStorage.setItem(CHANGE_KEY, JSON.stringify([Date.now(), Math.random()]))
  } catch {
    if (typeof BroadcastChannel !== 'undefined') {
      const channel = new BroadcastChannel(CHANGE_KEY)
      channel.postMessage('changed')
      channel.close()
    }
  }
}

function locked(status: AuthStatusPayload) {
  return Boolean(status.require_setup || status.authenticated === false)
}

export function useLedgerSession(clearPrivateUi: () => void) {
  const [authStatus, setAuthStatus] = useState<AuthStatusPayload>({})
  const [authChecked, setAuthChecked] = useState(false)
  const [checkError, setCheckError] = useState(false)
  const [lockUnconfirmed, setLockUnconfirmed] = useState(false)
  const [generation, setGeneration] = useState(0)
  const lastStatus = useRef<AuthStatusPayload | null>(null)
  const request = useRef(0)
  const checkedRequest = useRef(0)
  const activeCheck = useRef<AbortController | null>(null)
  const changing = useRef(false)
  const allowed = useRef(false)

  const invalidate = useCallback(() => {
    ++request.current
    activeCheck.current?.abort()
    allowed.current = false
    invalidateAccessRequests()
    clearPrivateUi()
  }, [clearPrivateUi])

  const apply = useCallback((next: AuthStatusPayload) => {
    const pending = hasPendingLock() && Boolean(next.enabled)
    setLockUnconfirmed(pending)
    if (pending) next = { ...next, authenticated: false }
    const previous = lastStatus.current
    const modeChanged = previous && next.explicit_demo_mode !== undefined &&
      previous.explicit_demo_mode !== next.explicit_demo_mode
    const reload = !allowed.current || modeChanged
    if (allowed.current && (locked(next) || modeChanged)) invalidate()
    allowed.current = !locked(next)
    lastStatus.current = next
    setAuthStatus(next)
    setAuthChecked(true)
    setCheckError(false)
    if (reload && allowed.current) setGeneration(value => value + 1)
  }, [invalidate])

  const check = useCallback(async (reset = false, background = false) => {
    if (changing.current) return
    if (background && (document.visibilityState === 'hidden' || activeCheck.current || !allowed.current)) return
    if (reset) invalidate()
    const id = ++request.current
    activeCheck.current?.abort()
    const controller = new AbortController()
    activeCheck.current = controller
    const timeout = window.setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS)
    if (!background) document.documentElement.dataset.sessionCheck = 'pending'
    try {
      const status = await api.authStatus(controller.signal)
      if (id !== request.current) return
      apply(status)
    } catch {
      if (id !== request.current || background) return
      invalidate()
      lastStatus.current = null
      setAuthStatus({})
      setAuthChecked(false)
      setCheckError(true)
    } finally {
      window.clearTimeout(timeout)
      if (activeCheck.current === controller) activeCheck.current = null
    }
    checkedRequest.current = request.current
  }, [apply, invalidate])

  // Only lift the curtain after React has committed the guarded UI, including
  // closing body-level portals if this check discovered a lock or mode change.
  useLayoutEffect(() => {
    if (checkedRequest.current === request.current && document.visibilityState !== 'hidden') {
      delete document.documentElement.dataset.sessionCheck
    }
  }, [authStatus, checkError, generation])

  const beginChange = useCallback(() => {
    changing.current = true
    invalidate()
    document.documentElement.dataset.sessionCheck = 'pending'
  }, [invalidate])

  const recheckAfterChange = useCallback(async () => {
    changing.current = false
    await check()
  }, [check])

  const changed = useCallback((next: AuthStatusPayload) => {
    changing.current = false
    invalidate()
    apply(next)
    checkedRequest.current = request.current
    notifyOtherTabs()
  }, [apply, invalidate])

  const lock = useCallback(async () => {
    beginChange()
    setPendingLock(true)
    notifyOtherTabs()
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS)
    try {
      const next = await api.authLogout(controller.signal)
      setPendingLock(false)
      changed({ ...next, authenticated: false })
    } catch {
      changed({ ...lastStatus.current, authenticated: false })
    } finally {
      window.clearTimeout(timeout)
    }
  }, [beginChange, changed])

  const unlocked = useCallback((next: AuthStatusPayload) => {
    setPendingLock(false)
    changed(next)
  }, [changed])

  useEffect(() => {
    const onAuthRequired = (event: Event) => {
      const { requireSetup } = (event as CustomEvent<ApiError>).detail
      ++request.current
      apply({ ...lastStatus.current, authenticated: false, require_setup: requireSetup })
      checkedRequest.current = request.current
    }
    const onRemoteChange = () => { void check(true) }
    const onStorage = (event: StorageEvent) => {
      if (event.key === CHANGE_KEY) onRemoteChange()
    }
    const onFocus = () => { void check() }
    const onVisibility = () => {
      if (document.visibilityState !== 'hidden') onFocus()
      else document.documentElement.dataset.sessionCheck = 'pending'
    }
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) onFocus()
    }
    const onPageHide = () => { document.documentElement.dataset.sessionCheck = 'pending' }
    const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel(CHANGE_KEY) : null
    channel?.addEventListener('message', onRemoteChange)
    window.addEventListener(AUTH_REQUIRED_EVENT, onAuthRequired)
    window.addEventListener('storage', onStorage)
    window.addEventListener('focus', onFocus)
    window.addEventListener('pageshow', onPageShow)
    window.addEventListener('pagehide', onPageHide)
    document.addEventListener('visibilitychange', onVisibility)
    document.addEventListener('freeze', onPageHide)
    document.addEventListener('resume', onFocus)
    const timer = window.setInterval(() => { void check(false, true) }, LEDGER_CHECK_INTERVAL_MS)
    void check()
    return () => {
      window.clearInterval(timer)
      ++request.current
      activeCheck.current?.abort()
      channel?.close()
      window.removeEventListener(AUTH_REQUIRED_EVENT, onAuthRequired)
      window.removeEventListener('storage', onStorage)
      window.removeEventListener('focus', onFocus)
      window.removeEventListener('pageshow', onPageShow)
      window.removeEventListener('pagehide', onPageHide)
      document.removeEventListener('visibilitychange', onVisibility)
      document.removeEventListener('freeze', onPageHide)
      document.removeEventListener('resume', onFocus)
      delete document.documentElement.dataset.sessionCheck
    }
  }, [apply, check])
  return {
    authStatus, authChecked, checkError, lockUnconfirmed, generation, allowed,
    beginChange, changed, check, lock, unlocked, recheckAfterChange, notifyOtherTabs,
  }
}
