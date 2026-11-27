import { useEffect, useState } from 'react'
import { Bell, ChevronDown } from 'lucide-react'
import { useI18n } from '../../i18n'
import { api } from '../../lib/api'
import type { UserSettings } from '../../lib/types'

interface BarkNotifyCardProps {
  settings: UserSettings
  onUpdateSettings: (patch: Record<string, unknown>) => Promise<void | boolean>
  flashOk: (msg: string, ms?: number) => void
  setErrorMsg: (msg: string) => void
}

export function BarkNotifyCard({
  settings,
  onUpdateSettings,
  flashOk,
  setErrorMsg,
}: BarkNotifyCardProps) {
  const { locale, t } = useI18n()
  const isZh = locale === 'zh-CN'

  const [barkServerUrl, setBarkServerUrl] = useState(
    settings.notifications?.bark_server_url || 'https://api.day.app',
  )
  const [barkKey, setBarkKey] = useState('')
  const [outboundProxyUrl, setOutboundProxyUrl] = useState(settings.outbound_proxy_url || '')
  const [barkAction, setBarkAction] = useState<'' | 'test' | 'save' | 'clear'>('')
  const [showBarkGuide, setShowBarkGuide] = useState(false)

  useEffect(() => {
    setBarkServerUrl(settings.notifications?.bark_server_url || 'https://api.day.app')
    setOutboundProxyUrl(settings.outbound_proxy_url || '')
  }, [settings.notifications?.bark_server_url, settings.outbound_proxy_url])

  return (
    <div data-testid="notifications-card" className="py-6 space-y-5">
      <div className="space-y-3">
        <div className="flex items-baseline justify-between gap-2">
          <div>
            <h3 className="t-h2">{t('set_notify_title')}</h3>
            <p className="m-0 text-[12px] text-[var(--fg-3)] mt-0.5">{t('set_notify_sub')}</p>
          </div>
          <button
            type="button"
            data-no-press="true"
            onClick={() => setShowBarkGuide(v => !v)}
            className="inline-flex items-center gap-1 text-[12px] text-[var(--fg-2)] hover:text-[var(--fg)] hover:underline bg-transparent border-0 p-0 cursor-pointer shrink-0"
          >
            <span>{isZh ? '配置教程' : 'Setup Guide'}</span>
            <ChevronDown
              size={13}
              className={`transition-transform duration-150 ${showBarkGuide ? 'rotate-180' : ''}`}
            />
          </button>
        </div>

        {/* Persistent-DOM CSS Grid Collapse so bark-onboarding-guide stays queryable while 0fr by default */}
        <div
          data-testid="bark-onboarding-guide"
          className={`otl-collapse-grid ${showBarkGuide ? 'is-open' : ''}`}
        >
          <div className="otl-collapse-inner">
            <p className="m-0 py-2 text-[12px] text-[var(--fg-2)]">
              {isZh
                ? '个人合约账户每次同步新增的减仓、平仓记录合并提醒一次。公开带单员的空位和调仓提醒在「公开盯盘」中按人开启。'
                : 'Each sync groups newly booked reductions and closes into one alert per account. Enable slot and trade alerts for each public trader in Public Watcher.'}
            </p>
            <ol className="m-0 pt-1 pb-2 pl-4 space-y-1 list-decimal text-[12px] text-[var(--fg-2)] leading-relaxed">
              <li>
                {isZh
                  ? '在苹果 App Store 搜索并安装免费开源应用「Bark」；'
                  : 'Install the free open-source "Bark" app from the iOS App Store;'}
              </li>
              <li>
                {isZh
                  ? '复制 Bark 首页示例链接，直接整条粘贴到下方右侧「Bark 链接或专属 Key」输入框（自动拆分服务器与 Key）；'
                  : 'Copy the sample URL from Bark and paste it into the right-hand Bark Key box below;'}
              </li>
              <li>
                {isZh
                  ? '测试推送和保存新设备都会发送一条验证通知。Bark 确认接收后才启用新设备。'
                  : 'Testing or saving a new device sends a verification push. The device becomes active after Bark accepts it.'}
              </li>
            </ol>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          <input
            type="text"
            aria-label={isZh ? 'Bark 服务器地址' : 'Bark Server URL'}
            data-testid="bark-server-url-input"
            disabled={Boolean(barkAction)}
            value={barkServerUrl}
            onChange={e => setBarkServerUrl(e.target.value)}
            placeholder="https://api.day.app"
            className="otl-input w-full"
          />
          <input
            type="password"
            aria-label={isZh ? 'Bark 链接或专属 Key' : 'Bark App URL or Push Key'}
            data-testid="bark-push-key-input"
            disabled={Boolean(barkAction)}
            value={barkKey}
            onChange={e => setBarkKey(e.target.value)}
            placeholder={
              settings.notifications?.bark_push_key_set
                ? isZh
                  ? '已配置（粘贴新链接或 Key 可覆盖）'
                  : 'Configured (paste new URL/Key to replace)'
                : isZh
                  ? '粘贴 Bark 完整链接或专属 Key'
                  : 'Paste full Bark URL or Push Key'
            }
            className="otl-input w-full font-mono"
          />
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2">
          {settings.notifications?.bark_push_key_set && (
            <button
              type="button"
              data-testid="clear-bark-key-btn"
              disabled={Boolean(barkAction)}
              onClick={async () => {
                setBarkAction('clear')
                const cleared = await onUpdateSettings({
                  notifications: { clear_bark_push_key: true },
                })
                setBarkAction('')
                if (cleared === false) return
                setBarkKey('')
                flashOk(
                  isZh ? '已清除保存的 Bark 推送密钥' : 'Saved Bark push key cleared',
                  2500,
                )
              }}
              className="otl-btn text-[var(--fg-2)] hover:text-[var(--err)]"
            >
              {isZh ? '清除密钥' : 'Clear Key'}
            </button>
          )}
          <button
            type="button"
            data-testid="test-notify-btn"
            disabled={Boolean(barkAction)}
            onClick={async () => {
              setBarkAction('test')
              setErrorMsg('')
              try {
                const pendingNotifications = {
                  bark_server_url: barkServerUrl.trim() || 'https://api.day.app',
                  bark_push_key: barkKey.trim(),
                }
                const res = await api.testNotify({ notifications: pendingNotifications })
                if (res.settings) {
                  setBarkServerUrl(res.settings.notifications?.bark_server_url || 'https://api.day.app')
                  if ((await onUpdateSettings({})) === false) return
                }
                setBarkKey('')
                flashOk(
                  isZh
                    ? 'Bark 已接收测试通知，设备配置已保存'
                    : 'Bark accepted the test alert. Device settings saved.',
                )
              } catch (err) {
                setErrorMsg(err instanceof Error ? err.message : 'Test notification failed')
              } finally {
                setBarkAction('')
              }
            }}
            className="otl-btn"
          >
            <Bell size={12} />
            <span>{barkAction === 'test' ? (isZh ? '发送中…' : 'Sending…') : isZh ? '测试推送' : 'Test Push'}</span>
          </button>
          <button
            type="button"
            disabled={Boolean(barkAction)}
            onClick={async () => {
              setBarkAction('save')
              const saved = await onUpdateSettings({
                notifications: {
                  bark_server_url: barkServerUrl.trim() || 'https://api.day.app',
                  bark_push_key: barkKey,
                },
              })
              setBarkAction('')
              if (saved === false) return
              setBarkKey('')
              flashOk(t('set_save_ok'), 2500)
            }}
            className="otl-btn otl-btn-primary"
          >
            {barkAction === 'save' ? (isZh ? '正在保存…' : 'Saving…') : (isZh ? '验证并保存配置' : 'Verify & Save')}
          </button>
        </div>
      </div>

      {/* Optional Outbound Network Proxy */}
      <div data-testid="outbound-proxy-card" className="pt-4 border-t border-[var(--border)] space-y-2">
        <div>
          <div className="text-[13px] font-semibold text-[var(--fg)]">
            {isZh ? '网络代理配置（国内 NAS 连币安可选）' : 'Outbound Network Proxy (Optional)'}
          </div>
          <p className="m-0 text-[12px] text-[var(--fg-3)] mt-0.5">
            {isZh
              ? '若 NAS 直连币安超时，可填局域网代理地址（Docker 容器内请填宿主机局域网 IP，勿填 127.0.0.1），留空直连'
              : 'Optional HTTP/HTTPS/SOCKS5 proxy. In Docker on NAS, use LAN IP rather than 127.0.0.1.'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            data-testid="outbound-proxy-input"
            value={outboundProxyUrl}
            onChange={e => setOutboundProxyUrl(e.target.value)}
            placeholder={
              isZh
                ? '例如 http://192.168.x.x:7890（留空直连）'
                : 'e.g. http://192.168.x.x:7890 (leave blank for direct)'
            }
            className="otl-input flex-1 font-mono"
          />
          <button
            type="button"
            data-testid="save-proxy-btn"
            onClick={async () => {
              try {
                const saved = await onUpdateSettings({ outbound_proxy_url: outboundProxyUrl.trim() })
                if (saved === false) return
                flashOk(
                  isZh
                    ? outboundProxyUrl.trim()
                      ? '网络代理已生效'
                      : '已清除网络代理，恢复直连模式'
                    : 'Proxy settings saved',
                  2500,
                )
              } catch (err) {
                setErrorMsg(err instanceof Error ? err.message : 'Invalid proxy URL')
              }
            }}
            className="otl-btn otl-btn-primary h-[34px]"
          >
            {t('save_config')}
          </button>
        </div>
      </div>
    </div>
  )
}
