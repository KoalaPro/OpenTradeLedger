"""Validate amounts before optional wallet responses can replace a stored balance."""

from __future__ import annotations

from collections.abc import Callable
from math import isfinite
from typing import Any


class InvalidWalletSnapshot(ValueError):
    """A stored balance cannot be used to value this account."""


def finite_amount(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        return isfinite(float(value))
    except (ValueError, OverflowError):
        return False


def has_amount(row: Any, *amount_keys: str) -> bool:
    """At least one supported amount must exist; any supplied alias must be numeric."""
    if not isinstance(row, dict):
        return False
    values = [row[key] for key in amount_keys if key in row]
    return bool(values) and all(finite_amount(value) for value in values)


def optional_amounts(row: dict[str, Any], *keys: str) -> bool:
    return all(row.get(key) in (None, "") or finite_amount(row[key]) for key in keys)


def wallet_rows(
    rows: Any, *, names: tuple[str, ...], amounts: tuple[str, ...], optional: tuple[str, ...] = ()
) -> bool:
    return isinstance(rows, list) and all(
        isinstance(row, dict)
        and any(isinstance(row.get(key), str) and row[key].strip() for key in names)
        and has_amount(row, *amounts)
        and optional_amounts(row, *optional)
        for row in rows
    )


def require_wallet_cache(
    snapshot: dict[str, Any],
    previous: dict[str, Any] | None,
    validators: dict[str, Callable[[Any], bool]],
) -> None:
    """A malformed wallet payload may reuse a verified prior value, including zero."""
    for field in snapshot.pop("_invalid_wallet_fields", ()):
        if not validators[field]((previous or {}).get(field)):
            raise ValueError(f"Malformed {field} wallet response and no valid cached balance")
