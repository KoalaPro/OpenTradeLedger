import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { I18nProvider } from '../i18n'
import { CurrencyProvider, DEFAULT_FX } from '../lib/currency'
import type { ClosedTrade, DashboardData } from '../lib/types'
import { TradesPage } from '../pages/TradesPage'

function makeBaseDashboard(trades: ClosedTrade[]): DashboardData {
  return {
    demo_mode: false,
    read_only: true,
    last_sync_ms: Date.now(),
    syncing: false,
    range_key: 'all',
    available_months: ['2026-11'],
    available_symbols: ['BTC_USDT'],
    summary: {
      total_equity_usd: 1000,
      net_principal_usd: 1000,
      whole_net_pnl_usd: 9,
      range_whole_net_pnl_usd: 9,
      whole_roi_pct: 0.9,
      range_whole_roi_pct: 0.9,
      futures_net_pnl_usd: 9,
      futures_book_pnl_usd: 9,
      trade_count: trades.length,
      all_time_trade_count: trades.length,
      win_rate_pct: 100,
      total_fee_usd: 1,
      total_rebate_usd: 0,
      total_discount_saved_usd: 0,
      net_fee_usd: 1,
      combined_relief_pct: 0,
      potential_maker_savings_usd: 0,
      max_drawdown_usd: 0,
    },
    equity_breakdown: {
      futures_margin_usd: 1000,
      copy_equity_usd: 0,
      stocks_equity_usd: 0,
      spot_and_other_usd: 0,
    },
    exchanges: [],
    open_positions: [],
    spot_holdings: [],
    trades,
    reconciliation: {
      net_principal_usd: 1000,
      total_equity_usd: 1000,
      net_pnl_usd: 9,
      residual_usd: 0,
      items: [],
      range_items: [],
    },
    equity_curve: [],
    calendar_days: [],
    symbol_attribution: [],
    fee_matrix: [],
    goal: null,
    ai_review: null,
    extensions: {
      copy_trading: {
        enabled: true,
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
        enabled: true,
        equity_usd: 0,
        unrealized_pnl_usd: 0,
        non_perp_fees_usd: 0,
        fee_rate_range: '0.1%',
        venue_fee_rates: [],
        holdings: [],
        cash_ledger: [],
        perpetuals: [],
      },
    },
  }
}

describe('v1.3.5 Per-Leg Unknown Fee vs Genuine Zero Fee UI', () => {
  it('shows 分腿费用未知 instead of 免佣 / 0.000 when total fee is 1 U without per-leg split', () => {
    const unsplitTrade: ClosedTrade = {
      trade_id: 'gate_unsplit_1',
      venue_id: 'gate',
      venue_name: 'Gate',
      venue_name_zh: 'Gate',
      color_token: 'gate',
      symbol: 'BTC_USDT',
      side: 'LONG',
      qty: 0.1,
      open_time_ms: 1732400000000,
      close_time_ms: 1732403600000,
      open_time_str: '11-24 10:00',
      close_time_str: '11-24 11:00',
      close_date: '2026-11-24',
      holding_sec: 3600,
      is_partial_close: false,
      gross_pnl_usd: 10,
      funding_pnl_usd: 0,
      book_pnl_usd: 9,
      total_fee_usd: 1,
      total_rebate_usd: 0,
      total_discount_saved_usd: 0,
      net_fee_usd: 1,
      net_pnl_usd: 9,
      effective_relief_pct: 0,
      rebate_status: 'NONE',
      entry_leg: {
        role: 'TAKER',
        mode_code: 'UNKNOWN',
        fee_known: false,
        fee_bps: 0,
        time_ms: 1732400000000,
        time_str: '11-24 10:00',
        avg_price: 90000,
        notional_usd: 9000,
        gross_fee_usd: 0,
        discount_saved_usd: 0,
        discount_asset: '',
        rebate_usd: 0,
        rebate_pct: 0,
        net_fee_usd: 0,
        rebate_status: 'NONE',
        settled_at: '',
      },
      exit_leg: {
        role: 'TAKER',
        mode_code: 'UNKNOWN',
        fee_known: false,
        fee_bps: 0,
        time_ms: 1732403600000,
        time_str: '11-24 11:00',
        avg_price: 90100,
        notional_usd: 9010,
        gross_fee_usd: 0,
        discount_saved_usd: 0,
        discount_asset: '',
        rebate_usd: 0,
        rebate_pct: 0,
        net_fee_usd: 0,
        rebate_status: 'NONE',
        settled_at: '',
      },
      tags: [],
      note: '',
    }

    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <TradesPage
            data={makeBaseDashboard([unsplitTrade])}
            venueFilter="ALL"
            onVenueChange={() => {}}
            symbolFilter="ALL"
            onSymbolChange={() => {}}
            onSaveJournal={async () => {}}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    const entryCard = screen.getByTestId('entry-leg-fee-card')
    const exitCard = screen.getByTestId('exit-leg-fee-card')
    const detailPanel = screen.getByTestId('trade-detail-panel')

    expect(entryCard.textContent).toContain('分腿费用未知')
    expect(exitCard.textContent).toContain('分腿费用未知')
    expect(entryCard.textContent).not.toContain('免佣')
    expect(exitCard.textContent).not.toContain('免佣')
    expect(entryCard.textContent).not.toContain('0.000')
    expect(exitCard.textContent).not.toContain('0.000')
    expect(detailPanel.textContent).toContain('-1.000')
  })
})
