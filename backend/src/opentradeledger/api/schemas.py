"""Pydantic request schemas for the OpenTradeLedger FastAPI layer."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class JournalUpdateRequest(BaseModel):
    demo_mode: bool | None = None
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class ExchangeUpsertRequest(BaseModel):
    venue_id: str
    account_id: str = ""
    create_new: bool = False
    label: str = ""
    api_key: str = ""
    api_secret: str = ""
    wallet_address: str = ""
    passphrase: str = ""
    enabled: bool = True
    expected_revision: str | None = None
    include_subaccounts: list[dict[str, str | bool]] = Field(default_factory=list)


class DemoToggleRequest(BaseModel):
    enabled: bool


class SyncRequest(BaseModel):
    force_all: bool = True
    venue_id: str | None = None
    candidate_credential: ExchangeUpsertRequest | None = None


class AiReviewRequest(BaseModel):
    demo_mode: bool | None = None
    range_key: str = "all"
    mode: str = "auto"


class AiModelsRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""


class WatcherTargetsRequest(BaseModel):
    targets: list[str] = Field(default_factory=list)


class WatcherAddRequest(BaseModel):
    raw_input: str
    slot_alert: bool = True
    trade_alert: bool = True


class WatcherPatchRequest(BaseModel):
    enabled: bool | None = None
    slot_alert: bool | None = None
    trade_alert: bool | None = None


class WatcherConfigRequest(BaseModel):
    interval_sec: int = 10
    trade_interval_sec: int = 30
    cooldown_sec: int = 180


class TestNotifyRequest(BaseModel):
    portfolio_id: str = ""
    notifications: dict[str, Any] | None = None


class AuthSetupRequest(BaseModel):
    password: str
    remember: bool = True


class AuthLoginRequest(BaseModel):
    password: str
    remember: bool = True


class AuthChangePasswordRequest(BaseModel):
    current_password: str = ""
    new_password: str
    remember: bool = True


class AuthDisableRequest(BaseModel):
    current_password: str = ""


__all__ = [
    "AiModelsRequest",
    "AiReviewRequest",
    "AuthChangePasswordRequest",
    "AuthDisableRequest",
    "AuthLoginRequest",
    "AuthSetupRequest",
    "DemoToggleRequest",
    "ExchangeUpsertRequest",
    "JournalUpdateRequest",
    "SyncRequest",
    "TestNotifyRequest",
    "WatcherAddRequest",
    "WatcherConfigRequest",
    "WatcherPatchRequest",
    "WatcherTargetsRequest",
]
