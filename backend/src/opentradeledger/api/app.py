"""FastAPI application for OpenTradeLedger (交易账本)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import math
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from opentradeledger import __app_name__, __app_name_zh__, __version__
from opentradeledger.adapters.registry import AdapterRegistry, build_default_registry
from opentradeledger.api.http_helpers import (
    _ORIG_HTTPS_PROXY,
    _ORIG_HTTP_PROXY,
    _ORIG_NO_PROXY,
    _SAFE_LAN_NO_PROXY,
    _import_dotenv_watcher_targets_once,
    _is_https_request,
    _is_trusted_local_or_private_host,
    _set_session_cookie,
    _validate_and_apply_outbound_proxy,
    build_monogram_svg,
    resolve_client_key,
)
from opentradeledger.api.routes_exchanges import register_exchange_routes
from opentradeledger.api.schemas import (
    AiModelsRequest,
    AiReviewRequest,
    AuthChangePasswordRequest,
    AuthDisableRequest,
    AuthLoginRequest,
    AuthSetupRequest,
    DemoToggleRequest,
    ExchangeUpsertRequest,
    JournalUpdateRequest,
    SyncRequest,
    TestNotifyRequest,
    WatcherAddRequest,
    WatcherConfigRequest,
    WatcherPatchRequest,
    WatcherTargetsRequest,
)
from opentradeledger.config import NotificationSettings
from opentradeledger.core.account_partition import parse_adapter_id
from opentradeledger.core.ai_reviewer import (
    _normalize_ai_base,
    generate_ai_review,
    is_local_ai_host,
    list_ai_models,
    validate_ai_base_url,
)
from opentradeledger.core.copy_watcher import (
    CopyWatcherError,
    CopyWatcherService,
    humanize_watcher_error,
)
from opentradeledger.core.fx_rates import FxRateService
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.logo_cache import InstrumentLogos
from opentradeledger.core.notifier import bark_configured, parse_bark_input
from opentradeledger.core.self_updater import SelfUpdateError, SelfUpdateService
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.sync_manager import SyncManager
from opentradeledger.core.venue_health import VenueHealthTracker, humanize_exchange_error
from opentradeledger.core.version_checker import VersionCheckService
from opentradeledger.security.local_auth import (
    AUTH_COOKIE_NAME,
    AuthRateLimitError,
    AuthValidationError,
    LocalAuthManager,
)
from opentradeledger.security.vault import (
    CredentialEditConflict,
    CredentialVault,
    ExchangeCredentialRecord,
)


def create_app(
    *,
    data_dir: Path | str = "./data",
    demo_mode: bool = False,
    web_dist_dir: Path | None = None,
    registry: AdapterRegistry | None = None,
    enable_background_sync: bool = True,
    poll_interval_sec: float = 20.0,
    require_auth: bool | None = None,
    auto_prompt_setup: bool = True,
) -> FastAPI:
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)

    vault = CredentialVault(data_path)
    # Non-destructively import local data/.env read-only keys if present
    dotenv_easicoin = None
    for candidate in vault.dotenv_candidates(data_path / ".env"):
        if candidate.venue_id == "easicoin":
            dotenv_easicoin = candidate
        else:
            vault.upsert(candidate)

    store = LedgerStore(data_path)
    _import_dotenv_watcher_targets_once(data_path, store)
    try:
        _validate_and_apply_outbound_proxy(store.get_preferences().outbound_proxy_url)
    except ValueError:
        pass
    health_tracker = VenueHealthTracker(store)
    auth_manager = LocalAuthManager(
        data_path,
        require_auth=require_auth,
        auto_prompt_setup=auto_prompt_setup,
    )
    reg = registry or build_default_registry()
    ledger_service = LedgerService(store, vault, reg, default_demo_mode=demo_mode)
    sync_manager = SyncManager(
        store=store,
        vault=vault,
        registry=reg,
        ledger_service=ledger_service,
        poll_interval_sec=poll_interval_sec,
        health_tracker=health_tracker,
    )
    copy_watcher = CopyWatcherService(store=store)
    logo_cache = InstrumentLogos(data_path / "instrument_logos")
    fx_service = FxRateService(store)
    version_service = VersionCheckService(store, current_version=__version__)
    base_img_ver = os.environ.get("OTL_BASE_IMAGE_VERSION", __version__)
    self_updater = SelfUpdateService(
        data_dir=data_path,
        version_service=version_service,
        base_version=base_img_ver,
    )

    async def import_easicoin(candidate: ExchangeCredentialRecord) -> None:
        try:
            await sync_manager.sync_candidate_and_commit(candidate, expected_revision="absent")
        except CredentialEditConflict:
            pass  # A newer explicit save won the race.
        except Exception as exc:
            reason = humanize_exchange_error("easicoin", exc)["reason"]
            logging.getLogger("opentradeledger.sync").warning("EasiCoin .env import deferred: %s", reason)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        import_task = asyncio.create_task(import_easicoin(dotenv_easicoin)) if dotenv_easicoin else None
        if enable_background_sync:
            await sync_manager.start()
            await copy_watcher.start()
        try:
            yield
        finally:
            if import_task:
                import_task.cancel()
                with suppress(asyncio.CancelledError):
                    await import_task
            await copy_watcher.stop()
            await sync_manager.stop()

    app = FastAPI(
        title=f"{__app_name__} ({__app_name_zh__})",
        version=__version__,
        description="Local-first, strictly read-only multi-exchange trade ledger.",
        lifespan=lifespan,
    )
    app.state.version_service = version_service
    app.state.self_updater = self_updater

    @app.middleware("http")
    async def local_auth_gate(request: Request, call_next):
        path = request.url.path
        if (
            not path.startswith("/api/v1/")
            or path == "/api/v1/health"
            or path.startswith("/api/v1/auth/")
        ):
            return await call_next(request)

        token = request.cookies.get(AUTH_COOKIE_NAME)
        st = auth_manager.get_status(token, demo_mode=bool(ledger_service.demo_mode))
        if not st["authenticated"] or st["require_setup"]:
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Authentication required",
                    "auth_required": True,
                    "require_setup": bool(st["require_setup"]),
                    "can_skip": bool(st["can_skip"]),
                },
            )
        return await call_next(request)

    @app.get("/api/v1/health")
    async def health(request: Request) -> dict[str, Any]:
        summary = {
            "status": "ok",
            "app": __app_name__,
            "app_zh": __app_name_zh__,
            "version": __version__,
            "base_version": os.environ.get("OTL_BASE_IMAGE_VERSION", __version__),
            "ota_active": os.environ.get("OTL_OTA_ACTIVE") == "1",
            "read_only": True,
            "trading_enabled": False,
        }
        if auth_manager.verify_session_token(request.cookies.get(AUTH_COOKIE_NAME)):
            summary.update(
                demo_mode=ledger_service.is_demo_active(),
                has_live_data=ledger_service.has_live_data(),
                adaptive_polling=sync_manager.get_schedule_summary(),
            )
        return summary

    # ── Local NAS Password Authentication Endpoints ──────────────────────────

    def _session_status(token: str | None) -> dict[str, Any]:
        status = auth_manager.get_status(token, demo_mode=bool(ledger_service.demo_mode))
        if status["authenticated"]:
            status["explicit_demo_mode"] = bool(ledger_service.demo_mode)
        return status

    @app.get("/api/v1/auth/status")
    async def auth_status(request: Request) -> dict[str, Any]:
        return _session_status(request.cookies.get(AUTH_COOKIE_NAME))

    @app.post("/api/v1/auth/setup")
    async def auth_setup(body: AuthSetupRequest, request: Request) -> Response:
        try:
            auth_manager.setup_password(body.password)
        except AuthValidationError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})
        token, ttl = auth_manager.issue_session_token(remember=body.remember)
        st = _session_status(token)
        resp = JSONResponse(content={"ok": True, **st})
        _set_session_cookie(resp, token, ttl, body.remember, request=request)
        return resp

    @app.post("/api/v1/auth/skip")
    async def auth_skip() -> Response:
        try:
            auth_manager.skip_setup()
        except AuthValidationError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})
        st = _session_status(None)
        return JSONResponse(content={"ok": True, **st})

    @app.post("/api/v1/auth/login")
    async def auth_login(body: AuthLoginRequest, request: Request) -> Response:
        try:
            auth_manager.authenticate(body.password, client_key=resolve_client_key(request))
        except AuthRateLimitError as exc:
            return JSONResponse(
                status_code=429,
                content={"detail": str(exc), "retry_after_sec": exc.retry_after_sec},
            )
        except AuthValidationError as exc:
            return JSONResponse(status_code=401, content={"detail": str(exc)})

        token, ttl = auth_manager.issue_session_token(remember=body.remember)
        st = _session_status(token)
        resp = JSONResponse(content={"ok": True, **st})
        _set_session_cookie(resp, token, ttl, body.remember, request=request)
        return resp

    @app.post("/api/v1/auth/logout")
    async def auth_logout() -> Response:
        st = _session_status(None)
        resp = JSONResponse(content={"ok": True, **st})
        resp.delete_cookie(key=AUTH_COOKIE_NAME, path="/")
        return resp

    @app.post("/api/v1/auth/change-password")
    async def auth_change_password(body: AuthChangePasswordRequest, request: Request) -> Response:
        cur_tok = request.cookies.get(AUTH_COOKIE_NAME)
        if not auth_manager.is_request_allowed(cur_tok, demo_mode=bool(ledger_service.demo_mode)):
            return JSONResponse(status_code=401, content={"detail": "Authentication required", "auth_required": True})
        try:
            auth_manager.change_password(
                body.current_password,
                body.new_password,
                client_key=resolve_client_key(request),
            )
        except AuthRateLimitError as exc:
            return JSONResponse(
                status_code=429,
                content={"detail": str(exc), "retry_after_sec": exc.retry_after_sec},
            )
        except AuthValidationError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        token, ttl = auth_manager.issue_session_token(remember=body.remember)
        st = _session_status(token)
        resp = JSONResponse(content={"ok": True, **st})
        _set_session_cookie(resp, token, ttl, body.remember, request=request)
        return resp

    @app.post("/api/v1/auth/disable")
    async def auth_disable_password(body: AuthDisableRequest, request: Request) -> Response:
        cur_tok = request.cookies.get(AUTH_COOKIE_NAME)
        if not auth_manager.is_request_allowed(cur_tok, demo_mode=bool(ledger_service.demo_mode)):
            return JSONResponse(status_code=401, content={"detail": "Authentication required", "auth_required": True})
        try:
            auth_manager.disable_password(body.current_password, client_key=resolve_client_key(request))
        except AuthRateLimitError as exc:
            return JSONResponse(
                status_code=429,
                content={"detail": str(exc), "retry_after_sec": exc.retry_after_sec},
            )
        except AuthValidationError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        st = _session_status(None)
        resp = JSONResponse(content={"ok": True, **st})
        resp.delete_cookie(key=AUTH_COOKIE_NAME, path="/")
        return resp

    # ── Core Ledger Endpoints ─────────────────────────────────────────────────

    @app.get("/api/v1/dashboard")
    async def get_dashboard(
        range_key: str = Query(default="all", alias="range"),
        venue: str = Query(default="ALL"),
        symbol: str = Query(default="ALL"),
    ) -> dict[str, Any]:
        return ledger_service.get_dashboard(
            range_key=range_key,
            venue_filter=venue,
            symbol_filter=symbol,
        )

    @app.get("/api/v1/fx-rates")
    async def get_fx_rates(refresh: bool = Query(default=False)) -> dict[str, Any]:
        return await fx_service.get_rates(force_refresh=refresh)

    @app.get("/api/v1/version-check")
    async def get_version_check(refresh: bool = Query(default=False)) -> dict[str, Any]:
        prefs = ledger_service.get_preferences()
        return await app.state.version_service.check(
            force_refresh=refresh,
            check_enabled=bool(prefs.update_check_enabled),
        )

    @app.post("/api/v1/system/self-update")
    async def trigger_self_update(request: Request) -> Response:
        # Even if demo_mode is currently toggled on, require valid auth if a local password was set
        token = request.cookies.get(AUTH_COOKIE_NAME)
        strict_auth = auth_manager.get_status(token, demo_mode=False)
        if strict_auth.get("enabled") and not strict_auth.get("authenticated"):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required", "auth_required": True},
            )
        try:
            res = await app.state.self_updater.perform_update()
            return JSONResponse(content=res)
        except SelfUpdateError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": exc.message_en,
                    "detail_zh": exc.message_zh,
                },
            )

    commit_candidate = register_exchange_routes(
        app=app,
        vault=vault,
        store=store,
        reg=reg,
        ledger_service=ledger_service,
        sync_manager=sync_manager,
        health_tracker=health_tracker,
        auth_manager=auth_manager,
    )

    @app.post("/api/v1/sync")
    async def trigger_sync(request: Request, body: SyncRequest | None = None) -> Response:
        if body and body.candidate_credential is not None:
            return await commit_candidate(body.candidate_credential, request, finish_onboarding=True)
        req_venue = body.venue_id if body else None
        if ledger_service.is_demo_active() and not req_venue:
            return JSONResponse(content={"ok": True, "demo_mode": True, "venues": {}})
        res = await sync_manager.sync_all(
            force_all=body.force_all if body else True,
            venue_id=req_venue,
        )
        return JSONResponse(content=res)

    @app.put("/api/v1/journal/{trade_id}")
    async def save_journal(trade_id: str, body: JournalUpdateRequest) -> dict[str, Any]:
        if body.demo_mode is not None and body.demo_mode != ledger_service.is_demo_active():
            raise HTTPException(status_code=409, detail="Ledger mode changed. Refresh this page.")
        ledger_service.save_journal(trade_id, body.tags, body.note)
        return {"ok": True, "trade_id": trade_id}

    @app.get("/api/v1/settings")
    async def get_settings() -> dict[str, Any]:
        prefs = ledger_service.get_preferences().model_dump()
        # Never leak raw secrets over GET settings
        prefs["ai_api_key_set"] = bool(prefs.get("ai_api_key"))
        prefs["ai_api_key"] = ""
        notifs = prefs.get("notifications") or {}
        notifs["bark_push_key_set"] = bool(notifs.get("bark_push_key"))
        notifs["bark_push_key"] = ""
        prefs["notifications"] = notifs
        return prefs

    def _apply_settings_patch(patch: dict[str, Any]) -> None:
        target = patch.get("goal_target_usd")
        if target is not None:
            try:
                amount = float(target)
            except (TypeError, ValueError, OverflowError):
                amount = float("nan")
            if isinstance(target, bool) or not math.isfinite(amount) or amount <= 0:
                raise HTTPException(status_code=422, detail="Target equity must be a positive finite number")
        if "outbound_proxy_url" in patch:
            try:
                patch = {
                    **patch,
                    "outbound_proxy_url": _validate_and_apply_outbound_proxy(str(patch.get("outbound_proxy_url") or "")),
                }
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        clear_ai_key = (patch.get("clear_ai_api_key") is True) or (
            str(patch.get("ai_api_key") or "").strip() == "__CLEAR__"
        )
        patch = {k: v for k, v in patch.items() if k != "clear_ai_api_key"}
        if clear_ai_key:
            patch = {**patch, "ai_api_key": ""}
        elif "ai_api_key" in patch and not str(patch["ai_api_key"] or "").strip():
            patch = {k: v for k, v in patch.items() if k != "ai_api_key"}
        ledger_service.update_preferences(patch)

    # A slower receipt must not resurrect a device after a newer edit or clear.
    bark_edit_generation = 0

    async def _save_settings(
        patch: dict[str, Any], *, test_ring: bool = False, portfolio_id: str = ""
    ) -> Response:
        nonlocal bark_edit_generation
        patch = dict(patch)
        changes = patch.pop("notifications", {})
        clear = patch.pop("clear_bark_push_key", False) is True
        if not isinstance(changes, dict):
            raise HTTPException(status_code=422, detail="Invalid notification settings")
        clear = clear or changes.get("clear_bark_push_key") is True or changes.get("bark_push_key") == "__CLEAR__"
        has_edit = clear or "bark_push_key" in changes or "bark_server_url" in changes
        current = ledger_service.get_preferences().notifications
        candidate = current
        if has_edit:
            raw_server = str(changes.get("bark_server_url", current.bark_server_url) or "")
            server, key = parse_bark_input(raw_server, str(changes.get("bark_push_key") or ""))
            if clear:
                key = ""
            elif not key:
                if server != current.bark_server_url and current.bark_push_key:
                    return JSONResponse(status_code=400, content={
                        "detail": "Enter a device key for the new Bark server",
                        "detail_zh": "更换 Bark 服务器时，请重新填写该服务器的设备 Key 或完整链接",
                    })
                key = current.bark_push_key
            candidate = NotificationSettings(bark_server_url=server, bark_push_key=key)
            bark_edit_generation += 1

        generation = bark_edit_generation
        receipt = None
        if test_ring or (candidate != current and bark_configured(candidate)):
            if not bark_configured(candidate):
                return JSONResponse(status_code=400, content={
                    "detail": "Configure a Bark device URL or key first",
                    "detail_zh": "尚未配置 Bark，请先填写设备链接或 Key，再测试推送",
                })
            receipt = await copy_watcher.send_test_ring(portfolio_id if candidate == current else "", notifications=candidate)
            if "bark" not in receipt["sent"]:
                errors = receipt.get("errors") or {"bark": "No delivery receipt"}
                err_summary = "; ".join(f"{ch}: {msg}" for ch, msg in errors.items())
                return JSONResponse(status_code=502, content={
                    "detail": f"Notification delivery failed — {err_summary}",
                    "detail_zh": f"推送测试发送失败 — {err_summary}",
                    "errors": errors,
                })
            if generation != bark_edit_generation:
                return JSONResponse(status_code=409, content={
                    "detail": "Bark settings changed while testing. Reload the saved device.",
                    "detail_zh": "测试期间已有新的 Bark 配置操作，请刷新查看当前设备。",
                })

        if has_edit and current != candidate:
            patch["notifications"] = candidate.model_dump()
        if patch:
            _apply_settings_patch(patch)
        settings = await get_settings()
        return JSONResponse(content={**receipt, "settings": settings} if test_ring else settings)

    @app.put("/api/v1/settings")
    async def update_settings(patch: dict[str, Any]) -> Response:
        return await _save_settings(patch)

    @app.post("/api/v1/demo")
    async def set_demo_mode(body: DemoToggleRequest) -> dict[str, Any]:
        ledger_service.set_demo_mode(body.enabled)
        return {
            "ok": True,
            "demo_mode": ledger_service.is_demo_active(),
            "explicit_demo_mode": bool(ledger_service.demo_mode),
        }

    @app.post("/api/v1/ai-review")
    async def run_ai_review(body: AiReviewRequest | None = None) -> dict[str, Any]:
        rk = body.range_key if body else "all"
        mode = (body.mode if body else "auto").strip().lower()
        prefs = ledger_service.get_preferences()
        dash = ledger_service.get_dashboard(range_key=rk)
        use_demo = bool(dash["demo_mode"])
        if body and body.demo_mode is not None and body.demo_mode != use_demo:
            raise HTTPException(status_code=409, detail="Ledger mode changed. Refresh this page.")
        review = await generate_ai_review(dash, prefs, force_local=(mode == "local_rules"))
        store.save_ai_review(review, float(dash["summary"]["total_equity_usd"]), demo_mode=use_demo)
        return review

    @app.post("/api/v1/ai-models")
    async def fetch_ai_models(body: AiModelsRequest | None = None) -> dict[str, Any]:
        prefs = ledger_service.get_preferences()
        req_base_raw = body.base_url.strip() if body and body.base_url.strip() else ""
        req_key_raw = body.api_key.strip() if body and body.api_key.strip() else ""
        saved_base_norm = _normalize_ai_base(prefs.ai_base_url)

        raw_target_url = req_base_raw or prefs.ai_base_url
        if not raw_target_url.strip():
            raise HTTPException(status_code=400, detail="Please enter an AI Base URL first")
        try:
            base_url = validate_ai_base_url(raw_target_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Never forward a stored API key to an unverified new host
        if req_key_raw:
            api_key = req_key_raw
        elif base_url == saved_base_norm:
            api_key = prefs.ai_api_key
        elif is_local_ai_host(base_url):
            api_key = ""
        else:
            raise HTTPException(
                status_code=400,
                detail="Please enter an API Key when testing a different AI Base URL",
            )

        try:
            models = await list_ai_models(base_url, api_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "models": models, "count": len(models)}

    @app.get("/api/v1/extensions/copy-watcher")
    async def get_copy_watcher(refresh: bool = Query(default=False)) -> dict[str, Any]:
        if bool(ledger_service.demo_mode):
            return copy_watcher.get_status(demo_mode=True)
        if refresh:
            return await copy_watcher.poll_once(force_trades=True)
        st = copy_watcher.get_status(demo_mode=False)
        # Auto-probe once on first read if legacy imported targets have no snapshot yet
        if enable_background_sync and st["items"] and all(it["status"] == "PENDING" for it in st["items"]):
            return await copy_watcher.poll_once(force_trades=True)
        return st

    @app.post("/api/v1/extensions/copy-watcher/targets")
    async def add_copy_watcher_targets(body: WatcherAddRequest) -> Response:
        if bool(ledger_service.demo_mode):
            ledger_service.set_demo_mode(False)
        try:
            await copy_watcher.add_targets(
                body.raw_input,
                slot_alert=body.slot_alert,
                trade_alert=body.trade_alert,
            )
            return JSONResponse(content={"ok": True, **copy_watcher.get_status(demo_mode=False)})
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc), "detail_zh": str(exc)})
        except CopyWatcherError as exc:
            en_err, zh_err = humanize_watcher_error(exc)
            return JSONResponse(status_code=502, content={"detail": en_err, "detail_zh": zh_err})

    @app.put("/api/v1/extensions/copy-watcher")
    async def update_copy_watcher(body: WatcherTargetsRequest) -> dict[str, Any]:
        cleaned = copy_watcher.set_target_ids_bulk(body.targets)
        return {"ok": True, "configured_targets": cleaned, **copy_watcher.get_status(demo_mode=False)}

    @app.put("/api/v1/extensions/copy-watcher/config")
    async def update_copy_watcher_config(body: WatcherConfigRequest) -> Response:
        if not (3 <= body.interval_sec <= 300):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Slot scan interval must be between 3 and 300 seconds (min 3s prevents Binance IP rate-limit bans)",
                    "detail_zh": "名额扫描间隔须在 3 ~ 300 秒之间（最低 3 秒以防止触发币安高频访问封禁 IP）",
                },
            )
        if not (10 <= body.trade_interval_sec <= 600):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Position inference interval must be between 10 and 600 seconds",
                    "detail_zh": "持仓推算间隔须在 10 ~ 600 秒之间",
                },
            )
        if not (30 <= body.cooldown_sec <= 3600):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Slot alarm repeat cooldown must be between 30 and 3600 seconds",
                    "detail_zh": "空位重复响铃冷却时间须在 30 ~ 3600 秒之间",
                },
            )
        ledger_service.update_preferences(
            {
                "watcher_interval_sec": int(body.interval_sec),
                "watcher_trade_interval_sec": int(body.trade_interval_sec),
                "watcher_cooldown_sec": int(body.cooldown_sec),
            }
        )
        copy_watcher.notify_config_changed()
        return JSONResponse(content={"ok": True, **copy_watcher.get_status(demo_mode=bool(ledger_service.demo_mode))})

    @app.patch("/api/v1/extensions/copy-watcher/targets/{portfolio_id}")
    async def patch_copy_watcher_target(portfolio_id: str, body: WatcherPatchRequest) -> dict[str, Any]:
        try:
            copy_watcher.update_target(
                portfolio_id,
                enabled=body.enabled,
                slot_alert=body.slot_alert,
                trade_alert=body.trade_alert,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, **copy_watcher.get_status(demo_mode=False)}

    @app.delete("/api/v1/extensions/copy-watcher/targets/{portfolio_id}")
    async def delete_copy_watcher_target(portfolio_id: str) -> dict[str, Any]:
        if not copy_watcher.remove_target(portfolio_id):
            raise HTTPException(status_code=404, detail="Watched portfolio not found")
        return {"ok": True, **copy_watcher.get_status(demo_mode=False)}

    @app.post("/api/v1/extensions/copy-watcher/test-notify")
    async def test_copy_watcher_notify(body: TestNotifyRequest | None = None) -> Response:
        patch = {"notifications": body.notifications} if body and body.notifications is not None else {}
        return await _save_settings(patch, test_ring=True, portfolio_id=body.portfolio_id if body else "")

    @app.get("/api/v1/instrument-logos/{venue}/{instrument}.png")
    async def get_instrument_logo(venue: str, instrument: str):
        path = await logo_cache.find(parse_adapter_id(venue).upper(), instrument.upper())
        if path is not None and path.exists():
            return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
        # Clean deterministic monogram SVG fallback (zero 404 console noise for new or offline symbols)
        svg = build_monogram_svg(instrument)
        return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=300"})

    # Mount static frontend build when present (respecting OTA overlay override if active)
    env_dist = os.environ.get("OTL_WEB_DIST_DIR", "").strip()
    dist = web_dist_dir or (Path(env_dist) if env_dist else (Path(__file__).resolve().parents[4] / "web" / "dist"))
    if dist.exists() and (dist / "index.html").exists():
        assets_dir = dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not found")
            dist_root = dist.resolve()
            if full_path:
                candidate = (dist_root / full_path).resolve()
                if candidate.is_relative_to(dist_root) and candidate.is_file():
                    return FileResponse(candidate)
            return FileResponse(
                dist_root / "index.html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

    return app
