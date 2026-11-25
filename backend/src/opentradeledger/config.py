"""Runtime configuration and user preference models (zero personal hardcodes)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ModuleSettings(BaseModel):
    copy_watcher: bool = True
    copy_trading: bool = True
    stocks: bool = True


class NotificationSettings(BaseModel):
    bark_server_url: str = "https://api.day.app"
    bark_push_key: str = ""


class UserPreferences(BaseModel):
    locale: Literal["zh-CN", "en"] = "zh-CN"
    theme: Literal["obsidian", "ocean", "light"] = "obsidian"
    polarity: Literal["cn", "global"] = "cn"
    display_currency: Literal["USDT", "USD", "CNY", "GBP", "EUR", "HKD", "JPY"] = "USDT"
    privacy_mode: bool = False
    update_check_enabled: bool = True
    goal_enabled: bool = False
    goal_target_usd: float | None = None
    goal_label: str = ""
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    outbound_proxy_url: str = ""
    watcher_interval_sec: int = 10
    watcher_trade_interval_sec: int = 30
    watcher_cooldown_sec: int = 180
    copy_watcher_targets: list[str] = Field(default_factory=list)
    modules: ModuleSettings = Field(default_factory=ModuleSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)


class AppConfig(BaseModel):
    host: str = Field(default_factory=lambda: os.getenv("OTL_HOST", "127.0.0.1"))
    port: int = Field(default_factory=lambda: int(os.getenv("OTL_PORT", "8012")))
    data_dir: Path = Field(default_factory=lambda: Path(os.getenv("OTL_DATA_DIR", "./data")))
    demo_mode: bool = Field(
        default_factory=lambda: os.getenv("OTL_DEMO_MODE", "false").strip().lower() in {"1", "true", "yes"}
    )
