import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useI18n } from '../../i18n'
import { api } from '../../lib/api'
import type { UserSettings } from '../../lib/types'

interface AiConfigCardProps {
  settings: UserSettings
  onUpdateSettings: (patch: Record<string, unknown>) => Promise<void>
  flashOk: (msg: string, ms?: number) => void
  setErrorMsg: (msg: string) => void
}

export function AiConfigCard({
  settings,
  onUpdateSettings,
  flashOk,
  setErrorMsg,
}: AiConfigCardProps) {
  const { locale, t } = useI18n()

  const [aiUrl, setAiUrl] = useState(settings.ai_base_url || '')
  const [aiModel, setAiModel] = useState(settings.ai_model || '')
  const [aiKey, setAiKey] = useState('')
  const [aiModels, setAiModels] = useState<string[]>([])
  const [fetchingModels, setFetchingModels] = useState(false)

  useEffect(() => {
    setAiUrl(settings.ai_base_url || '')
    setAiModel(settings.ai_model || '')
  }, [settings.ai_base_url, settings.ai_model])

  return (
    <div className="otl-card p-5 space-y-3">
      <div>
        <h3 className="m-0 text-[14px] font-semibold text-[var(--fg)]">{t('set_ai_title')}</h3>
        <p className="m-0 text-[11.5px] text-[var(--fg-3)] mt-0.5">{t('set_ai_sub')}</p>
      </div>
      <input
        type="text"
        value={aiUrl}
        onChange={e => setAiUrl(e.target.value)}
        placeholder="Base URL (e.g. https://api.openai.com/v1)"
        className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
      />
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <input
          type="password"
          value={aiKey}
          onChange={e => setAiKey(e.target.value)}
          placeholder={settings.ai_api_key_set ? 'API Key (configured)' : 'API Key'}
          className="h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
        />
        <button
          type="button"
          data-testid="fetch-ai-models-btn"
          disabled={fetchingModels || !aiUrl.trim()}
          onClick={async () => {
            setFetchingModels(true)
            setErrorMsg('')
            try {
              const res = await api.listAiModels(aiUrl.trim(), aiKey.trim())
              setAiModels(res.models)
              if (res.models.length > 0 && !aiModel.trim()) {
                setAiModel(res.models[0])
              }
              flashOk(
                locale === 'en'
                  ? `Loaded ${res.count} available models`
                  : `已获取 ${res.count} 个可用模型`,
                3000,
              )
            } catch (err) {
              setErrorMsg(err instanceof Error ? err.message : 'Failed to fetch models')
            } finally {
              setFetchingModels(false)
            }
          }}
          className="inline-flex items-center justify-center gap-1.5 h-8 px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] font-medium text-[var(--fg)] hover:bg-[var(--surface-hover)] disabled:opacity-50 cursor-pointer whitespace-nowrap"
        >
          <RefreshCw size={12} className={fetchingModels ? 'animate-spin' : ''} />
          <span>{locale === 'en' ? 'Fetch Models' : '获取模型列表'}</span>
        </button>
      </div>
      {aiModels.length > 0 ? (
        <div className="space-y-1.5">
          <select
            aria-label="AI Model"
            data-testid="ai-model-select"
            value={aiModels.includes(aiModel) ? aiModel : '__custom__'}
            onChange={e => {
              if (e.target.value !== '__custom__') setAiModel(e.target.value)
            }}
            className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)] cursor-pointer"
          >
            {aiModels.map(m => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
            {!aiModels.includes(aiModel) && (
              <option value="__custom__">
                {aiModel || (locale === 'en' ? 'Custom model…' : '自定义模型…')}
              </option>
            )}
          </select>
          <input
            type="text"
            value={aiModel}
            onChange={e => setAiModel(e.target.value)}
            placeholder="Model ID (e.g. gpt-4o-mini)"
            className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
          />
        </div>
      ) : (
        <input
          type="text"
          value={aiModel}
          onChange={e => setAiModel(e.target.value)}
          placeholder="Model (e.g. gpt-4o-mini / deepseek-chat)"
          className="h-8 w-full px-2.5 rounded-lg border border-[var(--border-strong)] bg-[var(--surface-2)] text-[12px] text-[var(--fg)]"
        />
      )}
      <div className="flex justify-end">
        <button
          type="button"
          onClick={async () => {
            await onUpdateSettings({ ai_base_url: aiUrl, ai_model: aiModel, ai_api_key: aiKey })
            setAiKey('')
            flashOk(t('set_save_ok'), 2500)
          }}
          className="h-8 px-3 rounded-lg text-[12px] font-semibold cursor-pointer"
          style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
        >
          {t('save_config')}
        </button>
      </div>
    </div>
  )
}
