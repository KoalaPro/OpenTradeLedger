"""Multi-account partition routing, UID masking, and standardized bundle stamping.

Decouples `adapter_id` (exchange protocol, e.g. `'binance'`) from `account_id`
(isolated ledger partition key, e.g. `'binance'` for the primary slot or
`'binance__a1b2c3d4'` for additional main/sub-accounts on the same exchange).
"""

from __future__ import annotations

from decimal import Decimal
import re
from typing import TYPE_CHECKING, Any, Mapping
from uuid import uuid4

if TYPE_CHECKING:
    from opentradeledger.adapters.base import AdapterMeta, VenueLedgerBundle
    from opentradeledger.core.store import LedgerStore
    from opentradeledger.security.vault import ExchangeCredentialRecord

ACCOUNT_SEP = "__"
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SAFE_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def parse_adapter_id(account_or_venue_id: str) -> str:
    raw = (account_or_venue_id or "").strip().lower()
    if not raw:
        return ""
    return raw.split(ACCOUNT_SEP, 1)[0]


def is_secondary_account_id(account_id: str) -> bool:
    raw = (account_id or "").strip().lower()
    return ACCOUNT_SEP in raw and bool(raw.split(ACCOUNT_SEP, 1)[1])


def normalize_evm_address(raw: str) -> str:
    val = (raw or "").strip()
    if _EVM_ADDRESS_RE.fullmatch(val):
        return val.lower()
    return ""


def mask_uid(uid: str) -> str:
    raw = (uid or "").strip()
    if not raw:
        return ""
    if _EVM_ADDRESS_RE.fullmatch(raw):
        return f"{raw[:6]}...{raw[-4:]}"
    if len(raw) <= 4:
        return f"***{raw[-2:]}"
    if len(raw) <= 7:
        return f"{raw[:2]}***{raw[-2:]}"
    return f"{raw[:3]}***{raw[-4:]}"


def mint_account_id(
    adapter_id: str,
    occupied_ids: set[str],
    *,
    preferred_suffix: str = "",
    force_secondary: bool = False,
) -> str:
    base = parse_adapter_id(adapter_id)
    norm_occupied = {x.strip().lower() for x in occupied_ids if x}
    if not force_secondary and not preferred_suffix and base not in norm_occupied:
        return base

    if preferred_suffix:
        slug = _SAFE_SLUG_RE.sub("", preferred_suffix.strip().lower())[:16]
        if slug:
            candidate = f"{base}{ACCOUNT_SEP}{slug}"
            if candidate not in norm_occupied:
                return candidate

    for _ in range(16):
        candidate = f"{base}{ACCOUNT_SEP}{uuid4().hex[:8]}"
        if candidate not in norm_occupied:
            return candidate
    return f"{base}{ACCOUNT_SEP}{uuid4().hex[:12]}"


class AccountIdentityError(RuntimeError):
    """Raised when an account credential duplicates an active UID or mismatches a bound partition."""

    def __init__(
        self,
        code: str,
        *,
        masked_uid: str = "",
        existing_label: str = "",
        exchange_name: str = "",
        exchange_name_zh: str = "",
    ) -> None:
        self.code = code
        ex_en = exchange_name or "this exchange"
        ex_zh = exchange_name_zh or ex_en
        uid_part_en = f" ({masked_uid})" if masked_uid else ""
        uid_part_zh = f"（{masked_uid}）" if masked_uid else ""
        lbl_part_en = f" as '{existing_label}'" if existing_label else ""
        lbl_part_zh = f"「{existing_label}」" if existing_label else ""

        if code == "DUPLICATE_ACCOUNT_UID":
            self.reason = (
                f"This {ex_en} account{uid_part_en} is already connected{lbl_part_en}. "
                "Do not add the same UID twice, or total equity will be double-counted."
            )
            self.reason_zh = (
                f"该 {ex_zh} 账号{uid_part_zh}已在账本中接入{lbl_part_zh}，请勿重复添加同一账号以免总净资产重复计算。"
            )
        elif code == "DUPLICATE_ACCOUNT_KEY":
            self.reason = (
                f"This API key / address is already connected under {ex_en}{lbl_part_en}."
            )
            self.reason_zh = (
                f"这组只读密钥或钱包地址已在 {ex_zh} 下接入{lbl_part_zh}，请勿重复添加。"
            )
        elif code == "ACCOUNT_UID_MISMATCH":
            self.reason = (
                f"This credential belongs to a different {ex_en} account{uid_part_en}. "
                "Use a read-only key from the original account, or click '+ Add Account' to connect it separately."
            )
            self.reason_zh = (
                f"这组凭证属于另一个 {ex_zh} 账号{uid_part_zh}。更新密钥请使用原账号只读 Key；如需接入子账户或另一账号，请点击「+ 添加账号」独立绑定。"
            )
        elif code == "INVALID_WALLET_ADDRESS":
            self.reason = "Invalid EVM wallet address — enter a 42-character 0x... public address (never enter a private key)."
            self.reason_zh = "钱包地址格式无效，请输入 0x 开头的 42 位公开钱包地址（切勿输入私钥或助记词）。"
        else:
            self.reason = f"Account identity check failed ({code})."
            self.reason_zh = f"账户身份核验未通过（{code}）。"
        super().__init__(self.reason)


class AccountScopedStore:
    """Transparent store proxy mapping an adapter's nominal `venue_id` calls onto `account_id`."""

    def __init__(self, store: "LedgerStore", account_id: str, adapter_id: str | None = None) -> None:
        self._store = store
        self.account_id = account_id.strip().lower()
        self.adapter_id = parse_adapter_id(adapter_id or self.account_id)

    def _resolve_vid(self, venue_id: str) -> str:
        vid = (venue_id or "").strip().lower()
        if vid == self.adapter_id or parse_adapter_id(vid) == self.adapter_id:
            return self.account_id
        return vid

    def venue_scope(self, venue_id: str):
        return self._store.venue_scope(self._resolve_vid(venue_id))

    def upsert_plugin_snapshot(
        self,
        venue_id: str,
        payload: dict[str, Any],
        *,
        merge_history: bool = True,
        merge_specs: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._store.upsert_plugin_snapshot(
            self._resolve_vid(venue_id),
            payload,
            merge_history=merge_history,
            merge_specs=merge_specs,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


def format_account_display_names(
    *,
    account_id: str,
    meta: "AdapterMeta",
    custom_label: str = "",
    account_role: str = "",
    masked_uid: str = "",
    sibling_count: int = 1,
    ordinal: int = 1,
) -> tuple[str, str]:
    base_en = meta.display_name
    base_zh = meta.display_name_zh or meta.display_name
    lbl = (custom_label or "").strip()
    default_names = {
        meta.venue_id.lower(),
        base_en.lower(),
        base_zh.lower(),
    }
    has_custom_label = bool(lbl) and lbl.lower() not in default_names

    if not has_custom_label and sibling_count <= 1 and not is_secondary_account_id(account_id):
        return base_en, base_zh

    if has_custom_label:
        low_lbl = lbl.lower()
        en_out = lbl if low_lbl.startswith(base_en.lower()) else f"{base_en} · {lbl}"
        zh_out = (
            lbl
            if (low_lbl.startswith(base_zh.lower()) or low_lbl.startswith(base_en.lower()))
            else f"{base_zh} · {lbl}"
        )
        return en_out, zh_out

    role_map = {
        "subaccount": ("Sub", "子账户"),
        "master": ("Main", "主账户"),
        "wallet": ("Wallet", "钱包"),
    }
    r_en, r_zh = role_map.get((account_role or "").strip().lower(), ("", ""))
    if account_role == "wallet" and masked_uid:
        return f"{base_en} · {masked_uid}", f"{base_zh} · {masked_uid}"
    if account_role == "subaccount":
        if sibling_count > 1:
            return f"{base_en} · {r_en} #{ordinal}", f"{base_zh} · {r_zh} #{ordinal}"
        if masked_uid:
            return f"{base_en} · {r_en} {masked_uid}", f"{base_zh} · {r_zh} {masked_uid}"
    if sibling_count <= 1 and masked_uid and is_secondary_account_id(account_id):
        return f"{base_en} · {masked_uid}", f"{base_zh} · {masked_uid}"
    return f"{base_en} #{ordinal}", f"{base_zh} #{ordinal}"


def stamp_bundle_for_account(
    bundle: "VenueLedgerBundle",
    *,
    account_id: str,
    meta: "AdapterMeta",
    cred: "ExchangeCredentialRecord | None",
    remote_uid: str = "",
    account_role: str = "",
    sibling_count: int = 1,
    ordinal: int = 1,
    journals: Mapping[str, Mapping[str, Any]] | None = None,
) -> "VenueLedgerBundle":
    acct_id = account_id.strip().lower()
    adapter_id = meta.venue_id.lower()
    custom_lbl = (cred.label if cred else "") or ""
    m_uid = mask_uid(remote_uid)
    eff_role = (account_role or (cred.account_role if cred else "") or "").strip().lower()

    disp_en, disp_zh = format_account_display_names(
        account_id=acct_id,
        meta=meta,
        custom_label=custom_lbl,
        account_role=eff_role,
        masked_uid=m_uid,
        sibling_count=sibling_count,
        ordinal=ordinal,
    )

    bundle.venue_id = acct_id
    ex_card = dict(bundle.exchange_card)
    ex_card["venue_id"] = acct_id
    ex_card["adapter_id"] = adapter_id
    ex_card["display_name"] = disp_en
    ex_card["display_name_zh"] = disp_zh
    ex_card["color_token"] = meta.color_token
    if eff_role:
        ex_card["account_role"] = eff_role
    if m_uid:
        ex_card["masked_uid"] = m_uid
    if custom_lbl:
        ex_card["account_label"] = custom_lbl
    bundle.exchange_card = ex_card

    is_sec = is_secondary_account_id(acct_id)
    j_map = journals or {}
    for tr in bundle.trades:
        tr["venue_id"] = acct_id
        tr["venue_name"] = disp_en
        tr["venue_name_zh"] = disp_zh
        tr["color_token"] = meta.color_token
        if is_sec:
            orig_tid = str(tr.get("trade_id") or "")
            pref = f"{acct_id}__"
            if orig_tid and not orig_tid.startswith(pref):
                new_tid = f"{pref}{orig_tid}"
                tr["trade_id"] = new_tid
                j_entry = j_map.get(new_tid)
                if isinstance(j_entry, Mapping):
                    tr["tags"] = list(j_entry.get("tags") or [])
                    tr["note"] = str(j_entry.get("note") or "")

    for pos in bundle.open_positions:
        pos["venue_id"] = acct_id
        pos["venue_name"] = disp_en
        pos["venue_name_zh"] = disp_zh
        pos["color_token"] = meta.color_token

    for sh in bundle.spot_holdings:
        sh["venue_id"] = acct_id
        sh["venue_name"] = disp_en
        sh["color_token"] = meta.color_token

    for sw in bundle.spot_wallet_balances:
        sw["venue_id"] = acct_id
        sw["venue_name"] = disp_en
        sw["color_token"] = meta.color_token

    for rs in bundle.reconcile_streams:
        rs["account_id"] = acct_id
        rs["account_display"] = disp_en
        rs["base_exchange_en"] = meta.display_name
    if sibling_count > 1 or disp_zh != (meta.display_name_zh or meta.display_name):
        base_en = meta.display_name
        base_zh = meta.display_name_zh or meta.display_name
        for rs in bundle.reconcile_streams:
            for k_zh in ("label_zh", "title_zh"):
                if rs.get(k_zh):
                    val_zh = str(rs[k_zh])
                    if base_zh in val_zh:
                        rs[k_zh] = val_zh.replace(base_zh, disp_zh, 1)
                    elif disp_zh not in val_zh:
                        rs[k_zh] = f"{disp_zh} · {val_zh}"
            for k_en in ("label", "title"):
                if rs.get(k_en):
                    val_en = str(rs[k_en])
                    if base_en in val_en:
                        rs[k_en] = val_en.replace(base_en, disp_en, 1)
                    elif disp_en not in val_en:
                        rs[k_en] = f"{disp_en} · {val_en}"

    ct = bundle.copy_trading
    if ct.has_copy_trading:
        if ct.platform is not None:
            ct.platform["venue_id"] = acct_id
            ct.platform["venue_name"] = disp_en
            ct.platform["venue_name_zh"] = disp_zh
            ct.platform["color_token"] = meta.color_token
        for row in ct.transfers:
            row["venue_id"] = acct_id
            row["venue_name"] = disp_en
            row["venue_name_zh"] = disp_zh
        for row in ct.rebates:
            row["venue_id"] = acct_id
            row["venue_name"] = disp_en
            row["venue_name_zh"] = disp_zh

    st = bundle.stocks
    if st.venue_fee_rate is not None:
        st.venue_fee_rate["venue_id"] = acct_id
        st.venue_fee_rate["venue_name"] = disp_en
        st.venue_fee_rate["venue_name_zh"] = disp_zh
    for h in st.holdings:
        h["venue_id"] = acct_id
        h["venue_name"] = disp_en
        h["venue_name_zh"] = disp_zh
    for cl in st.cash_ledger:
        cl["venue_id"] = acct_id
        cl["venue_name"] = disp_en
        cl["venue_name_zh"] = disp_zh
    for pp in st.perpetuals:
        pp["venue_id"] = acct_id
        pp["venue_name"] = disp_en
        pp["venue_name_zh"] = disp_zh

    return bundle


def stamp_fee_row_for_account(
    fee_row: dict[str, Any] | None,
    bundle: "VenueLedgerBundle",
) -> dict[str, Any] | None:
    if fee_row is None:
        return None
    ex = bundle.exchange_card
    out = dict(fee_row)
    out["venue_id"] = bundle.venue_id
    out["adapter_id"] = ex.get("adapter_id") or parse_adapter_id(bundle.venue_id)
    out["display_name"] = ex["display_name"]
    out["display_name_zh"] = ex.get("display_name_zh") or ex["display_name"]
    out["color_token"] = ex["color_token"]
    return out
