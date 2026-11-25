import { useEffect, useState } from 'react'
import { Bell } from 'lucide-react'
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
  const [testingNotify, setTestingNotify] = useState(false)

  useEffect(() => {
    setBarkServerUrl(settings.notifications?.bark_server_url || 'https://api.day.app')
    setOutboundProxyUrl(settings.outbound_proxy_url || '')
  }, [settings.notifications?.bark_server_url, settings.outbound_proxy_url])

  return (
    <>
      <div data-testid="notifications-card" className="py-6 space-y-3.5">
        <div>
          <h3 className="t-h2">{t('set_notify_title')}</h3>
          <p className="m-0 text-[13px] text-[var(--fg-3)] mt-0.5">{t('set_notify_sub')}</p>
        </div>

        {/* Beginner Step-by-Step Bark Guide */}
        <div
          data-testid="bark-onboarding-guide"
          className="otl-surface-2 p-3.5 space-y-1.5 text-[13px] text-[var(--fg-2)]"
        >
          <div className="font-semibold text-[var(--fg)]">
            {isZh
              ? 'iPhone / iPad 新用户如何获取 Bark 推送？'
              : 'How to set up iOS Bark Push Notifications'}
          </div>
          <ol className="m-0 pl-4 space-y-0.5 list-decimal leading-relaxed">
            <li>
              {isZh
                ? '在苹果 App Store 搜索并安装免费开源应用「Bark」；'
                : 'Install the free open-source "Bark" app from the iOS App Store;'}
            </li>
            <li>
              {isZh
                ? '打开 Bark App，点击首页第一条示例链接右侧的「复制」，直接整条粘贴到下方右侧「Bark 链接或专属 Key」输入框即可（系统会自动识别拆出服务器与 Key）；'
                : 'Open Bark, tap "Copy" next to the sample URL, and paste the full URL (or device key) into the right-hand Bark box below;'}
            </li>
            <li>
              {isZh
                ? '直接点击「测试推送」即可自动保存并听手机响铃！开启后将在：① 盯盘带单员空出名额、② 带单员新开/平仓、③ 个人账户新平仓入账、④ 交易所后台同步异常报错 时实时推送。'
                : 'Click "Test Push" directly to auto-save & verify! Alerts fire on: open copy slots, lead-trader fills, closed trades, and background sync errors.'}
            </li>
          </ol>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          <div className="space-y-1">
            <label className="block text-[13px] text-[var(--fg-2)]">
              {isZh ? 'Bark 服务器地址（默认官方即可）' : 'Bark Server URL (Default OK)'}
            </label>
            <input
              type="text"
              data-testid="bark-server-url-input"
              value={barkServerUrl}
              onChange={e => setBarkServerUrl(e.target.value)}
              placeholder="https://api.day.app"
              className="otl-input w-full"
            />
          </div>
          <div className="space-y-1">
            <label className="block text-[13px] text-[var(--fg)]">
              {isZh ? 'Bark 链接或专属 Key（直接粘贴处）' : 'Bark App URL or Push Key (Paste here)'}
            </label>
            <input
              type="password"
              data-testid="bark-push-key-input"
              value={barkKey}
              onChange={e => setBarkKey(e.target.value)}
              placeholder={
                settings.notifications?.bark_push_key_set
                  ? isZh
                    ? '已配置（粘贴新链接或 Key 可覆盖）'
                    : 'Configured (paste new URL/Key to replace)'
                  : isZh
                    ? '粘贴 Bark 完整链接或设备 Key'
                    : 'Paste full Bark URL or Push Key'
              }
              className="otl-input w-full font-mono"
            />
          </div>
        </div>

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            data-testid="test-notify-btn"
            disabled={testingNotify}
            onClick={async () => {
              setTestingNotify(true)
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
                const sentList = (res.sent || []).join(', ')
                flashOk(
                  isZh
                    ? `配置已自动保存，测试推送已送达（${sentList}）`
                    : `Saved & test alert delivered (${sentList})`,
                )
              } catch (err) {
                setErrorMsg(err instanceof Error ? err.message : 'Test notification failed')
              } finally {
                setTestingNotify(false)
              }
            }}
            className="otl-btn"
          >
            <Bell size={12} />
            <span>{testingNotify ? (isZh ? '发送中…' : 'Sending…') : isZh ? '测试推送' : 'Test Push'}</span>
          </button>
          <button
            type="button"
            onClick={async () => {
              const saved = await onUpdateSettings({
                notifications: {
                  bark_server_url: barkServerUrl.trim() || 'https://api.day.app',
                  bark_push_key: barkKey,
                },
              })
              if (saved === false) return
              setBarkKey('')
              flashOk(t('set_save_ok'), 2500)
            }}
            className="otl-btn otl-btn-primary"
          >
            {t('save_config')}
          </button>
        </div>
      </div>

      {/* Optional Outbound Network Proxy */}
      <div data-testid="outbound-proxy-card" className="py-6 space-y-2.5">
        <div>
          <h3 className="t-h2">
            {isZh ? '网络代理配置（国内 NAS 连币安可选）' : 'Outbound Network Proxy (Optional)'}
          </h3>
          <p className="m-0 text-[13px] text-[var(--fg-3)] mt-0.5">
            {isZh
              ? '若 NAS 直连币安超时，可填入局域网代理地址（如 http://192.168.x.x:7890；注意 Docker 容器内请填宿主机/软路由局域网 IP，勿填 127.0.0.1），留空则直连。'
              : 'Optional HTTP/HTTPS/SOCKS5 proxy for exchange APIs & public copy radar. In Docker on NAS, use LAN IP rather than 127.0.0.1.'}
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
                ? '例如 http://192.168.x.x:7890（NAS 容器勿填 127.0.0.1，留空直连）'
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
    </>
  )
}
