import { useEffect, useRef, useState } from 'react'
import { KeyRound, Languages, Lock, ShieldCheck } from 'lucide-react'
import ledgerLogo from '../assets/ledger-logo.png'
import { api, ApiError } from '../lib/api'
import type { AuthStatusPayload, Locale } from '../lib/types'
import { AnimatedCollapse } from './AnimatedCollapse'

interface AuthGateProps {
  auth: AuthStatusPayload
  authChecked?: boolean
  lockUnconfirmed?: boolean
  locale: Locale
  onToggleLocale: () => void
  onUnlocked: (nextAuth: AuthStatusPayload) => void
  onCeremonyLiftStart?: () => void
}

const TAB_CEREMONY_KEY = 'otl.tab_boot_ceremony'

export function AuthGate({
  auth,
  authChecked = true,
  lockUnconfirmed = false,
  locale,
  onToggleLocale,
  onUnlocked,
  onCeremonyLiftStart,
}: AuthGateProps) {
  const isZh = locale === 'zh-CN'
  const isSetup = Boolean(auth.require_setup)
  const isLocked = isSetup || auth.authenticated === false

  const [password, setPassword] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [remember, setRemember] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [errorText, setErrorText] = useState('')
  const [shakeKey, setShakeKey] = useState(0)
  const [cooldownSec, setCooldownSec] = useState(0)
  const [showResetHelp, setShowResetHelp] = useState(false)
  const [ceremonyMode, setCeremonyMode] = useState<'boot' | 'unlock'>('unlock')
  const [ceremonyPhase, setCeremonyPhase] = useState<'idle' | 'active' | 'lifting'>('idle')
  const checkedBootRef = useRef(false)
  const liftTimerRef = useRef<number | null>(null)
  const idleTimerRef = useRef<number | null>(null)

  const clearCeremonyTimers = () => {
    if (liftTimerRef.current !== null) {
      window.clearTimeout(liftTimerRef.current)
      liftTimerRef.current = null
    }
    if (idleTimerRef.current !== null) {
      window.clearTimeout(idleTimerRef.current)
      idleTimerRef.current = null
    }
  }

  useEffect(() => {
    return () => clearCeremonyTimers()
  }, [])

  // Play the Brand Entrance Curtain once per newly opened browser tab when already unlocked
  useEffect(() => {
    if (!authChecked || checkedBootRef.current) return
    checkedBootRef.current = true

    if (isLocked) {
      return
    }

    const reduced =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced) return

    let alreadyPlayedInTab = false
    try {
      alreadyPlayedInTab = window.sessionStorage.getItem(TAB_CEREMONY_KEY) === '1'
      if (!alreadyPlayedInTab) {
        window.sessionStorage.setItem(TAB_CEREMONY_KEY, '1')
      }
    } catch {
      alreadyPlayedInTab = true
    }

    if (alreadyPlayedInTab) return

    setCeremonyMode('boot')
    setCeremonyPhase('active')
    liftTimerRef.current = window.setTimeout(() => {
      setCeremonyPhase('lifting')
      onCeremonyLiftStart?.()
      liftTimerRef.current = null
    }, 960)
    idleTimerRef.current = window.setTimeout(() => {
      setCeremonyPhase('idle')
      idleTimerRef.current = null
    }, 1420)
  }, [authChecked, isLocked, onCeremonyLiftStart])

  // If an API 401 flips isLocked to true while boot curtain is up, yield immediately to login form
  useEffect(() => {
    if (isLocked && ceremonyMode === 'boot' && ceremonyPhase !== 'idle') {
      clearCeremonyTimers()
      setCeremonyPhase('idle')
    }
  }, [isLocked, ceremonyMode, ceremonyPhase])

  useEffect(() => {
    if (cooldownSec <= 0) return
    const timer = window.setInterval(() => {
      setCooldownSec(s => Math.max(0, s - 1))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [cooldownSec])

  const triggerShake = (msg: string) => {
    setErrorText(msg)
    setShakeKey(k => k + 1)
  }

  const runCeremonyAndUnlock = (nextAuth: AuthStatusPayload) => {
    const reduced =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches

    try {
      window.sessionStorage.setItem(TAB_CEREMONY_KEY, '1')
    } catch { /* ignore storage errors */ }

    onUnlocked(nextAuth)
    setPassword('')
    setConfirmPwd('')
    setErrorText('')

    if (reduced) {
      setCeremonyPhase('idle')
      return
    }

    clearCeremonyTimers()
    setCeremonyMode('unlock')
    setCeremonyPhase('active')
    liftTimerRef.current = window.setTimeout(() => {
      setCeremonyPhase('lifting')
      onCeremonyLiftStart?.()
      liftTimerRef.current = null
    }, 1020)
    idleTimerRef.current = window.setTimeout(() => {
      setCeremonyPhase('idle')
      idleTimerRef.current = null
    }, 1480)
  }

  const handleSubmit = async (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    if (submitting || cooldownSec > 0) return

    const pwd = password.trim()
    if (pwd.length < 4) {
      triggerShake(isZh ? '密码长度至少需要 4 位字符' : 'Password must be at least 4 characters')
      return
    }
    if (isSetup && pwd !== confirmPwd.trim()) {
      triggerShake(isZh ? '两次输入的密码不一致，请核对' : 'Passwords do not match')
      return
    }

    setSubmitting(true)
    setErrorText('')
    try {
      const res = isSetup
        ? await api.authSetup(pwd, remember)
        : await api.authLogin(pwd, remember)
      runCeremonyAndUnlock(res)
    } catch (err) {
      if (err instanceof ApiError && err.retryAfterSec > 0) {
        setCooldownSec(err.retryAfterSec)
        triggerShake(
          isZh
            ? `连续输错次数过多，请等待 ${err.retryAfterSec} 秒后再试`
            : `Too many failed attempts. Please wait ${err.retryAfterSec}s`,
        )
      } else {
        const rawMsg = err instanceof Error ? err.message : 'Authentication failed'
        if (rawMsg.toLowerCase().includes('incorrect password')) {
          const m = rawMsg.match(/\((\d+)\s+attempts/i)
          triggerShake(
            isZh
              ? m
                ? `密码不正确（剩余 ${m[1]} 次尝试后进入冷却）`
                : '密码不正确，请重试'
              : rawMsg,
          )
        } else {
          triggerShake(rawMsg)
        }
      }
    } finally {
      setSubmitting(false)
    }
  }

  const handleSkip = async () => {
    if (submitting) return
    setSubmitting(true)
    setErrorText('')
    try {
      const res = await api.authSkip()
      try {
        window.sessionStorage.setItem(TAB_CEREMONY_KEY, '1')
      } catch { /* ignore */ }
      setPassword('')
      setConfirmPwd('')
      onUnlocked(res)
    } catch (err) {
      triggerShake(err instanceof Error ? err.message : 'Failed to skip password setup')
    } finally {
      setSubmitting(false)
    }
  }

  if (!isLocked && ceremonyPhase === 'idle') {
    return null
  }

  return (
    <>
      {/* Brand Entrance & Unlock Shimmer Curtain */}
      {ceremonyPhase !== 'idle' && (
        <div
          data-testid="auth-ceremony-overlay"
          className={`fixed inset-0 z-50 flex flex-col items-center justify-center bg-[var(--bg)] otl-ceremony-curtain ${
            ceremonyPhase === 'lifting' ? 'lifting' : ''
          }`}
        >
          <img
            src={ledgerLogo}
            alt="OpenTradeLedger"
            draggable={false}
            className="otl-brand-logo-ceremony select-none mb-4 otl-ceremony-logo"
          />
          <div className="text-[18px] font-semibold tracking-wide otl-ceremony-shimmer">
            OpenTradeLedger · 交易账本
          </div>
          <div className="mt-1.5 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--fg-2)]">
            <ShieldCheck size={14} style={{ color: 'var(--ok)' }} />
            <span>
              {ceremonyMode === 'boot'
                ? isZh
                  ? '本地只读核账账本已就绪'
                  : 'Local Read-Only Ledger Ready'
                : isZh
                  ? '本地只读账本已验证解锁'
                  : 'Local Read-Only Ledger Verified & Unlocked'}
            </span>
          </div>
          <div className="w-44 h-[2.5px] rounded-full bg-[var(--track)] overflow-hidden mt-4">
            <div className="otl-ceremony-bar-fill" />
          </div>
        </div>
      )}

      {/* Login / First-Time NAS Password Setup Gate */}
      {isLocked && ceremonyPhase === 'idle' && (
        <div
          data-testid="auth-gate-overlay"
          className="fixed inset-0 z-40 flex items-center justify-center p-4 bg-[var(--bg)]"
        >
          <button
            type="button"
            onClick={onToggleLocale}
            className="otl-btn fixed top-5 right-5 z-10"
          >
            <Languages size={14} />
            <span>{isZh ? 'EN' : '中文'}</span>
          </button>

          <form
            key={shakeKey}
            onSubmit={e => void handleSubmit(e)}
            data-testid={isSetup ? 'auth-setup-card' : 'auth-login-card'}
            className={`relative w-full max-w-[396px] otl-card p-6 space-y-4 ${
              shakeKey > 0 && errorText ? 'otl-shake' : 'otl-page-enter'
            }`}
          >
            <div className="flex items-center gap-3.5">
              <img
                src={ledgerLogo}
                alt="OpenTradeLedger"
                draggable={false}
                className="otl-brand-logo-lg select-none"
              />
              <div className="min-w-0">
                <h1 className="t-h2 truncate">
                  {isSetup
                    ? isZh
                      ? '欢迎使用 · 设置访问密码'
                      : 'Welcome · Set Access Password'
                    : isZh
                      ? '交易账本 · 本地解锁'
                      : 'OpenTradeLedger · Unlock'}
                </h1>
                <p className="m-0 text-[13px] text-[var(--fg-3)] mt-0.5">
                  {isSetup
                    ? isZh
                      ? '密码经加盐哈希仅存于本地，保护 NAS / 局域网访问'
                      : 'Salted scrypt hash stored locally for NAS / LAN protection'
                    : isZh
                      ? '本地优先 · 严格 GET 只读模式'
                      : 'Local-first · Strictly Read-Only Mode'}
                </p>
              </div>
            </div>

            {lockUnconfirmed && (
              <p role="status" className="text-[13px] text-[var(--fg-2)]">
                {isZh ? '本页已遮住，服务端锁定尚未确认。' : 'This page is hidden. Server lock is awaiting confirmation.'}
              </p>
            )}

            <div className="space-y-2.5 pt-1">
              <div className="relative">
                <Lock
                  size={14}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--fg-3)] pointer-events-none"
                />
                <input
                  type="password"
                  autoFocus
                  data-testid="auth-password-input"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder={
                    isSetup
                      ? isZh
                        ? '设置访问密码（至少 4 位）'
                        : 'Create password (min 4 chars)'
                      : isZh
                        ? '输入本地访问密码'
                        : 'Enter access password'
                  }
                  className="otl-input w-full h-9 pl-9 pr-3"
                />
              </div>

              {isSetup && (
                <div className="relative">
                  <KeyRound
                    size={14}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--fg-3)] pointer-events-none"
                  />
                  <input
                    type="password"
                    data-testid="auth-confirm-input"
                    value={confirmPwd}
                    onChange={e => setConfirmPwd(e.target.value)}
                    placeholder={isZh ? '再次确认访问密码' : 'Confirm access password'}
                    className="otl-input w-full h-9 pl-9 pr-3"
                  />
                </div>
              )}
            </div>

            <div className="flex items-center justify-between gap-2 text-[13px]">
              <label className="inline-flex items-center gap-2 text-[var(--fg-2)] cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={e => setRemember(e.target.checked)}
                />
                <span>{isZh ? '记住此设备 30 天' : 'Remember device for 30 days'}</span>
              </label>

              {!isSetup && (
                <button
                  type="button"
                  onClick={() => setShowResetHelp(v => !v)}
                  className="text-[13px] text-[var(--fg-3)] hover:text-[var(--fg)] cursor-pointer"
                >
                  {isZh ? '忘记密码？' : 'Forgot password?'}
                </button>
              )}
            </div>

            <AnimatedCollapse open={showResetHelp && !isSetup}>
              <div className="otl-surface-2 p-3 text-[13px] text-[var(--fg-2)] space-y-1.5">
                <div className="font-medium text-[var(--fg)]">
                  {isZh ? '本地安全重置方式（不伤交易数据与 API 密钥）：' : 'Safe local reset (keeps all DB & API keys intact):'}
                </div>
                <code className="block px-2.5 py-1.5 rounded bg-[var(--bg)] border border-[var(--border)] text-[13px] font-mono break-all">
                  python scripts/run_server.py --reset-password --data-dir ./data
                </code>
              </div>
            </AnimatedCollapse>

            {(errorText || cooldownSec > 0) && (
              <div
                data-testid="auth-error-banner"
                className="otl-status-err px-3 py-2 rounded-md text-[13px] font-medium"
              >
                {cooldownSec > 0
                  ? isZh
                    ? `尝试过于频繁，请等待 ${cooldownSec} 秒后重试`
                    : `Locked temporarily · retry in ${cooldownSec}s`
                  : errorText}
              </div>
            )}

            <div className="space-y-2 pt-0.5">
              <button
                type="submit"
                data-testid="auth-submit-btn"
                disabled={submitting || cooldownSec > 0}
                className="otl-btn otl-btn-primary w-full h-9 text-[14px]"
              >
                {submitting
                  ? isZh
                    ? '验证中…'
                    : 'Verifying…'
                  : isSetup
                    ? isZh
                      ? '保存密码并进入账本 →'
                      : 'Save Password & Enter →'
                    : isZh
                      ? '解锁进入账本 →'
                      : 'Unlock Ledger →'}
              </button>

              {isSetup && auth.can_skip !== false && (
                <button
                  type="button"
                  data-testid="auth-skip-btn"
                  disabled={submitting}
                  onClick={() => void handleSkip()}
                  className="otl-btn-ghost w-full justify-center"
                >
                  {isZh
                    ? '跳过，仅本机免密使用（设置页可随时开启）'
                    : 'Skip for local use (can enable anytime in Settings)'}
                </button>
              )}
            </div>
          </form>
        </div>
      )}
    </>
  )
}
