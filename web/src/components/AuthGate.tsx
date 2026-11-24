import { useEffect, useState } from 'react'
import { KeyRound, Languages, Lock, ShieldCheck } from 'lucide-react'
import ledgerLogo from '../assets/ledger-logo.png'
import { api, ApiError } from '../lib/api'
import type { AuthStatusPayload, Locale } from '../lib/types'
import { AnimatedCollapse } from './AnimatedCollapse'

interface AuthGateProps {
  auth: AuthStatusPayload
  locale: Locale
  onToggleLocale: () => void
  onUnlocked: (nextAuth: AuthStatusPayload) => void
}

export function AuthGate({ auth, locale, onToggleLocale, onUnlocked }: AuthGateProps) {
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
  const [ceremonyPhase, setCeremonyPhase] = useState<'idle' | 'active' | 'lifting'>('idle')

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

    // Start loading dashboard data immediately underneath the ceremony curtain
    onUnlocked(nextAuth)
    setPassword('')
    setConfirmPwd('')
    setErrorText('')

    if (reduced) {
      setCeremonyPhase('idle')
      return
    }

    setCeremonyPhase('active')
    window.setTimeout(() => {
      setCeremonyPhase('lifting')
    }, 1050)
    window.setTimeout(() => {
      setCeremonyPhase('idle')
    }, 1520)
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
      {/* Ceremony C — Brand Shimmer & Curtain Lift (~1.5s) */}
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
            className="w-16 h-16 rounded-2xl object-contain select-none mb-4 otl-ceremony-logo"
          />
          <div className="text-[20px] font-semibold tracking-wide otl-ceremony-shimmer">
            OpenTradeLedger · 交易账本
          </div>
          <div className="mt-1.5 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--fg-2)]">
            <ShieldCheck size={14} className="text-emerald-400" />
            <span>
              {isZh
                ? '本地只读账本已验证解锁'
                : 'Local Read-Only Ledger Verified & Unlocked'}
            </span>
          </div>
          <div className="w-40 h-[2.5px] rounded-full bg-[var(--track)] overflow-hidden mt-4">
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
          {/* Subtle ambient radial glow */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute w-[360px] h-[360px] rounded-full opacity-50 blur-3xl"
            style={{
              background:
                'radial-gradient(circle, rgba(245,158,11,0.16), rgba(59,130,246,0.10) 52%, transparent 74%)',
            }}
          />

          {/* Top-right locale switcher */}
          <button
            type="button"
            onClick={onToggleLocale}
            className="fixed top-5 right-5 inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] font-medium text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer z-10"
          >
            <Languages size={14} />
            <span>{isZh ? 'EN' : '中文'}</span>
          </button>

          <form
            key={shakeKey}
            onSubmit={e => void handleSubmit(e)}
            data-testid={isSetup ? 'auth-setup-card' : 'auth-login-card'}
            className={`relative w-full max-w-[388px] otl-card p-6 space-y-4 ${
              shakeKey > 0 && errorText ? 'otl-shake' : 'otl-page-enter'
            }`}
          >
            {/* Brand Header */}
            <div className="flex items-center gap-3.5">
              <img
                src={ledgerLogo}
                alt="OpenTradeLedger"
                draggable={false}
                className="w-11 h-11 rounded-xl object-contain shrink-0 select-none"
              />
              <div className="min-w-0">
                <h1 className="m-0 text-[16px] font-semibold text-[var(--fg)] truncate">
                  {isSetup
                    ? isZh
                      ? '欢迎使用 · 设置访问密码'
                      : 'Welcome · Set Access Password'
                    : isZh
                      ? '交易账本 · 本地解锁'
                      : 'OpenTradeLedger · Unlock'}
                </h1>
                <p className="m-0 text-[12px] text-[var(--fg-3)] mt-0.5">
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

            {/* Password Inputs */}
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
                  placeholder={isSetup ? (isZh ? '设置访问密码（至少 4 位）' : 'Create password (min 4 chars)') : isZh ? '输入本地访问密码' : 'Enter access password'}
                  className="w-full h-9 pl-9 pr-3 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[13px] text-[var(--fg)] focus:outline-none focus:border-[var(--accent)]"
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
                    className="w-full h-9 pl-9 pr-3 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[13px] text-[var(--fg)] focus:outline-none focus:border-[var(--accent)]"
                  />
                </div>
              )}
            </div>

            {/* Remember device & Forget password hint */}
            <div className="flex items-center justify-between gap-2 text-[12px]">
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
                  className="text-[11.5px] text-[var(--fg-3)] hover:text-[var(--fg)] cursor-pointer"
                >
                  {isZh ? '忘记密码？' : 'Forgot password?'}
                </button>
              )}
            </div>

            <AnimatedCollapse open={showResetHelp && !isSetup}>
              <div className="otl-surface-2 rounded-lg p-3 text-[11.5px] text-[var(--fg-2)] space-y-1">
                <div className="font-medium text-[var(--fg)]">
                  {isZh ? '本地安全重置方式（不伤交易数据与 API 密钥）：' : 'Safe local reset (keeps all DB & API keys intact):'}
                </div>
                <code className="block px-2 py-1 rounded bg-[var(--bg)] border border-[var(--border)] text-[11px] font-mono break-all">
                  python scripts/run_server.py --reset-password --data-dir ./data
                </code>
              </div>
            </AnimatedCollapse>

            {/* Error or Cooldown message */}
            {(errorText || cooldownSec > 0) && (
              <div
                data-testid="auth-error-banner"
                className="otl-status-err px-3 py-2 rounded-lg text-[12px] font-medium"
              >
                {cooldownSec > 0
                  ? isZh
                    ? `尝试过于频繁，请等待 ${cooldownSec} 秒后重试`
                    : `Locked temporarily — retry in ${cooldownSec}s`
                  : errorText}
              </div>
            )}

            {/* Action Buttons */}
            <div className="space-y-2 pt-0.5">
              <button
                type="submit"
                data-testid="auth-submit-btn"
                disabled={submitting || cooldownSec > 0}
                className="w-full h-9 rounded-lg text-[13px] font-semibold cursor-pointer disabled:opacity-50 transition"
                style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
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
                  className="w-full h-8 rounded-lg text-[12px] text-[var(--fg-3)] hover:text-[var(--fg)] hover:bg-[var(--surface-2)] cursor-pointer transition"
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
