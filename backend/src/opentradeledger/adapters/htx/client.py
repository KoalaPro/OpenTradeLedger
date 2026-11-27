"""Strictly read-only HTX Spot (`GET`) & USDT-M Linear Swap (`POST` query-only) signed REST client.

Signature specification (HTX SignatureVersion=2):
  payload = f"{METHOD}\\n{host_lower}\\n{path}\\n{sorted_urlencoded_query}"
  Signature = Base64(HMAC-SHA256(payload, api_secret))
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import math
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode, urlsplit

import httpx

from opentradeledger.adapters.cycle_evidence import cycle_journal_keys, event_fill, usd_fee
from opentradeledger.core.venue_primitives import sum_fees
from opentradeledger.core.position_lifecycle import build_position_events, dated_cash

from opentradeledger.security.readonly_guard import assert_readonly_request

TZ_UTC8 = timezone(timedelta(hours=8))

HTX_ALLOWED_GET_PATHS: tuple[str, ...] = (
    "/v2/user/uid",
    "/v2/user/api-key",
    "/v1/account/accounts",
    "/v1/query/deposit-withdraw",
    "/market/tickers",
)

HTX_ALLOWED_SWAP_POST_PATHS: tuple[str, ...] = (
    "/linear-swap-api/v1/swap_cross_account_info",
    "/linear-swap-api/v1/swap_account_info",
    "/linear-swap-api/v1/swap_cross_position_info",
    "/linear-swap-api/v1/swap_position_info",
    "/linear-swap-api/v1/swap_financial_record_exact",
    "/linear-swap-api/v1/swap_financial_record",
    "/linear-swap-api/v3/swap_financial_record_exact",
    "/linear-swap-api/v3/swap_financial_record",
    "/linear-swap-api/v1/swap_cross_matchresults_exact",
    "/linear-swap-api/v1/swap_cross_matchresults",
    "/linear-swap-api/v3/swap_cross_matchresults",
    "/linear-swap-api/v1/swap_matchresults_exact",
    "/linear-swap-api/v1/swap_matchresults",
    "/linear-swap-api/v1/swap_fee",
)


class HtxApiError(RuntimeError):
    """Raised when HTX returns an error status/code or HTTP failure."""


def _is_valid_htx_num(val: Any) -> bool:
    if val is None or isinstance(val, bool):
        return False
    if isinstance(val, (int, float)):
        return math.isfinite(float(val))
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return False
        try:
            return math.isfinite(float(s))
        except ValueError:
            return False
    return False


def htx_swap_equity(row: dict[str, Any]) -> float:
    for key in ("margin_balance", "margin_available", "margin_static"):
        if key in row and not _is_valid_htx_num(row[key]):
            raise HtxApiError("Malformed HTX swap account payload")
    if "margin_balance" in row:
        return float(row["margin_balance"])
    if "margin_static" in row:
        return float(row["margin_static"])
    raise HtxApiError("Malformed HTX swap account payload: missing total equity")


def _fmt_bj_ms(ms: int) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=TZ_UTC8).strftime("%m-%d %H:%M")


def _default_htx_utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def sign_htx_v2(
    method: str,
    host: str,
    path: str,
    sorted_query_params: list[tuple[str, str]],
    api_secret: str,
) -> str:
    encoded_qs = urlencode(sorted_query_params)
    payload = f"{method.upper()}\n{host.lower()}\n{path}\n{encoded_qs}".encode("utf-8")
    digest = hmac.new(api_secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def normalize_htx_symbol(raw: str) -> str:
    s = (raw or "").strip().upper().replace("-", "_")
    if "_" in s:
        return s
    for q in ("USDT", "USDC", "USD"):
        if s.endswith(q) and len(s) > len(q):
            return f"{s[:-len(q)]}_{q}"
    return s


class HtxReadOnlyClient:
    """Strictly read-only signed REST client for HTX Spot (`api.huobi.pro`) & Swap (`api.hbdm.com`)."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        spot_base_url: str = "https://api.huobi.pro",
        swap_base_url: str = "https://api.hbdm.com",
        client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 12.0,
        clock_utc: Optional[Callable[[], str]] = None,
        clock_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.spot_base_url = spot_base_url.rstrip("/")
        self.swap_base_url = swap_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec, connect=6.0))
        self._clock_utc = clock_utc or _default_htx_utc_ts
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _build_signed_url(
        self,
        method: str,
        base_url: str,
        path: str,
        query_params: Optional[dict[str, Any]] = None,
    ) -> str:
        host = (urlsplit(base_url).netloc or "api.huobi.pro").lower()
        merged: dict[str, str] = {
            "AccessKeyId": self.api_key,
            "SignatureMethod": "HmacSHA256",
            "SignatureVersion": "2",
            "Timestamp": self._clock_utc(),
        }
        for k, v in (query_params or {}).items():
            if v is not None and v != "":
                merged[k] = str(v)
        sorted_items = sorted(merged.items(), key=lambda kv: kv[0])
        sig = sign_htx_v2(method, host, path, sorted_items, self.api_secret)
        final_qs = urlencode([*sorted_items, ("Signature", sig)])
        return f"{base_url}{path}?{final_qs}"

    async def spot_get(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        *,
        signed: bool = True,
    ) -> Any:
        assert_readonly_request("GET", path, HTX_ALLOWED_GET_PATHS)
        if signed:
            url = self._build_signed_url("GET", self.spot_base_url, path, params)
        else:
            clean = {k: str(v) for k, v in (params or {}).items() if v is not None and v != ""}
            qs = urlencode(sorted(clean.items()))
            url = f"{self.spot_base_url}{path}" + (f"?{qs}" if qs else "")
        resp = await self._client.get(url)
        if resp.status_code >= 400:
            raise HtxApiError(f"HTX GET {path} HTTP {resp.status_code}: {resp.text[:240]}")
        payload = resp.json()
        if not isinstance(payload, dict):
            raise HtxApiError(f"HTX GET {path} unexpected payload type: {type(payload).__name__}")
        st = str(payload.get("status") or "").lower()
        code = payload.get("code")
        code_ok = False
        if code is not None and not isinstance(code, bool):
            try:
                code_ok = int(code) == 200
            except (TypeError, ValueError):
                code_ok = False
        if (st and st != "ok") or (not st and not code_ok):
            raise HtxApiError(
                f"HTX GET {path} error: {payload.get('err-code') or code} {payload.get('err-msg') or payload.get('message') or ''}"
            )
        return payload.get("data")

    async def swap_query(self, path: str, body: Optional[dict[str, Any]] = None) -> Any:
        payload_body = dict(body or {})
        assert_readonly_request(
            "POST",
            path,
            HTX_ALLOWED_GET_PATHS,
            allowed_post_paths=HTX_ALLOWED_SWAP_POST_PATHS,
            post_payload=payload_body,
        )
        url = self._build_signed_url("POST", self.swap_base_url, path)
        resp = await self._client.post(
            url,
            json=payload_body,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code >= 400:
            raise HtxApiError(f"HTX POST {path} HTTP {resp.status_code}: {resp.text[:240]}")
        payload = resp.json()
        if not isinstance(payload, dict):
            raise HtxApiError(f"HTX POST {path} unexpected payload type: {type(payload).__name__}")
        if str(payload.get("status") or "").lower() != "ok":
            raise HtxApiError(
                f"HTX POST {path} err={payload.get('err_code')}: {payload.get('err_msg', '')}"
            )
        return payload.get("data")

    async def get_identity(self) -> dict[str, str]:
        uid_val = await self.spot_get("/v2/user/uid")
        uid = str(uid_val or "").strip() if uid_val not in (None, "", 0) else ""
        role = ""
        if uid:
            try:
                keys = await self.spot_get("/v2/user/api-key", {"uid": uid})
                if isinstance(keys, list) and keys and isinstance(keys[0], dict):
                    parent_id = str(keys[0].get("parentId") or "").strip()
                    if parent_id and parent_id != "0" and parent_id != uid:
                        role = "subaccount"
                    else:
                        role = "master"
            except Exception:
                pass
        return {"remote_uid": uid, "account_role": role}

    async def get_spot_balances(self) -> list[dict[str, Any]]:
        accts = await self.spot_get("/v1/account/accounts")
        if not isinstance(accts, list):
            raise HtxApiError("Malformed HTX spot accounts/balance payload")
        out: list[dict[str, Any]] = []
        allowed_acct_types = {"spot", "margin", "super-margin", "otc"}
        for acct in accts:
            if (
                not isinstance(acct, dict)
                or acct.get("id") in (None, "")
                or not isinstance(acct.get("type"), str)
                or not acct["type"].strip()
            ):
                raise HtxApiError("Malformed HTX spot accounts/balance payload")
            aid = acct["id"]
            atype_low = acct["type"].strip().lower()
            if atype_low not in allowed_acct_types:
                continue
            bal_res = await self.spot_get(f"/v1/account/accounts/{aid}/balance")
            if not isinstance(bal_res, dict) or "list" not in bal_res or not isinstance(bal_res["list"], list):
                raise HtxApiError("Malformed HTX spot accounts/balance payload")
            for item in bal_res["list"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("currency"), str)
                    or not item["currency"].strip()
                    or "balance" not in item
                    or not _is_valid_htx_num(item.get("balance"))
                ):
                    raise HtxApiError("Malformed HTX spot accounts/balance payload")
                item_tp = str(item.get("type") or "").strip().lower()
                if item_tp and item_tp not in {"trade", "frozen"}:
                    continue
                if float(item["balance"]) > 0:
                    out.append({**item, "_account_type": atype_low.upper()})
        return out

    async def get_swap_accounts(self) -> list[dict[str, Any]]:
        res = await self.swap_query("/linear-swap-api/v1/swap_cross_account_info", {"margin_account": "USDT"})
        if not isinstance(res, list):
            raise HtxApiError("Malformed HTX swap account payload")
        out: list[dict[str, Any]] = []
        for r in res:
            if not isinstance(r, dict):
                raise HtxApiError("Malformed HTX swap account payload")
            htx_swap_equity(r)
            out.append(r)
        return out

    async def get_swap_positions(self) -> list[dict[str, Any]]:
        res = await self.swap_query("/linear-swap-api/v1/swap_cross_position_info", {})
        return [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []

    @staticmethod
    def _build_htx_47h_windows(since_ms: int, now_ms: int) -> list[tuple[Optional[int], Optional[int]]]:
        chunk_ms = 47 * 3600 * 1000
        ninety_days_ms = 90 * 86_400_000 - 60_000
        if since_ms <= 0:
            eff_now = max(0, int(now_ms))
            lower_bound = max(0, eff_now - ninety_days_ms)
        else:
            eff_now = max(int(since_ms), int(now_ms))
            lower_bound = int(since_ms)
        windows: list[tuple[Optional[int], Optional[int]]] = []
        w_end = eff_now
        while w_end >= lower_bound:
            w_start = max(lower_bound, w_end - chunk_ms)
            windows.append((w_start, w_end))
            if w_start <= lower_bound:
                break
            w_end = w_start - 1
        return windows or [(lower_bound, eff_now)]

    @staticmethod
    def _extract_htx_cursor_id(row: dict[str, Any]) -> Optional[int]:
        for key in ("query_id", "id"):
            val = row.get(key)
            if val in (None, ""):
                continue
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
        return None

    async def get_swap_matches(
        self,
        *,
        since_ms: int = 0,
        max_pages: int = 40,
        warnings_out: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        windows = self._build_htx_47h_windows(since_ms, self._clock_ms())
        use_legacy_fallback = False
        truncated = False
        abort_windows = False

        for win_idx, (w_start, w_end) in enumerate(windows):
            if use_legacy_fallback or abort_windows:
                break
            from_id: Optional[int] = None
            saw_older_than_win0 = False
            for page_idx in range(max(1, int(max_pages))):
                body: dict[str, Any] = {"trade_type": 0, "size": 50}
                if w_start is not None:
                    body["start_time"] = w_start
                if w_end is not None:
                    body["end_time"] = w_end
                if from_id is not None:
                    body["from_id"] = from_id

                try:
                    res = await self.swap_query("/linear-swap-api/v1/swap_cross_matchresults_exact", body)
                except HtxApiError as exc:
                    msg_low = str(exc).lower()
                    if page_idx == 0 and ("http 404" in msg_low or "not found" in msg_low or "unsupported" in msg_low):
                        use_legacy_fallback = True
                        for leg_page in range(1, max(1, int(max_pages)) + 1):
                            leg_res = await self.swap_query(
                                "/linear-swap-api/v1/swap_cross_matchresults",
                                {"trade_type": 0, "create_date": 90, "page_index": leg_page, "page_size": 50},
                            )
                            leg_trades = leg_res.get("trades") if isinstance(leg_res, dict) else (leg_res if isinstance(leg_res, list) else [])
                            leg_valid = [r for r in leg_trades if isinstance(r, dict)] if isinstance(leg_trades, list) else []
                            if not leg_valid:
                                break
                            leg_new = 0
                            for idx_l, r in enumerate(leg_valid):
                                mk = htx_match_unique_key(r, fallback_idx=idx_l)
                                if mk not in merged:
                                    merged[mk] = r
                                    leg_new += 1
                            tot_pg = int(leg_res.get("total_page") or 0) if isinstance(leg_res, dict) else 0
                            if leg_new == 0 or len(leg_valid) < 50 or (tot_pg > 0 and leg_page >= tot_pg):
                                break
                        break
                    raise

                trades = res.get("trades") if isinstance(res, dict) else (res if isinstance(res, list) else [])
                valid_rows = [r for r in trades if isinstance(r, dict)] if isinstance(trades, list) else []
                if not valid_rows:
                    break

                new_cnt = 0
                cids: list[int] = []
                ts_list: list[int] = []
                for idx_r, r in enumerate(valid_rows):
                    mk = htx_match_unique_key(r, fallback_idx=idx_r)
                    if mk not in merged:
                        merged[mk] = r
                        new_cnt += 1
                    cid = self._extract_htx_cursor_id(r)
                    if cid is not None:
                        cids.append(cid)
                    rts = int(r.get("create_date") or r.get("ts") or 0)
                    if rts > 0:
                        ts_list.append(rts)

                if win_idx == 0 and w_start is not None and any(0 < rts < w_start - 60_000 for rts in ts_list):
                    saw_older_than_win0 = True
                if (
                    win_idx >= 1
                    and page_idx == 0
                    and new_cnt == 0
                    and w_start is not None
                    and w_end is not None
                    and (not ts_list or all(rts < w_start - 60_000 or rts > w_end + 60_000 for rts in ts_list))
                ):
                    abort_windows = True
                    break

                min_ts = min(ts_list) if ts_list else 0
                if new_cnt == 0 or len(valid_rows) < 50:
                    break
                if w_start is not None and min_ts > 0 and min_ts < w_start and win_idx > 0:
                    break
                if since_ms > 0 and min_ts > 0 and min_ts < since_ms:
                    break
                if not cids:
                    break
                if page_idx == max(1, int(max_pages)) - 1:
                    truncated = True
                    break

                min_cid = min(cids)
                from_id = min_cid if (from_id is None or min_cid < from_id) else (from_id - 1)

            if saw_older_than_win0:
                break

        if truncated and warnings_out is not None:
            warnings_out.append(
                f"HTX swap_cross_matchresults_exact reached {max_pages}-page pagination cap; older trades may be truncated."
            )
        return list(merged.values())

    async def get_swap_financial_records(
        self,
        *,
        since_ms: int = 0,
        max_pages: int = 40,
        warnings_out: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        windows = self._build_htx_47h_windows(since_ms, self._clock_ms())
        truncated = False
        abort_windows = False

        for win_idx, (w_start, w_end) in enumerate(windows):
            if abort_windows:
                break
            from_id: Optional[int] = None
            saw_older_than_win0 = False
            for page_idx in range(max(1, int(max_pages))):
                body: dict[str, Any] = {"margin_account": "USDT", "size": 50}
                if w_start is not None:
                    body["start_time"] = w_start
                if w_end is not None:
                    body["end_time"] = w_end
                if from_id is not None:
                    body["from_id"] = from_id

                res = await self.swap_query("/linear-swap-api/v1/swap_financial_record_exact", body)
                rows = res.get("financial_record") if isinstance(res, dict) else (res if isinstance(res, list) else [])
                valid_rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
                if not valid_rows:
                    break

                new_cnt = 0
                cids: list[int] = []
                ts_list: list[int] = []
                for r in valid_rows:
                    fk = str(r.get("id") or r.get("query_id") or f"{r.get('ts')}_{r.get('type')}_{r.get('amount')}_{r.get('contract_code')}")
                    if fk not in merged:
                        merged[fk] = r
                        new_cnt += 1
                    cid = self._extract_htx_cursor_id(r)
                    if cid is not None:
                        cids.append(cid)
                    rts = int(r.get("ts") or r.get("create_date") or 0)
                    if rts > 0:
                        ts_list.append(rts)

                if win_idx == 0 and w_start is not None and any(0 < rts < w_start - 60_000 for rts in ts_list):
                    saw_older_than_win0 = True
                if (
                    win_idx >= 1
                    and page_idx == 0
                    and new_cnt == 0
                    and w_start is not None
                    and w_end is not None
                    and (not ts_list or all(rts < w_start - 60_000 or rts > w_end + 60_000 for rts in ts_list))
                ):
                    abort_windows = True
                    break

                min_ts = min(ts_list) if ts_list else 0
                if new_cnt == 0 or len(valid_rows) < 50:
                    break
                if w_start is not None and min_ts > 0 and min_ts < w_start and win_idx > 0:
                    break
                if since_ms > 0 and min_ts > 0 and min_ts < since_ms:
                    break
                if not cids:
                    break
                if page_idx == max(1, int(max_pages)) - 1:
                    truncated = True
                    break

                min_cid = min(cids)
                from_id = min_cid if (from_id is None or min_cid < from_id) else (from_id - 1)

            if saw_older_than_win0:
                break

        if truncated and warnings_out is not None:
            warnings_out.append(
                f"HTX swap_financial_record_exact reached {max_pages}-page pagination cap; older financial records may be truncated."
            )
        return list(merged.values())

    async def get_deposit_withdraw(
        self,
        kind: str,
        *,
        max_pages: int = 20,
        warnings_out: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        cursor_from: Optional[str] = None
        for page_idx in range(max(1, int(max_pages))):
            params: dict[str, Any] = {"type": kind, "size": "200", "direct": "next"}
            if cursor_from is not None:
                params["from"] = cursor_from
            res = await self.spot_get("/v1/query/deposit-withdraw", params)
            valid_rows = [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []
            if not valid_rows:
                break

            new_cnt = 0
            num_ids: list[int] = []
            for r in valid_rows:
                rk = str(r.get("id") or r.get("tx-hash") or f"{r.get('created-at')}_{r.get('amount')}_{r.get('currency')}")
                if rk not in merged:
                    merged[rk] = r
                    new_cnt += 1
                cid = self._extract_htx_cursor_id(r)
                if cid is not None:
                    num_ids.append(cid)

            if new_cnt == 0 or len(valid_rows) < 200 or not num_ids:
                break
            if page_idx == max(1, int(max_pages)) - 1:
                if warnings_out is not None:
                    warnings_out.append(f"HTX {kind} history reached {max_pages}-page pagination cap.")
                break

            first_cid = self._extract_htx_cursor_id(valid_rows[0])
            last_cid = self._extract_htx_cursor_id(valid_rows[-1])
            if first_cid is not None and last_cid is not None and first_cid < last_cid:
                next_val = max(num_ids)
                if cursor_from is not None and str(next_val) == cursor_from:
                    next_val += 1
                cursor_from = str(next_val)
            else:
                next_val = min(num_ids)
                if cursor_from is not None and str(next_val) == cursor_from:
                    next_val -= 1
                if next_val <= 0:
                    break
                cursor_from = str(next_val)

        return list(merged.values())

    async def get_spot_tickers(self) -> dict[str, float]:
        res = await self.spot_get("/market/tickers", signed=False)
        prices: dict[str, float] = {}
        for row in res or []:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").upper()
            for q in ("USDT", "USDC"):
                if sym.endswith(q) and len(sym) > len(q):
                    base = sym[: -len(q)]
                    try:
                        px = float(row.get("close") or 0.0)
                        if px > 0:
                            prices[base] = px
                    except (TypeError, ValueError):
                        pass
                    break
        return prices

    async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
        warnings: list[str] = []
        failed_fields: list[str] = []

        def _is_fatal(exc: Exception) -> bool:
            msg_low = str(exc).lower()
            return (
                isinstance(exc, httpx.TransportError)
                or "http 5" in msg_low
                or "http 429" in msg_low
                or "err=1010" in msg_low
                or "err=1056" in msg_low
                or "err=1066" in msg_low
                or "1056" in msg_low
                or "1066" in msg_low
                or "too many" in msg_low
                or "rate limit" in msg_low
                or "system busy" in msg_low
                or "busy" in msg_low
                or "timeout" in msg_low
                or "invalid request" in msg_low
                or "parameter" in msg_low
            )

        swap_accounts: list[dict[str, Any]] = []
        spot_balances: list[dict[str, Any]] = []
        try:
            swap_accounts = await self.get_swap_accounts()
        except Exception as sw_exc:
            if _is_fatal(sw_exc):
                raise
            warnings.append(f"swap_accounts: {sw_exc}")
            failed_fields.append("swap_accounts")
        try:
            spot_balances = await self.get_spot_balances()
        except Exception as sp_exc:
            if _is_fatal(sp_exc) or "swap_accounts" in failed_fields:
                raise sp_exc
            warnings.append(f"spot_balances: {sp_exc}")
            failed_fields.append("spot_balances")

        async def _opt(name: str, coro: Any, default: Any) -> Any:
            try:
                return await coro
            except Exception as exc:
                if _is_fatal(exc):
                    raise
                warnings.append(f"{name}: {exc}")
                failed_fields.append(name)
                return default

        positions = await _opt("positions", self.get_swap_positions(), [])
        matches = await _opt("matches", self.get_swap_matches(since_ms=since_ms, warnings_out=warnings), [])
        financials = await _opt(
            "financials",
            self.get_swap_financial_records(since_ms=since_ms, warnings_out=warnings),
            [],
        )
        deposits = await _opt("deposits", self.get_deposit_withdraw("deposit", warnings_out=warnings), [])
        withdrawals = await _opt("withdrawals", self.get_deposit_withdraw("withdraw", warnings_out=warnings), [])
        tickers = await _opt("tickers", self.get_spot_tickers(), {})

        out_snap: dict[str, Any] = {
            "venue_id": "htx",
            "swap_accounts": swap_accounts,
            "spot_balances": spot_balances,
            "positions": positions,
            "matches": matches,
            "financials": financials,
            "deposits": deposits,
            "withdrawals": withdrawals,
            "tickers": tickers,
            "warnings": warnings,
        }
        if failed_fields:
            out_snap["_failed_fields"] = failed_fields
        return out_snap


def htx_match_unique_key(r: dict[str, Any], *, fallback_idx: int = 0) -> str:
    rid = r.get("id") or r.get("query_id")
    mid = r.get("match_id")
    if rid not in (None, "") and mid not in (None, ""):
        return f"{rid}_{mid}"
    if rid not in (None, ""):
        return str(rid)
    if mid not in (None, ""):
        return str(mid)
    oid = r.get("order_id")
    cd = r.get("create_date") or r.get("ts")
    if oid not in (None, "") or cd not in (None, ""):
        return f"{oid}_{cd}"
    return f"m_{fallback_idx}"


def htx_match_realized_pnl(m: dict[str, Any]) -> float:
    if m.get("real_profit") not in (None, ""):
        return float(m["real_profit"])
    if m.get("offset_profitloss") not in (None, ""):
        return float(m["offset_profitloss"])
    return float(m.get("profit") or 0.0)


def normalize_htx_closed_trades(
    matches: list[dict[str, Any]],
    financials: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Reconstructs closed USDT-M swap trades on HTX from `swap_cross_matchresults` & `swap_financial_record`."""
    funding_by_sym: dict[str, list[dict[str, Any]]] = {}
    rebate_sum = 0.0
    consumed_fund: set[str] = set()
    for idx, rec in enumerate(financials or []):
        if not isinstance(rec, dict):
            continue
        tp = int(rec.get("type") or 0)
        amt = float(rec.get("amount") or 0.0)
        ts = int(rec.get("ts") or 0)
        sym = normalize_htx_symbol(str(rec.get("contract_code") or ""))
        # Official HTX financial_record types: 30/31 = funding fee; 29 = fee rebate (9/10 are delivery settlement, NOT rebates!)
        if tp in {30, 31} and sym:
            fid = str(rec.get("id") or f"hf_{sym}_{ts}_{idx}")
            funding_by_sym.setdefault(sym, []).append({"_id": fid, "ts": ts, "amt": amt})
        elif tp == 29 and amt > 0:
            rebate_sum += amt

    dedup_matches: dict[str, dict[str, Any]] = {}
    for idx_m, m_row in enumerate(matches):
        if isinstance(m_row, dict):
            dedup_matches[htx_match_unique_key(m_row, fallback_idx=idx_m)] = m_row
    unique_matches = list(dedup_matches.values())

    total_fees_raw = sum_fees(max(0.0, -(usd_fee(m.get("trade_fee"), m.get("fee_asset") or "USDT") or 0.0))
                         for m in unique_matches)
    rebate_ratio = min(0.95, rebate_sum / total_fees_raw) if total_fees_raw > 0 and rebate_sum > 0 else 0.0

    sorted_matches = sorted(
        unique_matches,
        key=lambda r: (int(r.get("create_date") or r.get("ts") or 0), htx_match_unique_key(r)),
    )

    open_lots: dict[tuple[str, str], dict[str, Any]] = {}
    records: list[dict[str, Any]] = []

    def _emit_htx_record(
        *,
        sym: str,
        trade_side: str,
        qty: float,
        contract_count: float | None,
        open_ms: int,
        close_ms: int,
        en_px: float,
        ex_px: float,
        en_notional: float,
        ex_notional: float,
        en_fee: float,
        ex_fee: float,
        en_reb: float,
        ex_reb: float,
        en_known: bool,
        ex_known: bool,
        net_known: bool,
        en_makers: bool,
        en_takers: bool,
        ex_makers: bool,
        ex_takers: bool,
        offset_pl: float,
        mid_id: str,
        is_partial: bool,
        exit_batches: list[dict[str, Any]] | None = None,
        journal_keys: list[str] | None = None,
        position_events: list[dict[str, Any]] | None = None,
    ) -> None:
        funding_pnl = 0.0
        funding_receipts = []
        for fl in funding_by_sym.get(sym, []):
            if fl["_id"] in consumed_fund:
                continue
            if open_ms - 5_000 <= fl["ts"] <= close_ms + 5_000:
                consumed_fund.add(fl["_id"])
                funding_pnl += float(fl["amt"])
                funding_receipts.append((fl["ts"], fl["amt"]))

        total_fee = sum_fees((en_fee, ex_fee))
        total_rebate = sum_fees((en_reb, ex_reb))
        book_pnl = round(offset_pl - total_fee + funding_pnl, 6)
        net_pnl = round(book_pnl + total_rebate, 6)
        settled_str = _fmt_bj_ms(close_ms) if total_rebate > 0 else ""
        en_maker = en_makers and not en_takers
        ex_maker = ex_makers and not ex_takers
        en_mode = "MIXED" if (en_makers and en_takers) else ("LIMIT_MAKER" if en_makers else "MARKET_TAKER")
        ex_mode = "MIXED" if (ex_makers and ex_takers) else ("LIMIT_MAKER" if ex_makers else "MARKET_TAKER")

        if not net_known:
            book_pnl = net_pnl = 0.0
        records.append(
            {
                **cycle_journal_keys(journal_keys or [f"htx_{mid_id}_{close_ms}"], is_partial),
                "position_events": position_events or [],
                "venue_id": "htx",
                "venue_name": "HTX",
                "venue_name_zh": "火币 HTX",
                "symbol": sym,
                "side": trade_side,
                "qty": round(qty, 6),
                "contract_count": contract_count,
                "open_time_ms": open_ms,
                "close_time_ms": close_ms,
                "gross_pnl_usd": round(offset_pl, 4),
                "funding_pnl_usd": round(funding_pnl, 4),
                "funding_by_day": dated_cash(funding_receipts),
                "book_pnl_usd": round(book_pnl, 4),
                "total_fee_known": net_known,
                "net_pnl_known": net_known,
                "total_fee_usd": total_fee,
                "total_rebate_usd": total_rebate,
                "total_discount_saved_usd": 0.0,
                "net_fee_usd": sum_fees((total_fee, -total_rebate)),
                "net_pnl_usd": round(net_pnl, 4),
                "rebate_status": "SETTLED",
                "is_partial_close": is_partial,
                "entry_leg": {
                    "role": ("MAKER" if en_maker else "TAKER") if en_known else "UNKNOWN",
                    "mode_code": en_mode if en_known else "UNKNOWN",
                    "fee_known": en_known,
                    "fee_bps": round((en_fee / en_notional) * 10_000, 2) if (en_known and en_notional > 0) else None,
                    "time_ms": open_ms,
                    "avg_price": round(en_px, 6),
                    "notional_usd": round(en_notional, 2),
                    "gross_fee_usd": en_fee,
                    "discount_saved_usd": 0.0,
                    "discount_asset": "",
                    "rebate_usd": en_reb,
                    "rebate_pct": round((en_reb / en_fee) * 100.0, 2) if en_fee > 0 else (100.0 if en_reb > 0 else 0.0),
                    "net_fee_usd": sum_fees((en_fee, -en_reb)),
                    "rebate_status": "SETTLED",
                    "settled_at": settled_str,
                },
                "exit_leg": {
                    "role": "MAKER" if ex_maker else "TAKER",
                    "mode_code": ex_mode,
                    "fee_known": ex_known,
                    "fee_bps": round((ex_fee / ex_notional) * 10_000, 2) if ex_known and ex_notional > 0 else None,
                    "time_ms": close_ms,
                    "avg_price": round(ex_px, 6),
                    "notional_usd": round(ex_notional, 2),
                    "gross_fee_usd": ex_fee,
                    "discount_saved_usd": 0.0,
                    "discount_asset": "",
                    "rebate_usd": ex_reb,
                    "rebate_pct": round((ex_reb / ex_fee) * 100.0, 2) if ex_fee > 0 else (100.0 if ex_reb > 0 else 0.0),
                    "net_fee_usd": sum_fees((ex_fee, -ex_reb)),
                    "rebate_status": "SETTLED",
                    "settled_at": settled_str,
                },
                "exit_batches": list(exit_batches or []),
            }
        )

    def _flush_htx_lot(sym: str, trade_side: str, lot: dict[str, Any], *, full_close: bool) -> None:
        exits = lot.get("exits") or []
        if not exits:
            return
        closed_q = sum(float(x["qty"]) for x in exits)
        open_q = float(lot["qty"])
        ratio = 1.0 if (full_close or open_q <= 1e-12) else min(1.0, closed_q / open_q)
        open_ms = int(lot["open_ms"])
        close_ms = max(int(x["ts"]) for x in exits)
        en_notional = float(lot["notional"]) * ratio
        en_px = (float(lot["notional"]) / open_q) if open_q > 0 else float(exits[0]["px"])
        en_fee = float(lot["fee"]) * ratio
        en_reb = float(lot["rebate"]) * ratio + (en_fee * rebate_ratio)
        ex_notional = sum(float(x["notional"]) for x in exits)
        ex_px = (ex_notional / closed_q) if closed_q > 0 else float(exits[-1]["px"])
        ex_fee = sum_fees(float(x["fee_paid"]) for x in exits)
        ex_reb = sum_fees(float(x["maker_rebate"]) for x in exits) + (ex_fee * rebate_ratio)
        evidence = lot["event_fills"]
        units = {f["qty_unit"] for f in evidence}
        events = build_position_events(evidence, side=trade_side, linear=units == {"base"}) if len(units) == 1 else []
        for fill, event in zip(exits, (e for e in events if e["action"] in {"REDUCE", "CLOSE"})):
            if event["gross_pnl_usd"] is not None:
                fill["offset_pl"] = event["gross_pnl_usd"]
        offset_pl_sum = sum(float(x["offset_pl"]) for x in exits)
        has_turnover_all = all(x["has_turnover"] for x in exits)
        c_cnt = sum(float(x["vol"]) for x in exits) if has_turnover_all else None
        distinct_oids = {str(x["oid"]) for x in exits if x.get("oid")}
        distinct_ts = {int(x["ts"]) for x in exits}
        is_partial = (not full_close) or len(distinct_oids) > 1 or (len(exits) > 1 and len(distinct_ts) > 1)
        htx_batches: list[dict[str, Any]] = []
        htx_by_oid: dict[str, list[dict[str, Any]]] = {}
        for idx_e, x_e in enumerate(exits):
            ok_e = str(x_e.get("oid") or "").strip() or f"ts_{x_e['ts']}_{idx_e}"
            htx_by_oid.setdefault(ok_e, []).append(x_e)
        if len(htx_by_oid) > 1 and (len(distinct_oids) > 1 or len(distinct_ts) > 1):
            ordered_htx = sorted(htx_by_oid.values(), key=lambda g_e: max(int(y["ts"]) for y in g_e))
            for b_i, g_e in enumerate(ordered_htx):
                bq = sum(float(y["qty"]) for y in g_e)
                bnot = sum(float(y["notional"]) for y in g_e)
                bpx = (bnot / bq) if bq > 0 else float(g_e[-1]["px"])
                bfee = sum_fees(float(y["fee_paid"]) for y in g_e)
                breb = sum_fees(float(y["maker_rebate"]) for y in g_e) + (bfee * rebate_ratio)
                batch_known = all(y["fee_known"] for y in g_e)
                bnetf = sum_fees((bfee, -breb))
                bgpnl = sum(float(y["offset_pl"]) for y in g_e)
                bmk = any(bool(y["is_maker"]) for y in g_e)
                btk = any(not bool(y["is_maker"]) for y in g_e)
                bts = max(int(y["ts"]) for y in g_e)
                bvol = sum(float(y["vol"]) for y in g_e) if all(y["has_turnover"] for y in g_e) else None
                htx_batches.append(
                    {
                        "batch_index": b_i + 1,
                        "order_id": str(g_e[0].get("oid") or ""),
                        "qty": round(bq, 6),
                        **({"contract_count": bvol} if bvol is not None else {}),
                        "role": "MAKER" if (bmk and not btk) else "TAKER",
                        "mode_code": "MIXED" if (bmk and btk) else ("LIMIT_MAKER" if bmk else "MARKET_TAKER"),
                        "fee_known": batch_known,
                        "fee_bps": round(bfee / bnot * 10_000, 2) if batch_known and bnot > 0 else None,
                        "time_ms": bts,
                        "time_str": _fmt_bj_ms(bts),
                        "avg_price": round(bpx, 6),
                        "notional_usd": round(bnot, 2),
                        "gross_fee_usd": bfee,
                        "discount_saved_usd": 0.0,
                        "discount_asset": "",
                        "rebate_usd": breb,
                        "net_fee_usd": bnetf,
                        "gross_pnl_usd": round(bgpnl, 4),
                        "net_pnl_usd": round(bgpnl - bfee + breb, 4) if batch_known else None,
                    }
                )
        _emit_htx_record(
            sym=sym,
            trade_side=trade_side,
            qty=closed_q,
            contract_count=c_cnt,
            open_ms=open_ms,
            close_ms=close_ms,
            en_px=en_px,
            ex_px=ex_px,
            en_notional=en_notional,
            ex_notional=ex_notional,
            en_fee=en_fee,
            ex_fee=ex_fee,
            en_reb=en_reb,
            ex_reb=ex_reb,
            en_known=lot["entry_fees_known"],
            ex_known=all(x["fee_known"] for x in exits),
            net_known=lot["entry_fees_known"] and all(x["fee_known"] for x in exits),
            en_makers=bool(lot["maker_notional"] > 0),
            en_takers=bool(lot["taker_notional"] > 0),
            ex_makers=any(bool(x["is_maker"]) for x in exits),
            ex_takers=any(not bool(x["is_maker"]) for x in exits),
            offset_pl=offset_pl_sum,
            mid_id=str(exits[-1]["mid_id"]),
            is_partial=is_partial,
            exit_batches=htx_batches,
            journal_keys=[f"htx_{x['mid_id']}_{x['ts']}" for x in exits],
            position_events=events,
        )

    for idx, m in enumerate(sorted_matches):
        sym = normalize_htx_symbol(str(m.get("contract_code") or m.get("symbol") or ""))
        if not sym:
            continue
        ts = int(m.get("create_date") or m.get("ts") or 0)
        px = float(m.get("trade_price") or m.get("price") or 0.0)
        turnover = abs(float(m.get("trade_turnover") or 0.0))
        vol = abs(float(m.get("trade_volume") or m.get("volume") or 0.0))
        qty = (turnover / px) if (turnover > 0 and px > 0) else vol
        if px <= 0 or qty <= 0:
            continue
        # HTX reports a debit as a negative wallet movement.
        raw = m.get("trade_fee")
        charged = usd_fee(-float(raw), m.get("fee_asset") or "USDT") if raw not in (None, "") else None
        fee_paid = max(0.0, charged or 0.0)
        maker_rebate = max(0.0, -(charged or 0.0))
        role_str = str(m.get("role") or "").lower()
        is_maker = role_str == "maker"
        offset = str(m.get("offset") or "").lower()
        direction = str(m.get("direction") or "").lower()
        offset_pl = htx_match_realized_pnl(m)
        oid_str = str(m.get("order_id") or m.get("order_id_str") or "").strip()
        mid_id = htx_match_unique_key(m, fallback_idx=idx)

        unit = "base" if turnover > 0 else "contracts"

        is_open = offset == "open" and abs(offset_pl) <= 1e-9
        if is_open:
            trade_side = "LONG" if direction == "buy" else "SHORT"
            lot = open_lots.setdefault(
                (sym, trade_side),
                {
                    "qty": 0.0,
                    "closed_qty": 0.0,
                    "notional": 0.0,
                    "fee": 0.0,
                    "rebate": 0.0,
                    "open_ms": float(ts),
                    "maker_notional": 0.0,
                    "taker_notional": 0.0,
                    "entry_fees_known": True,
                    "event_fills": [],
                    "exits": [],
                },
            )
            if (lot["qty"] - lot["closed_qty"]) <= 1e-12 and not lot["exits"]:
                lot["open_ms"] = float(ts)
            notional_m = turnover if turnover > 0 else (px * qty)
            lot["entry_fees_known"] &= charged is not None
            lot["event_fills"].append(event_fill(ts, qty, px, True, charged, oid_str, qty_unit=unit, role=role_str))
            lot["qty"] += qty
            lot["notional"] += notional_m
            lot["fee"] = sum_fees((lot["fee"], fee_paid))
            lot["rebate"] = sum_fees((lot["rebate"], maker_rebate))
            if is_maker:
                lot["maker_notional"] += notional_m
            else:
                lot["taker_notional"] += notional_m
            continue

        # Closing leg: closing a LONG sells ("sell"), closing a SHORT buys ("buy")
        trade_side = "LONG" if direction == "sell" else "SHORT"
        lot = open_lots.get((sym, trade_side))
        rem_open_q = (float(lot["qty"]) - float(lot["closed_qty"])) if lot else 0.0
        ex_notional = turnover if turnover > 0 else (px * qty)
        if lot and rem_open_q > 1e-12:
            # Oversized explicit closes indicate that earlier inventory is missing.
            if qty > rem_open_q + 1e-9:
                lot["event_fills"].clear()
            lot["event_fills"].append(event_fill(ts, qty, px, False, charged, oid_str,
                                                gross=offset_pl if any(m.get(k) not in (None, "") for k in
                                                                       ("real_profit", "offset_profitloss", "profit")) else None,
                                                qty_unit=unit, role=role_str))
            lot["exits"].append(
                {
                    "qty": min(qty, rem_open_q) if qty > rem_open_q + 1e-9 else qty,
                    "px": px,
                    "notional": ex_notional * (rem_open_q / qty) if qty > rem_open_q + 1e-9 else ex_notional,
                    "fee_known": charged is not None,
                    "fee_paid": fee_paid * (rem_open_q / qty) if qty > rem_open_q + 1e-9 else fee_paid,
                    "maker_rebate": maker_rebate * (rem_open_q / qty) if qty > rem_open_q + 1e-9 else maker_rebate,
                    "offset_pl": offset_pl,
                    "vol": vol * (rem_open_q / qty) if qty > rem_open_q + 1e-9 else vol,
                    "has_turnover": turnover > 0,
                    "is_maker": is_maker,
                    "ts": ts,
                    "oid": oid_str,
                    "mid_id": mid_id,
                }
            )
            lot["closed_qty"] += min(qty, rem_open_q)
            if float(lot["qty"]) - float(lot["closed_qty"]) <= 1e-9:
                _flush_htx_lot(sym, trade_side, lot, full_close=True)
                open_lots.pop((sym, trade_side), None)
        else:
            en_px = (px - offset_pl / qty) if trade_side == "LONG" else (px + offset_pl / qty)
            if en_px <= 0:
                en_px = px
            _emit_htx_record(
                sym=sym,
                trade_side=trade_side,
                qty=qty,
                contract_count=vol if turnover > 0 else None,
                open_ms=ts,
                close_ms=ts,
                en_px=en_px,
                ex_px=px,
                en_notional=en_px * qty,
                ex_notional=ex_notional,
                en_fee=0.0,
                ex_fee=fee_paid,
                en_reb=0.0,
                ex_reb=maker_rebate + (fee_paid * rebate_ratio),
                en_known=False,
                ex_known=charged is not None,
                net_known=charged is not None,
                en_makers=False,
                en_takers=False,
                ex_makers=is_maker,
                ex_takers=not is_maker,
                offset_pl=offset_pl,
                mid_id=mid_id,
                is_partial=False,
            )

    for (sym_rem, side_rem), lot_rem in list(open_lots.items()):
        if lot_rem.get("exits"):
            _flush_htx_lot(sym_rem, side_rem, lot_rem, full_close=False)

    records.sort(key=lambda r: (r["close_time_ms"], r["open_time_ms"]), reverse=True)
    return records
