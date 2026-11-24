import { useEffect, useLayoutEffect, useRef, useState } from 'react'
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

interface SidebarProps {
  page: NavPage
  onNavigate: (next: NavPage) => void
  showExtensions: boolean
  lastSyncMs: number
  manualSyncing: boolean
  syncFailed?: boolean
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
  syncFailed = false,
  onSync,
  authEnabled = false,
  onLock,
  versionStatus,
}: SidebarProps) {
  const { locale, t } = useI18n()
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

  useEffect(() => {
    if (prevSyncingRef.current && !manualSyncing) {
      setJustSynced(true)
      const timer = window.setTimeout(() => setJustSynced(false), 1100)
      prevSyncingRef.current = manualSyncing
      return () => window.clearTimeout(timer)
    }
    prevSyncingRef.current = manualSyncing
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
    <aside
      ref={asideRef}
      data-testid="sidebar"
      className="relative flex flex-col border-b md:border-b-0 md:border-r border-[var(--border)] bg-[var(--rail)] px-3 py-3 md:py-4 w-full md:w-[212px] md:shrink-0 md:sticky md:top-0 md:h-screen transition-colors duration-200"
    >
      {/* Unified 2D Velvet Glider across all 6 navigation items (including Settings & Mobile) */}
      <div
        data-testid="sidebar-glider"
        data-active-page={page}
        aria-hidden="true"
        className="otl-glider absolute left-0 top-0 rounded-lg pointer-events-none z-0"
        style={{
          width: `${glider.width}px`,
          height: `${glider.height}px`,
          opacity: glider.visible ? 1 : 0,
          transform: `translate3d(${glider.left}px, ${glider.top}px, 0)`,
          background: 'var(--accent-soft)',
          boxShadow: '0 1px 10px var(--accent-soft)',
        }}
      >
        <span
          className="hidden md:block absolute left-0 top-2 bottom-2 w-[3px] rounded-full"
          style={{ background: 'var(--accent)' }}
        />
      </div>

      {/* Brand + Mobile Quick Sync Header */}
      <div className="relative z-10 flex items-center justify-between gap-2 px-1.5 pb-2.5 md:px-2 md:pb-5">
        <div className="flex items-center gap-2.5 min-w-0">
          <img
            src={ledgerLogo}
            alt={t('brand')}
            data-testid="brand-logo"
            draggable={false}
            className="h-7 w-7 rounded-lg object-contain shrink-0 select-none"
          />
          <span
            data-testid="brand-title"
            className="text-[15px] font-semibold tracking-tight text-[var(--fg)] truncate"
          >
            {t('brand')}
          </span>
        </div>
        <span className="md:hidden inline-flex items-center gap-1.5 text-[11px] text-[var(--fg-3)] shrink-0">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
          <span>{fmtSyncTime(lastSyncMs)}</span>
        </span>
      </div>

      {/* Primary navigation */}
      <nav
        ref={navRef}
        aria-label={t('brand')}
        className="relative z-10 flex flex-row flex-wrap md:flex-nowrap md:flex-col gap-1"
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
              className="relative z-10 flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] font-medium whitespace-nowrap cursor-pointer"
              style={{
                background: active && !glider.visible ? 'var(--accent-soft)' : 'transparent',
                color: active ? 'var(--accent)' : 'var(--fg-2)',
              }}
            >
              <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
              <span>{item.label}</span>
            </button>
          )
        })}
      </nav>

      {/* Footer: Settings + Read-Only Indicator + Manual Sync + Lock */}
      <div className="relative z-10 mt-4 md:mt-auto pt-3 border-t border-[var(--border)] flex flex-col gap-2">
        <button
          ref={el => {
            itemRefs.current.settings = el
          }}
          type="button"
          data-no-ripple="true"
          data-testid="nav-settings"
          onClick={() => onNavigate('settings')}
          className="relative z-10 flex items-center justify-between gap-2 px-2.5 py-2 rounded-lg text-[13px] font-medium cursor-pointer"
          style={{
            background: page === 'settings' && !glider.visible ? 'var(--accent-soft)' : 'transparent',
            color: page === 'settings' ? 'var(--accent)' : 'var(--fg-2)',
          }}
        >
          <span className="inline-flex items-center gap-2.5">
            <Settings size={16} strokeWidth={1.75} aria-hidden="true" />
            <span>{t('nav_settings')}</span>
          </span>
          {versionStatus?.update_available && (
            <span
              data-testid="sidebar-update-badge"
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10.5px] font-semibold tabular-nums"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
              v{versionStatus.latest_version}
            </span>
          )}
        </button>

        <div className="flex items-center justify-between px-2.5 pt-1 text-[11.5px] text-[var(--fg-3)]">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            <span>{t('read_only')}</span>
          </span>
          <span>{fmtSyncTime(lastSyncMs)}</span>
        </div>

        <div className="flex items-center gap-1.5">
          <button
            type="button"
            data-testid="sync-now-btn"
            disabled={manualSyncing}
            onClick={onSync}
            className="flex-1 flex items-center justify-center gap-1.5 h-8 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] disabled:opacity-50 cursor-pointer"
          >
            {justSynced && !manualSyncing ? (
              syncFailed ? (
                <>
                  <AlertTriangle size={13} className="text-amber-500" />
                  <span className="text-amber-500">
                    {locale === 'en' ? 'Sync Error' : '同步异常'}
                  </span>
                </>
              ) : (
                <>
                  <Check size={13} className="text-emerald-500" />
                  <span className="text-emerald-500">
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
              className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[var(--fg-2)] hover:text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer shrink-0"
            >
              <Lock size={13} />
            </button>
          )}
        </div>
      </div>
    </aside>
  )
}
