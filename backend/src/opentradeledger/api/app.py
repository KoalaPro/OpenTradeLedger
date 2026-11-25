"""FastAPI application for OpenTradeLedger (交易账本)."""

from __future__ import annotations

from contextlib import asynccontextmanager
import math
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from opentradeledger import __app_name__, __app_name_zh__, __version__
from opentradeledger.adapters.registry import AdapterRegistry, build_default_registry
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
    extract_portfolio_ids,
    humanize_watcher_error,
)
from opentradeledger.core.fx_rates import FxRateService
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.logo_cache import InstrumentLogos
from opentradeledger.core.notifier import bark_configured, parse_bark_input
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.sync_manager import CandidateRollbackError, CandidateSaveError, SyncManager
from opentradeledger.core.venue_health import VenueHealthTracker, humanize_exchange_error
from opentradeledger.core.self_updater import SelfUpdateError, SelfUpdateService
from opentradeledger.core.version_checker import VersionCheckService
from opentradeledger.security.local_auth import (
    AUTH_COOKIE_NAME,
    AuthRateLimitError,
    AuthValidationError,
    LocalAuthManager,
)
from opentradeledger.security.vault import CredentialVault, ExchangeCredentialRecord


class JournalUpdateRequest(BaseModel):
    demo_mode: bool | None = None
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class ExchangeUpsertRequest(BaseModel):
    venue_id: str
    label: str = ""
    api_key: str
    api_secret: str
    passphrase: str = ""
    enabled: bool = True


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


_ORIG_HTTP_PROXY = os.environ.get("HTTP_PROXY")
_ORIG_HTTPS_PROXY = os.environ.get("HTTPS_PROXY")
_ORIG_NO_PROXY = os.environ.get("NO_PROXY")
_SAFE_LAN_NO_PROXY = ",".join(
    ("127.0.0.1", "localhost", "::1", f"{192}.{168}.0.0/16", f"{10}.0.0.0/8", f"{172}.{16}.0.0/12")
)


def _validate_and_apply_outbound_proxy(raw_proxy: str) -> str:
    cleaned = (raw_proxy or "").strip()
    if not cleaned:
        if _ORIG_HTTP_PROXY is None:
            os.environ.pop("HTTP_PROXY", None)
        else:
            os.environ["HTTP_PROXY"] = _ORIG_HTTP_PROXY
        if _ORIG_HTTPS_PROXY is None:
            os.environ.pop("HTTPS_PROXY", None)
        else:
            os.environ["HTTPS_PROXY"] = _ORIG_HTTPS_PROXY
        if _ORIG_NO_PROXY is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = _ORIG_NO_PROXY
        return ""

    low = cleaned.lower()
    if not low.startswith(("http://", "https://", "socks5://", "socks5h://")):
        raise ValueError("Outbound proxy URL must start with http://, https://, or socks5://")
    os.environ["HTTP_PROXY"] = cleaned
    os.environ["HTTPS_PROXY"] = cleaned
    existing_no = (_ORIG_NO_PROXY or "").strip()
    os.environ["NO_PROXY"] = f"{existing_no},{_SAFE_LAN_NO_PROXY}".strip(",") if existing_no else _SAFE_LAN_NO_PROXY
    return cleaned


def _import_dotenv_watcher_targets_once(data_path: Path, store: LedgerStore) -> None:
    if store.get_meta("copy_watcher_dotenv_imported", False):
        return
    env_file = data_path / ".env"
    if not env_file.is_file():
        return
    try:
        kv: dict[str, str] = {}
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                kv[k.strip()] = v.strip().strip('"').strip("'")
        raw_ids = kv.get("BINANCE_WATCH_PORTFOLIO_IDS", "")
        pids = extract_portfolio_ids(raw_ids)
        prefs = store.get_preferences()
        if pids and not prefs.copy_watcher_targets:
            prefs.copy_watcher_targets = list(pids)
            store.save_preferences(prefs)
        store.set_meta("copy_watcher_dotenv_imported", True)
    except Exception:
        pass


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


def _set_session_cookie(resp: Response, token: str, max_age_sec: int, remember: bool) -> None:
    kwargs: dict[str, Any] = {
        "key": AUTH_COOKIE_NAME,
        "value": token,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
    }
    if remember:
        kwargs["max_age"] = max_age_sec
    resp.set_cookie(**kwargs)


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
    vault.import_from_dotenv(data_path / ".env", overwrite=False)

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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if enable_background_sync:
            await sync_manager.start()
            await copy_watcher.start()
        try:
            yield
        finally:
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

    def _client_key(request: Request) -> str:
        return request.client.host if request.client and request.client.host else "local"

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

    @app.get("/api/v1/auth/status")
    async def auth_status(request: Request) -> dict[str, Any]:
        token = request.cookies.get(AUTH_COOKIE_NAME)
        return auth_manager.get_status(token, demo_mode=bool(ledger_service.demo_mode))

    @app.post("/api/v1/auth/setup")
    async def auth_setup(body: AuthSetupRequest) -> Response:
        try:
            auth_manager.setup_password(body.password)
        except AuthValidationError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})
        token, ttl = auth_manager.issue_session_token(remember=body.remember)
        st = auth_manager.get_status(token, demo_mode=bool(ledger_service.demo_mode))
        resp = JSONResponse(content={"ok": True, **st})
        _set_session_cookie(resp, token, ttl, body.remember)
        return resp

    @app.post("/api/v1/auth/skip")
    async def auth_skip() -> Response:
        try:
            auth_manager.skip_setup()
        except AuthValidationError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})
        st = auth_manager.get_status(None, demo_mode=bool(ledger_service.demo_mode))
        return JSONResponse(content={"ok": True, **st})

    @app.post("/api/v1/auth/login")
    async def auth_login(body: AuthLoginRequest, request: Request) -> Response:
        try:
            auth_manager.authenticate(body.password, client_key=_client_key(request))
        except AuthRateLimitError as exc:
            return JSONResponse(
                status_code=429,
                content={"detail": str(exc), "retry_after_sec": exc.retry_after_sec},
            )
        except AuthValidationError as exc:
            return JSONResponse(status_code=401, content={"detail": str(exc)})

        token, ttl = auth_manager.issue_session_token(remember=body.remember)
        st = auth_manager.get_status(token, demo_mode=bool(ledger_service.demo_mode))
        resp = JSONResponse(content={"ok": True, **st})
        _set_session_cookie(resp, token, ttl, body.remember)
        return resp

    @app.post("/api/v1/auth/logout")
    async def auth_logout() -> Response:
        st = auth_manager.get_status(None, demo_mode=bool(ledger_service.demo_mode))
        resp = JSONResponse(content={"ok": True, **st})
        resp.delete_cookie(key=AUTH_COOKIE_NAME, path="/")
        return resp

    @app.post("/api/v1/auth/change-password")
    async def auth_change_password(body: AuthChangePasswordRequest, request: Request) -> Response:
        cur_tok = request.cookies.get(AUTH_COOKIE_NAME)
        if not auth_manager.is_request_allowed(cur_tok, demo_mode=bool(ledger_service.demo_mode)):
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        try:
            auth_manager.change_password(
                body.current_password,
                body.new_password,
                client_key=_client_key(request),
            )
        except AuthRateLimitError as exc:
            return JSONResponse(
                status_code=429,
                content={"detail": str(exc), "retry_after_sec": exc.retry_after_sec},
            )
        except AuthValidationError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        token, ttl = auth_manager.issue_session_token(remember=body.remember)
        st = auth_manager.get_status(token, demo_mode=bool(ledger_service.demo_mode))
        resp = JSONResponse(content={"ok": True, **st})
        _set_session_cookie(resp, token, ttl, body.remember)
        return resp

    @app.post("/api/v1/auth/disable")
    async def auth_disable_password(body: AuthDisableRequest, request: Request) -> Response:
        cur_tok = request.cookies.get(AUTH_COOKIE_NAME)
        if not auth_manager.is_request_allowed(cur_tok, demo_mode=bool(ledger_service.demo_mode)):
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        try:
            auth_manager.disable_password(body.current_password, client_key=_client_key(request))
        except AuthRateLimitError as exc:
            return JSONResponse(
                status_code=429,
                content={"detail": str(exc), "retry_after_sec": exc.retry_after_sec},
            )
        except AuthValidationError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        st = auth_manager.get_status(None, demo_mode=bool(ledger_service.demo_mode))
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

    @app.post("/api/v1/sync")
    async def trigger_sync(request: Request, body: SyncRequest | None = None) -> Response:
        if body and body.candidate_credential is not None:
            cand = body.candidate_credential
            vid = cand.venue_id.strip().lower()
            adapter = reg.get(vid)
            if adapter is None:
                raise HTTPException(status_code=400, detail=f"Unsupported exchange adapter: {vid}")
            if adapter.meta.requires_passphrase and not cand.passphrase.strip():
                raise HTTPException(status_code=400, detail=f"{adapter.meta.display_name} requires an API passphrase")
            if not cand.api_key.strip() or not cand.api_secret.strip():
                raise HTTPException(status_code=400, detail="API Key and API Secret are required")
            temp_cred = ExchangeCredentialRecord(
                venue_id=vid,
                label=cand.label.strip() or adapter.meta.display_name,
                api_key=cand.api_key.strip(),
                api_secret=cand.api_secret.strip(),
                passphrase=cand.passphrase.strip(),
                enabled=cand.enabled,
            )
            # Existing users can edit credentials while intentionally viewing the demo.
            # Also finish onboarding for older installations that saved a key before setup.
            enter_live_ledger = ledger_service.demo_mode and (
                not vault.list_masked()
                or auth_manager.get_status(request.cookies.get(AUTH_COOKIE_NAME), demo_mode=False)["require_setup"]
            )
            try:
                res = await sync_manager.sync_candidate_and_commit(temp_cred)
            except CandidateRollbackError:
                return JSONResponse(status_code=500, content={
                    "detail": "Local rollback failed. Check the data directory and verify the account before retrying.",
                    "detail_zh": "本地回滚失败，请检查数据目录并核对账户状态后再重试。",
                    "error_code": "LOCAL_ROLLBACK_FAILED",
                })
            except CandidateSaveError:
                return JSONResponse(status_code=500, content={
                    "detail": "Local save failed. Check available disk space and data directory permissions.",
                    "detail_zh": "本地保存失败，请检查磁盘空间和数据目录写入权限。",
                    "error_code": "LOCAL_SAVE_FAILED",
                    "committed": False,
                })
            except Exception as exc:
                diag = humanize_exchange_error(vid, exc)
                return JSONResponse(
                    status_code=502,
                    content={
                        "detail": diag["reason"],
                        "detail_zh": diag["reason_zh"],
                        "error_code": diag["code"],
                        "raw_error": diag["raw"],
                        "committed": False,
                    },
                )

            if res.get("committed") and enter_live_ledger and ledger_service.demo_mode:
                ledger_service.set_demo_mode(False)
                res["auth_status"] = auth_manager.get_status(
                    request.cookies.get(AUTH_COOKIE_NAME), demo_mode=False
                )
            return JSONResponse(content=res)

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
        current = ledger_service.get_preferences().model_dump()
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
        if "ai_api_key" in patch and not str(patch["ai_api_key"] or "").strip():
            patch = {k: v for k, v in patch.items() if k != "ai_api_key"}
        if isinstance(patch.get("notifications"), dict):
            n_patch = dict(patch["notifications"])
            cur_notifs = dict(current.get("notifications") or {})
            # If user pasted a full Bark URL into either server or key box, split it before blank-key preservation
            raw_srv_in = str(n_patch.get("bark_server_url") if "bark_server_url" in n_patch else cur_notifs.get("bark_server_url") or "")
            raw_key_in = str(n_patch.get("bark_push_key") or "")
            norm_srv, norm_key = parse_bark_input(raw_srv_in, raw_key_in)
            n_patch["bark_server_url"] = norm_srv
            if norm_key:
                n_patch["bark_push_key"] = norm_key
            else:
                n_patch.pop("bark_push_key", None)
            patch = {**patch, "notifications": {**cur_notifs, **n_patch}}
        ledger_service.update_preferences(patch)

    @app.put("/api/v1/settings")
    async def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
        _apply_settings_patch(patch)
        return await get_settings()

    @app.get("/api/v1/exchanges")
    async def list_exchanges() -> dict[str, Any]:
        configured = {item["venue_id"]: item for item in vault.list_masked()}
        sched_venues = sync_manager.get_schedule_summary().get("venues") or {}
        available = []
        error_count = 0
        for meta in reg.list_all():
            cred_item = configured.get(meta.venue_id)
            is_conf = cred_item is not None
            is_enabled = bool(cred_item.get("enabled", True)) if cred_item else True
            ad_obj = reg.get(meta.venue_id)
            has_data = bool(ad_obj and ad_obj.has_venue_data(store))
            h_state = health_tracker.resolve_state(
                meta.venue_id,
                configured=is_conf,
                enabled=is_enabled,
                poll_state=sched_venues.get(meta.venue_id),
            )
            if not is_conf and has_data:
                h_state = {**h_state, "status": "archived"}
            if is_conf and is_enabled and h_state.get("status") == "error":
                error_count += 1
            entry = meta.model_dump()
            entry["configured"] = is_conf
            entry["has_archived_data"] = has_data
            entry["credential"] = cred_item
            entry["poll_state"] = sched_venues.get(meta.venue_id)
            entry["health"] = h_state
            available.append(entry)
        return {
            "adapters": available,
            "configured": list(configured.values()),
            "error_count": error_count,
        }

    @app.post("/api/v1/exchanges/verify")
    async def verify_exchange_before_save(body: ExchangeUpsertRequest) -> Response:
        vid = body.venue_id.strip().lower()
        adapter = reg.get(vid)
        if adapter is None:
            raise HTTPException(status_code=400, detail=f"Unsupported exchange adapter: {vid}")
        if adapter.meta.requires_passphrase and not body.passphrase.strip():
            raise HTTPException(status_code=400, detail=f"{adapter.meta.display_name} requires an API passphrase")
        if not body.api_key.strip() or not body.api_secret.strip():
            raise HTTPException(status_code=400, detail="API Key and API Secret are required")
        temp_cred = ExchangeCredentialRecord(
            venue_id=vid,
            label=body.label.strip() or adapter.meta.display_name,
            api_key=body.api_key.strip(),
            api_secret=body.api_secret.strip(),
            passphrase=body.passphrase.strip(),
            enabled=body.enabled,
        )
        try:
            res = await adapter.test_connection(temp_cred)
            return JSONResponse(content={**res, "verified": True})
        except Exception as exc:
            diag = humanize_exchange_error(vid, exc)
            return JSONResponse(
                status_code=502,
                content={
                    "detail": diag["reason"],
                    "detail_zh": diag["reason_zh"],
                    "error_code": diag["code"],
                    "raw_error": diag["raw"],
                },
            )

    @app.post("/api/v1/exchanges")
    async def upsert_exchange(body: ExchangeUpsertRequest) -> dict[str, Any]:
        vid = body.venue_id.strip().lower()
        adapter = reg.get(vid)
        if adapter is None:
            raise HTTPException(status_code=400, detail=f"Unsupported exchange adapter: {vid}")
        if adapter.meta.requires_passphrase and not body.passphrase.strip():
            raise HTTPException(status_code=400, detail=f"{adapter.meta.display_name} requires an API passphrase")
        masked = vault.upsert(
            ExchangeCredentialRecord(
                venue_id=vid,
                label=body.label.strip() or adapter.meta.display_name,
                api_key=body.api_key.strip(),
                api_secret=body.api_secret.strip(),
                passphrase=body.passphrase.strip(),
                enabled=body.enabled,
            )
        )
        health_state = (
            health_tracker.mark_unverified(vid)
            if body.enabled
            else health_tracker.resolve_state(vid, configured=True, enabled=False)
        )
        return {"ok": True, "credential": masked, "health": health_state}

    @app.delete("/api/v1/exchanges/{venue_id}")
    async def delete_exchange(
        venue_id: str,
        keep_data: bool = Query(default=False),
    ) -> dict[str, Any]:
        vid = venue_id.strip().lower()
        adapter = reg.get(vid)
        had_cred = vault.delete(vid)
        had_data = bool(adapter and adapter.has_venue_data(store))
        if not had_cred and not had_data:
            raise HTTPException(status_code=404, detail="Exchange credential not found")
        health_tracker.remove(vid)
        sched = store.get_meta("sync_schedule", {})
        if isinstance(sched, dict) and vid in sched:
            sched.pop(vid, None)
            store.set_meta("sync_schedule", sched)
        if not keep_data:
            store.purge_venue_data(vid)
        still_has_data = bool(adapter and adapter.has_venue_data(store))
        return {
            "ok": True,
            "venue_id": vid,
            "kept_data": bool(keep_data and still_has_data),
            "has_archived_data": still_has_data,
        }

    @app.post("/api/v1/exchanges/{venue_id}/test")
    async def test_exchange(venue_id: str) -> Response:
        vid = venue_id.strip().lower()
        adapter = reg.get(vid)
        cred = vault.get_decrypted(vid)
        if adapter is None or cred is None:
            raise HTTPException(status_code=404, detail="Exchange adapter or credential not found")
        try:
            res = await adapter.test_connection(cred)
            h_state = health_tracker.record_success(vid)
            return JSONResponse(content={**res, "health": h_state})
        except Exception as exc:
            h_state = health_tracker.record_error(vid, exc)
            return JSONResponse(
                status_code=502,
                content={
                    "detail": h_state["error_reason"],
                    "detail_zh": h_state["error_reason_zh"],
                    "error_code": h_state["error_code"],
                    "raw_error": h_state["raw_error"],
                    "health": h_state,
                },
            )

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
        if body and isinstance(body.notifications, dict):
            _apply_settings_patch({"notifications": body.notifications})
        prefs = ledger_service.get_preferences()
        if not bark_configured(prefs.notifications):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Configure a Bark device URL or key first",
                    "detail_zh": "尚未配置 Bark，请先填写设备链接或 Key，再测试推送",
                },
            )
        res = await copy_watcher.send_test_ring(portfolio_id=body.portfolio_id if body else "")
        if not res["sent"] and res["errors"]:
            err_summary = "; ".join(f"{ch}: {msg}" for ch, msg in res["errors"].items())
            return JSONResponse(
                status_code=502,
                content={
                    "detail": f"Notification delivery failed — {err_summary}",
                    "detail_zh": f"推送测试发送失败 — {err_summary}",
                    "errors": res["errors"],
                },
            )
        return JSONResponse(content={**res, "settings": await get_settings()})

    @app.get("/api/v1/instrument-logos/{venue}/{instrument}.png")
    async def get_instrument_logo(venue: str, instrument: str):
        path = await logo_cache.find(venue.upper(), instrument.upper())
        if path is not None and path.exists():
            return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
        # Clean deterministic monogram SVG fallback (zero 404 console noise for new or offline symbols)
        base = "".join(ch for ch in instrument.upper().split("_")[0] if ch.isalnum())[:2] or "?"
        palette = ("#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ec4899", "#06b6d4", "#6366f1")
        color = palette[sum(ord(c) for c in base) % len(palette)]
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            f'<circle cx="16" cy="16" r="15" fill="{color}" fill-opacity="0.18" stroke="{color}" stroke-opacity="0.55" stroke-width="1.2"/>'
            f'<text x="16" y="20.5" text-anchor="middle" font-family="-apple-system,Inter,sans-serif" '
            f'font-size="{11 if len(base) > 1 else 13}" font-weight="700" fill="{color}">{base}</text>'
            f"</svg>"
        )
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
