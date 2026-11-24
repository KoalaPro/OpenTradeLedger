"""Strict HTTP GET-only & path-whitelist guard shared across all exchange adapters."""

from __future__ import annotations

import posixpath
from urllib.parse import unquote, urlsplit


class ReadOnlyGuardError(PermissionError):
    """Raised if any client attempts a non-GET or non-whitelisted HTTP request."""


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
