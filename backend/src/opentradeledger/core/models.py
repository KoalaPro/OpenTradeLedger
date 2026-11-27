"""Normalized plugin-ready domain models for OpenTradeLedger."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TradeLegFee(BaseModel):
    """Per-leg fee & rebate breakdown (mandatory for both entry_leg and exit_leg)."""

    role: Literal["MAKER", "TAKER", "UNKNOWN"] = "TAKER"
    mode_code: str = "MARKET_TAKER"
    fee_known: bool = True
    fee_bps: float | None = 0.0
    time_ms: int = 0
    time_str: str = ""
    avg_price: float = 0.0
    notional_usd: float | None = 0.0
    gross_fee_usd: float = 0.0
    discount_saved_usd: float = 0.0
    discount_asset: str = ""
    rebate_usd: float = 0.0
    rebate_pct: float = 0.0
    net_fee_usd: float = 0.0
    rebate_status: Literal["SETTLED", "PENDING", "NONE"] = "SETTLED"
    settled_at: str = ""


class TradeExitBatch(BaseModel):
    """Per-batch exit breakdown nested under a single consolidated ClosedTrade."""

    batch_index: int = 1
    order_id: str = ""
    net_basis: Literal["round_trip", "exit_only"] = "exit_only"
    qty: float | None = 0.0
    contract_count: float | None = None
    role: Literal["MAKER", "TAKER", "UNKNOWN"] = "TAKER"
    mode_code: str = "MARKET_TAKER"
    fee_known: bool = True
    fee_bps: float | None = 0.0
    time_ms: int = 0
    time_str: str = ""
    avg_price: float = 0.0
    notional_usd: float | None = 0.0
    gross_fee_usd: float = 0.0
    discount_saved_usd: float = 0.0
    discount_asset: str = ""
    rebate_usd: float = 0.0
    rebate_pct: float = 0.0
    net_fee_usd: float = 0.0
    gross_pnl_usd: float | None = None
    net_pnl_usd: float | None = None


class PositionEvent(BaseModel):
    time_ms: int
    action: Literal["OPEN", "ADD", "REDUCE", "CLOSE"]
    order_id: str
    qty_unit: Literal["base", "contracts"] = "base"
    qty: float
    price: float
    remaining_qty: float
    avg_entry_price: float | None
    cost_basis_price: float | None
    fee_usd: float | None
    gross_pnl_usd: float | None
    allocated_entry_fee_usd: float | None
    net_pnl_usd: float | None


class ClosedTrade(BaseModel):
    trade_id: str
    venue_id: str
    venue_name: str
    color_token: str
    symbol: str
    side: Literal["LONG", "SHORT"]
    qty: float | None = 0.0
    contract_count: float | None = None
    open_time_ms: int = 0
    close_time_ms: int = 0
    open_time_str: str = ""
    close_time_str: str = ""
    close_date: str = ""
    holding_sec: int = 0
    is_partial_close: bool = False
    gross_pnl_usd: float = 0.0
    funding_pnl_usd: float = 0.0
    book_pnl_usd: float = 0.0
    total_fee_usd: float = 0.0
    total_rebate_usd: float = 0.0
    total_discount_saved_usd: float = 0.0
    net_fee_usd: float = 0.0
    net_pnl_usd: float = 0.0
    effective_relief_pct: float = 0.0
    rebate_status: Literal["SETTLED", "PENDING", "NONE"] = "SETTLED"
    entry_leg: TradeLegFee
    exit_leg: TradeLegFee
    position_events: list[PositionEvent] = Field(default_factory=list)
    exit_batches: list[TradeExitBatch] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class OpenPosition(BaseModel):
    venue_id: str
    venue_name: str
    color_token: str
    symbol: str
    side: Literal["LONG", "SHORT"]
    leverage: float = 1.0
    qty: float | None = 0.0
    contract_count: float | None = None
    notional_usd: float = 0.0
    entry_price: float = 0.0
    mark_price: float = 0.0
    liq_price: float = 0.0
    unrealized_pnl_usd: float = 0.0
    unrealized_roi_pct: float = 0.0
    has_stop_order: bool = True


class ExchangeSummary(BaseModel):
    venue_id: str
    display_name: str
    display_name_zh: str
    color_token: str
    connected: bool = True
    equity_usd: float = 0.0
    net_principal_usd: float = 0.0
    net_pnl_usd: float = 0.0
    available_margin_usd: float = 0.0
    trade_count: int = 0
    rebate_rate_pct: float = 0.0
    discount_rate_pct: float = 0.0
    capabilities: dict[str, bool] = Field(default_factory=dict)


class VenueFeeRow(BaseModel):
    venue_id: str
    display_name: str
    color_token: str
    trade_count: int = 0
    maker_gross_bps: float = 0.0
    maker_net_bps: float = 0.0
    taker_gross_bps: float = 0.0
    taker_net_bps: float = 0.0
    gross_fee_usd: float = 0.0
    discount_saved_usd: float = 0.0
    discount_label: str = ""
    rebate_usd: float = 0.0
    rebate_pct: float = 0.0
    net_fee_usd: float = 0.0
    combined_relief_pct: float = 0.0
    potential_maker_savings_usd: float = 0.0


class ReconcileItem(BaseModel):
    key: str
    label: str
    detail: str
    amount_usd: float
    venue_id: str = ""


class EquityPoint(BaseModel):
    seq: int
    date: str
    label: str
    delta_usd: float
    cum_net_usd: float
    cum_book_usd: float


class TradeDayActivity(BaseModel):
    trade_id: str
    venue_id: str
    symbol: str
    side: Literal["LONG", "SHORT"]
    amount_usd: float
    exit_count: int


class CalendarDay(BaseModel):
    trade_activity: list[TradeDayActivity] = Field(default_factory=list)
    date: str
    net_pnl_usd: float
    trade_count: int = 0


class SymbolAttribution(BaseModel):
    venue_id: str
    venue_name: str
    color_token: str
    symbol: str
    trade_count: int
    win_rate_pct: float
    gross_fee_usd: float
    rebate_usd: float
    discount_saved_usd: float
    book_pnl_usd: float
    net_pnl_usd: float
