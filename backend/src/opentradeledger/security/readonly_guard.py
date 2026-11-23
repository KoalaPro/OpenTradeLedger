"""Strict HTTP GET-only & path-whitelist guard shared across all exchange adapters."""

from __future__ import annotations

from urllib.parse import urlsplit


class ReadOnlyViolationError(RuntimeError):
    """Raised whenever code attempts a non-GET request or non-whitelisted endpoint."""


def assert_readonly_request(
    method: str,
    path: str,
    allowed_prefixes: tuple[str, ...],
) -> None:
    normalized_method = (method or "").strip().upper()
    if normalized_method != "GET":
        raise ReadOnlyViolationError(
            f"Read-only guard blocked non-GET HTTP method: {normalized_method!r} on {path!r}"
        )

    clean_path = urlsplit(path).path or ""
    if not any(clean_path == prefix or clean_path.startswith(prefix) for prefix in allowed_prefixes):
        raise ReadOnlyViolationError(
            f"Read-only guard blocked non-whitelisted path: {clean_path!r}"
        )
