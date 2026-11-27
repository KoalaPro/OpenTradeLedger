import { useState } from 'react'
import { ChevronDown, Settings, ShieldCheck } from 'lucide-react'
import { AnimatedCollapse } from '../components/AnimatedCollapse'
import { FloatingModal } from '../components/FloatingModal'
import { formatDisplayKpiValue } from '../components/KpiCard'
import { SimpleMarkdown } from '../components/SimpleMarkdown'
import { VenueBadge } from '../components/VenueBadge'
import { useI18n } from '../i18n'
import { useCurrency } from '../lib/currency'
import type { DashboardData, UserSettings, VenueFeeRow } from '../lib/types'

function FeeRateCell({ row, side }: { row: VenueFeeRow; side: 'maker' | 'taker' }) {
  const { locale } = useI18n()
  const isZh = locale !== 'en'
  const gross = row[`${side}_gross_bps`].toFixed(2)
  const source = row[`${side}_rate_source`]
  if (source === 'configured') {
    const unknown = row.trade_count > 0 && row.maker_bps_known === false && row.taker_bps_known === false
    return (
      <span>
        {gross}{' '}
        <span className="text-[12px] text-[var(--fg-3)]">
          {isZh ? '配置毛费率' : 'quoted gross'}
        </span>{' '}
        / {unknown ? (isZh ? '分腿未知' : 'Split unknown') : '—'}
      </span>
    )
  }
  if (row.total_fee_known === false) {
    return <span className="text-[var(--fg-3)]">{isZh ? '费用未核全' : 'Incomplete fees'}</span>
  }
  if (source === 'unknown' || row[`${side}_bps_known`] === false || row.trade_count === 0) {
    const blended = side === 'taker' && row.blended_net_bps != null && row.blended_net_bps !== 0
    return (
      <span className="text-[var(--fg-3)] text-[12px]">
        {row.trade_count === 0 ? (isZh ? '无成交样本' : 'No fills') : (isZh ? '分腿未知' : 'Split unknown')}
        {blended && ` · ${isZh ? '综合' : 'Avg'} ${row.blended_net_bps!.toFixed(2)} bps`}
      </span>
    )
  }
  return (
    <>
      {gross} / <b>{row[`${side}_net_bps`]?.toFixed(2) ?? '—'}</b>
    </>
  )
}

export function FeesAiPage({
  data,
  settings,
  generating,
  onGenerate,
  onGoConfigureAi,
}: {
  data: DashboardData
  settings?: UserSettings
  generating: boolean
  onGenerate: (mode?: 'auto' | 'ai' | 'local_rules') => Promise<void>
  onGoConfigureAi?: () => void
}) {
  const { locale, t } = useI18n()
  const isZh = locale !== 'en'
  const cur = useCurrency()
  const [showAiPayload, setShowAiPayload] = useState(false)
  const [showAiUnconfiguredModal, setShowAiUnconfiguredModal] = useState(false)
  const ai = data.ai_review

  const rawAiUrl = (settings?.ai_base_url || '').trim()
  const isLocalAiHost = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(\/|$)/i.test(rawAiUrl)
  const aiConfigured = Boolean(rawAiUrl && (settings?.ai_api_key_set || isLocalAiHost))

  const rk = (data.range_key || 'all').toLowerCase()
  const currentPeriodLabel =
    locale === 'en'
      ? rk === '7d'
        ? 'Last 7 Days'
        : rk === '30d'
          ? 'Last 30 Days'
          : rk === 'all'
            ? 'All-Time'
            : rk
      : rk === '7d'
        ? '近 7 天'
        : rk === '30d'
          ? '近 30 天'
          : rk === 'all'
            ? '全部历史'
            : `${rk} 月度`

  const resolveExecutionMode = (review: DashboardData['ai_review']) => {
    if (!review) return 'local_only'
    if (review.execution_mode) return review.execution_mode
    const errTxt = `${review.ai_error || ''} ${review.ai_error_zh || ''}`.trim()
    if (errTxt && !errTxt.includes('without an API Key') && !errTxt.includes('尚未配置 API Key')) {
      return 'remote_failed_fallback'
    }
    if (review.trade_count_snapshot === 0 || review.highlights?.some(h => h.kind === 'no_trades')) {
      return 'zero_trades_local'
    }
    if (review.model === 'local-actuarial' || review.model === 'demo-local-actuarial') return 'local_only'
    return 'remote_llm'
  }

  const mode = resolveExecutionMode(ai)
  const delivery = ai?.transmission_status ??
    (mode === 'remote_llm' ? 'sent' : mode === 'remote_failed_fallback' ? 'unknown' : 'not_sent')
  const unknownDelivery = isZh ? '无法确认 AI 接口是否收到脱敏请求' : 'Delivery to the AI endpoint could not be confirmed'
  const deliveryBadge = {
    unknown: unknownDelivery,
    not_sent: isZh ? '本次未外发任何数据' : 'No data sent externally',
    sent: mode === 'remote_llm'
      ? (isZh ? `本次已外发脱敏汇总 → ${ai?.model}` : `Sanitized summary sent → ${ai?.model}`)
      : (isZh ? '本次已向 AI 接口发送脱敏请求' : 'Sanitized request sent to AI'),
  }[delivery]

  const formatModelAttribution = (review: NonNullable<DashboardData['ai_review']>) => {
    if (review.model === 'demo-local-actuarial') {
      return isZh ? '演示精算规则（纯本地示例）' : 'Demo Actuarial Rules (Local Sample)'
    }
    if (mode === 'remote_failed_fallback') {
      return isZh
        ? '正文由本地精算规则回退生成'
        : 'Body generated via local actuarial fallback'
    }
    if (mode === 'zero_trades_local' || mode === 'local_only') {
      return isZh ? '本地精算规则' : 'Local Actuarial Rules'
    }
    return review.model
  }

  const formatHighlightMetric = (h: NonNullable<DashboardData['ai_review']>['highlights'][number]) => {
    if (h.kind === 'no_trades') {
      return isZh ? '暂无可分析样本' : 'No Sample'
    }
    if (!h.metric) return ''
    if (cur.code !== 'USDT') {
      if (h.kind === 'net_fee' && typeof h.amount_usd === 'number') {
        const pctSuffix = typeof h.pct === 'number' ? ` (${h.pct.toFixed(1)}%)` : ''
        return `${cur.signedMoney(h.amount_usd, 2)}${pctSuffix}`
      }
      if (h.kind === 'maker_upside' && typeof h.amount_usd === 'number') {
        return `+${cur.money(Math.abs(h.amount_usd), 2)}`
      }
    }
    return cur.convertText(h.metric)
  }

  return (
    <div data-testid="fees-ai-page" className="otl-stagger">
      {/* Flush Cross-Venue Fee & Rebate Matrix */}
      <section data-testid="fee-matrix-section" className="otl-chapter space-y-3.5">
        <div className="px-5 flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h2 className="t-h2">{t('fee_matrix_title')}</h2>
            <p className="m-0 text-[12px] text-[var(--fg-3)] mt-0.5">{t('fee_matrix_sub')}</p>
          </div>
          {data.summary.total_fee_known !== false && data.summary.potential_maker_savings_usd > 0 && (
            <span className="text-[12px] text-[var(--fg-2)] whitespace-nowrap sensitive">
              {t('maker_savings_badge')}:{' '}
              <strong className="font-semibold txt-pos">
                +{cur.money(data.summary.potential_maker_savings_usd)}
              </strong>
            </span>
          )}
        </div>

        <div className="overflow-x-auto">
          <table className="otl-table">
            <thead>
              <tr>
                <th>{t('th_venue')}</th>
                <th className="text-right">{t('th_trades')}</th>
                <th className="text-right">{t('th_maker_bps')}</th>
                <th className="text-right">{t('th_taker_bps')}</th>
                <th className="text-right">{t('th_token_disc')}</th>
                <th className="text-right">{t('th_observed_rebate')}</th>
                <th className="text-right">{t('th_total_relief')}</th>
              </tr>
            </thead>
            <tbody>
              {data.fee_matrix.map(row => (
                <tr key={row.venue_id}>
                  <td>
                    <VenueBadge
                      venueId={row.venue_id}
                      name={locale === 'en' ? row.display_name : row.display_name_zh || row.display_name}
                    />
                  </td>
                  <td className="text-right whitespace-nowrap">{row.trade_count}</td>
                  <td className="text-right whitespace-nowrap">
                    <FeeRateCell row={row} side="maker" />
                  </td>
                  <td className="text-right whitespace-nowrap">
                    <FeeRateCell row={row} side="taker" />
                  </td>
                  <td className="text-right whitespace-nowrap sensitive">
                    {row.discount_saved_usd > 0 ? (
                      <span className="txt-pos">
                        {row.discount_label ? `${row.discount_label} ` : ''}+{cur.money(row.discount_saved_usd)}
                      </span>
                    ) : (
                      <span className="text-[var(--fg-3)]">—</span>
                    )}
                  </td>
                  <td className="text-right whitespace-nowrap sensitive">
                    {row.rebate_usd > 0 ? (
                      <span className="txt-pos">
                        +{cur.money(row.rebate_usd)}{row.total_fee_known !== false && row.gross_fee_usd > 0 && ` (${row.rebate_pct.toFixed(1)}%)`}
                      </span>
                    ) : (
                      <span className="text-[var(--fg-3)]">—</span>
                    )}
                  </td>
                  <td className="text-right font-semibold whitespace-nowrap">
                    {row.total_fee_known !== false && row.trade_count > 0 && row.gross_fee_usd + row.discount_saved_usd > 0
                      ? `${row.combined_relief_pct.toFixed(1)}%`
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Editorial AI & Actuarial Review Chapter */}
      <section className="py-6 px-5 space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <h2 className="t-h2">{t('ai_title')}</h2>
            <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px] text-[var(--fg-3)]">
              {settings && (
                <span
                  data-testid="ai-config-status-badge"
                  className={
                    !aiConfigured
                      ? 'text-[var(--fg-2)]'
                      : mode === 'remote_failed_fallback'
                        ? 'text-[var(--warn)] font-medium'
                        : 'text-[var(--ok)] font-medium'
                  }
                >
                  {!aiConfigured
                    ? isZh
                      ? '尚未配置 AI 模型'
                      : 'AI Not Configured'
                    : mode === 'remote_failed_fallback'
                      ? isZh
                        ? `AI 接口异常 · ${settings.ai_model || 'gpt-4o-mini'}`
                        : `AI Endpoint Error · ${settings.ai_model || 'gpt-4o-mini'}`
                      : isZh
                        ? `已配置 AI · ${settings.ai_model || 'gpt-4o-mini'}`
                        : `AI Configured · ${settings.ai_model || 'gpt-4o-mini'}`}
                </span>
              )}
              {ai && (
                <span data-testid="ai-scope-badge">
                  {settings ? '· ' : ''}
                  {isZh
                    ? `统计周期：${ai.range_label_zh || currentPeriodLabel}${
                        typeof ai.trade_count_snapshot === 'number' ? ` · ${ai.trade_count_snapshot} 笔` : ''
                      }`
                    : `Scope: ${ai.range_label || currentPeriodLabel}${
                        typeof ai.trade_count_snapshot === 'number' ? ` · ${ai.trade_count_snapshot} trades` : ''
                      }`}
                </span>
              )}
              {ai && (
                <span
                  data-testid="ai-transmission-badge"
                  className={
                    mode === 'remote_failed_fallback'
                      ? 'text-[var(--warn)]'
                      : delivery === 'sent'
                        ? 'text-[var(--fg-2)]'
                        : 'text-[var(--fg-3)]'
                  }
                >
                  ·{' '}
                  {deliveryBadge}
                  {mode === 'remote_failed_fallback' && (isZh ? ' · 已回退本地精算' : ' · Local fallback')}
                </span>
              )}
              {ai && (
                <span>
                  · {ai.generated_at} · {formatModelAttribution(ai)}
                </span>
              )}
            </div>
          </div>

          <button
            type="button"
            data-testid="generate-ai-btn"
            disabled={generating}
            onClick={() => {
              if (settings && !aiConfigured) {
                setShowAiUnconfiguredModal(true)
                return
              }
              void onGenerate(aiConfigured ? 'ai' : 'auto')
            }}
            className="otl-btn otl-btn-primary"
          >
            <span>{generating ? t('ai_generating') : t('ai_generate')}</span>
          </button>
        </div>

        {ai?.stale && (
          <div
            data-testid="ai-stale-banner"
            className="otl-status-warn px-3.5 py-2.5 rounded-md text-[13px] sensitive"
          >
            {cur.convertText((isZh ? ai.stale_reason_zh : ai.stale_reason) || t('ai_stale_notice'))}
          </div>
        )}

        {(ai?.ai_error || ai?.ai_error_zh) && (
          <div
            data-testid="ai-error-banner"
            className="otl-status-warn px-3.5 py-2.5 rounded-md text-[13px] font-medium"
          >
            {locale === 'en' ? ai.ai_error || ai.ai_error_zh : ai.ai_error_zh || ai.ai_error}
          </div>
        )}

        {!ai ? (
          <div className="py-12 text-center text-[13px] text-[var(--fg-3)] border-y border-[var(--border)]">
            {isZh
              ? `当前统计周期（${currentPeriodLabel} · ${data.summary.trade_count} 笔交割）尚未生成复盘报告，点击右上角「${t('ai_generate')}」即可针对本周期生成诊断。`
              : `No review generated yet for ${currentPeriodLabel} (${data.summary.trade_count} trades). Click "${t('ai_generate')}" above to analyze this period.`}
          </div>
        ) : (
          <>
            {ai.highlights.length > 0 && (
              <div
                className={`border-y border-[var(--border)] grid grid-cols-1 ${
                  ai.highlights.length > 1 ? 'md:grid-cols-3 divide-y md:divide-y-0 md:divide-x' : ''
                } divide-[var(--border)]`}
              >
                {ai.highlights.map((h, i) => {
                  const formattedMetric = formatHighlightMetric(h)
                  const rawBody = locale === 'en' ? h.body : h.body_zh || h.body
                  return (
                    <div
                      key={i}
                      className={`py-5 flex flex-col gap-1.5 ${
                        i === 0 ? 'md:pr-6' : i === 1 ? 'md:px-6' : 'md:pl-6'
                      }`}
                    >
                      <div className="text-[12px] text-[var(--fg-3)]">
                        {locale === 'en' ? h.title : h.title_zh || h.title}
                      </div>
                      {formattedMetric && (
                        <div
                          className={`t-kpi sensitive ${
                            h.kind === 'maker_upside' ? 'txt-pos' : ''
                          }`}
                        >
                          {formatDisplayKpiValue(formattedMetric)}
                        </div>
                      )}
                      <p className="m-0 text-[12.5px] leading-relaxed text-[var(--fg-2)] sensitive">
                        {cur.convertText(rawBody)}
                      </p>
                    </div>
                  )
                })}
              </div>
            )}

            {(ai.markdown_commentary || ai.markdown_commentary_zh) && (
              <div className="max-w-[65ch] space-y-2.5">
                <div className="text-[13px] font-semibold text-[var(--fg)]">{t('ai_commentary_title')}</div>
                <div className="sensitive">
                  <SimpleMarkdown
                    content={cur.convertText(
                      locale === 'en'
                        ? ai.markdown_commentary
                        : ai.markdown_commentary_zh || ai.markdown_commentary,
                    )}
                  />
                </div>
              </div>
            )}

            {/* Sanitized Payload Transparency Disclosure */}
            <div className="pt-4 border-t border-[var(--border)]">
              {mode === 'zero_trades_local' ? (
                <div className="inline-flex items-center gap-1.5 text-[12px] text-[var(--fg-3)]">
                  <ShieldCheck size={14} style={{ color: 'var(--ok)' }} className="shrink-0" />
                  <span>
                    {locale === 'en'
                      ? '0 closed trades in this period · returned locally without sending any request or data to an external AI endpoint.'
                      : '本周期 0 笔成交已在本地直接返回，未向外部 AI 接口发送任何推理请求或账本数据。'}
                  </span>
                </div>
              ) : (
                <>
                  <button
                    type="button"
                    data-no-press="true"
                    data-testid="toggle-ai-payload-btn"
                    onClick={() => setShowAiPayload(v => !v)}
                    className="inline-flex items-center gap-1.5 text-[12px] text-[var(--fg-3)] hover:text-[var(--fg)] cursor-pointer"
                  >
                    <ShieldCheck size={14} style={{ color: 'var(--ok)' }} className="shrink-0" />
                    <span>
                      {delivery === 'unknown'
                        ? (isZh ? `查看脱敏数据范围（${unknownDelivery}）` : `Preview sanitized data scope (${unknownDelivery})`)
                        : delivery === 'sent'
                          ? (isZh ? '查看本次已发送给 AI 接口的脱敏数据明细（绝不含 API Key / UID / 钱包地址）' : 'Inspect sanitized data sent to AI endpoint on this run (Zero API keys / UIDs)')
                          : (isZh ? '查看脱敏数据范围预览（本次未外发任何数据）' : 'Preview sanitized data scope (No data sent externally)')}
                    </span>
                    <ChevronDown
                      size={13}
                      className={`shrink-0 transition-transform duration-150 ${showAiPayload ? 'rotate-180' : ''}`}
                    />
                  </button>

                  <AnimatedCollapse open={showAiPayload} innerClassName="pt-3">
                    <div className="pt-3 border-t border-[var(--border)] space-y-3 text-[12px]">
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-[var(--fg-2)]">
                        <div>
                          <b className="text-[var(--fg)]">
                            {delivery === 'sent'
                              ? (isZh ? '本次实际外发的 4 大类脱敏汇总数据：' : 'Transmitted Metrics (4 Sanitized Groups)')
                              : (isZh ? '脱敏汇总范围预览' : 'Sanitized Scope Preview')}
                          </b>
                          <ul className="mt-1 pl-4 space-y-0.5 list-disc">
                            <li>
                              {locale === 'en'
                                ? 'Account summary (equity, net PnL, long/short win rate, hold time, drawdown)'
                                : '全账户指标（总净资产、净盈亏、多空胜率、盈/亏单均持仓时长、最大回撤）'}
                            </li>
                            <li>
                              {locale === 'en'
                                ? `Connected exchange summaries (${ai.input_summary?.exchanges_count ?? data.exchanges.length}) & live fee/rebate matrix`
                                : `各平台资产与实测挂/吃单费率及返佣矩阵（${ai.input_summary?.fee_matrix_count ?? data.fee_matrix.length} 所）`}
                            </li>
                            <li>
                              {locale === 'en'
                                ? `Top ${ai.input_summary?.top_symbols_count ?? Math.min(15, data.symbol_attribution.length)} symbols by PnL impact + open positions / spot / copy / stocks summary`
                                : `盈亏影响前 ${ai.input_summary?.top_symbols_count ?? Math.min(15, data.symbol_attribution.length)} 标的归因、当前持仓、现货/跟单/股票汇总与自定义目标`}
                            </li>
                            <li>
                              {locale === 'en'
                                ? `Recent ${ai.input_summary?.recent_trades_count ?? Math.min(20, data.trades.length)} closed trades (entry/exit mode, bps, fees, rebates, your journal tags & notes)`
                                : `最近 ${ai.input_summary?.recent_trades_count ?? Math.min(20, data.trades.length)} 笔平仓的逐笔进/出场方式、bps、手续费、返佣与你的复盘标签/笔记`}
                            </li>
                          </ul>
                        </div>
                        <div>
                          <b className="text-[var(--fg)]">
                            {locale === 'en' ? 'Strictly Excluded (Never Sent)' : '严格剔除（绝不发送）：'}
                          </b>
                          <ul className="mt-1 pl-4 space-y-0.5 list-disc">
                            <li>API Key / API Secret / Passphrase</li>
                            <li>{locale === 'en' ? 'Exchange UIDs & account emails' : '交易所 UID 与账号信息'}</li>
                            <li>{locale === 'en' ? 'Deposit / withdrawal wallet addresses & TX hashes' : '充提币链上地址与哈希'}</li>
                          </ul>
                        </div>
                      </div>
                      {ai.context_preview && (
                        <pre
                          data-testid="ai-payload-pre"
                          className="m-0 p-3 rounded-md bg-[var(--bg)] border border-[var(--border)] text-[12px] font-mono text-[var(--fg-2)] overflow-x-auto max-h-64 sensitive"
                        >
                          {JSON.stringify(ai.context_preview, null, 2)}
                        </pre>
                      )}
                    </div>
                  </AnimatedCollapse>
                </>
              )}
            </div>
          </>
        )}
      </section>

      <FloatingModal
        open={showAiUnconfiguredModal}
        onClose={() => setShowAiUnconfiguredModal(false)}
        testId="ai-unconfigured-modal"
        maxWidthClass="max-w-[460px]"
        title={
          rawAiUrl
            ? isZh
              ? '尚未配置 AI API Key'
              : 'AI API Key Missing'
            : isZh
              ? '尚未配置 AI 大模型'
              : 'AI Model Not Configured'
        }
      >
        <div className="p-6 space-y-4 text-[13px]">
          <p className="m-0 text-[var(--fg-2)] leading-relaxed">
            {rawAiUrl
              ? isZh
                ? `已配置 AI 接口地址（${rawAiUrl}），但尚未填写 API Key，无法调用外部远程模型生成深度复盘。你可以前往「设置与连接」补充 API Key，或选择使用内置本地精算规则生成「${currentPeriodLabel}」基础诊断报告。`
                : `AI Base URL (${rawAiUrl}) is set, but no API Key is configured yet. Add an API Key in Settings, or generate a "${currentPeriodLabel}" report using built-in local actuarial rules.`
              : isZh
                ? `当前尚未配置 AI 接口地址与 API Key，无法调用外部大模型生成深度复盘。你可以前往「设置与连接」接入任意 OpenAI 兼容模型，或选择使用内置本地精算规则生成「${currentPeriodLabel}」基础诊断报告。`
                : `No AI Base URL or API Key is configured yet. You can configure any OpenAI-compatible model in Settings, or generate a "${currentPeriodLabel}" report using built-in local actuarial rules.`}
          </p>
          <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => setShowAiUnconfiguredModal(false)}
              className="otl-btn"
            >
              {t('cancel')}
            </button>
            {onGoConfigureAi && (
              <button
                type="button"
                data-testid="ai-modal-go-settings-btn"
                onClick={() => {
                  setShowAiUnconfiguredModal(false)
                  onGoConfigureAi()
                }}
                className="otl-btn"
              >
                <Settings size={13} />
                <span>{isZh ? '前往配置 AI' : 'Configure AI'}</span>
              </button>
            )}
            <button
              type="button"
              data-testid="ai-modal-use-local-btn"
              onClick={() => {
                setShowAiUnconfiguredModal(false)
                void onGenerate('local_rules')
              }}
              className="otl-btn otl-btn-primary"
            >
              {isZh ? '使用本地规则生成' : 'Use Local Rules'}
            </button>
          </div>
        </div>
      </FloatingModal>
    </div>
  )
}
