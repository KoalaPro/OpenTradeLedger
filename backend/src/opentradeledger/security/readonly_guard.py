"""Strict HTTP GET-only & explicit read-only query guard shared across all exchange adapters."""

from __future__ import annotations

import posixpath
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit


class ReadOnlyGuardError(PermissionError):
    """Raised if any client attempts a non-readonly or non-whitelisted HTTP request."""


class ReadOnlyViolationError(RuntimeError):
    """Raised whenever code attempts a write/order/transfer HTTP request or non-whitelisted endpoint."""


_FORBIDDEN_POST_PAYLOAD_KEYS = frozenset(
    {
        "signature",
        "action",
        "nonce",
        "orders",
        "order",
        "withdraw",
        "amount_to_withdraw",
        "privateKey",
        "secret",
    }
)


def _normalize_and_check_path(path: str, allowed_prefixes: tuple[str, ...]) -> str:
    raw_path = unquote(urlsplit(path).path or "")
    if "\\" in raw_path or ".." in raw_path.split("/"):
        raise ReadOnlyViolationError(
            f"Read-only guard blocked path traversal attempt: {raw_path!r}"
        )
    clean_path = posixpath.normpath(raw_path) if raw_path else ""
    if raw_path.endswith("/") and not clean_path.endswith("/"):
        clean_path += "/"
    if not any(clean_path == prefix or clean_path.startswith(prefix) for prefix in allowed_prefixes):
        raise ReadOnlyViolationError(
            f"Read-only guard blocked non-whitelisted path: {clean_path!r}"
        )
    return clean_path


def assert_readonly_request(
    method: str,
    path: str,
    allowed_prefixes: tuple[str, ...],
    *,
    allowed_post_paths: tuple[str, ...] = (),
    allowed_post_types: tuple[str, ...] | None = None,
    post_payload: Any = None,
) -> None:
    normalized_method = (method or "").strip().upper()
    if normalized_method == "GET":
        _normalize_and_check_path(path, allowed_prefixes)
        return

    if normalized_method == "POST" and allowed_post_paths:
        clean_path = _normalize_and_check_path(path, allowed_post_paths)
        if isinstance(post_payload, Mapping):
            bad_keys = _FORBIDDEN_POST_PAYLOAD_KEYS.intersection(post_payload.keys())
            if bad_keys:
                raise ReadOnlyViolationError(
                    f"Read-only guard blocked write/signed payload fields {sorted(bad_keys)!r} on POST {clean_path!r}"
                )
        if allowed_post_types is not None:
            if not isinstance(post_payload, Mapping):
                raise ReadOnlyViolationError(
                    f"Read-only guard requires dict payload with whitelisted 'type' on POST {clean_path!r}"
                )
            req_type = str(post_payload.get("type") or "").strip()
            if req_type not in allowed_post_types:
                raise ReadOnlyViolationError(
                    f"Read-only guard blocked non-whitelisted POST info type {req_type!r} on {clean_path!r}"
                )
        return

    raise ReadOnlyViolationError(
        f"Read-only guard blocked non-GET HTTP method: {normalized_method!r} on {path!r}"
    )
