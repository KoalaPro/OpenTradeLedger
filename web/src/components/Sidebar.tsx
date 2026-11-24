import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  Activity,
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
  onSync,
  authEnabled = false,
  onLock,
  versionStatus,
}: SidebarProps) {
  const { locale, t } = useI18n()
  const navRef = useRef<HTMLElement | null>(null)
  const itemRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const [glider, setGlider] = useState<{ top: number; height: number; visible: boolean }>({
    top: 0,
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
    const btn = itemRefs.current[page]
    if (!btn) {
      setGlider(g => ({ ...g, visible: false }))
      return
    }
    const h = btn.offsetHeight
    const top = btn.offsetTop
    if (h > 0) {
      setGlider({ top, height: h, visible: true })
    }
  }, [page, showExtensions, locale])

  return (
    <aside
      data-testid="sidebar"
      className="flex flex-col border-r border-[var(--border)] bg-[var(--rail)] px-3 py-4 w-full md:w-[212px] md:shrink-0 md:sticky md:top-0 md:h-screen"
    >
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-2 pb-5">
        <img
          src={ledgerLogo}
          alt={t('brand')}
          data-testid="brand-logo"
          draggable={false}
          className="h-7 w-7 rounded-lg object-contain shrink-0 select-none"
        />
        <span data-testid="brand-title" className="text-[15px] font-semibold tracking-tight text-[var(--fg)]">
          {t('brand')}
        </span>
      </div>

      {/* Primary navigation with smooth Velvet glider */}
      <nav
        ref={navRef}
        aria-label={t('brand')}
        className="relative flex flex-row md:flex-col gap-1 overflow-x-auto md:overflow-visible"
      >
        {glider.visible && (
          <div
            aria-hidden="true"
            className="otl-glider hidden md:block absolute left-0 right-0 rounded-lg pointer-events-none"
            style={{
              height: `${glider.height}px`,
              transform: `translateY(${glider.top}px)`,
              background: 'var(--accent-soft)',
              boxShadow: '0 0 14px var(--accent-soft)',
            }}
          />
        )}

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
              data-testid={`nav-${item.id}`}
              onClick={() => onNavigate(item.id)}
              className="relative z-10 flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] font-medium transition-colors whitespace-nowrap cursor-pointer"
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
      <div className="mt-4 md:mt-auto pt-3 border-t border-[var(--border)] flex flex-col gap-2">
        <button
          type="button"
          data-testid="nav-settings"
          onClick={() => onNavigate('settings')}
          className="flex items-center justify-between gap-2 px-2.5 py-2 rounded-lg text-[13px] font-medium transition-colors cursor-pointer"
          style={{
            background: page === 'settings' ? 'var(--accent-soft)' : 'transparent',
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
            className="flex-1 flex items-center justify-center gap-1.5 h-8 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[12px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] disabled:opacity-50 cursor-pointer transition-colors"
          >
            {justSynced && !manualSyncing ? (
              <>
                <Check size={13} className="text-emerald-500" />
                <span className="text-emerald-500">
                  {locale === 'en' ? 'Synced' : '已同步'}
                </span>
              </>
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
              className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-[var(--border-strong)] bg-[var(--surface)] text-[var(--fg-2)] hover:text-[var(--fg)] hover:bg-[var(--surface-hover)] cursor-pointer shrink-0 transition-colors"
            >
              <Lock size={13} />
            </button>
          )}
        </div>
      </div>
    </aside>
  )
}
