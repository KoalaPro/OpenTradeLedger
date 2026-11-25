export type Locale = 'zh-CN' | 'en'
export type Theme = 'obsidian' | 'ocean' | 'light'
export type Polarity = 'cn' | 'global'
export type CurrencyCode = 'USDT' | 'USD' | 'CNY' | 'GBP' | 'EUR' | 'HKD' | 'JPY'
export type NavPage = 'overview' | 'trades' | 'analytics' | 'fees' | 'extensions' | 'settings'
export type ExtensionSubTab = 'watcher' | 'copy' | 'stock'

export interface AuthStatusPayload {
  demo_bypass?: boolean
  initialized?: boolean
  enabled?: boolean
  authenticated?: boolean
  require_setup?: boolean
  can_skip?: boolean
}

export interface VenueHealthState {
  status: 'connected' | 'error' | 'unverified' | 'disabled' | 'archived' | 'not_configured'
  last_attempt_ms: number
  last_ok_ms: number
  error_code: string
  error_reason: string
  error_reason_zh: string
  raw_error: string
}

export interface FxRatesPayload {
  base: string
  source: string
  fetched_sec?: number
  updated_at: string
  rates: Record<CurrencyCode, number>
  stale?: boolean
}

export interface VersionCheckPayload {
  current_version: string
  latest_version: string
  update_available: boolean
  check_enabled: boolean
  github_repo: string
  release_url: string
  release_name: string
  release_notes: string
  published_at: string
  checked_sec: number
  checked_at: string
  stale: boolean
  error: string
  error_zh: string
}

export interface TradeLegFee {
  role: 'MAKER' | 'TAKER'
  mode_code: string
  fee_bps: number
  time_ms: number
  time_str: string
  avg_price: number
  notional_usd: number
  gross_fee_usd: number
  discount_saved_usd: number
  discount_asset: string
  rebate_usd: number
  rebate_pct: number
  net_fee_usd: number
  rebate_status: 'SETTLED' | 'PENDING' | 'NONE'
  settled_at: string
}

export interface ClosedTrade {
  trade_id: string
  venue_id: string
  venue_name: string
  venue_name_zh?: string
  color_token: string
  symbol: string
  side: 'LONG' | 'SHORT'
  qty: number
  open_time_ms: number
  close_time_ms: number
  open_time_str: string
  close_time_str: string
  close_date: string
  holding_sec: number
  is_partial_close: boolean
  gross_pnl_usd: number
  funding_pnl_usd: number
  book_pnl_usd: number
  total_fee_usd: number
  total_rebate_usd: number
  total_discount_saved_usd: number
  net_fee_usd: number
  net_pnl_usd: number
  effective_relief_pct: number
  rebate_status: 'SETTLED' | 'PENDING' | 'NONE'
  entry_leg: TradeLegFee
  exit_leg: TradeLegFee
  tags: string[]
  note: string
}

export interface ExchangeSummary {
  venue_id: string
  display_name: string
  display_name_zh: string
  color_token: string
  connected: boolean
  health_status?: 'connected' | 'error' | 'archived'
  error_reason?: string
  error_reason_zh?: string
  equity_usd: number
  net_principal_usd: number
  net_pnl_usd: number
  available_margin_usd: number
  trade_count: number
  rebate_rate_pct: number
  discount_rate_pct: number
  capabilities: Record<string, boolean>
}

export interface OpenPosition {
  venue_id: string
  venue_name: string
  color_token: string
  symbol: string
  side: 'LONG' | 'SHORT'
  leverage: number
  qty: number
  notional_usd: number
  entry_price: number
  mark_price: number
  liq_price: number
  unrealized_pnl_usd: number
  unrealized_roi_pct: number
  has_stop_order: boolean
}

export interface VenueFeeRow {
  venue_id: string
  display_name: string
  display_name_zh?: string
  color_token: string
  trade_count: number
  maker_gross_bps: number
  maker_net_bps: number
  taker_gross_bps: number
  taker_net_bps: number
  gross_fee_usd: number
  discount_saved_usd: number
  discount_label: string
  rebate_usd: number
  rebate_pct: number
  net_fee_usd: number
  combined_relief_pct: number
  potential_maker_savings_usd: number
}

export interface SpotHolding {
  venue_id: string
  venue_name: string
  color_token: string
  asset: string
  qty: number
  price_usd: number
  market_value_usd: number
  wallet_type: string
}

export interface DashboardData {
  demo_mode: boolean
  read_only: boolean
  last_sync_ms: number
  syncing: boolean
  range_key: string
  available_months: string[]
  available_symbols: string[]
  summary: {
    total_equity_usd: number
    net_principal_usd: number
    whole_net_pnl_usd: number
    range_whole_net_pnl_usd?: number
    whole_roi_pct: number
    range_whole_roi_pct?: number
    futures_net_pnl_usd: number
    futures_book_pnl_usd: number
    trade_count: number
    all_time_trade_count: number
    win_rate_pct: number
    total_fee_usd: number
    total_rebate_usd: number
    total_discount_saved_usd: number
    net_fee_usd: number
    combined_relief_pct: number
    potential_maker_savings_usd: number
    max_drawdown_usd: number
  }
  equity_breakdown?: {
    futures_margin_usd: number
    copy_equity_usd: number
    stocks_equity_usd: number
    spot_and_other_usd: number
  }
  exchanges: ExchangeSummary[]
  open_positions: OpenPosition[]
  spot_holdings?: SpotHolding[]
  trades: ClosedTrade[]
  reconciliation: {
    net_principal_usd: number
    total_equity_usd: number
    net_pnl_usd: number
    residual_usd: number
    items: Array<{
      key: string
      label: string
      label_zh: string
      detail: string
      detail_zh: string
      amount_usd: number
    }>
    range_items?: Array<{
      key: string
      label: string
      label_zh: string
      detail: string
      detail_zh: string
      amount_usd: number
    }>
  }
  equity_curve: Array<{
    seq: number
    date: string
    label: string
    delta_usd: number
    cum_net_usd: number
    cum_book_usd: number
  }>
  calendar_days: Array<{
    date: string
    net_pnl_usd: number
    trade_count: number
  }>
  symbol_attribution: Array<{
    venue_id: string
    venue_name: string
    venue_name_zh?: string
    color_token: string
    symbol: string
    trade_count: number
    win_rate_pct: number
    gross_fee_usd: number
    rebate_usd: number
    discount_saved_usd: number
    book_pnl_usd: number
    net_pnl_usd: number
  }>
  fee_matrix: VenueFeeRow[]
  goal: null | {
    enabled: boolean
    label: string
    target_usd: number
    current_equity_usd: number
    remaining_usd: number
    surplus_usd: number
    is_reached: boolean
    progress_pct: number
    needed_gain_pct: number
    today_delta_usd: number
  }
  ai_review: null | {
    generated_at: string
    generated_ms: number
    model: string
    range_key?: string
    range_label?: string
    range_label_zh?: string
    trade_count_snapshot?: number
    equity_snapshot_usd: number
    stale?: boolean
    stale_reason?: string
    stale_reason_zh?: string
    highlights: Array<{
      kind?: string
      title: string
      title_zh: string
      metric: string
      amount_usd?: number
      pct?: number
      trade_count?: number
      avg_win_m?: number
      avg_loss_m?: number
      body: string
      body_zh: string
    }>
    markdown_commentary: string
    markdown_commentary_zh?: string
    ai_error?: string
    ai_error_zh?: string
    input_summary?: {
      exchanges_count: number
      fee_matrix_count: number
      top_symbols_count: number
      recent_trades_count: number
      open_positions_count: number
      spot_holdings_count: number
      has_copy_trading: boolean
      has_stocks: boolean
      excluded_secrets: string[]
    }
    context_preview?: Record<string, unknown>
  }
  extensions: {
    copy_trading: {
      enabled: boolean
      total_invested_usd: number
      active_equity_usd: number
      book_pnl_usd: number
      rebate_total_usd: number
      combined_pnl_usd: number
      combined_roi_pct: number
      platforms: Array<{
        venue_id: string
        venue_name: string
        venue_name_zh?: string
        color_token: string
        status: string
        invested_usd: number
        equity_usd: number
        rebate_usd: number
        combined_pnl_usd: number
        rebate_count: number
      }>
      transfers: Array<{
        venue_id: string
        venue_name: string
        venue_name_zh?: string
        time_str: string
        direction: string
        direction_zh: string
        amount_usd: number
      }>
      rebates: Array<{
        venue_id: string
        venue_name: string
        venue_name_zh?: string
        time_str: string
        destination: string
        destination_zh?: string
        est_gross_fee_usd: number
        rebate_usd: number
      }>
    }
    stocks: {
      enabled: boolean
      equity_usd: number
      unrealized_pnl_usd: number
      non_perp_fees_usd: number
      fee_rate_range: string
      venue_fee_rates: Array<{
        venue_id: string
        venue_name: string
        venue_name_zh?: string
        summary: string
        summary_zh?: string
      }>
      holdings: Array<{
        venue_id: string
        venue_name: string
        venue_name_zh?: string
        symbol: string
        name: string
        channel: string
        channel_zh: string
        shares: number
        avg_cost_usd: number
        current_price_usd: number
        market_value_usd: number
        pnl_usd: number
        pnl_pct: number
      }>
      cash_ledger: Array<{
        venue_id: string
        venue_name: string
        venue_name_zh?: string
        time_str: string
        type: string
        type_zh?: string
        symbol: string
        change: string
      }>
      perpetuals: Array<{
        venue_id: string
        venue_name: string
        venue_name_zh?: string
        symbol: string
        category: string
        category_zh?: string
        trade_count: number
        qty: number
        maker_bps: number
        taker_bps: number
        gross_fee_usd: number
        rebate_usd: number
        net_fee_usd: number
        net_pnl_usd: number
      }>
    }
  }
}

export interface CopyWatcherInferredPosition {
  symbol: string
  position_side: 'LONG' | 'SHORT' | string
  side_zh: string
  qty: number
  avg_entry_price: number
  notional_usd: number
  mark_price: number
  unrealized_pnl_usd: number
  unrealized_roi_pct: number
  opened_at_bj: string
  last_action_bj: string
}

export interface CopyWatcherOrderItem {
  order_time_ms: number
  time_bj: string
  symbol: string
  side: string
  position_side: string
  action_zh: string
  action_en?: string
  order_type: string
  executed_qty: number
  avg_price: number
  realized_pnl: number
}

export interface CopyWatcherTargetItem {
  portfolio_id: string
  venue: string
  nickname: string
  status: string
  portfolio_type: string
  enabled: boolean
  slot_alert: boolean
  trade_alert: boolean
  current_copy_count: number
  max_copy_count: number
  available_slots: number
  has_open_slot: boolean
  min_copy_usd: number
  profit_sharing_rate: string
  lead_url: string
  app_deeplink: string
  last_alert_ms: number
  inferred_positions: CopyWatcherInferredPosition[]
  recent_orders: CopyWatcherOrderItem[]
  probe_error?: string
  probe_error_zh?: string
}

export interface CopyWatcherStatusPayload {
  demo_mode: boolean
  watcher_running: boolean
  interval_sec: number
  trade_poll_sec: number
  cooldown_sec: number
  updated_at_ms: number
  last_error?: string
  last_error_zh?: string
  open_slot_count: number
  channels: {
    webhook: boolean
    telegram: boolean
    bark: boolean
  }
  configured_targets: string[]
  items: CopyWatcherTargetItem[]
}

export interface UserSettings {
  locale: Locale
  theme: Theme
  polarity: Polarity
  display_currency?: CurrencyCode
  privacy_mode: boolean
  update_check_enabled?: boolean
  goal_enabled: boolean
  goal_target_usd: number | null
  goal_label: string
  ai_base_url: string
  ai_api_key: string
  ai_api_key_set?: boolean
  ai_model: string
  outbound_proxy_url?: string
  watcher_interval_sec?: number
  watcher_trade_interval_sec?: number
  watcher_cooldown_sec?: number
  copy_watcher_targets: string[]
  modules: {
    copy_watcher?: boolean
    copy_trading: boolean
    stocks: boolean
  }
  notifications: {
    webhook_url: string
    telegram_bot_token: string
    telegram_bot_token_set?: boolean
    telegram_chat_id: string
    bark_server_url: string
    bark_push_key: string
    bark_push_key_set?: boolean
  }
}

export interface ExchangeAdapterInfo {
  venue_id: string
  display_name: string
  display_name_zh: string
  color_token: string
  requires_passphrase: boolean
  capabilities: Record<string, boolean>
  docs_url: string
  api_base_url: string
  configured: boolean
  has_archived_data?: boolean
  credential?: {
    venue_id: string
    label: string
    masked_api_key: string
    has_passphrase: boolean
    enabled: boolean
  } | null
  poll_state?: Record<string, unknown> | null
  health?: VenueHealthState
}
