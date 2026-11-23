import { useState } from 'react'
import { ChevronDown, ChevronUp, ShieldCheck, Sparkles } from 'lucide-react'
import { SimpleMarkdown } from '../components/SimpleMarkdown'
import { VenueBadge } from '../components/VenueBadge'
import { useI18n } from '../i18n'
import { useCurrency } from '../lib/currency'
import type { DashboardData } from '../lib/types'

export function FeesAiPage({
  data,
  generating,
  onGenerate,
}: {
  data: DashboardData
  generating: boolean
  onGenerate: () => Promise<void>
}) {
  const { locale, t } = useI18n()
  const cur = useCurrency()
  const [showAiPayload, setShowAiPayload] = useState(false)
  const ai = data.ai_review

  return (
    <div data-testid="fees-ai-page" className="space-y-4">
      {/* Cross-venue Fee & Rebate Comparison Matrix Table */}
      <section data-testid="fee-matrix-section" className="otl-card overflow-hidden">
        <div className="px-5 py-3.5 border-b border-[var(--border)] flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="m-0 text-[14.5px] font-semibold text-[var(--fg)]">{t('fee_matrix_title')}</h2>
            <p className="m-0 text-[12px] text-[var(--fg-3)] mt-0.5">{t('fee_matrix_sub')}</p>
          </div>
          {data.summary.potential_maker_savings_usd > 0 && (
            <span
              className="px-2.5 py-1 rounded-md text-[12px] font-semibold whitespace-nowrap sensitive"
              style={{ background: 'var(--pos-soft)', color: 'var(--pos)' }}
            >
              {t('maker_savings_badge')}: +{cur.money(data.summary.potential_maker_savings_usd)}
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
                    <VenueBadge venueId={row.venue_id} name={row.display_name} />
                  </td>
                  <td className="text-right whitespace-nowrap">{row.trade_count}</td>
                  <td className="text-right whitespace-nowrap">
                    {row.maker_gross_bps.toFixed(2)} / <b>{row.maker_net_bps.toFixed(2)}</b>
                  </td>
                  <td className="text-right whitespace-nowrap">
                    {row.taker_gross_bps.toFixed(2)} / <b>{row.taker_net_bps.toFixed(2)}</b>
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
                        +{cur.money(row.rebate_usd)} ({row.rebate_pct.toFixed(1)}%)
                      </span>
                    ) : (
                      <span className="text-[var(--fg-3)]">—</span>
                    )}
                  </td>
                  <td className="text-right font-semibold whitespace-nowrap">
                    {row.combined_relief_pct.toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Structured AI Review */}
      <section className="otl-card p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="m-0 text-[15px] font-semibold text-[var(--fg)]">{t('ai_title')}</h2>
            {ai && (
              <p className="m-0 text-[12px] text-[var(--fg-3)] mt-0.5">
                {ai.generated_at} · {ai.model}
              </p>
            )}
          </div>
          <button
            type="button"
            data-testid="generate-ai-btn"
            disabled={generating}
            onClick={() => void onGenerate()}
            className="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-lg text-[12px] font-semibold cursor-pointer disabled:opacity-50"
            style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
          >
            <Sparkles size={14} />
            <span>{generating ? t('ai_generating') : t('ai_generate')}</span>
          </button>
        </div>

        {ai?.stale && (
          <div
            data-testid="ai-stale-banner"
            className="px-3.5 py-2.5 rounded-lg text-[12px]"
            style={{
              background: 'var(--warn-soft)',
              border: '1px solid var(--warn-bd)',
              color: 'var(--warn)',
            }}
          >
            {t('ai_stale_notice')}
          </div>
        )}

        {!ai ? (
          <div className="py-10 text-center text-[13px] text-[var(--fg-3)]">{t('ai_empty')}</div>
        ) : (
          <>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {ai.highlights.map((h, i) => (
                <div key={i} className="otl-surface-2 rounded-xl p-4 flex flex-col gap-1.5">
                  <div className="text-[12px] text-[var(--fg-3)]">
                    {locale === 'en' ? h.title : h.title_zh || h.title}
                  </div>
                  {h.metric && <div className="text-[17px] font-semibold text-[var(--fg)] sensitive">{h.metric}</div>}
                  <p className="m-0 text-[12px] leading-relaxed text-[var(--fg-2)]">
                    {locale === 'en' ? h.body : h.body_zh || h.body}
                  </p>
                </div>
              ))}
            </div>

            {(ai.markdown_commentary || ai.markdown_commentary_zh) && (
              <div className="otl-surface-2 rounded-xl p-4 space-y-2.5">
                <div className="text-[13px] font-semibold text-[var(--fg)]">{t('ai_commentary_title')}</div>
                <SimpleMarkdown
                  content={
                    locale === 'en'
                      ? ai.markdown_commentary
                      : ai.markdown_commentary_zh || ai.markdown_commentary
                  }
                />
              </div>
            )}

            {/* Prompt Data Transparency Disclosure */}
            <div className="pt-1">
              <button
                type="button"
                data-testid="toggle-ai-payload-btn"
                onClick={() => setShowAiPayload(v => !v)}
                className="inline-flex items-center gap-1.5 text-[12px] text-[var(--fg-3)] hover:text-[var(--fg)] cursor-pointer"
              >
                <ShieldCheck size={13} className="txt-pos" />
                <span>
                  {locale === 'en'
                    ? 'Inspect sanitized data sent to AI (Zero API keys / UIDs)'
                    : '查看本次喂给 AI 的脱敏数据维度（绝不含 API Key / UID / 钱包地址）'}
                </span>
                {showAiPayload ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
              </button>

              {showAiPayload && (
                <div className="otl-drawer-expand mt-2.5 otl-surface-2 rounded-xl p-4 space-y-3 text-[12px]">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5 text-[var(--fg-2)]">
                    <div>
                      <b className="text-[var(--fg)]">
                        {locale === 'en' ? 'Included Metrics' : '已喂给 AI 的 8 类脱敏数据：'}
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
                    <pre className="m-0 p-3 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-[11px] text-[var(--fg-2)] overflow-x-auto max-h-64">
                      {JSON.stringify(ai.context_preview, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  )
}
