import { useLayoutEffect, useRef, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  BookOpen,
  Check,
  LayoutDashboard,
  LayoutGrid,
  Lock,
  RefreshCw,
  Settings,
  TrendingUp,
} from 'lucide-react'
import ledgerLogo from '../assets/ledger-logo.png'
import { useI18n } from '../i18n'
import { fmtSyncTime } from '../lib/format'
import type { NavPage, VersionCheckPayload } from '../lib/types'

export type ManualSyncResult = 'synced' | 'failed' | 'no_accounts' | 'demo'

interface SidebarProps {
  page: NavPage
  onNavigate: (next: NavPage) => void
  showExtensions: boolean
  lastSyncMs: number
  manualSyncing: boolean
  syncResult?: ManualSyncResult
  syncPartialWarning?: boolean
  syncWarningTitle?: string
  onSync: () => void
  authEnabled?: boolean
  onLock?: () => void
  versionStatus?: VersionCheckPayload | null
}

export function Sidebar({
  page,
  onNavigate,
  showExtensions,
  lastSyncMs,
  manualSyncing,
  syncResult,
  syncPartialWarning = false,
  syncWarningTitle = '',
  onSync,
  authEnabled = false,
  onLock,
  versionStatus,
}: SidebarProps) {
  const { locale, t } = useI18n()
  const syncFailed = syncResult === 'failed'
  const asideRef = useRef<HTMLElement | null>(null)
  const navRef = useRef<HTMLElement | null>(null)
  const itemRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const [glider, setGlider] = useState<{
    left: number
    top: number
    width: number
    height: number
    visible: boolean
  }>({
    left: 12,
    top: 0,
    width: 0,
    height: 36,
    visible: false,
  })
  const prevSyncingRef = useRef(manualSyncing)
  const [justSynced, setJustSynced] = useState(false)

  if (manualSyncing) {
    prevSyncingRef.current = true
  }

  useLayoutEffect(() => {
    if (prevSyncingRef.current && !manualSyncing) {
      prevSyncingRef.current = false
      setJustSynced(true)
      const timer = window.setTimeout(() => setJustSynced(false), 1500)
      return () => window.clearTimeout(timer)
    }
  }, [manualSyncing])

  const primaryItems: Array<{ id: NavPage; label: string; icon: typeof LayoutDashboard }> = [
    { id: 'overview', label: t('nav_overview'), icon: LayoutDashboard },
    { id: 'trades', label: t('nav_trades'), icon: BookOpen },
    { id: 'analytics', label: t('nav_analytics'), icon: TrendingUp },
    { id: 'fees', label: t('nav_fees'), icon: Activity },
  ]
  if (showExtensions) {
    primaryItems.push({ id: 'extensions', label: t('nav_extensions'), icon: LayoutGrid })
  }

  useLayoutEffect(() => {
    const syncGlider = () => {
      const aside = asideRef.current
      const btn = itemRefs.current[page]
      if (!aside || !btn) {
        setGlider(g => (g.visible ? { ...g, visible: false } : g))
        return
      }
      const asideRect = aside.getBoundingClientRect()
      const btnRect = btn.getBoundingClientRect()
      if (btnRect.width > 0 && btnRect.height > 0) {
        setGlider({
          left: btnRect.left - asideRect.left - aside.clientLeft,
          top: btnRect.top - asideRect.top - aside.clientTop,
          width: btnRect.width,
          height: btnRect.height,
          visible: true,
        })
        return
      }
      if (btn.offsetWidth > 0 && btn.offsetHeight > 0) {
        let l = 0
        let tp = 0
        let curEl: HTMLElement | null = btn
        while (curEl && curEl !== aside) {
          l += curEl.offsetLeft
          tp += curEl.offsetTop
          curEl = curEl.offsetParent as HTMLElement | null
        }
        setGlider({
          left: l,
          top: tp,
          width: btn.offsetWidth,
          height: btn.offsetHeight,
          visible: true,
        })
      }
    }

    syncGlider()
    if (typeof ResizeObserver !== 'undefined' && asideRef.current) {
      const ro = new ResizeObserver(() => syncGlider())
      ro.observe(asideRef.current)
      if (navRef.current) ro.observe(navRef.current)
      return () => ro.disconnect()
    }
  }, [page, showExtensions, locale, versionStatus?.update_available])

  return (
    <aside ref={asideRef} data-testid="sidebar" className="otl-sidebar">
      {/* Persistent 2D Architectural Rail Glider */}
      <div
        data-testid="sidebar-glider"
        data-active-page={page}
        aria-hidden="true"
        className="otl-glider hidden md:block absolute left-0 top-0 rounded-[7px] pointer-events-none z-0"
        style={{
          width: `${glider.width}px`,
          height: `${glider.height}px`,
          opacity: glider.visible ? 1 : 0,
          transform: `translate3d(${glider.left}px, ${glider.top}px, 0)`,
          background: 'var(--accent-soft)',
        }}
      >
        <span
          className="hidden md:block absolute left-0 top-[7px] bottom-[7px] w-[2.5px] rounded-full"
          style={{ background: 'var(--accent)' }}
        />
      </div>

      {/* Brand Wordmark + Mobile Status */}
      <div className="relative z-10 flex items-center justify-between gap-2 px-1.5 pb-1 md:px-2 md:pb-4">
        <div className="flex items-center gap-2.5 min-w-0">
          <img
            src={ledgerLogo}
            alt={t('brand')}
            data-testid="brand-logo"
            draggable={false}
            className="otl-brand-logo select-none"
          />
          <span
            data-testid="brand-title"
            className="text-[16px] font-semibold tracking-[-0.02em] text-[var(--fg)] truncate"
          >
            {t('brand')}
          </span>
        </div>
        <span
          title={syncWarningTitle || undefined}
          className="md:hidden inline-flex items-center gap-1.5 text-[12px] text-[var(--fg-3)] shrink-0"
        >
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: syncFailed || syncPartialWarning ? 'var(--warn)' : 'var(--ok)' }}
          />
          {syncPartialWarning && !syncFailed && (
            <span style={{ color: 'var(--warn)' }} className="font-medium">
              {locale === 'en' ? 'Partial Sync' : '部分同步'}
            </span>
          )}
          <span>{fmtSyncTime(lastSyncMs)}</span>
        </span>
      </div>

      {/* Primary Navigation */}
      <nav
        ref={navRef}
        aria-label={t('brand')}
        className="otl-primary-nav no-scrollbar relative z-10 flex flex-row overflow-x-auto md:overflow-visible flex-nowrap md:flex-col gap-0.5"
      >
        {primaryItems.map(item => {
          const Icon = item.icon
          const active = page === item.id
          return (
            <button
              key={item.id}
              ref={el => {
                itemRefs.current[item.id] = el
              }}
              type="button"
              data-no-ripple="true"
              data-testid={`nav-${item.id}`}
              onClick={() => onNavigate(item.id)}
              className={`otl-nav-btn ${active ? 'active' : ''}`}
              style={{
                background: active && !glider.visible ? 'var(--accent-soft)' : undefined,
              }}
            >
              <Icon size={16} strokeWidth={1.7} aria-hidden="true" />
              <span>{item.label}</span>
            </button>
          )
        })}
      </nav>

      {/* Footer: Settings + Read-Only Sync + Lock */}
      <div className="relative z-10 mt-1 md:mt-auto pt-2.5 border-t border-[var(--border)] flex flex-col gap-2">
        <button
          ref={el => {
            itemRefs.current.settings = el
          }}
          type="button"
          data-no-ripple="true"
          data-testid="nav-settings"
          onClick={() => onNavigate('settings')}
          className={`otl-nav-btn justify-between ${page === 'settings' ? 'active' : ''}`}
          style={{
            background: page === 'settings' && !glider.visible ? 'var(--accent-soft)' : undefined,
          }}
        >
          <span className="inline-flex items-center gap-2.5">
            <Settings size={16} strokeWidth={1.7} aria-hidden="true" />
            <span>{t('nav_settings')}</span>
          </span>
          {versionStatus?.update_available && (
            <span
              data-testid="sidebar-update-badge"
              className="inline-flex items-center gap-1.5 text-[12px] font-medium tabular-nums"
              style={{ color: 'var(--accent)' }}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
              v{versionStatus.latest_version}
            </span>
          )}
        </button>

        <div
          title={syncWarningTitle || undefined}
          className="hidden md:flex items-center justify-between px-3 pt-0.5 text-[12px] text-[var(--fg-3)]"
        >
          <span className="inline-flex items-center gap-1.5">
            <span
              className="h-1.5 w-1.5 rounded-full"
              style={{ background: syncFailed || syncPartialWarning ? 'var(--warn)' : 'var(--ok)' }}
            />
            <span>
              {syncPartialWarning && !syncFailed
                ? locale === 'en'
                  ? 'Partial Sync'
                  : '部分同步'
                : t('read_only')}
            </span>
          </span>
          <span>{fmtSyncTime(lastSyncMs)}</span>
        </div>

        <div className="flex items-center gap-1.5">
          <button
            type="button"
            data-testid="sync-now-btn"
            title={syncWarningTitle || undefined}
            aria-live="polite"
            disabled={manualSyncing}
            onClick={onSync}
            className="otl-btn flex-1"
          >
            {justSynced && !manualSyncing && syncResult ? (
              syncResult === 'no_accounts' ? (
                <span>{locale === 'en' ? 'No accounts to sync' : '没有可同步的账户'}</span>
              ) : syncResult === 'demo' ? (
                <span>{locale === 'en' ? 'Demo data' : '演示数据'}</span>
              ) : syncFailed ? (
                <>
                  <AlertTriangle size={13} style={{ color: 'var(--warn)' }} />
                  <span style={{ color: 'var(--warn)' }}>
                    {locale === 'en' ? 'Sync Error' : '同步异常'}
                  </span>
                </>
              ) : syncPartialWarning ? (
                <>
                  <AlertTriangle size={13} style={{ color: 'var(--warn)' }} />
                  <span style={{ color: 'var(--warn)' }}>
                    {locale === 'en' ? 'Partial Sync' : '部分同步'}
                  </span>
                </>
              ) : (
                <>
                  <Check size={13} style={{ color: 'var(--ok)' }} />
                  <span style={{ color: 'var(--ok)' }}>
                    {locale === 'en' ? 'Synced' : '已同步'}
                  </span>
                </>
              )
            ) : (
              <>
                <RefreshCw size={13} className={manualSyncing ? 'animate-spin' : ''} />
                <span>{manualSyncing ? t('syncing') : t('sync_now')}</span>
              </>
            )}
          </button>
          {authEnabled && onLock && (
            <button
              type="button"
              data-testid="sidebar-lock-btn"
              title={locale === 'en' ? 'Lock Ledger' : '立即锁定账本'}
              aria-label={locale === 'en' ? 'Lock Ledger' : '立即锁定账本'}
              onClick={onLock}
              className="otl-btn w-8 px-0 shrink-0 text-[var(--fg-2)] hover:text-[var(--fg)]"
            >
              <Lock size={13} />
            </button>
          )}
        </div>
      </div>
    </aside>
  )
}
