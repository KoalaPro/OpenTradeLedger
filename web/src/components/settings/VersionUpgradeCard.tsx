import { useState } from 'react'
import { ArrowUpCircle, Check, ChevronDown, Copy, ExternalLink, RefreshCw } from 'lucide-react'
import { useI18n } from '../../i18n'
import { api } from '../../lib/api'
import type { UserSettings, VersionCheckPayload } from '../../lib/types'
import { AnimatedCollapse } from '../AnimatedCollapse'

interface VersionUpgradeCardProps {
  settings: UserSettings
  versionStatus?: VersionCheckPayload | null
  onCheckVersion?: (force?: boolean) => Promise<VersionCheckPayload | null>
  onUpdateSettings: (patch: Record<string, unknown>) => Promise<void | boolean>
  flashOk: (msg: string, ms?: number) => void
  setErrorMsg: (msg: string) => void
}

export function VersionUpgradeCard({
  settings,
  versionStatus,
  onCheckVersion,
  onUpdateSettings,
  flashOk,
  setErrorMsg,
}: VersionUpgradeCardProps) {
  const { locale } = useI18n()
  const isZh = locale === 'zh-CN'

  const [checkingVersion, setCheckingVersion] = useState(false)
  const [upgradingStep, setUpgradingStep] = useState<'' | 'downloading' | 'restarting' | 'done'>('')
  const [manualGuideOpen, setManualGuideOpen] = useState(false)
  const [copiedKey, setCopiedKey] = useState<string>('')

  const handleSelfUpgrade = async () => {
    if (upgradingStep) return
    setUpgradingStep('downloading')
    setErrorMsg('')
    try {
      const res = await api.triggerSelfUpdate()
      const targetVer = res.updated_to || versionStatus?.latest_version || ''
      setUpgradingStep('restarting')
      const deadline = Date.now() + 25000
      while (Date.now() < deadline) {
        try {
          const h = await api.getHealth()
          if (h?.status === 'ok' && (!targetVer || h.version === targetVer)) {
            setUpgradingStep('done')
            flashOk(
              isZh
                ? `已成功升级至 v${h.version}，正在自动加载新版界面…`
                : `Upgraded to v${h.version} — reloading…`,
              3000,
            )
            window.setTimeout(() => {
              if (typeof window !== 'undefined' && typeof window.location?.reload === 'function') {
                window.location.reload()
              }
            }, 600)
            return
          }
        } catch {
          // Server is restarting; keep polling cleanly
        }
        await new Promise(resolve => window.setTimeout(resolve, 900))
      }
      setUpgradingStep('done')
      if (typeof window !== 'undefined' && typeof window.location?.reload === 'function') {
        window.location.reload()
      }
    } catch (err) {
      setUpgradingStep('')
      setErrorMsg(err instanceof Error ? err.message : isZh ? '在线升级失败' : 'Self-update failed')
    }
  }

  const copyTextSafe = async (key: string, text: string) => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text)
      } else {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
      }
      setCopiedKey(key)
      window.setTimeout(() => setCopiedKey(prev => (prev === key ? '' : prev)), 1800)
    } catch {
      // Ignore clipboard permission errors silently
    }
  }

  return (
    <div data-testid="version-update-card" className="otl-card p-5 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <ArrowUpCircle size={15} className="text-[var(--accent)] shrink-0" />
          <h3 className="m-0 text-[14px] font-semibold text-[var(--fg)] truncate">
            {isZh ? '版本与更新' : 'Version & Updates'}
          </h3>
          <span className="px-1.5 py-0.5 rounded bg-[var(--surface-2)] border border-[var(--border)] font-mono text-[11px] text-[var(--fg-2)]">
            v{versionStatus?.current_version || '1.3.7'}
          </span>
        </div>

        {versionStatus?.update_available ? (
          <span
            data-testid="version-status-badge"
            className="otl-status-warn px-2 py-0.5 rounded-md text-[11px] font-semibold shrink-0"
          >
            {isZh
              ? `新版本 v${versionStatus.latest_version}`
              : `New v${versionStatus.latest_version}`}
          </span>
        ) : versionStatus?.error && !versionStatus.checked_sec ? (
          <span
            data-testid="version-status-badge"
            className="otl-status-err px-2 py-0.5 rounded-md text-[11px] font-semibold shrink-0"
          >
            {isZh ? '检测失败' : 'Check Error'}
          </span>
        ) : versionStatus?.checked_sec ? (
          <span
            data-testid="version-status-badge"
            className="otl-status-ok px-2 py-0.5 rounded-md text-[11px] font-semibold shrink-0"
          >
            {isZh ? '已是最新' : 'Up to Date'}
          </span>
        ) : (
          <span
            data-testid="version-status-badge"
            className="px-2 py-0.5 rounded-md border border-[var(--border)] bg-[var(--surface-2)] text-[11px] text-[var(--fg-3)] shrink-0"
          >
            {settings.update_check_enabled === false
              ? isZh
                ? '自动检测关闭'
                : 'Auto-Check Off'
              : isZh
                ? '待检测'
                : 'Pending'}
          </span>
        )}
      </div>

      {versionStatus?.error && (
        <div
          data-testid="version-error-box"
          className="otl-status-err rounded-lg px-3 py-2 text-[11.5px]"
        >
          {isZh ? versionStatus.error_zh || versionStatus.error : versionStatus.error}
        </div>
      )}

      {versionStatus?.update_available && (
        <div
          data-testid="version-release-box"
          className="rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] p-3.5 space-y-3"
        >
          <div className="flex items-center justify-between gap-2 text-[12px]">
            <span className="font-semibold text-[var(--fg)] truncate">
              {versionStatus.release_name || `v${versionStatus.latest_version}`}
            </span>
            <a
              href={versionStatus.release_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-[11.5px] font-medium text-[var(--accent)] hover:underline shrink-0"
            >
              <span>{isZh ? '更新日志' : 'Release Notes'}</span>
              <ExternalLink size={11} />
            </a>
          </div>
          {versionStatus.release_notes && (
            <pre className="m-0 max-h-32 overflow-y-auto whitespace-pre-wrap font-sans text-[11.5px] leading-relaxed text-[var(--fg-2)]">
              {versionStatus.release_notes}
            </pre>
          )}

          <div className="pt-2 border-t border-[var(--border)] space-y-2">
            <button
              type="button"
              data-testid="one-click-upgrade-btn"
              disabled={Boolean(upgradingStep)}
              onClick={() => void handleSelfUpgrade()}
              className="w-full h-9 px-4 rounded-lg text-[12.5px] font-semibold inline-flex items-center justify-center gap-2 cursor-pointer disabled:opacity-70 transition-all"
              style={{
                background: upgradingStep === 'done' ? '#10b981' : 'var(--accent)',
                color: upgradingStep === 'done' ? '#ffffff' : 'var(--accent-fg)',
              }}
            >
              {upgradingStep === 'done' ? (
                <>
                  <Check size={14} />
                  <span>
                    {isZh
                      ? `已完成升级至 v${versionStatus.latest_version} · 正在刷新…`
                      : `Upgraded to v${versionStatus.latest_version} · Reloading…`}
                  </span>
                </>
              ) : upgradingStep ? (
                <>
                  <RefreshCw size={13} className="animate-spin" />
                  <span>
                    {upgradingStep === 'downloading'
                      ? isZh
                        ? `正在下载并 SHA-256 校验 v${versionStatus.latest_version} 更新包…`
                        : `Downloading & verifying v${versionStatus.latest_version}…`
                      : isZh
                        ? '校验通过 · 正在平滑热重启服务（约 2 秒）…'
                        : 'Verified · Restarting service (~2s)…'}
                  </span>
                </>
              ) : (
                <>
                  <ArrowUpCircle size={14} />
                  <span>
                    {isZh
                      ? `一键在线升级至 v${versionStatus.latest_version}（免终端 · 自动保留全部数据）`
                      : `One-Click Upgrade to v${versionStatus.latest_version} (Preserves All Data)`}
                  </span>
                </>
              )}
            </button>
            {upgradingStep && upgradingStep !== 'done' && (
              <div className="h-1 w-full rounded-full bg-[var(--track)] overflow-hidden">
                <div className="otl-ceremony-indeterminate" />
              </div>
            )}
            <p className="m-0 text-[11px] text-[var(--fg-3)] text-center">
              {isZh
                ? '兼容极空间、绿联、飞牛 fnOS、群晖、威联通及本地 Docker，无需敲命令或进 NAS 后台，密钥与历史账单 100% 保留'
                : 'Works across all NAS platforms without SSH or Docker socket access; 100% of keys & ledger history are preserved.'}
            </p>
          </div>
        </div>
      )}

      {/* Optional Collapsible NAS GUI / CLI Manual Upgrade Reference */}
      <div className="space-y-2">
        <button
          type="button"
          data-testid="manual-upgrade-guide-toggle"
          onClick={() => setManualGuideOpen(v => !v)}
          className="w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] text-[11.5px] font-medium text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer"
        >
          <span>
            {isZh
              ? '查看各品牌 NAS 图形界面重拉镜像 / 命令行手动更新说明（可选）'
              : 'Manual NAS Docker GUI / CLI Image Refresh Guide (Optional)'}
          </span>
          <ChevronDown
            size={13}
            className={`shrink-0 transition-transform duration-200 ${manualGuideOpen ? 'rotate-180' : ''}`}
          />
        </button>

        <AnimatedCollapse open={manualGuideOpen}>
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface-2)] p-3 space-y-2.5 text-[11.5px] text-[var(--fg-2)]">
            <div className="space-y-1 leading-relaxed">
              <div className="font-semibold text-[var(--fg)]">
                {isZh
                  ? '方式一：NAS 图形界面更新底层镜像（极空间 / 绿联 / 飞牛 / 群晖通用）'
                  : 'Option 1: NAS Docker Graphical UI (Zero Terminal)'}
              </div>
              <p className="m-0">
                {isZh
                  ? '日常小版本直接点上方「一键在线升级」即可。若需更新底层基础镜像：打开 NAS 的「Docker」应用 → 若用 Compose/项目部署，进入项目点击「重新构建 / 更新」即可；若用单容器部署，先在「镜像」重新拉取 latest 标签，再对原容器执行「重置 / 重新创建」（保持 /app/data 挂载目录不变，数据零丢失）。'
                  : 'For routine updates, use One-Click Upgrade above. To refresh the base Docker image via NAS GUI: open Docker -> Compose -> Rebuild/Update (or pull :latest in Images and recreate container keeping /app/data volume).'}
              </p>
            </div>

            <div className="space-y-1 pt-2 border-t border-[var(--border)]">
              <div className="text-[11px] text-[var(--fg-3)]">
                {isZh ? '方式二：SSH 终端命令（进阶可选）' : 'Option 2: SSH / Docker Compose CLI'}
              </div>
              <div className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5">
                <code className="flex-1 font-mono text-[11.5px] text-[var(--fg)] truncate select-all">
                  docker compose pull &amp;&amp; docker compose up -d
                </code>
                <button
                  type="button"
                  data-testid="copy-nas-upgrade-cmd"
                  onClick={() =>
                    void copyTextSafe('nas_pull', 'docker compose pull && docker compose up -d')
                  }
                  title={isZh ? '复制命令' : 'Copy command'}
                  aria-label={isZh ? '复制命令' : 'Copy command'}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-[var(--border)] bg-[var(--surface-2)] text-[11px] font-medium text-[var(--fg-2)] hover:text-[var(--fg)] cursor-pointer shrink-0 transition-colors"
                >
                  {copiedKey === 'nas_pull' ? (
                    <>
                      <Check size={11} className="text-emerald-500" />
                      <span className="text-emerald-500">{isZh ? '已复制' : 'Copied'}</span>
                    </>
                  ) : (
                    <>
                      <Copy size={11} />
                      <span>{isZh ? '复制' : 'Copy'}</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </AnimatedCollapse>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-[var(--border)] text-[12px]">
        <div className="flex items-center gap-2">
          <button
            type="button"
            role="switch"
            aria-label={isZh ? '自动检查新版本' : 'Auto-check new versions'}
            aria-checked={settings.update_check_enabled !== false}
            data-testid="auto-update-check-switch"
            onClick={() =>
              void onUpdateSettings({
                update_check_enabled: settings.update_check_enabled === false,
              })
            }
            className="otl-switch"
          >
            <span className="otl-switch-knob" />
          </button>
          <span className="text-[var(--fg-2)]">{isZh ? '自动检查更新' : 'Auto-check'}</span>
          {versionStatus?.checked_at && (
            <span className="text-[11px] text-[var(--fg-3)] hidden sm:inline">
              · {versionStatus.checked_at}
            </span>
          )}
        </div>

        <button
          type="button"
          data-testid="check-version-btn"
          disabled={checkingVersion || !onCheckVersion}
          onClick={async () => {
            if (!onCheckVersion) return
            setCheckingVersion(true)
            setErrorMsg('')
            try {
              const res = await onCheckVersion(true)
              if (res?.error) {
                setErrorMsg(isZh ? res.error_zh || res.error : res.error)
              } else if (res?.update_available) {
                flashOk(
                  isZh
                    ? `检测到新版本 v${res.latest_version}`
                    : `New version v${res.latest_version} available`,
                  3000,
                )
              } else if (res) {
                flashOk(
                  isZh
                    ? `当前 v${res.current_version} 已是最新版本`
                    : `v${res.current_version} is up to date`,
                  2500,
                )
              }
            } finally {
              setCheckingVersion(false)
            }
          }}
          className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[11.5px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] disabled:opacity-50 cursor-pointer"
        >
          <RefreshCw size={12} className={checkingVersion ? 'animate-spin' : ''} />
          <span>
            {checkingVersion
              ? isZh
                ? '检查中…'
                : 'Checking…'
              : isZh
                ? '检查更新'
                : 'Check Now'}
          </span>
        </button>
      </div>
    </div>
  )
}
