import { useState } from 'react'
import { Lock } from 'lucide-react'
import { useI18n } from '../../i18n'
import { api } from '../../lib/api'
import type { AuthStatusPayload } from '../../lib/types'

interface AccessPasswordCardProps {
  authStatus?: AuthStatusPayload
  onAuthChanged?: (next: AuthStatusPayload, receipt: string) => void
  onLockNow?: () => Promise<void>
  flashOk: (msg: string, ms?: number) => void
  setErrorMsg: (msg: string) => void
}

export function AccessPasswordCard({
  authStatus,
  onAuthChanged,
  onLockNow,
  flashOk,
  setErrorMsg,
}: AccessPasswordCardProps) {
  const { locale } = useI18n()
  const isZh = locale === 'zh-CN'
  const authEnabled = Boolean(authStatus?.enabled)

  const [curPwd, setCurPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmNewPwd, setConfirmNewPwd] = useState('')
  const [pwdBusy, setPwdBusy] = useState(false)

  const handleSavePassword = async () => {
    if (pwdBusy) return
    const np = newPwd.trim()
    if (np.length < 4) {
      setErrorMsg(isZh ? '新密码长度至少需要 4 位字符' : 'New password must be at least 4 characters')
      return
    }
    if (!authEnabled && np !== confirmNewPwd.trim()) {
      setErrorMsg(isZh ? '两次输入的密码不一致' : 'New passwords do not match')
      return
    }
    setPwdBusy(true)
    setErrorMsg('')
    try {
      const res = await api.authChangePassword(curPwd, np, true)
      setCurPwd('')
      setNewPwd('')
      setConfirmNewPwd('')
      const receipt = authEnabled
        ? (isZh ? '访问密码已修改（其他旧会话已自动失效）' : 'Password updated (older sessions invalidated)')
        : (isZh ? '已开启本地访问密码保护' : 'Local access password enabled')
      if (onAuthChanged) onAuthChanged(res, receipt)
      else flashOk(receipt)
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to update password')
    } finally {
      setPwdBusy(false)
    }
  }

  const handleDisablePassword = async () => {
    if (pwdBusy) return
    if (!curPwd.trim()) {
      setErrorMsg(isZh ? '关闭密码锁前请先输入当前访问密码' : 'Enter current password to disable lock')
      return
    }
    setPwdBusy(true)
    setErrorMsg('')
    try {
      const res = await api.authDisablePassword(curPwd)
      setCurPwd('')
      setNewPwd('')
      setConfirmNewPwd('')
      const receipt = isZh ? '已关闭本地访问密码，当前为本机免密模式' : 'Password lock disabled'
      if (onAuthChanged) onAuthChanged(res, receipt)
      else flashOk(receipt)
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to disable password')
    } finally {
      setPwdBusy(false)
    }
  }

  return (
    <div data-testid="security-password-card" className="py-6 space-y-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="t-h2">
          {isZh ? '安全与本地访问密码' : 'Security & Local Access Password'}
        </h3>
        <span
          className={`text-[12px] font-medium shrink-0 ${
            authEnabled ? 'text-[var(--ok)]' : 'text-[var(--fg-3)]'
          }`}
        >
          {authEnabled
            ? isZh
              ? '密码已开启'
              : 'Protected'
            : isZh
              ? '免密模式'
              : 'Password Off'}
        </span>
      </div>

      <p className="m-0 text-[12px] text-[var(--fg-3)]">
        {isZh
          ? '部署到 NAS 或局域网时建议开启，密码仅以 scrypt 加盐哈希保存在本地磁盘'
          : 'Recommended on NAS / LAN · salted scrypt hash stored exclusively on disk'}
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 pt-0.5">
        {authEnabled && (
          <input
            type="password"
            data-testid="sec-current-pwd"
            value={curPwd}
            onChange={e => setCurPwd(e.target.value)}
            placeholder={isZh ? '当前访问密码' : 'Current password'}
            className="otl-input w-full"
          />
        )}
        <input
          type="password"
          data-testid="sec-new-pwd"
          value={newPwd}
          onChange={e => setNewPwd(e.target.value)}
          placeholder={
            authEnabled
              ? isZh
                ? '新密码（至少 4 位）'
                : 'New password (min 4 chars)'
              : isZh
                ? '设置访问密码（至少 4 位）'
                : 'Set access password (min 4 chars)'
          }
          className="otl-input w-full"
        />
        {!authEnabled && (
          <input
            type="password"
            data-testid="sec-confirm-pwd"
            value={confirmNewPwd}
            onChange={e => setConfirmNewPwd(e.target.value)}
            placeholder={isZh ? '再次确认访问密码' : 'Confirm access password'}
            className="otl-input w-full"
          />
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <div className="flex items-center gap-2">
          {authEnabled && onLockNow && (
            <button
              type="button"
              data-testid="sec-lock-now-btn"
              onClick={() => void onLockNow()}
              className="otl-btn"
            >
              <Lock size={12} />
              <span>{isZh ? '立即锁定' : 'Lock Now'}</span>
            </button>
          )}
          {authEnabled && authStatus?.can_skip !== false && (
            <button
              type="button"
              data-testid="sec-disable-pwd-btn"
              disabled={pwdBusy}
              onClick={() => void handleDisablePassword()}
              className="otl-btn text-[var(--fg-2)]"
            >
              {isZh ? '关闭密码锁' : 'Disable Lock'}
            </button>
          )}
        </div>
        <button
          type="button"
          data-testid="sec-save-pwd-btn"
          disabled={pwdBusy || !newPwd.trim()}
          onClick={() => void handleSavePassword()}
          className="otl-btn otl-btn-primary"
        >
          {authEnabled
            ? isZh
              ? '修改密码'
              : 'Update Password'
            : isZh
              ? '开启密码保护'
              : 'Enable Password'}
        </button>
      </div>
    </div>
  )
}
