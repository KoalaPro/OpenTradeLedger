"""Evidence required to bind an EasiCoin ledger to its signed private account UID."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any


class EasiCoinIdentityError(RuntimeError):
    """Raised before any venue write when the signed account cannot own this archive."""

    messages = {
        "ACCOUNT_UID_UNAVAILABLE": (
            "EasiCoin did not return an account UID. Check read access to the account ID and try again.",
            "易币未返回账户 UID，请检查账户身份接口的读取权限后重试。",
        ),
        "ACCOUNT_UID_MISMATCH": (
            "This key belongs to a different EasiCoin UID. Use a key from the account that owns this ledger.",
            "这组密钥属于另一个易币 UID，请使用此账本原账户的只读密钥。",
        ),
        "ARCHIVE_OWNER_UNVERIFIED": (
            "Some archived EasiCoin data could not be verified against this account. "
            "The archive was preserved. Check the original account and its history before reconnecting.",
            "部分易币封存数据无法与当前账户核对，历史数据已保留。请核对原账户和交易历史后再绑定。",
        ),
    }

    def __init__(self, code: str, stream: str = "", count: int = 0) -> None:
        self.code = code
        self.reason, self.reason_zh = self.messages[code]
        if stream:
            self.reason += f" Unverified {stream} records: {count}."
            self.reason_zh += f" 待核对的 {stream} 记录共 {count} 条。"
        super().__init__(self.reason)


def account_uid(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return ""
    uid = str(value).strip()
    return "" if uid.lower() in {"", "0", "null", "none", "unknown"} else uid


# Exchange IDs and immutable fields that corroborate their identity. New response
# fields and mutable order status/fill totals are irrelevant to this comparison.
_RECORD_KEYS = {
    "closed_pnl": (("order_id",), ("symbol", "created_at"), ("side", "qty", "closed_size", "avg_entry_price", "avg_exit_price", "closed_pnl")),
    "fills": (("exec_id",), ("symbol", "exec_time"), ("order_id", "side", "exec_price", "exec_qty")),
    "orders": (("order_id",), ("symbol", "created_time"), ("side",)),
    "transfers": (("transfer_id", "id"), ("coin", "time", "amount", "from_wallet", "to_wallet"), ()),
}
_TEXT_FIELDS = {"symbol", "side", "coin", "from_wallet", "to_wallet"}


def _record_id(row: dict[str, Any], id_fields: tuple[str, ...]) -> str:
    return next((str(row[key]).strip() for key in id_fields if row.get(key)), "")


def transfer_record_id(row: dict[str, Any]) -> str:
    """Keep the legacy record key when EasiCoin omits a transfer ID."""
    return f"{int(float(row.get('time') or 0))}_{row.get('coin')}_{row.get('amount')}_{row.get('from_wallet')}_{row.get('to_wallet')}"


def _same_field(key: str, archived: Any, remote: Any) -> bool:
    if archived is None or remote is None or archived == "" or remote == "":
        return False
    if key in _TEXT_FIELDS:
        return str(archived).strip().upper() == str(remote).strip().upper()
    if key == "order_id":
        return str(archived) == str(remote)
    try:
        left, right = Decimal(str(archived)), Decimal(str(remote))
        return left.is_finite() and right.is_finite() and left == right
    except InvalidOperation:
        return False


def verify_archive_rows(stream: str, archived: list[dict[str, Any]], fetched: list[dict[str, Any]], uid: str) -> None:
    """Verify an unbound partition without assuming its last live UID owns all rows."""
    id_fields, required, optional = _RECORD_KEYS[stream]
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    transfers_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in fetched:
        if rec_id := _record_id(row, id_fields):
            by_id[rec_id].append(row)
        if stream == "transfers" and all(row.get(key) not in (None, "") for key in required):
            transfers_by_key[transfer_record_id(row)].append(row)

    unverified = 0
    for row in archived:
        row_uid = account_uid(row.get("userid"))
        rec_id = _record_id(row, id_fields)
        matches = by_id.get(rec_id, [])
        if stream == "transfers" and not rec_id:
            matches = transfers_by_key.get(transfer_record_id(row), [])
            # Two distinct upstream transfers can share a coarse timestamp and amount.
            if len({_record_id(remote, id_fields) for remote in matches}) > 1:
                matches = []
        if row_uid and row_uid != uid:
            unverified += 1
        elif matches:
            fields = required + tuple(key for key in optional if row.get(key) not in (None, ""))
            if not all(_same_field(key, row.get(key), remote.get(key)) for remote in matches for key in fields):
                unverified += 1
        elif row_uid != uid:
            # An explicitly owned historical row can predate the exchange lookback.
            unverified += 1
    if unverified:
        raise EasiCoinIdentityError("ARCHIVE_OWNER_UNVERIFIED", stream, unverified)
