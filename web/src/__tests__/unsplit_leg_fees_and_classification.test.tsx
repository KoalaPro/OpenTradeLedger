import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
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

describe('Equity and commodity perpetual fee history on Trades', () => {
  it('keeps each venue and contract separate with Stocks disabled, including rebate-only history', () => {
    const data = makeBaseDashboard([])
    data.range_key = '7d'
    const stocks = data.extensions.stocks
    stocks.enabled = false
    const gateEquity = {
      venue_id: 'gate', venue_name: 'Gate', symbol: 'QCOM_USDT', category: 'US Equity Perp',
      trade_count: 2, qty: 10, maker_bps: 1.2, taker_bps: 3.6,
      gross_fee_usd: 1.2, rebate_usd: 0.24, net_fee_usd: 0.96, net_pnl_usd: 45.2,
    }
    stocks.perpetuals = [
      gateEquity,
      { ...gateEquity, symbol: 'XAU_USDT', category: 'Commodity Perp' },
      {
        ...gateEquity, venue_id: 'binance', venue_name: 'Binance', venue_name_zh: '币安',
        trade_count: 0, qty: 0, maker_bps: 0, taker_bps: 0,
        gross_fee_usd: 0, rebate_usd: 0.03, net_fee_usd: 0, net_pnl_usd: 0.03,
      },
    ]
    const inspect = vi.fn()
    const page = (venue: string, symbol: string, locale: 'en' | 'zh-CN' = 'en') => (
      <I18nProvider locale={locale}>
        <TradesPage
          data={data} venueFilter={venue} symbolFilter={symbol}
          onVenueChange={() => {}} onSymbolChange={() => {}} onSaveJournal={async () => {}}
          onInspectSymbol={inspect}
        />
      </I18nProvider>
    )
    const { rerender } = render(page('ALL', 'ALL'))
    const table = screen.getByRole('table', { name: /equity & commodity perps.*all time/i })
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(4)
    const equity = within(rows[1])
    expect(equity.getByText('2 / 10')).toBeTruthy()
    expect(equity.getByText('1.2 / 3.6')).toBeTruthy()
    expect(rows[1].textContent).toContain('-1.2000 / +0.2400')
    expect(rows[1].textContent).toContain('+45.20 U')
    expect(rows[2].textContent).toContain('XAU_USDT')
    expect(rows[3].textContent).toContain('+0.0300')

    rerender(page('BINANCE', 'qcom_usdt', 'zh-CN'))
    const scoped = screen.getByRole('table', { name: /股票与商品永续.*全部历史/ })
    expect(within(scoped).getAllByRole('row')).toHaveLength(2)
    fireEvent.click(within(scoped).getByRole('button', { name: /QCOM_USDT/ }))
    expect(inspect).toHaveBeenCalledWith('binance', 'QCOM_USDT', '币安', 'all')

    rerender(page('binance', 'XAU_USDT'))
    expect(screen.queryByRole('table', { name: /equity & commodity perps/i })).toBeNull()
    expect(screen.getByText(/no equity or commodity perpetual records/i)).toBeTruthy()
  })
})

it.each([[1, '1 BTC', -2, '-2.0 bps'], [null, '100 张', null, '费率未知']] as const)(
  'displays confirmed OKX units and maker credit with quantity %s', (qty, quantityText, rate, rateText) => {
    const leg = {
      role: 'MAKER' as const, mode_code: 'LIMIT_MAKER', fee_known: true, fee_bps: rate,
      time_ms: 1, time_str: '10:00', avg_price: 100000, notional_usd: rate === null ? null : 100000,
      gross_fee_usd: 0, discount_saved_usd: 0, discount_asset: '', rebate_usd: 20, rebate_pct: 0,
      net_fee_usd: -20, rebate_status: 'SETTLED' as const, settled_at: '',
    }
    const trade: ClosedTrade = {
      open_time_str: '10:00', close_time_str: '11:00', close_date: '2026-11-25', is_partial_close: false,
      venue_id: 'okx', venue_name: 'OKX', trade_id: 'okx_btc', symbol: 'BTC_USDT', side: 'LONG',
      color_token: 'okx', qty, contract_count: 100, open_time_ms: 1, close_time_ms: 2,
      holding_sec: 1, gross_pnl_usd: 1000, funding_pnl_usd: 0, book_pnl_usd: 1000,
      total_rebate_usd: 20, total_fee_usd: 0, total_discount_saved_usd: 0, net_fee_usd: -20,
      net_pnl_usd: 1020, effective_relief_pct: 0, rebate_status: 'SETTLED', tags: [], note: '',
      entry_leg: leg, exit_leg: { ...leg, rebate_usd: 0, fee_bps: 0, net_fee_usd: 0 },
    }
    render(<I18nProvider locale="zh-CN"><CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
      <TradesPage data={makeBaseDashboard([trade])} venueFilter="ALL" symbolFilter="ALL"
        onVenueChange={() => {}} onSymbolChange={() => {}} onSaveJournal={async () => {}} />
    </CurrencyProvider></I18nProvider>)
    const detail = screen.getByTestId('trade-detail-panel')
    expect(within(detail).getByText(/100 张/).closest('.sensitive')).not.toBeNull()
    expect(detail.textContent).toContain(quantityText)
    expect(detail.textContent).not.toContain('100 BTC')
    const entry = screen.getByTestId('entry-leg-fee-card')
    expect(entry.textContent).toContain(rateText)
    expect(within(entry).getAllByText('+20.000 U')).toHaveLength(2)
    expect(entry.textContent).not.toContain('免佣')
  },
)

describe('Consolidated partial take-profit trade UI', () => {
  it('renders single entry fee and localized MIXED exit mode label when TP batches mix Maker & Taker', () => {
    const consolidatedTrade: ClosedTrade = {
      trade_id: 'fut_ETH_USDT_1795705714_1795708860_long',
      venue_id: 'gate',
      venue_name: 'Gate',
      venue_name_zh: 'Gate',
      color_token: 'gate',
      symbol: 'ETH_USDT',
      side: 'LONG',
      qty: 3.684,
      open_time_ms: 1795705714217,
      close_time_ms: 1795708860515,
      open_time_str: '11-26 23:08',
      close_time_str: '11-27 00:01',
      close_date: '2026-11-27',
      holding_sec: 3146,
      is_partial_close: true,
      gross_pnl_usd: 64.7927,
      funding_pnl_usd: -0.1274,
      book_pnl_usd: 61.2546,
      total_fee_usd: 3.4106,
      total_rebate_usd: 2.6036,
      total_discount_saved_usd: 0,
      net_fee_usd: 0.807,
      net_pnl_usd: 63.8582,
      effective_relief_pct: 76.3,
      rebate_status: 'SETTLED',
      entry_leg: {
        role: 'MAKER',
        mode_code: 'LIMIT_MAKER',
        fee_known: true,
        fee_bps: 2.0,
        time_ms: 1795705714000,
        time_str: '11-26 23:08:34',
        avg_price: 1670,
        notional_usd: 6152.28,
        gross_fee_usd: 1.2305,
        discount_saved_usd: 0,
        discount_asset: '',
        rebate_usd: 0.9844,
        rebate_pct: 80,
        net_fee_usd: 0.2461,
        rebate_status: 'SETTLED',
        settled_at: '',
      },
      exit_leg: {
        role: 'TAKER',
        mode_code: 'MIXED',
        fee_known: true,
        fee_bps: 3.51,
        time_ms: 1795708860000,
        time_str: '11-27 00:01:00',
        avg_price: 1687.5876,
        notional_usd: 6217.07,
        gross_fee_usd: 2.1802,
        discount_saved_usd: 0,
        discount_asset: '',
        rebate_usd: 1.6192,
        rebate_pct: 74.3,
        net_fee_usd: 0.561,
        rebate_status: 'SETTLED',
        settled_at: '',
      },
      exit_batches: [
        {
          batch_index: 1,
          qty: 1.842,
          role: 'MAKER',
          mode_code: 'LIMIT_MAKER',
          fee_known: true,
          fee_bps: 2.0,
          time_ms: 1795706198000,
          time_str: '11-26 23:16:38',
          avg_price: 1680,
          notional_usd: 3094.56,
          gross_fee_usd: 0.6189,
          discount_saved_usd: 0,
          discount_asset: '',
          rebate_usd: 0.4951,
          rebate_pct: 80,
          net_fee_usd: 0.1238,
          gross_pnl_usd: 18.42,
          net_pnl_usd: 18.2962,
        },
        {
          batch_index: 2,
          qty: 1.842,
          role: 'TAKER',
          mode_code: 'TP_MARKET',
          fee_known: true,
          fee_bps: 5.0,
          time_ms: 1795708860000,
          time_str: '11-27 00:01:00',
          avg_price: 1695.1752,
          notional_usd: 3122.51,
          gross_fee_usd: 1.5613,
          discount_saved_usd: 0,
          discount_asset: '',
          rebate_usd: 1.1241,
          rebate_pct: 72,
          net_fee_usd: 0.4372,
          gross_pnl_usd: 46.3727,
          net_pnl_usd: 45.9355,
        },
      ],
      tags: [],
      note: '',
    }

    render(
      <I18nProvider locale="zh-CN">
        <CurrencyProvider currency="USDT" fx={DEFAULT_FX}>
          <TradesPage
            data={makeBaseDashboard([consolidatedTrade])}
            venueFilter="ALL"
            onVenueChange={() => {}}
            symbolFilter="ALL"
            onSymbolChange={() => {}}
            onSaveJournal={async () => {}}
          />
        </CurrencyProvider>
      </I18nProvider>,
    )

    // Left table keeps 1 clean compact row ("分 2 笔") with 2-decimal fees so PnL column is never clipped
    const parentRow = screen.getByTestId('trade-row-fut_ETH_USDT_1795705714_1795708860_long')
    expect(parentRow.textContent).toContain('分 2 笔')
    expect(parentRow.textContent).toContain('进 -1.23 · 出 -2.18')
    expect(parentRow.textContent).toContain('+63.86')
    const tbody = parentRow.closest('tbody')!
    expect(within(tbody).getAllByRole('row')).toHaveLength(1)

    // Top-right inspector ALWAYS displays symmetric Entry | Exit fee cards with mode, bps & per-leg rebate %
    const entryCard = screen.getByTestId('entry-leg-fee-card')
    const exitCard = screen.getByTestId('exit-leg-fee-card')
    expect(entryCard.textContent).toContain('限价挂单')
    expect(entryCard.textContent).toContain('2.0 bps')
    expect(entryCard.textContent).toContain('-1.2305 U')
    expect(entryCard.textContent).toContain('+0.9844 U (80%)')
    expect(exitCard.textContent).toContain('混合成交')
    expect(exitCard.textContent).toContain('3.5 bps')
    expect(exitCard.textContent).toContain('-2.1802 U')
    expect(exitCard.textContent).toContain('+1.6192 U (74%)')

    // Bottom full-width horizontal timeline renders per-batch mode, bps, rebate % & PnL cleanly
    const strip = screen.getByTestId('scaleout-timeline-strip')
    const batch1Row = within(strip).getByTestId('exit-batch-row-1')
    const batch2Row = within(strip).getByTestId('exit-batch-row-2')
    expect(batch1Row.textContent).toContain('平仓第 1 笔')
    expect(batch1Row.textContent).toContain('限价挂单')
    expect(batch1Row.textContent).toContain('-0.6189 U')
    expect(batch1Row.textContent).toContain('+0.4951 U (80%)')
    expect(batch1Row.textContent).toContain('+18.42 U')
    expect(batch1Row.textContent).toContain('扣出场费后+18.30 U')
    expect(batch2Row.textContent).toContain('平仓第 2 笔')
    expect(batch2Row.textContent).toContain('止盈市价')
    expect(batch2Row.textContent).not.toContain('止损市价')
    expect(batch2Row.textContent).toContain('-1.5613 U')
    expect(batch2Row.textContent).toContain('+1.1241 U (72%)')
    expect(batch2Row.textContent).toContain('+46.37 U')
    expect(screen.getByTestId('trade-detail-panel').textContent).toContain('-3.4106 U')
  })
})

describe('Position lifecycle detail', () => {
  const leg: ClosedTrade['entry_leg'] = {
    role: 'MAKER', mode_code: 'LIMIT_MAKER', fee_bps: 1, fee_known: true,
    time_ms: 1, time_str: '11-17 20:00', avg_price: 100, notional_usd: 200,
    gross_fee_usd: 0.3, rebate_usd: 0, rebate_pct: 0, discount_saved_usd: 0,
    discount_asset: '', net_fee_usd: 0.3, rebate_status: 'NONE', settled_at: '',
  }
  const first = {
    action: 'OPEN' as const, time_ms: Date.parse('2026-11-17T12:00:00Z'), order_id: '101',
    qty: 2, price: 100, remaining_qty: 2, avg_entry_price: 100, cost_basis_price: null,
    fee_usd: 0.2, gross_pnl_usd: null, allocated_entry_fee_usd: null, net_pnl_usd: null,
  }
  const reduce = { ...first, action: 'REDUCE' as const, order_id: '102', time_ms: first.time_ms + 20000,
    qty: 1, price: 110, remaining_qty: 1, cost_basis_price: 100, fee_usd: 0.1,
    gross_pnl_usd: 10, allocated_entry_fee_usd: 0.1, net_pnl_usd: 9.8 }
  const add = { ...first, action: 'ADD' as const, order_id: '103', time_ms: first.time_ms + 60000,
    qty: 1, price: 130, remaining_qty: 2, avg_entry_price: 115, fee_usd: 0.1 }
  const close = { ...reduce, action: 'CLOSE' as const, order_id: '104', time_ms: first.time_ms + 8 * 86400000,
    qty: 2, price: 120, remaining_qty: 0, avg_entry_price: null, cost_basis_price: 115,
    fee_usd: 0.2, gross_pnl_usd: 10, allocated_entry_fee_usd: 0.2, net_pnl_usd: 9.6 }
  const trade: ClosedTrade = {
    trade_id: 'lifecycle', venue_id: 'okx', venue_name: 'OKX', color_token: 'okx', symbol: 'ETH_USDT',
    side: 'LONG', qty: 3, open_time_ms: first.time_ms, close_time_ms: close.time_ms,
    open_time_str: '11-17 20:00', close_time_str: '11-25 20:00', close_date: '2026-11-25',
    holding_sec: 691200, is_partial_close: true, gross_pnl_usd: 20, funding_pnl_usd: 0,
    book_pnl_usd: 19.4, total_rebate_usd: 0, total_fee_usd: 0.6, total_discount_saved_usd: 0,
    net_fee_usd: 0.6, net_pnl_usd: 19.4, effective_relief_pct: 0, rebate_status: 'NONE',
    entry_leg: leg, exit_leg: leg, tags: [], note: 'risk journal', position_events: [first, reduce, add, close],
  }
  const page = (rows: ClosedTrade[]) => <TradesPage data={makeBaseDashboard(rows)} venueFilter="ALL"
    symbolFilter="ALL" onVenueChange={() => {}} onSymbolChange={() => {}} onSaveJournal={async () => {}} />

  it('keeps normal single-close trades clean without timeline clutter while showing entry/exit mode and rebate % directly', () => {
    const singleClosed: ClosedTrade = {
      ...trade,
      trade_id: 'single-close',
      is_partial_close: false,
      entry_leg: { ...leg, mode_code: 'LIMIT_MAKER', fee_bps: 2.0, gross_fee_usd: 0.2, rebate_usd: 0.16, rebate_pct: 80, net_fee_usd: 0.04 },
      exit_leg: { ...leg, role: 'TAKER', mode_code: 'MARKET_TAKER', fee_bps: 5.0, gross_fee_usd: 0.5, rebate_usd: 0.36, rebate_pct: 72, net_fee_usd: 0.14 },
      position_events: [first, close],
    }
    render(page([singleClosed]))
    expect(screen.queryByTestId('scaleout-timeline-strip')).toBeNull()
    expect(screen.queryByRole('list', { name: '持仓时间线' })).toBeNull()
    const row = screen.getByTestId('trade-row-single-close')
    expect(row.textContent).not.toContain('已清仓')
    const entry = screen.getByTestId('entry-leg-fee-card')
    const exit = screen.getByTestId('exit-leg-fee-card')
    expect(entry.textContent).toContain('限价挂单')
    expect(entry.textContent).toContain('(80%)')
    expect(exit.textContent).toContain('市价吃单')
    expect(exit.textContent).toContain('(72%)')
  })

  it('shows the actual inventory before each exit, both fees, time and the correct net in full-width horizontal timeline', () => {
    render(page([trade]))
    const strip = screen.getByTestId('scaleout-timeline-strip')
    const timeline = within(strip).getByRole('list', { name: '持仓时间线' })
    const events = within(timeline).getAllByRole('listitem')
    expect(events.map(row => row.querySelector('h4')?.textContent)).toEqual(['开仓', '减仓', '加仓', '清仓'])
    expect(events[0].textContent).toContain('11-17 20:00:00')
    expect(events[0].textContent).toContain('剩余仓位2 ETH')
    expect(events[1].textContent).toContain('分摊开仓费-0.1000')
    expect(events[1].textContent).toContain('扣费后已实现+9.80')
    expect(events[2].textContent).toContain('持仓均价115.000')
    expect(events[3].textContent).toContain('平仓前均价115.000')
    expect(events[3].textContent).toContain('剩余仓位0 ETH')
    expect(events[3].textContent).toContain('扣费后已实现+9.60')
    expect(within(strip).getByText('已清仓')).toBeTruthy()
    expect(within(screen.getByTestId('trade-detail-panel')).getByDisplayValue('risk journal')).toBeTruthy()
    expect(within(events[1]).getByText(/9\.80/).className).toContain('sensitive')
  })

  it('groups same-order fills into one opening and two exits with 4-decimal aligned fees and expandable source fills', () => {
    const makeExit = (qty: number, price: number, fee: number, remaining: number, order: string, index: number) => ({
      ...reduce, action: remaining ? 'REDUCE' as const : 'CLOSE' as const, time_ms: reduce.time_ms + index,
      order_id: order, qty, price, remaining_qty: remaining, fee_usd: fee,
      gross_pnl_usd: qty * 10, allocated_entry_fee_usd: qty * 0.1, net_pnl_usd: qty * 9.9 - fee,
    })
    const split = { ...trade, position_events: [
      { ...first, qty: 2, remaining_qty: 2 }, { ...add, order_id: first.order_id, time_ms: first.time_ms + 1, qty: 1.684, remaining_qty: 3.684 },
      makeExit(0.673, 1680, 0.226128, 3.011, 'exit-a', 0),
      makeExit(1, 1680, 0.336, 2.011, 'exit-a', 1),
      makeExit(0.169, 1680, 0.056784, 1.842, 'exit-a', 2),
      makeExit(0.955, 1695.18, 0.80944845, 0.887, 'exit-b', 3),
      makeExit(0.887, 1695.17, 0.751807895, 0, 'exit-b', 4),
    ] }
    render(page([split]))
    const ledger = within(screen.getByRole('list', { name: '持仓时间线' }))
    const orders = ledger.getAllByRole('listitem')
    expect(orders).toHaveLength(3)
    expect(orders.map(row => row.querySelector('h4')?.textContent)).toEqual(['开仓', '减仓', '清仓'])
    expect(within(orders[1]).getByText('-0.6189 U')).toBeTruthy()
    expect(within(orders[2]).getByText('-1.5613 U')).toBeTruthy()
    const fills = within(orders[1]).getByText('3 笔成交').closest('details')!
    fireEvent.click(within(fills).getByText('3 笔成交'))
    expect(fills.open).toBe(true)
    expect(within(fills).getByText('-0.2261 U')).toBeTruthy()
    expect(within(fills).getByText('-0.0568 U')).toBeTruthy()
  })

  it('keeps nonadjacent and cross-midnight fills separate, and requires all components for an order total', () => {
    const midnight = Date.parse('2026-11-17T16:00:00Z')
    const rows = [first,
      { ...reduce, order_id: 'same', time_ms: midnight - 1 },
      { ...reduce, order_id: 'same', time_ms: midnight, fee_usd: null, net_pnl_usd: null },
      { ...reduce, order_id: 'same', time_ms: midnight + 1 },
      { ...add, order_id: 'same', time_ms: midnight + 2 },
      { ...reduce, order_id: 'same', time_ms: midnight + 3 },
      { ...reduce, order_id: '', time_ms: midnight + 4 },
      { ...close, order_id: '', time_ms: midnight + 5 },
    ]
    render(page([{ ...trade, position_events: rows }]))
    const ledger = within(screen.getByRole('list', { name: '持仓时间线' }))
    const orders = ledger.getAllByRole('listitem')
    expect(orders).toHaveLength(7)
    expect(orders[2].textContent).toContain('本笔手续费未知')
    expect(orders[2].textContent).toContain('扣费后已实现未知')
    expect(orders[2].textContent).toContain('已实现价差+20.00')
  })

  it('keeps a missing fee and net unknown in historical batch timeline', () => {
    const batch = { ...leg, batch_index: 1, qty: 1, gross_pnl_usd: 10, net_pnl_usd: 9.8,
      net_basis: 'round_trip' as const, gross_fee_usd: 0.1 }
    render(page([{ ...trade, position_events: [], exit_batches: [batch,
      { ...batch, batch_index: 2, gross_pnl_usd: null, fee_known: false, gross_fee_usd: 0, net_pnl_usd: null },
    ] }]))
    expect(screen.getByTestId('exit-batch-row-1').textContent).toContain('扣费后已实现+9.80 U')
    const unknown = screen.getByTestId('exit-batch-row-2')
    expect(unknown.textContent).toContain('已实现价差未知')
    expect(unknown.textContent).toContain('扣费后已实现未知')
  })

  it('separates realized reductions from the entry fees carried by a remaining position', () => {
    const openTrade = { ...trade, position_events: [first, reduce, add] }
    render(page([openTrade]))
    const settlement = screen.getByTestId('trade-settlement')
    expect(settlement.textContent).toContain('已平部分扣费后合计+9.80')
    expect(settlement.textContent).toContain('剩余仓位分摊开仓费-0.2000')
  })

  it('distinguishes partial exits and a new position, with explicit missing monetary evidence and contract units', () => {
    const partial = { ...trade, trade_id: 'partial', qty: null, contract_count: 1, close_time_ms: reduce.time_ms,
      position_events: [first, { ...reduce, fee_usd: null, net_pnl_usd: null, gross_pnl_usd: 0 }] }
    render(page([partial, { ...trade, trade_id: 'reopen', position_events: [first] }]))
    fireEvent.click(screen.getByTestId('trade-row-partial'))
    const strip = within(screen.getByTestId('scaleout-timeline-strip'))
    expect(strip.getByText('已减仓 · 仍持有')).toBeTruthy()
    const rows = within(strip.getByRole('list', { name: '持仓时间线' })).getAllByRole('listitem')
    expect(rows[1].textContent).toContain('剩余仓位1 张')
    expect(rows[1].textContent).toContain('本笔手续费未知')
    expect(rows[1].textContent).toContain('扣费后已实现未知')
    expect(rows[1].textContent).toContain('已实现价差0.00')
    fireEvent.click(screen.getByTestId('trade-row-reopen'))
    const strip2 = within(screen.getByTestId('scaleout-timeline-strip'))
    expect(strip2.getByText('持仓中')).toBeTruthy()
    expect(within(strip2.getByRole('list', { name: '持仓时间线' })).getAllByRole('listitem')).toHaveLength(1)
  })
})
