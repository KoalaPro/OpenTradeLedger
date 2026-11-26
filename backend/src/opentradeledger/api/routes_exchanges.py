"""Multi-account exchange onboarding, verification, listing, archival, and health-probe routes."""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from opentradeledger.adapters.easicoin.identity import EasiCoinIdentityError
from opentradeledger.adapters.registry import AdapterRegistry
from opentradeledger.api.schemas import ExchangeUpsertRequest
from opentradeledger.core.account_partition import (
    AccountIdentityError,
    deterministic_uid_suffix,
    is_secondary_account_id,
    mask_uid,
    mint_account_id,
    normalize_evm_address,
    parse_adapter_id,
)
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.sync_manager import CandidateRollbackError, CandidateSaveError, SyncManager
from opentradeledger.core.venue_health import (
    IdentityProbeTransientError,
    VenueHealthTracker,
    humanize_exchange_error,
)
from opentradeledger.security.local_auth import AUTH_COOKIE_NAME, LocalAuthManager
from opentradeledger.security.vault import (
    CredentialEditConflict,
    CredentialVault,
    ExchangeCredentialRecord,
)


class CommitCandidateFn(Protocol):
    async def __call__(
        self,
        cand: ExchangeUpsertRequest,
        request: Request,
        *,
        finish_onboarding: bool,
    ) -> Response: ...


def register_exchange_routes(
    *,
    app: FastAPI,
    vault: CredentialVault,
    store: LedgerStore,
    reg: AdapterRegistry,
    ledger_service: LedgerService,
    sync_manager: SyncManager,
    health_tracker: VenueHealthTracker,
    auth_manager: LocalAuthManager,
) -> CommitCandidateFn:
    @app.exception_handler(CredentialEditConflict)
    async def credential_conflict(_request: Request, _exc: CredentialEditConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={
            "detail": "This account configuration has changed. Refresh the page and reopen the form before saving.",
            "detail_zh": "这份账户配置已被更新，请刷新页面，重新打开表单后再保存。",
            "error_code": "CREDENTIAL_EDIT_CONFLICT",
            "committed": False,
        })

    def _occupied_account_ids(adapter_id: str, adapter: Any) -> set[str]:
        occupied = {c.venue_id.lower() for c in vault.list_for_adapter(adapter_id)}
        occupied.update(store.list_partition_account_ids(adapter_id))
        if adapter.has_venue_data(store.for_account(adapter_id, adapter_id)):
            occupied.add(adapter_id)
        return occupied

    def _prepare_candidate(cand: ExchangeUpsertRequest) -> tuple[str, str, Any, ExchangeCredentialRecord]:
        raw_target = (cand.account_id or cand.venue_id).strip().lower()
        adapter_id = parse_adapter_id(raw_target)
        adapter = reg.get(adapter_id)
        if adapter is None:
            raise HTTPException(status_code=400, detail=f"Unsupported exchange adapter: {adapter_id}")

        if cand.account_id.strip():
            acct_id = cand.account_id.strip().lower()
        elif is_secondary_account_id(raw_target):
            acct_id = raw_target
        elif cand.create_new:
            acct_id = mint_account_id(adapter_id, _occupied_account_ids(adapter_id, adapter))
        else:
            acct_id = adapter_id

        if adapter.meta.auth_mode == "wallet_address":
            raw_addr = (cand.wallet_address or cand.api_key).strip()
            norm_addr = normalize_evm_address(raw_addr)
            if not norm_addr:
                err = AccountIdentityError("INVALID_WALLET_ADDRESS")
                raise HTTPException(status_code=400, detail=err.reason_zh)
            eff_key = norm_addr
            eff_sec = ""
            eff_uid = norm_addr
            eff_role = "wallet"
        else:
            if adapter.meta.requires_passphrase and not cand.passphrase.strip():
                raise HTTPException(status_code=400, detail=f"{adapter.meta.display_name} requires an API passphrase")
            if not cand.api_key.strip() or not cand.api_secret.strip():
                raise HTTPException(status_code=400, detail="API Key and API Secret are required")
            eff_key = cand.api_key.strip()
            eff_sec = cand.api_secret.strip()
            eff_uid = ""
            eff_role = ""

        rec = ExchangeCredentialRecord(
            venue_id=acct_id,
            adapter_id=adapter_id,
            label=cand.label.strip() or adapter.meta.display_name,
            api_key=eff_key,
            api_secret=eff_sec,
            passphrase=cand.passphrase.strip(),
            remote_uid=eff_uid,
            account_role=eff_role,
            enabled=cand.enabled,
        )
        return acct_id, adapter_id, adapter, rec

    async def commit_candidate(cand: ExchangeUpsertRequest, request: Request, *, finish_onboarding: bool) -> Response:
        acct_id, adapter_id, adapter, temp_cred = _prepare_candidate(cand)
        eff_expected_rev = "absent" if (cand.create_new and not cand.account_id.strip()) else cand.expected_revision
        # Existing users can edit credentials while intentionally viewing the demo.
        # Also finish onboarding for older installations that saved a key before setup.
        enter_live_ledger = finish_onboarding and ledger_service.demo_mode and (
            not vault.list_masked()
            or auth_manager.get_status(request.cookies.get(AUTH_COOKIE_NAME), demo_mode=False)["require_setup"]
        )
        try:
            if adapter.meta.identity_mode == "probe" and (cand.create_new or is_secondary_account_id(acct_id)):
                await sync_manager.backfill_unattributed_sibling_uids(adapter_id, exclude_acct_id=acct_id)
            if cand.create_new and not cand.account_id.strip():
                disc_uid = temp_cred.remote_uid
                if not disc_uid:
                    info = await adapter.probe_identity(temp_cred)
                    if isinstance(info, dict):
                        if adapter.meta.identity_mode == "probe" and info.get("_transient_error"):
                            raise RuntimeError(str(info["_transient_error"]))
                        disc_uid = str(info.get("remote_uid") or "").strip()
                if adapter.meta.identity_mode == "probe" and not disc_uid:
                    raise AccountIdentityError(
                        "ACCOUNT_UID_UNAVAILABLE",
                        exchange_name=adapter.meta.display_name,
                        exchange_name_zh=adapter.meta.display_name_zh,
                    )
                if disc_uid:
                    det_id = mint_account_id(
                        adapter_id,
                        _occupied_account_ids(adapter_id, adapter),
                        preferred_suffix=deterministic_uid_suffix(adapter_id, disc_uid),
                        force_secondary=True,
                    )
                    acct_id = det_id
                    temp_cred = temp_cred.model_copy(update={"venue_id": acct_id})
            res = await sync_manager.sync_candidate_and_commit(temp_cred, eff_expected_rev)
        except CredentialEditConflict:
            raise
        except (EasiCoinIdentityError, AccountIdentityError) as exc:
            return JSONResponse(status_code=409, content={
                "detail": exc.reason, "detail_zh": exc.reason_zh,
                "error_code": exc.code, "committed": False,
            })
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
            diag = humanize_exchange_error(adapter_id, exc)
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

        # Optional batch import of selected on-chain sub-accounts (e.g. Hyperliquid subAccounts)
        if res.get("committed") and cand.include_subaccounts and adapter.meta.auth_mode == "wallet_address":
            existing_wallet_addrs: set[str] = {temp_cred.api_key.strip().lower()}
            for c_rec in vault.list_for_adapter(adapter_id):
                if c_rec.api_key.strip():
                    existing_wallet_addrs.add(c_rec.api_key.strip().lower())
                if c_rec.remote_uid.strip():
                    existing_wallet_addrs.add(c_rec.remote_uid.strip().lower())
            added_subs: list[str] = []
            failed_subs: list[dict[str, str]] = []
            for sub in cand.include_subaccounts:
                sub_addr = normalize_evm_address(str(sub.get("address") or sub.get("subAccountUser") or ""))
                if not sub_addr or sub_addr.lower() in existing_wallet_addrs:
                    continue
                existing_wallet_addrs.add(sub_addr.lower())
                sub_name = str(sub.get("name") or sub.get("label") or "").strip() or f"Sub {sub_addr[:6]}"
                occ = _occupied_account_ids(adapter_id, adapter)
                sub_acct_id = mint_account_id(
                    adapter_id,
                    occ,
                    preferred_suffix=f"sub_{sub_addr[2:8]}",
                    force_secondary=True,
                )
                sub_cred = ExchangeCredentialRecord(
                    venue_id=sub_acct_id,
                    adapter_id=adapter_id,
                    label=sub_name,
                    api_key=sub_addr,
                    api_secret="",
                    remote_uid=sub_addr,
                    account_role="subaccount",
                    enabled=True,
                )
                try:
                    sub_res = await sync_manager.sync_candidate_and_commit(sub_cred, expected_revision="absent")
                    if sub_res.get("committed"):
                        added_subs.append(sub_acct_id)
                        res.setdefault("venues", {}).update(sub_res.get("venues") or {})
                except Exception as sub_exc:
                    sub_diag = humanize_exchange_error(adapter_id, sub_exc)
                    failed_subs.append(
                        {
                            "address": sub_addr,
                            "name": sub_name,
                            "error": getattr(sub_exc, "reason", None) or sub_diag["reason"],
                            "error_zh": getattr(sub_exc, "reason_zh", None) or sub_diag["reason_zh"],
                        }
                    )
            if added_subs:
                res["added_subaccounts"] = added_subs
            if failed_subs:
                res["failed_subaccounts"] = failed_subs

        if res.get("committed") and enter_live_ledger and ledger_service.demo_mode:
            ledger_service.set_demo_mode(False)
            res["auth_status"] = auth_manager.get_status(
                request.cookies.get(AUTH_COOKIE_NAME), demo_mode=False
            )
        return JSONResponse(content=res)

    @app.get("/api/v1/exchanges")
    async def list_exchanges() -> dict[str, Any]:
        configured_list = vault.list_masked()
        configured = {item["venue_id"]: item for item in configured_list}
        sched_venues = sync_manager.get_schedule_summary().get("venues") or {}
        available = []
        error_count = 0
        for meta in reg.list_all():
            ad_id = meta.venue_id.lower()
            ad_obj = reg.get(ad_id)

            acct_ids: list[str] = [ad_id]
            for c_item in configured_list:
                cid = str(c_item.get("venue_id") or "").lower()
                if parse_adapter_id(cid) == ad_id and cid not in acct_ids:
                    acct_ids.append(cid)
            for pid in store.list_partition_account_ids(ad_id):
                if pid not in acct_ids:
                    acct_ids.append(pid)

            accounts_out: list[dict[str, Any]] = []
            for acct_id in acct_ids:
                c_item = configured.get(acct_id)
                is_c = c_item is not None
                is_en = bool(c_item.get("enabled", True)) if c_item else True
                has_d = bool(ad_obj and ad_obj.has_venue_data(store.for_account(acct_id, ad_id)))
                if not is_c and not has_d:
                    continue
                h_st = health_tracker.resolve_state(
                    acct_id,
                    configured=is_c,
                    enabled=is_en,
                    poll_state=sched_venues.get(acct_id),
                )
                if not is_c and has_d:
                    h_st = {**h_st, "status": "archived"}
                if is_c and is_en and h_st.get("status") == "error":
                    error_count += 1
                raw_sc = store.venue_scope(acct_id)
                cached_uid_raw = (
                    ad_obj.extract_cached_account_uid(store.for_account(acct_id, ad_id))
                    if ad_obj is not None
                    else str(raw_sc.get_kv("account_uid") or "")
                )
                saved_uid = str((c_item.get("masked_uid") if c_item else "") or mask_uid(cached_uid_raw))
                saved_role = str((c_item.get("account_role") if c_item else "") or raw_sc.get_kv("account_role") or "")
                saved_label = str((c_item.get("label") if c_item else "") or raw_sc.get_kv("account_label") or "")
                eff_c_item = (
                    {**c_item, "masked_uid": saved_uid, "account_role": saved_role}
                    if c_item is not None
                    else None
                )
                accounts_out.append(
                    {
                        "account_id": acct_id,
                        "adapter_id": ad_id,
                        "label": saved_label,
                        "configured": is_c,
                        "has_archived_data": has_d,
                        "masked_uid": saved_uid,
                        "account_role": saved_role,
                        "credential": eff_c_item,
                        "poll_state": sched_venues.get(acct_id),
                        "health": h_st,
                    }
                )

            primary_acct = next((a for a in accounts_out if a["configured"]), accounts_out[0] if accounts_out else None)
            if primary_acct is not None:
                is_conf = bool(primary_acct["configured"])
                has_data = bool(primary_acct["has_archived_data"])
                cred_item = primary_acct["credential"]
                poll_st = primary_acct["poll_state"]
                h_state = primary_acct["health"]
            else:
                is_conf = False
                has_data = False
                cred_item = None
                poll_st = None
                h_state = health_tracker.resolve_state(ad_id, configured=False, enabled=True)

            entry = meta.model_dump()
            entry["configured"] = is_conf
            entry["has_archived_data"] = has_data
            entry["credential"] = cred_item
            entry["poll_state"] = poll_st
            entry["health"] = h_state
            entry["accounts"] = accounts_out
            available.append(entry)
        return {
            "adapters": available,
            "configured": configured_list,
            "error_count": error_count,
        }

    @app.post("/api/v1/exchanges/verify")
    async def verify_exchange_before_save(body: ExchangeUpsertRequest) -> Response:
        acct_id, adapter_id, adapter, temp_cred = _prepare_candidate(body)
        existing_cred = vault.get_decrypted(acct_id)
        scoped_store = store.for_account(acct_id, adapter_id)
        has_existing_data = bool(adapter.has_venue_data(scoped_store))
        bound_uid = str(
            (existing_cred.remote_uid if existing_cred else "")
            or store.venue_scope(acct_id).get_kv("account_uid")
            or adapter.extract_cached_account_uid(scoped_store)
            or ""
        ).strip()
        uses_probe_uid = adapter.meta.identity_mode == "probe"
        try:
            is_rotating_existing = (
                uses_probe_uid
                and existing_cred is not None
                and (
                    existing_cred.api_key.strip() != temp_cred.api_key.strip()
                    or existing_cred.api_secret.strip() != temp_cred.api_secret.strip()
                    or existing_cred.passphrase.strip() != temp_cred.passphrase.strip()
                )
            )
            if not bound_uid and is_rotating_existing and existing_cred is not None:
                prev_info = await adapter.probe_identity(existing_cred)
                if isinstance(prev_info, dict) and prev_info.get("remote_uid"):
                    bound_uid = str(prev_info["remote_uid"]).strip()

            if uses_probe_uid and (body.create_new or is_secondary_account_id(acct_id)):
                await sync_manager.backfill_unattributed_sibling_uids(adapter_id, exclude_acct_id=acct_id)

            probed_transient_err = ""
            try:
                res = await adapter.test_connection(temp_cred)
            except IdentityProbeTransientError as t_exc:
                probed_transient_err = str(t_exc)
                res = {"ok": True, "venue_id": adapter_id}

            if (
                uses_probe_uid
                and probed_transient_err
                and not bound_uid
                and not (is_rotating_existing and has_existing_data)
            ):
                raise RuntimeError(probed_transient_err)

            probed_uid = str((res.get("remote_uid") if isinstance(res, dict) else "") or temp_cred.remote_uid or "").strip()
            occupied_for_adapter = _occupied_account_ids(adapter_id, adapter)
            if body.create_new and not body.account_id.strip() and probed_uid:
                acct_id = mint_account_id(
                    adapter_id,
                    occupied_for_adapter,
                    preferred_suffix=deterministic_uid_suffix(adapter_id, probed_uid),
                    force_secondary=True,
                )
            must_verify_uid = bool(uses_probe_uid)
            sync_manager.check_identity_conflicts(
                account_id=acct_id,
                adapter_id=adapter_id,
                api_key=temp_cred.api_key,
                resolved_uid=probed_uid,
                bound_uid=bound_uid,
                exchange_name=adapter.meta.display_name,
                exchange_name_zh=adapter.meta.display_name_zh,
                require_verified_uid=must_verify_uid,
                require_uid_for_existing_data=bool(uses_probe_uid and is_rotating_existing and has_existing_data),
            )
            out_payload = {
                **(res if isinstance(res, dict) else {}),
                "verified": True,
                "account_id": acct_id,
                "adapter_id": adapter_id,
                "masked_uid": mask_uid(probed_uid),
            }
            out_payload.pop("remote_uid", None)
            return JSONResponse(content=out_payload)
        except (AccountIdentityError, EasiCoinIdentityError) as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": exc.reason,
                    "detail_zh": exc.reason_zh,
                    "error_code": exc.code,
                    "committed": False,
                },
            )
        except Exception as exc:
            diag = humanize_exchange_error(adapter_id, exc)
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
    async def upsert_exchange(request: Request, body: ExchangeUpsertRequest) -> Response:
        acct_id, adapter_id, adapter, temp_cred = _prepare_candidate(body)
        if adapter.meta.identity_mode == "sync_bound":
            return await commit_candidate(body, request, finish_onboarding=False)

        uses_probe_uid = adapter.meta.identity_mode == "probe"
        if body.create_new and uses_probe_uid:
            try:
                await sync_manager.backfill_unattributed_sibling_uids(adapter_id, exclude_acct_id=acct_id)
            except IdentityProbeTransientError as t_exc:
                diag = humanize_exchange_error(adapter_id, t_exc)
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
            if sync_manager.has_unattributed_data_partitions(adapter_id, exclude_acct_id=acct_id):
                return await commit_candidate(body, request, finish_onboarding=False)
            probed = await adapter.probe_identity(temp_cred)
            if isinstance(probed, dict) and probed.get("_transient_error"):
                diag = humanize_exchange_error(adapter_id, probed["_transient_error"])
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
            disc_uid = str((probed.get("remote_uid") if isinstance(probed, dict) else "") or temp_cred.remote_uid or "").strip()
            disc_role = str((probed.get("account_role") if isinstance(probed, dict) else "") or temp_cred.account_role or "").strip()
            if not disc_uid:
                err = AccountIdentityError(
                    "ACCOUNT_UID_UNAVAILABLE",
                    exchange_name=adapter.meta.display_name,
                    exchange_name_zh=adapter.meta.display_name_zh,
                )
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": err.reason,
                        "detail_zh": err.reason_zh,
                        "error_code": err.code,
                        "committed": False,
                    },
                )
            if not body.account_id.strip():
                acct_id = mint_account_id(
                    adapter_id,
                    _occupied_account_ids(adapter_id, adapter),
                    preferred_suffix=deterministic_uid_suffix(adapter_id, disc_uid),
                    force_secondary=True,
                )
            temp_cred = temp_cred.model_copy(
                update={"venue_id": acct_id, "remote_uid": disc_uid, "account_role": disc_role}
            )

        eff_expected_rev = "absent" if (body.create_new and not body.account_id.strip()) else body.expected_revision
        async with sync_manager.credential_change(acct_id, eff_expected_rev):
            existing_cred = vault.get_decrypted(acct_id)
            bound_uid = str(
                (existing_cred.remote_uid if existing_cred else "")
                or store.venue_scope(acct_id).get_kv("account_uid")
                or adapter.extract_cached_account_uid(store.for_account(acct_id, adapter_id))
                or ""
            ).strip()
            try:
                sync_manager.check_identity_conflicts(
                    account_id=acct_id,
                    adapter_id=adapter_id,
                    api_key=temp_cred.api_key,
                    resolved_uid=temp_cred.remote_uid,
                    bound_uid=bound_uid,
                    exchange_name=adapter.meta.display_name,
                    exchange_name_zh=adapter.meta.display_name_zh,
                    require_verified_uid=bool(body.create_new and uses_probe_uid),
                )
            except AccountIdentityError as exc:
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": exc.reason,
                        "detail_zh": exc.reason_zh,
                        "error_code": exc.code,
                        "committed": False,
                    },
                )
            masked = vault.upsert(temp_cred)
            raw_sc = store.venue_scope(acct_id)
            if temp_cred.remote_uid:
                raw_sc.set_kv("account_uid", temp_cred.remote_uid)
            if temp_cred.account_role:
                raw_sc.set_kv("account_role", temp_cred.account_role)
            if temp_cred.label:
                raw_sc.set_kv("account_label", temp_cred.label)
            health_state = (
                health_tracker.mark_unverified(acct_id)
                if body.enabled
                else health_tracker.resolve_state(acct_id, configured=True, enabled=False)
            )
            return JSONResponse(content={"ok": True, "account_id": acct_id, "credential": masked, "health": health_state})

    @app.delete("/api/v1/exchanges/{venue_id}")
    async def delete_exchange(
        venue_id: str,
        keep_data: bool = Query(default=False),
        expected_revision: str | None = Query(default=None),
    ) -> dict[str, Any]:
        vid = venue_id.strip().lower()
        adapter_id = parse_adapter_id(vid)
        adapter = reg.get(adapter_id)
        scoped_store = store.for_account(vid, adapter_id)
        async with sync_manager.credential_change(vid, expected_revision):
            existing_c = vault.get_decrypted(vid)
            raw_sc = store.venue_scope(vid)
            if keep_data and existing_c is not None:
                if existing_c.label and raw_sc.get_kv("account_label") != existing_c.label:
                    raw_sc.set_kv("account_label", existing_c.label)
                if existing_c.remote_uid and raw_sc.get_kv("account_uid") != existing_c.remote_uid:
                    raw_sc.set_kv("account_uid", existing_c.remote_uid)
                if existing_c.account_role and raw_sc.get_kv("account_role") != existing_c.account_role:
                    raw_sc.set_kv("account_role", existing_c.account_role)
            elif (not keep_data) and is_secondary_account_id(vid):
                known_uid = str(
                    (existing_c.remote_uid if existing_c is not None else "")
                    or raw_sc.get_kv("account_uid")
                    or (adapter.extract_cached_account_uid(scoped_store) if adapter is not None else "")
                    or ""
                ).strip()
                if known_uid:
                    det_suffix = deterministic_uid_suffix(adapter_id, known_uid)
                    if det_suffix:
                        det_id = f"{adapter_id}__{det_suffix}"
                        if det_id != vid:
                            store.migrate_account_journal_prefix(vid, det_id)
            had_cred = vault.delete(vid)
            had_data = bool(adapter and adapter.has_venue_data(scoped_store))
            if not had_cred and not had_data:
                raise HTTPException(status_code=404, detail="Exchange credential not found")
            health_tracker.remove(vid)
            sched = store.get_meta("sync_schedule", {})
            if isinstance(sched, dict) and vid in sched:
                sched.pop(vid, None)
                store.set_meta("sync_schedule", sched)
            if not keep_data:
                store.purge_venue_data(vid)
            still_has_data = bool(adapter and adapter.has_venue_data(scoped_store))
            return {
                "ok": True,
                "venue_id": vid,
                "account_id": vid,
                "kept_data": bool(keep_data and still_has_data),
                "has_archived_data": still_has_data,
            }

    @app.post("/api/v1/exchanges/{venue_id}/test")
    async def test_exchange(venue_id: str) -> Response:
        vid = venue_id.strip().lower()
        adapter_id = parse_adapter_id(vid)
        adapter = reg.get(adapter_id)
        cred = vault.get_decrypted(vid)
        if adapter is None or cred is None:
            raise HTTPException(status_code=404, detail="Exchange adapter or credential not found")
        try:
            res = await adapter.test_connection(cred)
            h_state = health_tracker.record_success(vid)
            raw_sc = store.venue_scope(vid)
            disc_uid = str((res.get("remote_uid") if isinstance(res, dict) else "") or cred.remote_uid or raw_sc.get_kv("account_uid") or "").strip()
            disc_role = str((res.get("account_role") if isinstance(res, dict) else "") or cred.account_role or raw_sc.get_kv("account_role") or "").strip()
            if disc_uid:
                raw_sc.set_kv("account_uid", disc_uid)
            if disc_role:
                raw_sc.set_kv("account_role", disc_role)
            if (disc_uid and disc_uid != cred.remote_uid) or (disc_role and disc_role != cred.account_role):
                vault.upsert(cred.model_copy(update={"remote_uid": disc_uid, "account_role": disc_role}))
            clean_res = {k: v for k, v in (res.items() if isinstance(res, dict) else ()) if k != "remote_uid"}
            if disc_uid:
                clean_res["masked_uid"] = mask_uid(disc_uid)
            if disc_role:
                clean_res["account_role"] = disc_role
            return JSONResponse(content={**clean_res, "account_id": vid, "health": h_state})
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

    return commit_candidate
