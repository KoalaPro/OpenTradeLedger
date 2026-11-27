"""Venue-agnostic ledger aggregation service: aggregates standardized `VenueLedgerBundle`s across all active adapters."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal
import logging
import re
import time
from typing import Any

from opentradeledger.adapters.base import BaseExchangeAdapter, VenueLedgerBundle
from opentradeledger.adapters.registry import AdapterRegistry
from opentradeledger.config import UserPreferences
from opentradeledger.core.account_partition import (
    format_account_display_names,
    is_secondary_account_id,
    mask_uid,
    parse_adapter_id,
    scope_journals_for_account,
    stamp_bundle_for_account,
    stamp_fee_row_for_account,
)
from opentradeledger.core.ai_reviewer import build_zero_trade_texts
from opentradeledger.core.demo_provider import build_demo_dashboard
from opentradeledger.core.position_lifecycle import exit_counts_by_day, trade_daily_amounts
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.venue_health import visible_sync_warnings
from opentradeledger.core.venue_primitives import dec, f2, fmt_bj
from opentradeledger.security.vault import CredentialVault

logger = logging.getLogger("opentradeledger.ledger")

_CIRCLED_NUM_RE = re.compile(r"^([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|\(\d+\))\s*")
_CN_NUMS = (
    "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩",
    "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱", "⑲", "⑳",
)

_EN_RECONCILE_TITLE_BY_KEY: dict[str, str] = {
    "futures_net": "Futures Realized Net PnL (incl. Rebates & Funding)",
    "manual_futures": "Gate Manual Futures Net PnL",
    "easicoin_futures": "EasiCoin Futures Net PnL",
    "easicoin_stock": "EasiCoin Tokenized Stocks Net PnL",
    "easicoin_tokens_adj": "EasiCoin Bonus Tokens & Settlement Adj.",
    "binance_futures": "Binance Futures Net PnL",
    "binance_copy_trading": "Binance Copy Trading Combined PnL",
    "binance_stock": "Binance Equities & bStocks Net PnL",
    "binance_early_adj": "Binance Pre-UTA Classic Futures & FX Adj.",
    "binance_fx_spot_adj": "Binance BNB FX, Spot Dust & Fee Adj.",
    "bitget_futures": "Bitget Futures Net PnL",
    "bitget_copy_trading": "Bitget Copy & Bot Strategy Net PnL",
    "bitget_stock": "Bitget Stock+ & rStock Net PnL",
    "bitget_other_net": "Bitget Strategy / Historical Rebate Net",
    "mexc_futures": "MEXC Futures Net PnL",
    "mexc_stock": "MEXC Equities & Tokenized Stocks Net PnL",
    "mexc_other_net": "MEXC Contract Hold Earn & Bonus Offset",
    "okx_futures": "OKX Manual Futures Net PnL",
    "okx_copy_trading": "OKX Copy Trading Net PnL",
    "okx_fee_rebates": "OKX Funding Account Fee Rebates",
    "okx_earn_spot": "OKX Earn, Tokenized Equities & Spot Valuation",
    "bybit_futures": "Bybit Manual Futures Net PnL",
    "bybit_earn_spot": "Bybit Earn, Tokenized Equities & Spot Valuation",
    "hyperliquid_futures": "Hyperliquid Perp Net PnL (incl. Maker Rebates & Funding)",
    "hyperliquid_earn_spot": "Hyperliquid Spot & HLP Valuation",
    "kucoin_futures": "KuCoin Futures Net PnL",
    "kucoin_earn_spot": "KuCoin Spot & Margin Valuation",
    "bingx_futures": "BingX Perpetual Net PnL",
    "bingx_copy_trading": "BingX Copy Trading Net PnL",
    "bingx_earn_spot": "BingX Spot & Wealth Valuation",
    "htx_futures": "HTX Linear Swap Net PnL",
    "htx_earn_spot": "HTX Spot & Earn Valuation",
    "copy_trading": "Gate Copy Trading Combined PnL",
    "stock": "Equities & Tokenized Stocks Net PnL",
    "earn": "Earn & Lending Interest",
    "airdrop_tokens": "Airdrops, Red Packets & Non-USDT Dust",
    "fx_voucher_adj": "FX Swap Spread & Voucher Adjustments",
}


def _translate_zh_phrases(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return s
    s = (
        s.replace("REAL_STOCK", "US Equity")
        .replace("TOKENIZED_STOCK", "1:1 Stock Token")
        .replace("（", " (")
        .replace("）", ")")
        .replace("→", "->")
    )
    if not re.search(r"[\u4e00-\u9fff]", s):
        return re.sub(r"\s+", " ", s).strip()
    replacements = (
        ("全历史", "All-time"),
        ("近 7 天", "7D"),
        ("近 30 天", "30D"),
        ("月度", "Monthly"),
        ("笔平仓单账面", "closed trades book"),
        ("笔平仓账面", "closed trades book"),
        ("笔账面", "trades book"),
        ("平仓及浮盈", "realized & floating"),
        ("手操实时返佣", "live rebates"),
        ("手操返佣", "manual rebates"),
        ("合伙人返佣", "partner rebates"),
        ("实时返佣", "live rebates"),
        ("赠金免佣", "bonus fee relief"),
        ("另 BNB抵扣已省", "BNB discount saved"),
        ("另 BNB 抵扣已省", "BNB discount saved"),
        ("另 MX抵扣已省", "MX discount saved"),
        ("另 OKB 抵扣已省", "OKB discount saved"),
        ("另 MNT 抵扣已省", "MNT discount saved"),
        ("跟单池浮盈", "Copy pool floating"),
        ("返佣直退统一账户", "rebates to Unified"),
        ("笔跟单返佣", "copy rebates"),
        ("跟单返佣", "copy rebates"),
        ("累计投入", "invested"),
        ("投入本金", "invested"),
        ("投入", "invested"),
        ("结清赎回", "redeemed"),
        ("当前净值", "current equity"),
        ("跟单在途净值", "Active copy equity"),
        ("池账面", "pool book"),
        ("笔", " rows "),
        ("认购净扣", "IPO net debit"),
        ("当前股票市值", "current stock value"),
        ("当前股票持仓浮盈", "open equity floating PnL"),
        ("合计净损益", "combined net PnL"),
        ("定期/新手理财利息 + 余币宝赎回收益 + 活动加息奖励", "Fixed/flexible Earn interest & bonus rewards"),
        ("统一账户内 WLFI / GT 等空投代币折合估值", "Unified account airdrop & non-USDT token valuation"),
        ("易币红包代币", "EasiCoin bonus tokens"),
        ("币安现货尾仓", "Binance spot dust"),
        ("币安 BNB 闪兑升值与提币费净差", "Binance BNB swap & withdrawal fee delta"),
        ("及体验券/结算尾差校准", "& voucher/settlement rounding"),
        ("及结算尾差校准", "& settlement rounding"),
        ("体验券与结算尾差校准", "Voucher & settlement rounding"),
        ("闪兑损耗", "swap spread"),
        ("闪兑", "swap"),
        ("持币生息", "hold-to-earn"),
        ("早期经典合约损益及汇兑/提币费校准", "Pre-UTA futures & FX/fee delta"),
        ("网格策略/早期仓位/现货代币及额外返佣闭环净额", "Grid/legacy positions, spot tokens & rebates"),
        ("划入跟单本金", "Transfer to Copy Pool"),
        ("跟单赎回", "Copy Pool Redemption"),
        ("统一账户", "Unified Account"),
        ("现货账户", "Spot Account"),
        ("合约跟单", "Copy Futures"),
        ("美股正股", "US Equity"),
        ("港股正股", "HK Equity"),
        ("1:1美股代币", "1:1 Stock Token"),
        ("1:1 美股代币", "1:1 Stock Token"),
        ("大宗商品合约", "Commodity Perp"),
        ("韩股永续", "KR Equity Perp"),
        ("独角兽股权永续", "Pre-IPO Perp"),
        ("美股永续", "US Equity Perp"),
        ("易币", "EasiCoin"),
        ("币安", "Binance"),
        ("已结清", "Settled"),
        ("跟单在途", "Active"),
        ("至今", "Present"),
    )
    for zh_tok, en_tok in replacements:
        s = s.replace(zh_tok, en_tok)
    s = re.sub(r"[\u4e00-\u9fff]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" ·,;")
    return s or "Reconciled ledger component"


def _translate_reconcile_label_en(
    key: str,
    raw_label: str,
    label_zh: str,
    *,
    account_display: str = "",
    base_exchange_en: str = "",
) -> str:
    if raw_label and not re.search(r"[\u4e00-\u9fff]", raw_label):
        m_p = _CIRCLED_NUM_RE.match(label_zh or "")
        if m_p and not _CIRCLED_NUM_RE.match(raw_label):
            return f"{m_p.group(1)} {raw_label}"
        return raw_label
    m = _CIRCLED_NUM_RE.match(label_zh or "")
    prefix = f"{m.group(1)} " if m else ""
    base = _EN_RECONCILE_TITLE_BY_KEY.get(key)
    if base:
        if account_display and base_exchange_en and account_display != base_exchange_en:
            if base_exchange_en in base:
                base = base.replace(base_exchange_en, account_display, 1)
            elif account_display not in base:
                base = f"{account_display} · {base}"
        return f"{prefix}{base}"
    cleaned = _translate_zh_phrases(label_zh or raw_label)
    return cleaned or key.replace("_", " ").title()


def _format_numbered_reconcile_items(
    raw_items: list[dict[str, Any]],
    target_total_2dp: float | None = None,
) -> list[dict[str, Any]]:
    if not raw_items:
        return []
    numbered: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for idx, it in enumerate(raw_items):
        num = _CN_NUMS[idx] if idx < len(_CN_NUMS) else f"({idx + 1})"
        k = str(it.get("key") or idx)
        acct_id = str(it.get("account_id") or "").strip().lower()
        out_key = f"{acct_id}__{k}" if (acct_id and "__" in acct_id) else k
        if out_key in seen_keys:
            out_key = f"{acct_id or idx}__{k}__{idx}"
        seen_keys.add(out_key)
        raw_zh = str(it.get("label_zh") or it.get("title_zh") or it.get("title") or k)
        clean_zh = _CIRCLED_NUM_RE.sub("", raw_zh).strip()
        lbl_zh = f"{num} {clean_zh}"
        lbl_en = _translate_reconcile_label_en(
            k,
            str(it.get("label") or ""),
            lbl_zh,
            account_display=str(it.get("account_display") or ""),
            base_exchange_en=str(it.get("base_exchange_en") or ""),
        )
        det_zh = str(it.get("detail_zh") or it.get("detail") or "")
        det_en = (
            str(it["detail"])
            if it.get("detail") and not re.search(r"[\u4e00-\u9fff]", str(it["detail"]))
            else _translate_zh_phrases(det_zh)
        )
        numbered.append(
            {
                "key": out_key,
                "label": lbl_en,
                "label_zh": lbl_zh,
                "detail": det_en,
                "detail_zh": det_zh or det_en,
                "amount_usd": round(float(it.get("amount_usd") or 0.0), 2),
            }
        )
    if target_total_2dp is not None and numbered:
        prev_sum = round(sum(float(x["amount_usd"]) for x in numbered[:-1]), 2)
        numbered[-1]["amount_usd"] = round(round(target_total_2dp, 2) - prev_sum, 2)
    return numbered


def _trade_matches_range(close_time_ms: int, close_date: str, range_key: str, now_s: float) -> bool:
    rk = (range_key or "all").strip().lower()
    if rk == "all" or not rk:
        return True
    if rk == "7d":
        return close_time_ms >= int((now_s - 7 * 86400) * 1000)
    if rk == "30d":
        return close_time_ms >= int((now_s - 30 * 86400) * 1000)
    if len(rk) == 7 and rk[4] == "-" and rk[:4].isdigit() and rk[5:].isdigit():
        return (close_date or "").startswith(rk)
    return True


def _collect_venue_warnings(
    raw_sc: Any,
    vh: dict[str, Any] | None,
    extra_errors: list[str] | None = None,
) -> list[str]:
    candidates: list[str] = []
    if isinstance(vh, dict) and isinstance(vh.get("warnings"), list):
        candidates.extend(str(w) for w in vh["warnings"] if w)
    snap = raw_sc.get_kv("snapshot")
    if isinstance(snap, dict):
        for wk in ("warnings", "_warnings"):
            if isinstance(snap.get(wk), list):
                candidates.extend(str(w) for w in snap[wk] if w)
    lb = raw_sc.get_kv("live_bundle")
    if isinstance(lb, dict):
        for wk in ("_warnings", "warnings"):
            if isinstance(lb.get(wk), list):
                candidates.extend(str(w) for w in lb[wk] if w)
    if extra_errors:
        candidates.extend(str(w) for w in extra_errors if w)
    return visible_sync_warnings(candidates)


def _summarize_venue_warnings(
    w_list: list[str], labels: Mapping[str, tuple[str, str]],
) -> tuple[str, str]:
    en_parts: list[str] = []
    zh_parts: list[str] = []
    fields = {w.partition(":")[0] for w in w_list}
    for field, (en, zh) in labels.items():
        if field in fields:
            en_parts.append(en)
            zh_parts.append(zh)
    remaining = [w for w in w_list if w.partition(":")[0] not in labels]
    joined_low = " ".join(remaining).lower()
    has_perm = any(
        tok in joined_low
        for tok in ("spot", "wallet", "deposit", "withdraw", "permission", "403", "unauthorized", "forbidden", "funding")
    )
    has_trunc = any(
        tok in joined_low
        for tok in ("2,000", "2000", "10,000", "10000", "truncated", "ceiling", "cap", "pagination", "limit reached")
    )
    has_crash = any("ledger computation failed" in w.lower() for w in w_list)

    invalid_wallet = "invalidwalletsnapshot" in joined_low
    if has_perm and not invalid_wallet:
        en_parts.append(
            "Some sub-account balances or deposit/withdrawal records could not be read (check Spot/Wallet read-only permissions); total equity or principal may be understated."
        )
        zh_parts.append("部分子账户或充提流水未读取（可能缺少现货/钱包只读权限），总资产或本金可能少算")
    if has_trunc:
        en_parts.append(
            "Historical records reached the exchange API pagination cap; earlier history may be truncated."
        )
        zh_parts.append("历史记录已达交易所接口分页上限，更早历史可能被截断")
    if invalid_wallet:
        en_parts.append("The saved balance is invalid. This account is excluded from totals until a successful sync.")
        zh_parts.append("余额待重新同步，该账户已暂时从总资产中排除")
    elif has_crash:
        en_parts.append(
            "Local snapshot encountered a calculation error and was isolated to protect other accounts."
        )
        zh_parts.append("该账户本地快照核算异常已自动隔离，不影响其他平台数据展示")
    if not zh_parts:
        first_w = w_list[0] if w_list else ""
        en_parts.append(first_w)
        zh_parts.append(f"同步产生数据完整性提示：{first_w}" if first_w else "")
    return "; ".join(en_parts), "；".join(zh_parts)


def _date_matches_range(date_str: str, range_key: str, cutoff_day_bj: str) -> bool:
    rk = (range_key or "all").strip().lower()
    if rk == "all" or not rk:
        return True
    if len(rk) == 7 and rk[4] == "-" and rk[:4].isdigit() and rk[5:].isdigit():
        return date_str.startswith(rk)
    if cutoff_day_bj:
        return date_str >= cutoff_day_bj
    return True


class LedgerService:
    """Produces unified dashboard payloads by aggregating independent `BaseExchangeAdapter` modules."""

    def __init__(
        self,
        store: LedgerStore,
        vault: CredentialVault,
        registry: AdapterRegistry,
        *,
        default_demo_mode: bool = False,
    ) -> None:
        self.store = store
        self.vault = vault
        self.registry = registry
        saved_demo = self.store.get_meta("demo_mode_override", None)
        self.demo_mode = bool(saved_demo) if isinstance(saved_demo, bool) else default_demo_mode
        self.syncing = False
        self.last_sync_ms = 0

    def set_demo_mode(self, enabled: bool) -> None:
        self.demo_mode = bool(enabled)
        self.store.set_meta("demo_mode_override", self.demo_mode)

    def _iter_account_targets(self) -> list[tuple[BaseExchangeAdapter, str]]:
        vault_creds = self.vault.list_decrypted()
        targets: list[tuple[BaseExchangeAdapter, str]] = []
        for adapter in self.registry.iter_adapters():
            ad_id = adapter.meta.venue_id.lower()
            cand_ids: list[str] = [ad_id]
            for cred in vault_creds:
                cid = cred.venue_id.lower()
                if cred.effective_adapter_id == ad_id and cid not in cand_ids:
                    cand_ids.append(cid)
            for pid in self.store.list_partition_account_ids(ad_id):
                if pid not in cand_ids:
                    cand_ids.append(pid)
            for acct_id in cand_ids:
                targets.append((adapter, acct_id))
        return targets

    def has_live_data(self) -> bool:
        if self.vault.list_decrypted():
            return True
        return any(
            adapter.has_venue_data(self.store.for_account(acct_id, adapter.meta.venue_id))
            for adapter, acct_id in self._iter_account_targets()
        )

    def is_demo_active(self) -> bool:
        return self.demo_mode or (not self.has_live_data())

    def get_preferences(self) -> UserPreferences:
        return self.store.get_preferences()

    def update_preferences(self, patch: dict[str, Any]) -> UserPreferences:
        current = self.store.get_preferences().model_dump()
        for key, value in patch.items():
            if key in {"modules", "notifications"} and isinstance(value, dict):
                current[key] = {**current.get(key, {}), **value}
            elif key in current:
                current[key] = value
        updated = UserPreferences.model_validate(current)
        return self.store.save_preferences(updated)

    def _apply_archived_trade_notes(
        self, adapter: BaseExchangeAdapter, archive_ids: list[str], bundle: VenueLedgerBundle,
        journals: dict[str, dict[str, Any]], current_journals: Mapping[str, Any],
        now_s: float, spot_prices: dict[str, float],
    ) -> None:
        """Show a corroborated archive note without rewriting its original journal key."""
        def trade_key(tr: dict[str, Any]) -> tuple[Any, ...]:
            return (
                tr.get("trade_id"), tr.get("symbol"), tr.get("side"), tr.get("open_time_ms"),
                tr.get("close_time_ms"), tr.get("qty"), tr.get("contract_count"),
                (tr.get("entry_leg") or {}).get("avg_price"), (tr.get("exit_leg") or {}).get("avg_price"),
            )

        eligible = {
            trade_key(tr): tr for tr in bundle.trades
            if tr.get("trade_id") and tr.get("symbol") and tr.get("close_time_ms")
            and tr["trade_id"] not in current_journals and not (tr.get("note") or tr.get("tags"))
        }
        if not eligible:
            return
        archive_notes: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
        for archive_id in archive_ids:
            scoped = scope_journals_for_account(journals, archive_id)
            if not scoped:
                continue
            try:
                archived = adapter.build_venue_ledger(
                    self.store.for_account(archive_id, adapter.meta.venue_id),
                    journals=scoped, now_s=now_s, global_spot_prices=spot_prices,
                )
            except Exception as exc:
                logger.warning("Could not read archived trades for %s: %s", archive_id, exc)
                continue
            for tr in archived.trades if archived else []:
                key = trade_key(tr)
                if key in eligible and (tr.get("note") or tr.get("tags")):
                    archive_notes[key].append(tr)
        for key, notes in archive_notes.items():
            if len(notes) == 1:
                eligible[key]["note"] = notes[0].get("note", "")
                eligible[key]["tags"] = list(notes[0].get("tags") or [])

    def save_journal(self, trade_id: str, tags: list[str], note: str) -> None:
        self.store.save_journal(trade_id, tags, note, demo_mode=self.is_demo_active())

    def get_dashboard(
        self,
        *,
        range_key: str = "all",
        venue_filter: str = "ALL",
        symbol_filter: str = "ALL",
        force_demo: bool | None = None,
    ) -> dict[str, Any]:
        prefs = self.get_preferences()
        use_demo = force_demo if force_demo is not None else self.is_demo_active()
        journals = self.store.load_journals(demo_mode=use_demo)
        latest_ai = self.store.load_latest_ai_review(range_key, demo_mode=use_demo)
        if use_demo:
            payload = build_demo_dashboard(
                range_key=range_key,
                venue_filter=venue_filter,
                symbol_filter=symbol_filter,
                prefs=prefs,
                journals=journals,
                latest_ai_review=latest_ai,
            )
            payload["explicit_demo_mode"] = bool(self.demo_mode)
            payload["has_live_data"] = self.has_live_data()
            return payload
        live_payload = self._build_live_dashboard(
            range_key=range_key,
            venue_filter=venue_filter,
            symbol_filter=symbol_filter,
            prefs=prefs,
            journals=journals,
            latest_ai_review=latest_ai,
        )
        live_payload["explicit_demo_mode"] = bool(self.demo_mode)
        live_payload["has_live_data"] = True
        return live_payload

    def _build_live_dashboard(
        self,
        *,
        range_key: str,
        venue_filter: str,
        symbol_filter: str,
        prefs: UserPreferences,
        journals: dict[str, dict[str, Any]],
        latest_ai_review: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now_s = time.time()
        today_bj_str = fmt_bj(now_s, "%Y-%m-%d")
        rk_norm = (range_key or "all").strip().lower()
        vf_norm = (venue_filter or "ALL").strip().lower()
        sf_norm = (symbol_filter or "ALL").strip().upper()

        cutoff_sec = 0.0
        if rk_norm == "7d":
            cutoff_sec = now_s - 7 * 86400
        elif rk_norm == "30d":
            cutoff_sec = now_s - 30 * 86400
        cutoff_day_bj = fmt_bj(cutoff_sec, "%Y-%m-%d") if cutoff_sec > 0 else ""

        # 1. Gather cross-venue spot ticker fallback map uniformly across all active account partitions
        vault_creds = self.vault.list_decrypted()
        creds_by_acct = {c.venue_id.lower(): c for c in vault_creds}
        candidate_targets: list[tuple[BaseExchangeAdapter, str]] = []
        active_accts_by_adapter: dict[str, list[tuple[BaseExchangeAdapter, str]]] = defaultdict(list)
        unattributed_archived_by_adapter: dict[str, list[tuple[BaseExchangeAdapter, str]]] = defaultdict(list)
        active_by_uid: dict[tuple[str, str], list[str]] = defaultdict(list)
        archived_uids: dict[str, tuple[str, str]] = {}

        for adapter, acct_id in self._iter_account_targets():
            ad_id = adapter.meta.venue_id.lower()
            scoped_check = self.store.for_account(acct_id, ad_id)
            try:
                has_data = bool(adapter.has_venue_data(scoped_check))
            except Exception:
                has_data = False
            if acct_id in creds_by_acct and not creds_by_acct[acct_id].enabled:
                continue
            if is_secondary_account_id(acct_id) and bool(self.store.venue_scope(acct_id).get_kv("duplicate_uid_conflict")):
                continue
            candidate_targets.append((adapter, acct_id))
            credential = creds_by_acct.get(acct_id)
            if credential or has_data:
                uid = str(
                    (credential.remote_uid if credential else "")
                    or adapter.extract_cached_account_uid(scoped_check)
                    or ""
                ).strip().lower()
                if credential:
                    active_accts_by_adapter[ad_id].append((adapter, acct_id))
                    if uid:
                        active_by_uid[ad_id, uid].append(acct_id)
                elif uid:
                    archived_uids[acct_id] = (ad_id, uid)
                else:
                    unattributed_archived_by_adapter[ad_id].append((adapter, acct_id))

        dashboard_warnings: list[dict[str, Any]] = []
        suppressed_archived_ids: set[str] = set()
        journal_archives_by_owner: dict[str, list[str]] = defaultdict(list)
        for archive_id, uid_key in archived_uids.items():
            owners = active_by_uid.get(uid_key, [])
            if owners:
                suppressed_archived_ids.add(archive_id)
                if len(owners) == 1 and journals:
                    journal_archives_by_owner[owners[0]].append(archive_id)

        for ad_id, arch_list in unattributed_archived_by_adapter.items():
            active_fps_by_id: dict[str, set[str]] = {}
            for act_adapter, act_id in active_accts_by_adapter.get(ad_id, []):
                try:
                    active_fps_by_id[act_id] = act_adapter.extract_archive_record_fingerprints(
                        self.store.for_account(act_id, ad_id),
                        include_legacy_fallback=not is_secondary_account_id(act_id),
                    )
                except Exception:
                    continue
            if not active_accts_by_adapter.get(ad_id):
                continue
            for arch_adapter, arch_id in arch_list:
                try:
                    arch_fps = arch_adapter.extract_archive_record_fingerprints(
                        self.store.for_account(arch_id, ad_id),
                        include_legacy_fallback=not is_secondary_account_id(arch_id),
                    )
                except Exception:
                    arch_fps = set()
                matches = {}
                for aid, fps in active_fps_by_id.items():
                    shared = arch_fps & fps
                    if shared:
                        matches[aid] = shared
                if not matches and arch_adapter.meta.identity_mode not in ("probe", "sync_bound"):
                    continue
                suppressed_archived_ids.add(arch_id)
                if not matches:
                    label = self.store.venue_scope(arch_id).get_kv("account_label") or arch_id
                    en = f"Archived account {label} has no verified owner. Its history is kept and excluded from totals. Rebind the original account in Settings to verify its UID."
                    zh = f"封存账号「{label}」尚未确认归属，历史数据已保留，暂不计入汇总。请在设置中用原账户密钥重新绑定以确认 UID。"
                    dashboard_warnings.append({
                        "venue_id": arch_id,
                        "display_name": arch_adapter.meta.display_name,
                        "display_name_zh": arch_adapter.meta.display_name_zh,
                        "summary": en, "summary_zh": zh, "warnings": [en],
                    })
                if len(matches) == 1 and journals:
                    owner, shared = next(iter(matches.items()))
                    # A second distinct historical record corroborates ownership.
                    # The same record appearing in two streams is only one witness.
                    if len({fp.partition(":")[2] for fp in shared}) >= 2:
                        journal_archives_by_owner[owner].append(arch_id)

        account_targets: list[tuple[BaseExchangeAdapter, str]] = [
            (adapter, acct_id)
            for adapter, acct_id in candidate_targets
            if acct_id not in suppressed_archived_ids
        ]

        global_spot_prices: dict[str, float] = {}
        for adapter, acct_id in account_targets:
            scoped_store = self.store.for_account(acct_id, adapter.meta.venue_id)
            try:
                spot_map = adapter.extract_spot_prices(scoped_store)
            except Exception as exc:
                logger.warning("Isolated spot price extraction error for %s: %s", acct_id, exc)
                continue
            for sym, px in spot_map.items():
                if px > 0:
                    global_spot_prices.setdefault(sym, px)

        # 2. Compute each active account's self-contained ledger bundle independently
        raw_bundles: list[tuple[BaseExchangeAdapter, str, VenueLedgerBundle]] = []
        isolated_build_errors: list[tuple[BaseExchangeAdapter, str, str]] = []
        sibling_counts: dict[str, int] = defaultdict(int)
        for adapter, acct_id in account_targets:
            ad_id = adapter.meta.venue_id.lower()
            scoped_store = self.store.for_account(acct_id, adapter.meta.venue_id)
            scoped_journals = scope_journals_for_account(journals, acct_id)
            try:
                bundle = adapter.build_venue_ledger(
                    scoped_store,
                    journals=scoped_journals,
                    now_s=now_s,
                    global_spot_prices=global_spot_prices,
                )
            except Exception as exc:
                logger.warning("Isolated venue ledger build failure for %s: %s", acct_id, exc)
                isolated_build_errors.append(
                    (adapter, acct_id, f"Ledger computation failed ({type(exc).__name__}): {exc}")
                )
                continue
            if bundle is not None:
                archives = journal_archives_by_owner.get(acct_id)
                if archives:
                    self._apply_archived_trade_notes(
                        adapter, archives, bundle, journals, scoped_journals, now_s, global_spot_prices,
                    )
                raw_bundles.append((adapter, acct_id, bundle))
                sibling_counts[ad_id] += 1

        ordinals: dict[str, int] = defaultdict(int)
        active_entries: list[tuple[BaseExchangeAdapter, VenueLedgerBundle]] = []
        for adapter, acct_id, bundle in raw_bundles:
            ad_id = adapter.meta.venue_id.lower()
            ordinals[ad_id] += 1
            cred_rec = creds_by_acct.get(acct_id)
            raw_sc = self.store.venue_scope(acct_id)
            scoped_for_uid = self.store.for_account(acct_id, ad_id)
            saved_uid = str(
                (cred_rec.remote_uid if cred_rec else "")
                or raw_sc.get_kv("account_uid")
                or adapter.extract_cached_account_uid(scoped_for_uid)
                or ""
            )
            saved_role = str((cred_rec.account_role if cred_rec else "") or raw_sc.get_kv("account_role") or "")
            saved_label = str((cred_rec.label if cred_rec else "") or raw_sc.get_kv("account_label") or "")
            stamp_bundle_for_account(
                bundle,
                account_id=acct_id,
                meta=adapter.meta,
                cred=cred_rec,
                remote_uid=saved_uid,
                account_role=saved_role,
                custom_label=saved_label,
                sibling_count=sibling_counts[ad_id],
                ordinal=ordinals[ad_id],
                journals=journals,
            )
            active_entries.append((adapter, bundle))

        # Order exchange cards by descending equity, then descending principal, then venue_id
        active_entries.sort(
            key=lambda pair: (
                -float(pair[1].exchange_card.get("equity_usd") or 0.0),
                -float(pair[1].exchange_card.get("net_principal_usd") or 0.0),
                pair[1].venue_id,
            )
        )

        venue_health_map = self.store.get_meta("venue_health", {})
        if not isinstance(venue_health_map, dict):
            venue_health_map = {}
        configured_vids = {
            c.venue_id.lower() for c in vault_creds if c.enabled
        }

        exchanges: list[dict[str, Any]] = []
        all_open_positions: list[dict[str, Any]] = []
        spot_wallet_balances: list[dict[str, Any]] = []
        spot_holdings: list[dict[str, Any]] = []
        fee_matrix: list[dict[str, Any]] = []
        all_trades_unfiltered: list[dict[str, Any]] = []
        venue_scoped_all_time_trades: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []

        copy_platforms: list[dict[str, Any]] = []
        copy_transfers: list[dict[str, Any]] = []
        copy_rebates: list[dict[str, Any]] = []
        total_copy_invested = Decimal("0")
        total_copy_equity = Decimal("0")
        total_copy_book = Decimal("0")
        total_copy_reb = Decimal("0")
        total_copy_comb = Decimal("0")

        stock_holdings: list[dict[str, Any]] = []
        stock_cash_ledger: list[dict[str, Any]] = []
        stock_perpetuals: list[dict[str, Any]] = []
        stock_venue_fee_rates: list[dict[str, Any]] = []
        total_stocks_eq = Decimal("0")
        total_stocks_upnl = Decimal("0")
        total_stocks_fees = Decimal("0")

        range_trades_by_vid: dict[str, list[dict[str, Any]]] = {}

        for adapter, bundle in active_entries:
            vid = bundle.venue_id
            vh = venue_health_map.get(vid) if isinstance(venue_health_map.get(vid), dict) else {}
            is_conf = vid in configured_vids
            is_err = is_conf and (vh.get("status") == "error")
            raw_sc = self.store.venue_scope(vid)
            w_list = _collect_venue_warnings(raw_sc, vh)

            ex_card = dict(bundle.exchange_card)
            ex_card["connected"] = is_conf and (not is_err)
            ex_card["health_status"] = (
                "archived" if not is_conf else ("error" if is_err else "connected")
            )
            ex_card["error_reason"] = str(vh.get("error_reason") or "") if is_err else ""
            ex_card["error_reason_zh"] = str(vh.get("error_reason_zh") or "") if is_err else ""
            ex_card["warnings"] = w_list

            if w_list:
                sum_en, sum_zh = _summarize_venue_warnings(w_list, adapter.sync_warning_labels)
                dashboard_warnings.append(
                    {
                        "venue_id": vid,
                        "display_name": str(ex_card.get("display_name") or adapter.meta.display_name),
                        "display_name_zh": str(
                            ex_card.get("display_name_zh") or adapter.meta.display_name_zh or adapter.meta.display_name
                        ),
                        "warnings": w_list,
                        "summary": sum_en,
                        "summary_zh": sum_zh,
                    }
                )

            all_open_positions.extend(bundle.open_positions)
            spot_holdings.extend(bundle.spot_holdings)
            spot_wallet_balances.extend(bundle.spot_wallet_balances or bundle.spot_holdings)

            # Trades & range filtering
            v_range_trades: list[dict[str, Any]] = []
            v_range_sym_trades: list[dict[str, Any]] = []
            for tr in bundle.trades:
                all_trades_unfiltered.append(tr)
                if vf_norm in ("all", vid):
                    venue_scoped_all_time_trades.append(tr)
                in_range = _trade_matches_range(tr["close_time_ms"], tr["close_date"], rk_norm, now_s)
                if in_range:
                    v_range_trades.append(tr)
                    if sf_norm in ("ALL", "", str(tr["symbol"]).upper()):
                        v_range_sym_trades.append(tr)
                        if vf_norm in ("all", vid):
                            trades.append(tr)

            range_trades_by_vid[vid] = v_range_trades
            if rk_norm != "all" and vf_norm == "all" and sf_norm in ("ALL", ""):
                ex_card["trade_count"] = len(v_range_trades)
            exchanges.append(ex_card)

            try:
                raw_fee_row = adapter.build_fee_matrix_row(bundle, v_range_sym_trades)
            except Exception as exc:
                logger.warning("Isolated fee matrix build error for %s: %s", vid, exc)
                raw_fee_row = None
            fee_row = stamp_fee_row_for_account(raw_fee_row, bundle)
            if fee_row is not None:
                fee_matrix.append(fee_row)

            # Copy Trading aggregation
            ct = bundle.copy_trading
            if ct.has_copy_trading:
                total_copy_invested += dec(ct.invested_usd)
                total_copy_equity += dec(ct.active_equity_usd)
                total_copy_book += dec(ct.book_pnl_usd)
                total_copy_reb += dec(ct.rebate_usd)
                total_copy_comb += dec(ct.combined_pnl_usd)
                if ct.platform is not None:
                    copy_platforms.append(ct.platform)
                copy_transfers.extend(ct.transfers)
                copy_rebates.extend(ct.rebates)

            # Stocks & Commodities aggregation
            st = bundle.stocks
            total_stocks_eq += dec(st.equity_usd)
            total_stocks_upnl += dec(st.unrealized_pnl_usd)
            total_stocks_fees += dec(st.non_perp_fees_usd)
            stock_holdings.extend(st.holdings)
            stock_cash_ledger.extend(st.cash_ledger)
            stock_perpetuals.extend(st.perpetuals)
            if st.venue_fee_rate is not None:
                stock_venue_fee_rates.append(st.venue_fee_rate)

        for err_adapter, err_vid, err_msg in isolated_build_errors:
            err_sc = self.store.venue_scope(err_vid)
            err_vh = venue_health_map.get(err_vid) if isinstance(venue_health_map.get(err_vid), dict) else {}
            err_cred = creds_by_acct.get(err_vid)
            err_uid = str((err_cred.remote_uid if err_cred else "") or err_sc.get_kv("account_uid") or "")
            err_role = str((err_cred.account_role if err_cred else "") or err_sc.get_kv("account_role") or "")
            err_lbl = str((err_cred.label if err_cred else "") or err_sc.get_kv("account_label") or "")
            disp_en_err, disp_zh_err = format_account_display_names(
                account_id=err_vid,
                meta=err_adapter.meta,
                custom_label=err_lbl,
                account_role=err_role,
                masked_uid=mask_uid(err_uid),
                sibling_count=1,
                ordinal=1,
            )
            err_w_list = _collect_venue_warnings(err_sc, err_vh, extra_errors=[err_msg])
            sum_en_err, sum_zh_err = _summarize_venue_warnings(err_w_list, err_adapter.sync_warning_labels)
            dashboard_warnings.append(
                {
                    "venue_id": err_vid,
                    "display_name": disp_en_err,
                    "display_name_zh": disp_zh_err,
                    "warnings": err_w_list,
                    "summary": sum_en_err,
                    "summary_zh": sum_zh_err,
                }
            )

        spot_holdings.sort(key=lambda x: float(x.get("market_value_usd") or 0.0), reverse=True)
        trades.sort(key=lambda t: (t["close_time_ms"], t["open_time_ms"], t["trade_id"]), reverse=True)
        copy_transfers.sort(key=lambda x: int(x.get("time_sec") or 0), reverse=True)
        copy_rebates.sort(key=lambda x: int(x.get("time_sec") or 0), reverse=True)

        open_positions = [
            p
            for p in all_open_positions
            if (vf_norm == "all" or str(p["venue_id"]).lower() == vf_norm)
            and (sf_norm in ("ALL", "") or str(p["symbol"]).upper() == sf_norm)
        ]

        # 3. Whole-account totals & closed-loop reconciliation (`0.00` residual)
        total_equity = round(sum(float(ex["equity_usd"]) for ex in exchanges), 2)
        total_principal = round(sum(float(ex["net_principal_usd"]) for ex in exchanges), 2)
        whole_net = round(sum(float(ex["net_pnl_usd"]) for ex in exchanges), 2)

        raw_reconcile_all = [s for _, b in active_entries for s in b.reconcile_streams]
        reconcile_items = _format_numbered_reconcile_items(raw_reconcile_all, target_total_2dp=whole_net)

        # 4. Calendar days (all-time whole-account daily PnL across all active venues)
        all_daily_pnl: dict[str, Decimal] = defaultdict(Decimal)
        activity_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trade in all_trades_unfiltered:
            counts = exit_counts_by_day(trade)
            actual = trade.get("realized_pnl_usd", trade.get("realized_net_pnl_usd", trade["net_pnl_usd"]))
            daily = trade_daily_amounts(trade, dec(actual))
            for day in daily.keys() | counts.keys():
                activity_by_day[day].append({
                    "trade_id": trade["trade_id"], "venue_id": trade["venue_id"],
                    "symbol": trade["symbol"], "side": trade["side"],
                    "amount_usd": f2(daily.get(day, Decimal("0")), 6),
                    "exit_count": counts.get(day, 0),
                })
        for _, b in active_entries:
            for d_k, v_dec in b.daily_pnl.items():
                all_daily_pnl[d_k] += v_dec

        calendar_days: list[dict[str, Any]] = []
        for d_sorted in sorted(all_daily_pnl.keys() | activity_by_day.keys()):
            d_val = all_daily_pnl[d_sorted]
            if abs(d_val) < Decimal("0.0005") and d_sorted != today_bj_str and not activity_by_day.get(d_sorted):
                continue
            calendar_days.append(
                {
                    "date": d_sorted,
                    "net_pnl_usd": f2(d_val, 2),
                    "trade_count": sum(1 for row in activity_by_day[d_sorted] if row["exit_count"]),
                    "trade_activity": activity_by_day[d_sorted],
                }
            )

        # 5. Equity curve & drawdown (strictly honoring BOTH `venue_filter` and `symbol_filter`!)
        curve_by_date: dict[str, dict[str, Decimal]] = {}
        range_comp_by_vid: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))

        # Compute whole-account range_whole_net across all active venues for `summary.range_whole_net_pnl_usd`
        range_all_venues_cum = Decimal("0")
        for _, b in active_entries:
            for d_k, v_dec in b.daily_pnl.items():
                if _date_matches_range(d_k, rk_norm, cutoff_day_bj):
                    range_all_venues_cum += v_dec
                    for ck, cv in (b.daily_components.get(d_k) or {}).items():
                        range_comp_by_vid[b.venue_id][ck] += cv

        if sf_norm not in ("ALL", ""):
            for tr in venue_scoped_all_time_trades:
                if str(tr["symbol"]).upper() != sf_norm:
                    continue
                daily_net = trade_daily_amounts(tr, dec(tr.get("realized_net_pnl_usd", tr["net_pnl_usd"])))
                daily_book = trade_daily_amounts(tr, dec(tr["book_pnl_usd"]), include_rebates=False)
                for day, amount in daily_net.items():
                    if _date_matches_range(day, rk_norm, cutoff_day_bj):
                        bucket = curve_by_date.setdefault(day, {"delta": Decimal("0"), "book": Decimal("0")})
                        bucket["delta"] += amount
                        bucket["book"] += daily_book.get(day, Decimal("0"))
        else:
            scoped_bundles = [
                b for _, b in active_entries if (vf_norm == "all" or b.venue_id == vf_norm)
            ]
            merged_scoped_daily: dict[str, Decimal] = defaultdict(Decimal)
            for b in scoped_bundles:
                for d_k, v_dec in b.daily_pnl.items():
                    merged_scoped_daily[d_k] += v_dec
            for d_k in sorted(merged_scoped_daily.keys()):
                v_dec = merged_scoped_daily[d_k]
                if abs(v_dec) < Decimal("0.0005") and d_k != today_bj_str:
                    continue
                if _date_matches_range(d_k, rk_norm, cutoff_day_bj):
                    curve_by_date[d_k] = {"delta": v_dec, "book": v_dec}

        equity_curve: list[dict[str, Any]] = []
        run_net = Decimal("0")
        run_book = Decimal("0")
        peak_net = Decimal("0")
        max_dd_dec = Decimal("0")
        for idx_c, d_str in enumerate(sorted(curve_by_date.keys()), start=1):
            d_net = curve_by_date[d_str]["delta"]
            d_book = curve_by_date[d_str]["book"]
            run_net += d_net
            run_book += d_book
            if run_net > peak_net:
                peak_net = run_net
            if peak_net - run_net > max_dd_dec:
                max_dd_dec = peak_net - run_net
            equity_curve.append(
                {
                    "seq": idx_c,
                    "date": d_str,
                    "label": d_str[5:] if len(d_str) >= 10 else d_str,
                    "delta_usd": f2(d_net, 2),
                    "cum_net_usd": f2(run_net, 2),
                    "cum_book_usd": f2(run_book, 2),
                }
            )

        # Range reconciliation items
        if rk_norm == "all":
            range_whole_net = whole_net
            range_reconcile_items = reconcile_items
        else:
            range_whole_net = f2(range_all_venues_cum, 2)
            is_month_rk = len(rk_norm) == 7 and rk_norm[4] == "-"
            period_lbl = f"{rk_norm} 月度" if is_month_rk else ("近 7 天" if rk_norm == "7d" else "近 30 天")
            raw_r_streams: list[dict[str, Any]] = []
            for adapter, b in active_entries:
                try:
                    raw_r_streams.extend(
                        adapter.build_range_reconcile_streams(
                            b,
                            period_label_zh=period_lbl,
                            comp_sums=range_comp_by_vid.get(b.venue_id, {}),
                            range_trades=range_trades_by_vid.get(b.venue_id, []),
                        )
                    )
                except Exception as exc:
                    logger.warning("Isolated range reconcile build error for %s: %s", b.venue_id, exc)
            range_reconcile_items = _format_numbered_reconcile_items(raw_r_streams, target_total_2dp=range_whole_net)

        # 6. Symbol attribution across matched `trades`
        sym_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for tr in trades:
            sym_groups[(str(tr["venue_id"]), str(tr["symbol"]))].append(tr)

        ex_card_by_vid = {str(ex["venue_id"]): ex for ex in exchanges}
        symbol_attribution: list[dict[str, Any]] = []
        for (s_vid, s_sym), grp in sym_groups.items():
            ex_meta = ex_card_by_vid.get(s_vid)
            ad_obj = self.registry.get(parse_adapter_id(s_vid))
            meta_sb = ad_obj.meta if ad_obj else None
            wins_cnt = sum(1 for x in grp if dec(x["net_pnl_usd"]) > 0)
            symbol_attribution.append(
                {
                    "venue_id": s_vid,
                    "venue_name": (
                        str(ex_meta["display_name"])
                        if ex_meta
                        else (meta_sb.display_name if meta_sb else s_vid.upper())
                    ),
                    "venue_name_zh": (
                        str(ex_meta.get("display_name_zh") or ex_meta["display_name"])
                        if ex_meta
                        else (meta_sb.display_name_zh if meta_sb else s_vid.upper())
                    ),
                    "color_token": (
                        str(ex_meta["color_token"])
                        if ex_meta
                        else (meta_sb.color_token if meta_sb else parse_adapter_id(s_vid))
                    ),
                    "symbol": s_sym,
                    "trade_count": len(grp),
                    "win_rate_pct": f2(Decimal(wins_cnt) / Decimal(len(grp)) * Decimal("100"), 1) if grp else 0.0,
                    "gross_fee_usd": f2(sum((dec(x["total_fee_usd"]) for x in grp), Decimal("0")), 2),
                    "rebate_usd": f2(sum((dec(x["total_rebate_usd"]) for x in grp), Decimal("0")), 2),
                    "discount_saved_usd": f2(sum((dec(x["total_discount_saved_usd"]) for x in grp), Decimal("0")), 2),
                    "book_pnl_usd": f2(sum((dec(x["book_pnl_usd"]) for x in grp), Decimal("0")), 2),
                    "net_pnl_usd": f2(sum((dec(x["net_pnl_usd"]) for x in grp), Decimal("0")), 2),
                }
            )
        symbol_attribution.sort(key=lambda r: (r["trade_count"], abs(r["net_pnl_usd"])), reverse=True)

        # 7. Unified summary stats with exact 4-decimal intermediate precision
        t_cnt = len(trades)
        t_wins = sum(1 for t in trades if dec(t["net_pnl_usd"]) > 0)
        sum_win_rate = f2(Decimal(t_wins) / Decimal(t_cnt) * Decimal("100"), 1) if t_cnt > 0 else 0.0
        fee_dec = sum((dec(t["total_fee_usd"]) for t in trades), Decimal("0"))
        reb_dec = sum((dec(t["total_rebate_usd"]) for t in trades), Decimal("0"))
        disc_dec = sum((dec(t["total_discount_saved_usd"]) for t in trades), Decimal("0"))
        net_fee_dec = fee_dec - reb_dec
        base_fee_dec = fee_dec + disc_dec
        relief_pct_dec = (
            ((reb_dec + disc_dec) / base_fee_dec * Decimal("100"))
            if base_fee_dec > 0
            else ((reb_dec / fee_dec * Decimal("100")) if fee_dec > 0 else Decimal("0"))
        )
        fut_net_dec = sum((dec(t["net_pnl_usd"]) for t in trades), Decimal("0"))
        fut_book_dec = sum((dec(t["book_pnl_usd"]) for t in trades), Decimal("0"))

        scoped_fee_rows = (
            fee_matrix if vf_norm == "all" else [r for r in fee_matrix if str(r["venue_id"]).lower() == vf_norm]
        )
        total_maker_savings = round(sum(float(r.get("potential_maker_savings_usd") or 0.0) for r in scoped_fee_rows), 2)

        # 8. Closed-loop 4-bucket equity breakdown (using each venue's actual futures equity rather than available-only)
        raw_futures_eq_sum = 0.0
        for ex in exchanges:
            if ex.get("futures_equity_usd") is not None:
                raw_futures_eq_sum += max(0.0, float(ex["futures_equity_usd"]))
            else:
                ex_vid = str(ex.get("venue_id") or "").lower()
                v_locked = sum(
                    max(0.0, float(p["notional_usd"]) / max(1.0, float(p["leverage"])) + float(p["unrealized_pnl_usd"]))
                    for p in all_open_positions
                    if str(p.get("venue_id") or "").lower() == ex_vid
                )
                raw_futures_eq_sum += max(0.0, float(ex.get("available_margin_usd") or 0.0)) + v_locked

        copy_bucket_usd = round(max(0.0, float(total_copy_equity)), 2)
        stocks_bucket_usd = round(max(0.0, float(total_stocks_eq)), 2)
        futures_bucket_usd = round(
            min(max(0.0, total_equity - copy_bucket_usd - stocks_bucket_usd), round(raw_futures_eq_sum, 2)),
            2,
        )
        spot_and_dust_bucket_usd = round(
            max(0.0, total_equity - futures_bucket_usd - copy_bucket_usd - stocks_bucket_usd),
            2,
        )

        # 9. Goal & AI staleness
        goal_info = None
        goal_target = prefs.goal_target_usd if (prefs.goal_enabled and prefs.goal_target_usd) else None
        if goal_target and goal_target > 0:
            eq_dec = dec(total_equity)
            tgt_dec = dec(goal_target)
            rem = max(0.0, f2(tgt_dec - eq_dec, 2))
            surp = max(0.0, f2(eq_dec - tgt_dec, 2))
            prog_dec = min(Decimal("100"), (eq_dec / tgt_dec) * Decimal("100"))
            need_dec = (dec(rem) / eq_dec * Decimal("100")) if eq_dec > 0 and rem > 0 else Decimal("0")
            goal_info = {
                "enabled": True,
                "label": prefs.goal_label or "",
                "target_usd": goal_target,
                "current_equity_usd": total_equity,
                "remaining_usd": rem,
                "surplus_usd": surp,
                "is_reached": total_equity >= goal_target,
                "progress_pct": f2(prog_dec, 1),
                "needed_gain_pct": f2(need_dec, 2) if eq_dec > 0 and rem > 0 else 0.0,
                "today_delta_usd": f2(all_daily_pnl.get(today_bj_str, Decimal("0")), 2),
            }

        ai_out = latest_ai_review
        if ai_out is not None:
            rk_lbl_en = str(
                ai_out.get("range_label")
                or ("Last 7 Days" if rk_norm == "7d" else ("Last 30 Days" if rk_norm == "30d" else (rk_norm if rk_norm != "all" else "All-Time")))
            )
            rk_lbl_zh = str(
                ai_out.get("range_label_zh")
                or ("近 7 天" if rk_norm == "7d" else ("近 30 天" if rk_norm == "30d" else (f"{rk_norm} 月度" if rk_norm != "all" else "全部历史")))
            )
            saved_tc = ai_out.get("trade_count_snapshot")
            saved_eq = float(ai_out.get("equity_snapshot_usd") or 0.0)
            has_no_trades_card = any(
                isinstance(h, dict) and h.get("kind") == "no_trades" for h in (ai_out.get("highlights") or [])
            )
            if t_cnt == 0 and (saved_tc is None or int(saved_tc) == 0) and not has_no_trades_card:
                z_highlights, z_en, z_zh = build_zero_trade_texts(
                    all_time_cnt=len(all_trades_unfiltered),
                    range_label_en=rk_lbl_en,
                    range_label_zh=rk_lbl_zh,
                )
                ai_out = {
                    **ai_out,
                    "model": "local-actuarial",
                    "execution_mode": "zero_trades_local",
                    "data_transmitted": False,
                    "trade_count_snapshot": 0,
                    "highlights": z_highlights,
                    "markdown_commentary": z_en,
                    "markdown_commentary_zh": z_zh,
                    "ai_error": "",
                    "ai_error_zh": "",
                    "context_preview": None,
                }
                saved_tc = 0
                self.store.save_ai_review(ai_out, total_equity)
            stale_flag = False
            stale_en = ""
            stale_zh = ""
            if saved_tc is not None and int(saved_tc) != t_cnt:
                stale_flag = True
                stale_en = (
                    f"This {rk_lbl_en} report was generated across {saved_tc} trades "
                    f"(current period now has {t_cnt} trades) — regenerate to refresh."
                )
                stale_zh = (
                    f"该「{rk_lbl_zh}」报告生成时包含 {saved_tc} 笔交割，当前同周期已变为 {t_cnt} 笔，建议点击右上角重新生成。"
                )
            elif abs(total_equity - saved_eq) >= 25.0:
                stale_flag = True
                stale_en = (
                    f"This {rk_lbl_en} report was generated at {saved_eq:.2f} U equity "
                    f"(now {total_equity:.2f} U) — regenerate to refresh."
                )
                stale_zh = (
                    f"该「{rk_lbl_zh}」报告生成时总净资产为 {saved_eq:.2f} U（当前 {total_equity:.2f} U），可重新生成同步最新状态。"
                )
            ai_out = {
                **ai_out,
                "range_key": rk_norm,
                "range_label": rk_lbl_en,
                "range_label_zh": rk_lbl_zh,
                "stale": stale_flag,
                "stale_reason": stale_en,
                "stale_reason_zh": stale_zh,
            }

        all_months = sorted(
            {today_bj_str[:7]}
            | {t["close_date"][:7] for t in all_trades_unfiltered if len(t.get("close_date") or "") >= 7}
            | {c["date"][:7] for c in calendar_days if len(c.get("date") or "") >= 7},
            reverse=True,
        )

        merged_copy_inv_f = f2(total_copy_invested, 2)
        merged_copy_comb_f = f2(total_copy_comb, 2)
        merged_copy_roi = round((merged_copy_comb_f / merged_copy_inv_f) * 100.0, 2) if merged_copy_inv_f > 0 else 0.0

        rate_candidates = [
            float(x.get("maker_fee_pct") or 0.0) for x in stock_venue_fee_rates if x.get("maker_fee_pct") is not None
        ] + [
            float(x.get("taker_fee_pct") or 0.0) for x in stock_venue_fee_rates if x.get("taker_fee_pct") is not None
        ]
        fee_rate_range_str = (
            f"{min(rate_candidates):g}% ~ {max(rate_candidates):g}%"
            if rate_candidates and min(rate_candidates) != max(rate_candidates)
            else (f"{rate_candidates[0]:g}%" if rate_candidates else "0.1%")
        )

        return {
            "demo_mode": False,
            "read_only": True,
            "last_sync_ms": self.last_sync_ms or int(self.store.get_meta("last_sync_ms", 0) or 0),
            "syncing": self.syncing,
            "range_key": range_key,
            "venue_filter": "ALL" if vf_norm in ("all", "") else vf_norm,
            "symbol_filter": "ALL" if sf_norm in ("ALL", "") else sf_norm,
            "warnings": dashboard_warnings,
            "available_months": all_months,
            "available_symbols": sorted({t["symbol"] for t in all_trades_unfiltered if t.get("symbol")}),
            "summary": {
                "total_equity_usd": total_equity,
                "net_principal_usd": total_principal,
                "whole_net_pnl_usd": whole_net,
                "range_whole_net_pnl_usd": range_whole_net,
                "whole_roi_pct": round((whole_net / total_principal) * 100.0, 2) if total_principal > 0 else 0.0,
                "range_whole_roi_pct": round((range_whole_net / total_principal) * 100.0, 2) if total_principal > 0 else 0.0,
                "futures_net_pnl_usd": f2(fut_net_dec, 2),
                "futures_book_pnl_usd": f2(fut_book_dec, 2),
                "trade_count": t_cnt,
                "all_time_trade_count": len(venue_scoped_all_time_trades),
                "win_rate_pct": sum_win_rate,
                "total_fee_usd": f2(fee_dec, 2),
                "total_rebate_usd": f2(reb_dec, 2),
                "total_discount_saved_usd": f2(disc_dec, 2),
                "net_fee_usd": f2(net_fee_dec, 2),
                "combined_relief_pct": f2(relief_pct_dec, 1),
                "potential_maker_savings_usd": total_maker_savings,
                "max_drawdown_usd": f2(max_dd_dec, 2),
            },
            "equity_breakdown": {
                "futures_margin_usd": futures_bucket_usd,
                "copy_equity_usd": copy_bucket_usd,
                "stocks_equity_usd": stocks_bucket_usd,
                "spot_and_other_usd": spot_and_dust_bucket_usd,
            },
            "exchanges": exchanges,
            "open_positions": open_positions,
            "spot_holdings": spot_holdings,
            "spot_wallet_balances": sorted(spot_wallet_balances, key=lambda r: r["market_value_usd"] or 0, reverse=True),
            "trades": trades,
            "reconciliation": {
                "net_principal_usd": total_principal,
                "total_equity_usd": total_equity,
                "net_pnl_usd": whole_net,
                "residual_usd": 0.0,
                "items": reconcile_items,
                "range_items": range_reconcile_items,
            },
            "equity_curve": equity_curve,
            "calendar_days": calendar_days,
            "symbol_attribution": symbol_attribution,
            "fee_matrix": fee_matrix,
            "goal": goal_info,
            "ai_review": ai_out,
            "extensions": {
                "copy_trading": {
                    "enabled": prefs.modules.copy_trading,
                    "total_invested_usd": merged_copy_inv_f,
                    "active_equity_usd": f2(total_copy_equity, 2),
                    "book_pnl_usd": f2(total_copy_book, 2),
                    "rebate_total_usd": f2(total_copy_reb, 2),
                    "combined_pnl_usd": merged_copy_comb_f,
                    "combined_roi_pct": merged_copy_roi,
                    "platforms": copy_platforms,
                    "transfers": [
                        {k: v for k, v in tr.items() if k != "time_sec"}
                        for tr in copy_transfers
                    ],
                    "rebates": [
                        {k: v for k, v in rb.items() if k != "time_sec"}
                        for rb in copy_rebates
                    ],
                },
                "stocks": {
                    "enabled": prefs.modules.stocks,
                    "equity_usd": f2(total_stocks_eq, 2),
                    "unrealized_pnl_usd": f2(total_stocks_upnl, 2),
                    "non_perp_fees_usd": f2(total_stocks_fees, 4),
                    "fee_rate_range": fee_rate_range_str,
                    "venue_fee_rates": [
                        {
                            "venue_id": r["venue_id"],
                            "venue_name": r["venue_name"],
                            "venue_name_zh": r["venue_name_zh"],
                            "summary": r["summary"],
                            "summary_zh": r["summary_zh"],
                        }
                        for r in stock_venue_fee_rates
                    ],
                    "holdings": stock_holdings,
                    "cash_ledger": stock_cash_ledger,
                    "perpetuals": stock_perpetuals,
                },
            },
        }
