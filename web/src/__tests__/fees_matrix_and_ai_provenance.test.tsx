import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PublicCopyWatcherPanel } from '../components/PublicCopyWatcherPanel'
import { I18nProvider } from '../i18n'
import { CurrencyProvider, DEFAULT_FX } from '../lib/currency'
import type { DashboardData, UserSettings } from '../lib/types'
import { AnalyticsPage } from '../pages/AnalyticsPage'
import { FeesAiPage } from '../pages/FeesAiPage'
import { OverviewPage } from '../pages/OverviewPage'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const BASE_SETTINGS: UserSettings = {
  locale: 'zh-CN',
  polarity: 'cn',
  theme: 'obsidian',
  display_currency: 'USDT',
  privacy_mode: false,
  goal_enabled: false,
  goal_target_usd: null,
  goal_label: '',
  ai_base_url: 'https://llm.example.com/v1',
  ai_api_key: '',
  ai_api_key_set: true,
  ai_model: 'gpt-4o-mini',
  outbound_proxy_url: '',
  watcher_interval_sec: 3,
  watcher_trade_interval_sec: 10,
  watcher_cooldown_sec: 180,
  copy_watcher_targets: [],
  modules: { copy_trading: true, copy_watcher: true, stocks: true },
  notifications: {
    bark_server_url: 'https://api.day.app',
    bark_push_key: '',
    bark_push_key_set: true,
  },
}

function makeDashboard(overrides?: Partial<DashboardData>): DashboardData {
  return {
    demo_mode: false,
    explicit_demo_mode: false,
    has_live_data: true,
    read_only: true,
    last_sync_ms: 1732500000000,
    syncing: false,
    range_key: '7d',
    available_months: ['2026-11'],
    available_symbols: ['BTC_USDT'],
    summary: {
      total_equity_usd: 2500,
      net_principal_usd: 2500,
      whole_net_pnl_usd: 0,
      range_whole_net_pnl_usd: 0,
      whole_roi_pct: 0,
      range_whole_roi_pct: 0,
      futures_net_pnl_usd: 0,
      futures_book_pnl_usd: 0,
      trade_count: 0,
      all_time_trade_count: 1,
      win_rate_pct: 0,
      total_fee_usd: 0,
      total_rebate_usd: 0,
      total_discount_saved_usd: 0,
      net_fee_usd: 0,
      combined_relief_pct: 0,
      potential_maker_savings_usd: 0,
      max_drawdown_usd: 0,
    },
    equity_breakdown: {
      futures_margin_usd: 2500,
      copy_equity_usd: 0,
      stocks_equity_usd: 0,
      spot_and_other_usd: 0,
    },
    exchanges: [],
    reconciliation: {
      net_principal_usd: 2500,
      total_equity_usd: 2500,
      net_pnl_usd: 0,
      residual_usd: 0,
      items: [],
      range_items: [],
    },
    equity_curve: [],
    calendar_days: [],
    symbol_attribution: [],
    fee_matrix: [
      {
        venue_id: 'gate',
        display_name: 'Gate',
        display_name_zh: 'Gate',
        color_token: 'gate',
        trade_count: 1,
        maker_gross_bps: 0,
        maker_net_bps: 0,
        taker_gross_bps: 0,
        taker_net_bps: 0,
        maker_bps_known: false,
        taker_bps_known: false,
        blended_gross_bps: 2.5,
        blended_net_bps: 2.5,
        gross_fee_usd: 1.0,
        discount_saved_usd: 0,
        discount_label: '',
        rebate_usd: 0,
        rebate_pct: 0,
        net_fee_usd: 1.0,
        combined_relief_pct: 0,
        potential_maker_savings_usd: 0,
      },
    ],
    goal: null,
    ai_review: null,
    open_positions: [],
    spot_holdings: [],
    trades: [],
    extensions: {
      copy_trading: {
        enabled: false,
        total_invested_usd: 0,
        active_equity_usd: 0,
        book_pnl_usd: 0,
        rebate_total_usd: 0,
        combined_pnl_usd: 0,
        combined_roi_pct: 0,
        platforms: [],
        transfers: [],
        rebates: [],
      },
      stocks: {
        enabled: false,
        equity_usd: 0,
        unrealized_pnl_usd: 0,
        non_perp_fees_usd: 0,
        fee_rate_range: '0.1%',
        venue_fee_rates: [],
        holdings: [],
        perpetuals: [],
        cash_ledger: [],
      },
    },
    ...overrides,
  }
}

describe('v1.3.6 Follow-up 4 Audit Fixes', () => {
  it('renders unknown per-leg bps cleanly on FeesAiPage instead of 0.00 / 0.00, and hides 0.0% win rate on 0 trades', () => {
    const dash = makeDashboard()
    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <div>
            <OverviewPage data={dash} onNavigate={vi.fn()} />
            <AnalyticsPage data={dash} rangeKey="7d" settings={BASE_SETTINGS} />
            <FeesAiPage data={dash} settings={BASE_SETTINGS} generating={false} onGenerate={vi.fn()} />
          </div>
        </CurrencyProvider>
      </I18nProvider>,
    )

    // Overview & Analytics must NOT show "胜率 0.0%" when trade_count === 0
    expect(document.body.textContent).not.toContain('胜率 0.0%')

    // Fee matrix row with unknown leg split must NOT render "0.00 / 0.00"
    const feeSection = screen.getByTestId('fee-matrix-section')
    expect(feeSection.textContent).toContain('分腿未知')
    expect(feeSection.textContent).toContain('综合 2.50 bps')
    expect(feeSection.textContent).not.toContain('0.00 / 0.00')
  })

  it('distinguishes 0-trade local short-circuit vs 503 remote-failed fallback without contradictory AI status labels', () => {
    const zeroTradeDash = makeDashboard({
      ai_review: {
        generated_at: '2026-11-25 03:00:00 UTC',
        generated_ms: 1764040000000,
        model: 'local-actuarial',
        execution_mode: 'zero_trades_local',
        data_transmitted: false,
        range_key: '7d',
        range_label_zh: '近 7 天',
        trade_count_snapshot: 0,
        equity_snapshot_usd: 2500,
        highlights: [
          {
            kind: 'no_trades',
            title: 'No Closed Trades in Period',
            title_zh: '本周期无成交',
            metric: '0',
            body: 'No closed trades.',
            body_zh: '本周期无成交，暂无可分析样本。',
          },
        ],
        markdown_commentary: 'No closed trades.',
        markdown_commentary_zh: '**本周期无成交，暂无可分析样本。**',
      },
    })

    const { rerender } = render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <FeesAiPage data={zeroTradeDash} settings={BASE_SETTINGS} generating={false} onGenerate={vi.fn()} />
        </CurrencyProvider>
      </I18nProvider>,
    )

    const pageZero = screen.getByTestId('fees-ai-page')
    expect(pageZero.textContent).not.toContain('已喂给 AI 的 4 大类脱敏汇总数据')
    expect(pageZero.textContent).toContain('未外发任何数据')

    const failed503Dash = makeDashboard({
      summary: { ...zeroTradeDash.summary, trade_count: 1, win_rate_pct: 100 },
      ai_review: {
        generated_at: '2026-11-25 03:05:00 UTC',
        generated_ms: 1764040300000,
        model: 'local-actuarial',
        execution_mode: 'remote_failed_fallback',
        data_transmitted: true,
        range_key: 'all',
        range_label_zh: '全部历史',
        trade_count_snapshot: 1,
        equity_snapshot_usd: 2500,
        ai_error_zh: '本次已向所配 AI 接口发送脱敏汇总请求，但模型返回异常（HTTP 503）；下方报告已自动回退为本地精算结果。',
        highlights: [],
        markdown_commentary: 'Fallback',
        markdown_commentary_zh: '1. 本地回退结论',
        context_preview: { summary: { trade_count: 1 } },
      },
    })

    rerender(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <FeesAiPage data={failed503Dash} settings={BASE_SETTINGS} generating={false} onGenerate={vi.fn()} />
        </CurrencyProvider>
      </I18nProvider>,
    )

    const page503 = screen.getByTestId('fees-ai-page')
    // Must NEVER simultaneously claim "已连接 AI" and "未调用 AI" on HTTP 503!
    expect(page503.textContent).not.toContain('已连接 AI')
    expect(page503.textContent).not.toContain('未调用 AI')
    expect(screen.getByTestId('ai-config-status-badge').textContent).toContain('AI 接口异常')
    expect(screen.getByTestId('ai-transmission-badge').textContent).toContain('已向 AI 接口发送脱敏请求')
  })

  it('uses configured 3s / 10s intervals in PublicCopyWatcherPanel add toast instead of hardcoded 10s / 30s', async () => {
    const pid = '5075281354358777856'
    let items: Array<Record<string, unknown>> = []

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      const method = (init?.method || 'GET').toUpperCase()
      if (url.includes('/copy-watcher/targets') && method === 'POST') {
        items = [
          {
            portfolio_id: pid,
            nickname: '测试员',
            enabled: true,
            slot_alert: true,
            trade_alert: true,
            status: 'ACTIVE',
            current_copy_count: 500,
            max_copy_count: 500,
            available_slots: 0,
            has_open_slot: false,
            min_copy_usd: 100,
            profit_sharing_rate: '10',
            lead_url: `https://www.binance.com/zh-CN/copy-trading/lead-details/${pid}`,
            app_deeplink: '',
            last_alert_ms: 0,
            inferred_positions: [],
            recent_orders: [],
            probe_error: '',
            probe_error_zh: '',
          },
        ]
      }
      return new Response(
        JSON.stringify({
          running: true,
          demo_mode: false,
          interval_sec: 3,
          trade_poll_sec: 10,
          cooldown_sec: 180,
          cycles: 1,
          updated_at_ms: Date.now(),
          last_error: '',
          last_error_zh: '',
          open_slot_count: 0,
          channels: { bark: true },
          configured_targets: items.map(x => String(x.portfolio_id)),
          items,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    })

    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <PublicCopyWatcherPanel />
        </CurrencyProvider>
      </I18nProvider>,
    )

    const input = await screen.findByTestId('watcher-add-input')
    fireEvent.change(input, { target: { value: pid } })
    fireEvent.click(screen.getByTestId('watcher-add-btn'))

    await waitFor(() => {
      expect(document.body.textContent).toContain('3 秒空位扫描与 10 秒持仓推算')
    })
    expect(document.body.textContent).not.toContain('10 秒空位扫描与 30 秒持仓推算')

    // Re-adding the same ID shows preserved-settings message instead of claiming newly added
    fireEvent.change(input, { target: { value: pid } })
    fireEvent.click(screen.getByTestId('watcher-add-btn'))
    await waitFor(() => {
      expect(document.body.textContent).toContain('保留你原有的监控、响铃与推送开关设置')
    })
  })
})

it('labels configured quotes and withholds measured relief when the period has no fills', () => {
  const dash = makeDashboard()
  Object.assign(dash.fee_matrix[0], {
    trade_count: 0, maker_gross_bps: 2, maker_rate_source: 'configured',
    taker_gross_bps: 5, taker_rate_source: 'configured', combined_relief_pct: 20,
  })
  render(<I18nProvider locale="zh-CN"><FeesAiPage data={dash} generating={false} onGenerate={vi.fn()} /></I18nProvider>)
  const row = screen.getByTestId('fee-matrix-section').querySelector('tbody tr')!
  expect(row.textContent).toContain('配置')
  expect(row.textContent).toContain('2.00')
  expect(row.textContent).not.toContain('20.0%')
})
