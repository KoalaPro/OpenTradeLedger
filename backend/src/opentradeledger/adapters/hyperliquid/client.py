"""Strictly read-only Hyperliquid Info API client (`0x` public wallet address, zero private keys).

Security guarantee:
  - Only `POST /info` with explicitly whitelisted read-only `type` values is permitted.
  - `/exchange` (order/cancel/withdraw) and any signed action payloads are hard-blocked by `readonly_guard`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import time
from typing import Any, Callable, Optional

import httpx

from opentradeledger.core.account_partition import AccountIdentityError, normalize_evm_address
from opentradeledger.security.readonly_guard import assert_readonly_request

TZ_UTC8 = timezone(timedelta(hours=8))

HYPERLIQUID_ALLOWED_POST_PATHS: tuple[str, ...] = ("/info",)
HYPERLIQUID_ALLOWED_INFO_TYPES: tuple[str, ...] = (
    "clearinghouseState",
    "spotClearinghouseState",
    "userFills",
    "userFillsByTime",
    "userFunding",
    "userNonFundingLedgerUpdates",
    "userVaultEquities",
    "subAccounts",
    "allMids",
    "meta",
    "spotMeta",
)
HYPERLIQUID_ALLOWED_POST_TYPES: tuple[str, ...] = HYPERLIQUID_ALLOWED_INFO_TYPES


class HyperliquidApiError(RuntimeError):
    """Raised when Hyperliquid `/info` returns an HTTP error or unexpected payload."""


def _is_valid_hl_num(val: Any) -> bool:
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


def _fmt_bj_ms(ms: int) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=TZ_UTC8).strftime("%m-%d %H:%M")


class HyperliquidReadOnlyClient:
    """Zero-secret read-only client querying Hyperliquid L1 public `/info` state by `0x` address."""

    def __init__(
        self,
        *,
        wallet_address: str,
        base_url: str = "https://api.hyperliquid.xyz",
        client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 14.0,
        clock_ms: Optional[Callable[[], int]] = None,
        max_fill_pages: int = 10,
    ) -> None:
        norm_addr = normalize_evm_address(wallet_address)
        if not norm_addr:
            raise AccountIdentityError("INVALID_WALLET_ADDRESS")
        self.wallet_address = norm_addr
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec, connect=6.0))
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.max_fill_pages = max(1, int(max_fill_pages))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request_info(self, payload: dict[str, Any], *, method: str = "POST", path: str = "/info") -> Any:
        assert_readonly_request(
            method,
            path,
            (),
            allowed_post_paths=HYPERLIQUID_ALLOWED_POST_PATHS,
            allowed_post_types=HYPERLIQUID_ALLOWED_INFO_TYPES,
            post_payload=payload,
        )
        url = f"{self.base_url}{path}"
        resp = await self._client.post(url, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code >= 400:
            raise HyperliquidApiError(f"Hyperliquid POST {path} HTTP {resp.status_code}: {resp.text[:240]}")
        return resp.json()

    async def get_clearinghouse_state(self) -> dict[str, Any]:
        res = await self.request_info({"type": "clearinghouseState", "user": self.wallet_address})
        if (
            not isinstance(res, dict)
            or not isinstance(res.get("marginSummary"), dict)
            or "accountValue" not in res["marginSummary"]
            or not _is_valid_hl_num(res["marginSummary"].get("accountValue"))
            or ("assetPositions" in res and not isinstance(res.get("assetPositions"), list))
        ):
            raise HyperliquidApiError("Malformed clearinghouseState payload")
        return res

    async def get_spot_state(self) -> dict[str, Any]:
        res = await self.request_info({"type": "spotClearinghouseState", "user": self.wallet_address})
        if not isinstance(res, dict) or "balances" not in res or not isinstance(res["balances"], list):
            raise HyperliquidApiError("Malformed spotClearinghouseState payload")
        for bal in res["balances"]:
            if (
                not isinstance(bal, dict)
                or not isinstance(bal.get("coin"), str)
                or not bal["coin"].strip()
                or "total" not in bal
                or not _is_valid_hl_num(bal.get("total"))
            ):
                raise HyperliquidApiError("Malformed spotClearinghouseState payload")
        return res

    async def get_subaccounts(self) -> list[dict[str, str]]:
        res = await self.request_info({"type": "subAccounts", "user": self.wallet_address})
        if not isinstance(res, list):
            return []
        out: list[dict[str, str]] = []
        for row in res:
            if not isinstance(row, dict):
                continue
            addr = normalize_evm_address(str(row.get("subAccountUser") or ""))
            if not addr or addr == self.wallet_address:
                continue
            name = str(row.get("name") or "").strip() or f"Sub {addr[:6]}...{addr[-4:]}"
            ch = row.get("clearinghouseState") if isinstance(row.get("clearinghouseState"), dict) else {}
            ms = ch.get("marginSummary") if isinstance(ch.get("marginSummary"), dict) else {}
            eq_str = str(ms.get("accountValue") or "0")
            out.append({"name": name, "address": addr, "equity_usd": eq_str})
        return out

    async def get_raw_all_mids(self) -> dict[str, float]:
        res = await self.request_info({"type": "allMids"})
        if not isinstance(res, dict):
            return {}
        out: dict[str, float] = {}
        for k, v in res.items():
            raw_k = str(k or "").strip()
            if not raw_k:
                continue
            norm_k = raw_k if raw_k.startswith("@") else raw_k.upper()
            try:
                px = float(v)
                if px > 0:
                    out[norm_k] = px
            except (TypeError, ValueError):
                continue
        return out

    async def get_all_mids(self) -> dict[str, float]:
        raw = await self.get_raw_all_mids()
        return {k: v for k, v in raw.items() if not k.startswith("@") and "/" not in k}

    async def get_spot_meta(self) -> dict[str, Any]:
        res = await self.request_info({"type": "spotMeta"})
        return res if isinstance(res, dict) else {}

    async def get_vault_equities(self) -> list[dict[str, Any]]:
        res = await self.request_info({"type": "userVaultEquities", "user": self.wallet_address})
        if not isinstance(res, list):
            raise HyperliquidApiError("Malformed userVaultEquities payload")
        out: list[dict[str, Any]] = []
        for r in res:
            if not isinstance(r, dict) or "equity" not in r or not _is_valid_hl_num(r.get("equity")):
                raise HyperliquidApiError("Malformed userVaultEquities payload")
            out.append(r)
        return out

    @staticmethod
    def _fill_unique_key(r: dict[str, Any]) -> str:
        return str(r.get("tid") or r.get("hash") or f"{r.get('oid')}_{r.get('time')}_{r.get('coin')}")

    async def get_user_fills(
        self,
        *,
        since_ms: int = 0,
        warnings_out: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        collected: dict[str, dict[str, Any]] = {}

        if since_ms <= 0:
            res = await self.request_info({"type": "userFills", "user": self.wallet_address})
            first_page = [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []
            first_ts_list: list[int] = []
            for r in first_page:
                collected[self._fill_unique_key(r)] = r
                rts = int(r.get("time") or 0)
                if rts > 0:
                    first_ts_list.append(rts)

            if len(first_page) < 2000 or not first_ts_list:
                return list(collected.values())

            boundary_ts = min(first_ts_list)
            first_at_boundary_keys = {
                self._fill_unique_key(r)
                for r in first_page
                if int(r.get("time") or 0) == boundary_ts
            }

            truncated = False
            same_ms_res = await self.request_info(
                {
                    "type": "userFillsByTime",
                    "user": self.wallet_address,
                    "startTime": boundary_ts,
                    "endTime": boundary_ts,
                }
            )
            same_ms_rows = [r for r in same_ms_res if isinstance(r, dict)] if isinstance(same_ms_res, list) else []
            same_ms_keys: set[str] = set()
            for r in same_ms_rows:
                fk = self._fill_unique_key(r)
                collected[fk] = r
                if int(r.get("time") or 0) == boundary_ts:
                    same_ms_keys.add(fk)

            if len(same_ms_rows) >= 2000 and not (first_at_boundary_keys & same_ms_keys):
                truncated = True

            low_bound = 0
            high_bound = boundary_ts - 1
            for page_idx in range(self.max_fill_pages):
                if high_bound < low_bound:
                    break
                page_res = await self.request_info(
                    {
                        "type": "userFillsByTime",
                        "user": self.wallet_address,
                        "startTime": low_bound,
                        "endTime": high_bound,
                    }
                )
                valid_rows = [r for r in page_res if isinstance(r, dict)] if isinstance(page_res, list) else []
                if not valid_rows:
                    if page_idx == 0 and not same_ms_rows:
                        truncated = True
                    break

                new_cnt = 0
                page_ts_list: list[int] = []
                for r in valid_rows:
                    fk = self._fill_unique_key(r)
                    if fk not in collected:
                        collected[fk] = r
                        new_cnt += 1
                    rts = int(r.get("time") or 0)
                    page_ts_list.append(rts)

                if len(valid_rows) < 2000 or new_cnt == 0 or not page_ts_list:
                    break

                p_min = min(page_ts_list)
                p_max = max(page_ts_list)
                if p_min == p_max:
                    truncated = True
                    low_bound = p_max + 1
                else:
                    low_bound = p_max

                if page_idx == self.max_fill_pages - 1 and low_bound <= high_bound:
                    truncated = True
                    break

            if truncated and warnings_out is not None:
                warnings_out.append(
                    "Hyperliquid userFills reached 2,000 per-page Info API cap; older trades may be truncated."
                )
            return list(collected.values())

        cursor_start = max(0, int(since_ms))
        truncated = False
        for page_idx in range(self.max_fill_pages):
            res = await self.request_info(
                {
                    "type": "userFillsByTime",
                    "user": self.wallet_address,
                    "startTime": cursor_start,
                }
            )
            valid_rows = [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []
            if not valid_rows:
                break
            page_ts_list: list[int] = []
            new_cnt = 0
            for r in valid_rows:
                fk = self._fill_unique_key(r)
                if fk not in collected:
                    collected[fk] = r
                    new_cnt += 1
                rts = int(r.get("time") or 0)
                page_ts_list.append(rts)
            if len(valid_rows) < 2000 or new_cnt == 0 or not page_ts_list:
                break
            if page_idx == self.max_fill_pages - 1:
                truncated = True
                break
            max_ts = max(page_ts_list)
            min_ts = min(page_ts_list)
            if max_ts > cursor_start and min_ts < max_ts:
                cursor_start = max_ts
            else:
                cursor_start = max(cursor_start, max_ts) + 1

        if truncated and warnings_out is not None:
            warnings_out.append(
                "Hyperliquid userFillsByTime reached 2,000 per-page pagination cap during incremental sync."
            )
        return list(collected.values())

    async def get_user_funding(
        self,
        *,
        since_ms: int = 0,
        max_pages: int = 40,
        warnings_out: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        collected: dict[str, dict[str, Any]] = {}
        cursor_start = max(0, int(since_ms)) if since_ms > 0 else 0
        truncated = False

        for page_idx in range(max(1, int(max_pages))):
            res = await self.request_info(
                {"type": "userFunding", "user": self.wallet_address, "startTime": cursor_start}
            )
            valid_rows = [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []
            if not valid_rows:
                break

            new_cnt = 0
            page_ts_list: list[int] = []
            for r in valid_rows:
                delta = r.get("delta") if isinstance(r.get("delta"), dict) else r
                ts = int(r.get("time") or delta.get("time") or 0)
                coin = str(delta.get("coin") or r.get("coin") or "").strip().upper()
                usdc = str(delta.get("usdc") or r.get("usdc") or "")
                fk = str(r.get("hash") or f"{ts}_{coin}_{usdc}")
                if fk not in collected:
                    collected[fk] = r
                    new_cnt += 1
                page_ts_list.append(ts)

            if len(valid_rows) < 500 or new_cnt == 0 or not page_ts_list:
                break
            if page_idx == max(1, int(max_pages)) - 1:
                truncated = True
                break

            max_ts = max(page_ts_list)
            min_ts = min(page_ts_list)
            if max_ts > cursor_start and min_ts < max_ts:
                cursor_start = max_ts
            else:
                cursor_start = max(cursor_start, max_ts) + 1

        if truncated and warnings_out is not None:
            warnings_out.append(
                f"Hyperliquid userFunding reached {max_pages}-page (500/page) pagination cap; older funding fees may be truncated."
            )
        return list(collected.values())

    async def get_ledger_updates(
        self,
        *,
        since_ms: int = 0,
        max_pages: int = 40,
        warnings_out: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        collected: dict[str, dict[str, Any]] = {}
        cursor_start = max(0, int(since_ms)) if since_ms > 0 else 0
        truncated = False

        for page_idx in range(max(1, int(max_pages))):
            res = await self.request_info(
                {"type": "userNonFundingLedgerUpdates", "user": self.wallet_address, "startTime": cursor_start}
            )
            valid_rows = [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []
            if not valid_rows:
                break

            new_cnt = 0
            page_ts_list: list[int] = []
            for r in valid_rows:
                delta = r.get("delta") if isinstance(r.get("delta"), dict) else r
                ts = int(r.get("time") or delta.get("time") or 0)
                tp = str(delta.get("type") or "")
                amt = str(delta.get("usdc") or delta.get("amount") or "")
                lk = str(r.get("hash") or f"{ts}_{tp}_{amt}")
                if lk not in collected:
                    collected[lk] = r
                    new_cnt += 1
                page_ts_list.append(ts)

            if len(valid_rows) < 500 or new_cnt == 0 or not page_ts_list:
                break
            if page_idx == max(1, int(max_pages)) - 1:
                truncated = True
                break

            max_ts = max(page_ts_list)
            min_ts = min(page_ts_list)
            if max_ts > cursor_start and min_ts < max_ts:
                cursor_start = max_ts
            else:
                cursor_start = max(cursor_start, max_ts) + 1

        if truncated and warnings_out is not None:
            warnings_out.append(
                f"Hyperliquid userNonFundingLedgerUpdates reached {max_pages}-page (500/page) pagination cap."
            )
        return list(collected.values())

    async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
        warnings: list[str] = []
        failed_fields: list[str] = []
        clearinghouse = await self.get_clearinghouse_state()

        async def _opt_aux(name: str, coro: Any, default: Any) -> Any:
            try:
                return await coro
            except Exception as exc:
                msg_low = str(exc).lower()
                if isinstance(exc, httpx.TransportError) or "http 5" in msg_low or "http 429" in msg_low:
                    raise
                warnings.append(f"{name}: {exc}")
                failed_fields.append(name)
                return default

        spot = await _opt_aux("spot", self.get_spot_state(), {"balances": []})
        raw_mids = await self.get_raw_all_mids()
        fills = await self.get_user_fills(since_ms=since_ms, warnings_out=warnings)
        funding = await self.get_user_funding(since_ms=since_ms, warnings_out=warnings)
        ledger_updates = await self.get_ledger_updates(since_ms=since_ms, warnings_out=warnings)

        spot_meta = await _opt_aux("spot_meta", self.get_spot_meta(), {})
        subaccounts = await _opt_aux("subaccounts", self.get_subaccounts(), [])
        vault_equities = await _opt_aux("vault_equities", self.get_vault_equities(), [])

        mids = {k: v for k, v in raw_mids.items() if not k.startswith("@") and "/" not in k}
        spot_token_prices, spot_tickers, has_spot_meta = build_hyperliquid_spot_prices(raw_mids, spot_meta)
        has_at_mids = any(k.startswith("@") for k in raw_mids)
        if "spot_meta" in failed_fields:
            failed_fields.extend(["spot_tickers", "spot_token_prices", "has_spot_meta"])

        out: dict[str, Any] = {
            "venue_id": "hyperliquid",
            "wallet_address": self.wallet_address,
            "clearinghouse": clearinghouse,
            "spot": spot,
            "vault_equities": vault_equities,
            "tickers": mids,
            "spot_tickers": spot_tickers,
            "spot_token_prices": spot_token_prices,
            "has_spot_meta": has_spot_meta,
            "has_at_mids": has_at_mids,
            "fills": fills,
            "funding_history": funding,
            "ledger_updates": ledger_updates,
            "subaccounts": subaccounts,
            "warnings": warnings,
        }
        if failed_fields:
            out["_failed_fields"] = failed_fields
        return out


_HL_QUOTE_STABLES = {"USDC", "USDT", "USDH", "USD"}


def build_hyperliquid_spot_prices(
    raw_mids: dict[str, float],
    spot_meta: dict[str, Any] | None,
) -> tuple[dict[str, float], dict[str, float], bool]:
    spot_by_token: dict[str, float] = {}
    spot_by_coin: dict[str, float] = {}
    has_spot_meta = False

    for k, px in raw_mids.items():
        if "/" in k and px > 0:
            base_part, quote_part = k.split("/", 1)
            if base_part.strip() and quote_part.strip().upper() in _HL_QUOTE_STABLES:
                spot_by_coin[base_part.strip().upper()] = float(px)

    if isinstance(spot_meta, dict):
        tokens = spot_meta.get("tokens")
        universe = spot_meta.get("universe")
        if isinstance(tokens, list) and isinstance(universe, list) and universe:
            has_spot_meta = True
            idx_to_name: dict[int, str] = {}
            stable_quote_indices: set[int] = {0}
            for tok in tokens:
                if not isinstance(tok, dict) or tok.get("index") is None:
                    continue
                t_idx = int(tok["index"])
                t_name = str(tok.get("name") or "").strip().upper()
                if t_name:
                    idx_to_name[t_idx] = t_name
                    if t_name in _HL_QUOTE_STABLES:
                        stable_quote_indices.add(t_idx)

            for pair in universe:
                if not isinstance(pair, dict):
                    continue
                ptoks = pair.get("tokens")
                if not isinstance(ptoks, list) or len(ptoks) < 2:
                    continue
                base_idx, quote_idx = int(ptoks[0]), int(ptoks[1])
                if quote_idx not in stable_quote_indices:
                    continue
                p_name = str(pair.get("name") or "").strip()
                p_idx = pair.get("index")
                px = raw_mids.get(p_name) or raw_mids.get(p_name.upper())
                if px is None and p_idx is not None:
                    px = raw_mids.get(f"@{p_idx}")
                if px is None:
                    b_name = idx_to_name.get(base_idx, "")
                    if b_name:
                        px = raw_mids.get(f"{b_name}/USDC")
                if px is not None and float(px) > 0:
                    key_tok = str(base_idx)
                    if key_tok not in spot_by_token or quote_idx == 0:
                        spot_by_token[key_tok] = float(px)
                        b_name = idx_to_name.get(base_idx, "")
                        if b_name:
                            spot_by_coin[b_name] = float(px)

    return spot_by_token, spot_by_coin, has_spot_meta


def resolve_hyperliquid_spot_price(bal: dict[str, Any], snap: dict[str, Any]) -> float:
    ccy = str(bal.get("coin") or "").strip().upper()
    if not ccy:
        return 0.0
    if ccy in _HL_QUOTE_STABLES:
        return 1.0
    spot_tok_px = snap.get("spot_token_prices") if isinstance(snap.get("spot_token_prices"), dict) else {}
    spot_coin_px = snap.get("spot_tickers") if isinstance(snap.get("spot_tickers"), dict) else {}
    tok_idx = bal.get("token")
    if tok_idx is not None and str(tok_idx) in spot_tok_px:
        try:
            return max(0.0, float(spot_tok_px[str(tok_idx)] or 0.0))
        except (TypeError, ValueError):
            return 0.0
    if ccy in spot_coin_px:
        try:
            return max(0.0, float(spot_coin_px[ccy] or 0.0))
        except (TypeError, ValueError):
            return 0.0
    if not snap.get("has_spot_meta") and not snap.get("has_at_mids") and tok_idx is None:
        tickers = snap.get("tickers") if isinstance(snap.get("tickers"), dict) else {}
        try:
            return max(0.0, float(tickers.get(ccy) or 0.0))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _norm_hl_symbol(raw_coin: str) -> str:
    c = (raw_coin or "").strip()
    if not c:
        return "UNKNOWN_USDC"
    if ":" in c:
        c = c.split(":", 1)[1]
    c_up = c.upper()
    if "_" in c_up:
        return c_up
    if c_up.endswith(("USDC", "USDT", "USD")) and len(c_up) > 4:
        return f"{c_up[:-4]}_{c_up[-4:]}"
    return f"{c_up}_USDC"


def normalize_hyperliquid_closed_trades(
    fills: list[dict[str, Any]],
    funding_rows: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Reconstructs closed perpetual trades with exact entry/exit fees, Maker rebates (`fee < 0`), and funding."""
    sorted_fills = sorted(
        (f for f in fills if isinstance(f, dict) and not str(f.get("coin") or "").startswith("@")),
        key=lambda f: (int(f.get("time") or 0), int(f.get("tid") or 0)),
    )

    funding_by_coin: dict[str, list[dict[str, Any]]] = {}
    consumed_funding: set[str] = set()
    for idx, fr in enumerate(funding_rows or []):
        if not isinstance(fr, dict):
            continue
        delta = fr.get("delta") if isinstance(fr.get("delta"), dict) else fr
        coin = str(delta.get("coin") or fr.get("coin") or "").strip().upper()
        if not coin:
            continue
        ts = int(fr.get("time") or delta.get("time") or 0)
        usdc = float(delta.get("usdc") or fr.get("usdc") or 0.0)
        fid = str(fr.get("hash") or f"fund_{coin}_{ts}_{idx}")
        funding_by_coin.setdefault(coin, []).append({"_id": fid, "time": ts, "usdc": usdc})

    open_lots: dict[str, dict[str, float]] = {}
    records: list[dict[str, Any]] = []

    for idx, fill in enumerate(sorted_fills):
        raw_coin = str(fill.get("coin") or "").strip()
        if not raw_coin or "/" in raw_coin:
            continue
        coin_key = raw_coin.upper()
        px = float(fill.get("px") or 0.0)
        sz = abs(float(fill.get("sz") or 0.0))
        if px <= 0 or sz <= 0:
            continue
        ts = int(fill.get("time") or 0)
        raw_fee = float(fill.get("fee") or 0.0)
        fee_paid = max(0.0, raw_fee)
        maker_rebate = max(0.0, -raw_fee)
        crossed = bool(fill.get("crossed", True))
        dir_str = str(fill.get("dir") or "").strip()
        closed_pnl = float(fill.get("closedPnl") or 0.0)
        side_tok = str(fill.get("side") or "").upper()

        is_close = ("Close" in dir_str) or (">" in dir_str) or (abs(closed_pnl) > 1e-9)
        if not is_close:
            lot = open_lots.setdefault(
                coin_key,
                {
                    "qty": 0.0,
                    "notional": 0.0,
                    "gross_fee": 0.0,
                    "rebate": 0.0,
                    "open_ms": float(ts),
                    "maker_notional": 0.0,
                },
            )
            if lot["qty"] <= 1e-12:
                lot["open_ms"] = float(ts)
            lot["qty"] += sz
            lot["notional"] += px * sz
            lot["gross_fee"] += fee_paid
            lot["rebate"] += maker_rebate
            if not crossed:
                lot["maker_notional"] += px * sz
            continue

        # Determine closed trade direction: closing Long sells ("A"), closing Short buys ("B")
        if "Long" in dir_str and not dir_str.startswith("Short >"):
            trade_side = "LONG"
        elif "Short" in dir_str and not dir_str.startswith("Long >"):
            trade_side = "SHORT"
        else:
            trade_side = "LONG" if side_tok in {"A", "SELL"} else "SHORT"

        lot = open_lots.get(coin_key)
        if lot and lot["qty"] > 1e-12:
            alloc_ratio = min(1.0, sz / lot["qty"])
            open_ms = int(lot["open_ms"])
            entry_notional = lot["notional"] * alloc_ratio
            entry_px = (lot["notional"] / lot["qty"]) if lot["qty"] > 0 else px
            entry_gross_fee = lot["gross_fee"] * alloc_ratio
            entry_rebate = lot["rebate"] * alloc_ratio
            entry_is_maker = lot["maker_notional"] >= (lot["notional"] * 0.5)
            lot["qty"] = max(0.0, lot["qty"] - sz)
            lot["notional"] = max(0.0, lot["notional"] - entry_notional)
            lot["gross_fee"] = max(0.0, lot["gross_fee"] - entry_gross_fee)
            lot["rebate"] = max(0.0, lot["rebate"] - entry_rebate)
            lot["maker_notional"] = max(0.0, lot["maker_notional"] * (1.0 - alloc_ratio))
        else:
            open_ms = ts
            # Infer entry price from closedPnl when opening fill predates history window
            if sz > 0:
                entry_px = (px - closed_pnl / sz) if trade_side == "LONG" else (px + closed_pnl / sz)
                if entry_px <= 0:
                    entry_px = px
            else:
                entry_px = px
            entry_notional = entry_px * sz
            entry_gross_fee = 0.0
            entry_rebate = 0.0
            entry_is_maker = False

        exit_notional = px * sz
        exit_gross_fee = fee_paid
        exit_rebate = maker_rebate
        exit_is_maker = not crossed

        funding_pnl = 0.0
        for fl in funding_by_coin.get(coin_key, []):
            if fl["_id"] in consumed_funding:
                continue
            if open_ms - 5_000 <= fl["time"] <= ts + 5_000:
                consumed_funding.add(fl["_id"])
                funding_pnl += float(fl["usdc"])

        total_fee = round(entry_gross_fee + exit_gross_fee, 6)
        total_rebate = round(entry_rebate + exit_rebate, 6)
        net_fee = round(total_fee - total_rebate, 6)
        book_pnl = round(closed_pnl - total_fee + funding_pnl, 6)
        net_pnl = round(book_pnl + total_rebate, 6)

        entry_bps = (entry_gross_fee / entry_notional * 10_000) if entry_notional > 0 else 0.0
        exit_bps = (exit_gross_fee / exit_notional * 10_000) if exit_notional > 0 else 0.0
        tid_str = str(fill.get("tid") or fill.get("oid") or idx)
        sym = _norm_hl_symbol(raw_coin)
        settled_str = _fmt_bj_ms(ts) if total_rebate > 0 else ""

        records.append(
            {
                "trade_id": f"hl_{tid_str}_{ts}",
                "venue_id": "hyperliquid",
                "venue_name": "Hyperliquid",
                "venue_name_zh": "Hyperliquid",
                "symbol": sym,
                "side": trade_side,
                "qty": sz,
                "open_time_ms": open_ms,
                "close_time_ms": ts,
                "gross_pnl_usd": round(closed_pnl, 4),
                "funding_pnl_usd": round(funding_pnl, 4),
                "book_pnl_usd": round(book_pnl, 4),
                "total_fee_usd": round(total_fee, 4),
                "total_rebate_usd": round(total_rebate, 4),
                "total_discount_saved_usd": 0.0,
                "net_fee_usd": round(max(0.0, net_fee), 4),
                "net_pnl_usd": round(net_pnl, 4),
                "rebate_status": "SETTLED",
                "is_partial_close": bool(lot and lot["qty"] > 1e-9),
                "entry_leg": {
                    "role": "MAKER" if entry_is_maker else "TAKER",
                    "mode_code": "LIMIT_MAKER" if entry_is_maker else "MARKET_TAKER",
                    "fee_known": lot is not None,
                    "fee_bps": round(entry_bps, 2) if lot is not None else None,
                    "time_ms": open_ms,
                    "avg_price": round(entry_px, 6),
                    "notional_usd": round(entry_notional, 2),
                    "gross_fee_usd": round(entry_gross_fee, 4),
                    "discount_saved_usd": 0.0,
                    "discount_asset": "",
                    "rebate_usd": round(entry_rebate, 4),
                    "rebate_pct": 100.0 if entry_rebate > 0 and entry_gross_fee == 0 else (
                        round((entry_rebate / entry_gross_fee) * 100.0, 2) if entry_gross_fee > 0 else 0.0
                    ),
                    "net_fee_usd": round(max(0.0, entry_gross_fee - entry_rebate), 4),
                    "rebate_status": "SETTLED",
                    "settled_at": settled_str,
                },
                "exit_leg": {
                    "role": "MAKER" if exit_is_maker else "TAKER",
                    "mode_code": "LIMIT_MAKER" if exit_is_maker else "MARKET_TAKER",
                    "fee_known": True,
                    "fee_bps": round(exit_bps, 2),
                    "time_ms": ts,
                    "avg_price": round(px, 6),
                    "notional_usd": round(exit_notional, 2),
                    "gross_fee_usd": round(exit_gross_fee, 4),
                    "discount_saved_usd": 0.0,
                    "discount_asset": "",
                    "rebate_usd": round(exit_rebate, 4),
                    "rebate_pct": 100.0 if exit_rebate > 0 and exit_gross_fee == 0 else (
                        round((exit_rebate / exit_gross_fee) * 100.0, 2) if exit_gross_fee > 0 else 0.0
                    ),
                    "net_fee_usd": round(max(0.0, exit_gross_fee - exit_rebate), 4),
                    "rebate_status": "SETTLED",
                    "settled_at": settled_str,
                },
            }
        )

    records.sort(key=lambda r: (r["close_time_ms"], r["open_time_ms"]), reverse=True)
    return records
