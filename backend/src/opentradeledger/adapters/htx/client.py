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
from typing import Any, Callable, Optional
from urllib.parse import urlencode, urlsplit

import httpx

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
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.spot_base_url = spot_base_url.rstrip("/")
        self.swap_base_url = swap_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec, connect=6.0))
        self._clock_utc = clock_utc or _default_htx_utc_ts

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
        st = str(payload.get("status") or "").lower()
        code = payload.get("code")
        if (st and st != "ok") or (code is not None and int(code) != 200):
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
            return []
        out: list[dict[str, Any]] = []
        for acct in accts:
            if not isinstance(acct, dict):
                continue
            aid = acct.get("id")
            atype = str(acct.get("type") or "spot").upper()
            if not aid or atype not in {"SPOT", "POINT"}:
                continue
            bal_res = await self.spot_get(f"/v1/account/accounts/{aid}/balance")
            lst = bal_res.get("list") if isinstance(bal_res, dict) else []
            for item in lst or []:
                if isinstance(item, dict) and float(item.get("balance") or 0.0) > 0:
                    out.append({**item, "_account_type": atype})
        return out

    async def get_swap_accounts(self) -> list[dict[str, Any]]:
        res = await self.swap_query("/linear-swap-api/v1/swap_cross_account_info", {"margin_account": "USDT"})
        return [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []

    async def get_swap_positions(self) -> list[dict[str, Any]]:
        res = await self.swap_query("/linear-swap-api/v1/swap_cross_position_info", {})
        return [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []

    async def get_swap_matches(self, *, since_ms: int = 0) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"trade_type": 0, "size": 50}
        if since_ms > 0:
            body["start_time"] = since_ms
        try:
            res = await self.swap_query("/linear-swap-api/v1/swap_cross_matchresults_exact", body)
        except HtxApiError as exc:
            msg_low = str(exc).lower()
            if "http 404" in msg_low or "not found" in msg_low or "unsupported" in msg_low:
                res = await self.swap_query(
                    "/linear-swap-api/v1/swap_cross_matchresults",
                    {"trade_type": 0, "create_date": 90, "page_index": 1, "page_size": 50},
                )
            else:
                raise
        trades = res.get("trades") if isinstance(res, dict) else (res if isinstance(res, list) else [])
        return [r for r in trades if isinstance(r, dict)] if isinstance(trades, list) else []

    async def get_swap_financial_records(self, *, since_ms: int = 0) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"margin_account": "USDT", "size": 50}
        if since_ms > 0:
            body["start_time"] = since_ms
        res = await self.swap_query("/linear-swap-api/v1/swap_financial_record_exact", body)
        rows = res.get("financial_record") if isinstance(res, dict) else (res if isinstance(res, list) else [])
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    async def get_deposit_withdraw(self, kind: str) -> list[dict[str, Any]]:
        res = await self.spot_get("/v1/query/deposit-withdraw", {"type": kind, "size": "200", "direct": "next"})
        return [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []

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
        matches = await _opt("matches", self.get_swap_matches(since_ms=since_ms), [])
        financials = await _opt("financials", self.get_swap_financial_records(since_ms=since_ms), [])
        deposits = await _opt("deposits", self.get_deposit_withdraw("deposit"), [])
        withdrawals = await _opt("withdrawals", self.get_deposit_withdraw("withdraw"), [])
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

    total_fees_raw = sum(abs(float(m.get("trade_fee") or 0.0)) for m in unique_matches if float(m.get("trade_fee") or 0.0) < 0)
    rebate_ratio = min(0.95, rebate_sum / total_fees_raw) if total_fees_raw > 0 and rebate_sum > 0 else 0.0

    sorted_matches = sorted(
        unique_matches,
        key=lambda r: (int(r.get("create_date") or r.get("ts") or 0), htx_match_unique_key(r)),
    )

    open_lots: dict[tuple[str, str], dict[str, float]] = {}
    records: list[dict[str, Any]] = []

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
        raw_fee = float(m.get("trade_fee") or 0.0)
        # In HTX swap_matchresults: negative trade_fee = fee paid, positive trade_fee = Maker rebate!
        fee_paid = abs(raw_fee) if raw_fee < 0 else 0.0
        maker_rebate = raw_fee if raw_fee > 0 else 0.0
        role_str = str(m.get("role") or "").lower()
        is_maker = role_str == "maker"
        offset = str(m.get("offset") or "").lower()
        direction = str(m.get("direction") or "").lower()
        offset_pl = htx_match_realized_pnl(m)

        is_open = offset == "open" and abs(offset_pl) <= 1e-9
        if is_open:
            trade_side = "LONG" if direction == "buy" else "SHORT"
            lot = open_lots.setdefault(
                (sym, trade_side),
                {"qty": 0.0, "notional": 0.0, "fee": 0.0, "rebate": 0.0, "open_ms": float(ts), "maker_notional": 0.0},
            )
            if lot["qty"] <= 1e-12:
                lot["open_ms"] = float(ts)
            notional_m = turnover if turnover > 0 else (px * qty)
            lot["qty"] += qty
            lot["notional"] += notional_m
            lot["fee"] += fee_paid
            lot["rebate"] += maker_rebate
            if is_maker:
                lot["maker_notional"] += notional_m
            continue

        # Closing leg: closing a LONG sells ("sell"), closing a SHORT buys ("buy")
        trade_side = "LONG" if direction == "sell" else "SHORT"
        lot = open_lots.get((sym, trade_side))
        if lot and lot["qty"] > 1e-12:
            ratio = min(1.0, qty / lot["qty"])
            open_ms = int(lot["open_ms"])
            en_notional = lot["notional"] * ratio
            en_px = lot["notional"] / lot["qty"]
            en_fee = lot["fee"] * ratio
            en_reb = lot["rebate"] * ratio + (en_fee * rebate_ratio)
            en_maker = lot["maker_notional"] >= (lot["notional"] * 0.5)
            lot["qty"] = max(0.0, lot["qty"] - qty)
            lot["notional"] = max(0.0, lot["notional"] - en_notional)
            lot["fee"] = max(0.0, lot["fee"] - en_fee)
            lot["rebate"] = max(0.0, lot["rebate"] * (1.0 - ratio))
            lot["maker_notional"] = max(0.0, lot["maker_notional"] * (1.0 - ratio))
            en_known = True
        else:
            open_ms = ts
            en_px = (px - offset_pl / qty) if trade_side == "LONG" else (px + offset_pl / qty)
            if en_px <= 0:
                en_px = px
            en_notional = en_px * qty
            en_fee = 0.0
            en_reb = 0.0
            en_maker = False
            en_known = False

        ex_notional = turnover if turnover > 0 else (px * qty)
        ex_fee = fee_paid
        ex_reb = maker_rebate + (ex_fee * rebate_ratio)

        funding_pnl = 0.0
        for fl in funding_by_sym.get(sym, []):
            if fl["_id"] in consumed_fund:
                continue
            if open_ms - 5_000 <= fl["ts"] <= ts + 5_000:
                consumed_fund.add(fl["_id"])
                funding_pnl += float(fl["amt"])

        total_fee = round(en_fee + ex_fee, 6)
        total_rebate = round(en_reb + ex_reb, 6)
        book_pnl = round(offset_pl - total_fee + funding_pnl, 6)
        net_pnl = round(book_pnl + total_rebate, 6)
        mid_id = htx_match_unique_key(m, fallback_idx=idx)
        settled_str = _fmt_bj_ms(ts) if total_rebate > 0 else ""

        records.append(
            {
                "trade_id": f"htx_{mid_id}_{ts}",
                "venue_id": "htx",
                "venue_name": "HTX",
                "venue_name_zh": "火币 HTX",
                "symbol": sym,
                "side": trade_side,
                "qty": round(qty, 6),
                "contract_count": vol if turnover > 0 else None,
                "open_time_ms": open_ms,
                "close_time_ms": ts,
                "gross_pnl_usd": round(offset_pl, 4),
                "funding_pnl_usd": round(funding_pnl, 4),
                "book_pnl_usd": round(book_pnl, 4),
                "total_fee_usd": round(total_fee, 4),
                "total_rebate_usd": round(total_rebate, 4),
                "total_discount_saved_usd": 0.0,
                "net_fee_usd": round(max(0.0, total_fee - total_rebate), 4),
                "net_pnl_usd": round(net_pnl, 4),
                "rebate_status": "SETTLED",
                "is_partial_close": bool(lot and lot["qty"] > 1e-9),
                "entry_leg": {
                    "role": ("MAKER" if en_maker else "TAKER") if en_known else "UNKNOWN",
                    "mode_code": ("LIMIT_MAKER" if en_maker else "MARKET_TAKER") if en_known else "UNKNOWN",
                    "fee_known": en_known,
                    "fee_bps": round((en_fee / en_notional) * 10_000, 2) if (en_known and en_notional > 0) else None,
                    "time_ms": open_ms,
                    "avg_price": round(en_px, 6),
                    "notional_usd": round(en_notional, 2),
                    "gross_fee_usd": round(en_fee, 4),
                    "discount_saved_usd": 0.0,
                    "discount_asset": "",
                    "rebate_usd": round(en_reb, 4),
                    "rebate_pct": round((en_reb / en_fee) * 100.0, 2) if en_fee > 0 else (100.0 if en_reb > 0 else 0.0),
                    "net_fee_usd": round(max(0.0, en_fee - en_reb), 4),
                    "rebate_status": "SETTLED",
                    "settled_at": settled_str,
                },
                "exit_leg": {
                    "role": "MAKER" if is_maker else "TAKER",
                    "mode_code": "LIMIT_MAKER" if is_maker else "MARKET_TAKER",
                    "fee_known": True,
                    "fee_bps": round((ex_fee / ex_notional) * 10_000, 2) if ex_notional > 0 else 0.0,
                    "time_ms": ts,
                    "avg_price": round(px, 6),
                    "notional_usd": round(ex_notional, 2),
                    "gross_fee_usd": round(ex_fee, 4),
                    "discount_saved_usd": 0.0,
                    "discount_asset": "",
                    "rebate_usd": round(ex_reb, 4),
                    "rebate_pct": round((ex_reb / ex_fee) * 100.0, 2) if ex_fee > 0 else (100.0 if ex_reb > 0 else 0.0),
                    "net_fee_usd": round(max(0.0, ex_fee - ex_reb), 4),
                    "rebate_status": "SETTLED",
                    "settled_at": settled_str,
                },
            }
        )
    records.sort(key=lambda r: (r["close_time_ms"], r["open_time_ms"]), reverse=True)
    return records
