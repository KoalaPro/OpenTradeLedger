"""Normalized plugin-ready domain models for OpenTradeLedger."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TradeLegFee(BaseModel):
    """Per-leg fee & rebate breakdown (mandatory for both entry_leg and exit_leg)."""

    role: Literal["MAKER", "TAKER"] = "TAKER"
    mode_code: str = "MARKET_TAKER"
    fee_bps: float = 0.0
    time_ms: int = 0
    time_str: str = ""
    avg_price: float = 0.0
    notional_usd: float = 0.0
    gross_fee_usd: float = 0.0
    discount_saved_usd: float = 0.0
    discount_asset: str = ""
    rebate_usd: float = 0.0
    rebate_pct: float = 0.0
    net_fee_usd: float = 0.0
    rebate_status: Literal["SETTLED", "PENDING", "NONE"] = "SETTLED"
    settled_at: str = ""


class ClosedTrade(BaseModel):
    trade_id: str
    venue_id: str
    venue_name: str
    color_token: str
    symbol: str
    side: Literal["LONG", "SHORT"]
    qty: float = 0.0
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
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class OpenPosition(BaseModel):
    venue_id: str
    venue_name: str
    color_token: str
    symbol: str
    side: Literal["LONG", "SHORT"]
    leverage: float = 1.0
    qty: float = 0.0
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


class CalendarDay(BaseModel):
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
