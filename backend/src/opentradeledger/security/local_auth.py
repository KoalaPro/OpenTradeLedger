"""Local-first password authentication & HMAC session manager for NAS / self-hosted deployments.

Security properties:
- Passwords are NEVER stored in plaintext; hashed with `hashlib.scrypt` (16-byte CSPRNG salt).
- Stored in `<data_dir>/.auth.json` with strict `0600` file permissions.
- Stateless HMAC-SHA256 signed session tokens with `token_version` invalidation on password change/reset.
- Built-in brute-force cooldown (5 failed attempts -> 60s lock per client key).
- Safe local emergency reset via `--reset-password` CLI flag (leaves SQLite DB & exchange vault untouched).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any

AUTH_COOKIE_NAME = "otl_session"
REMEMBER_MAX_AGE_SEC = 30 * 86400
SESSION_ONLY_TTL_SEC = 24 * 3600
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_SEC = 60
MIN_PASSWORD_LEN = 4


class AuthRateLimitError(PermissionError):
    def __init__(self, retry_after_sec: int) -> None:
        self.retry_after_sec = max(1, int(retry_after_sec))
        super().__init__(f"Too many failed attempts. Try again in {self.retry_after_sec}s.")


class AuthValidationError(ValueError):
    """Raised when password validation or current-password verification fails."""


def _hash_password(password: str, salt_bytes: bytes) -> str:
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_bytes,
        n=16384,
        r=8,
        p=1,
        dklen=32,
    )
    return derived.hex()


class LocalAuthManager:
    def __init__(
        self,
        data_dir: Path | str,
        *,
        require_auth: bool | None = None,
        initial_password: str | None = None,
        auto_prompt_setup: bool = True,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.auth_file = self.data_dir / ".auth.json"

        env_req = os.getenv("OTL_REQUIRE_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}
        self.require_auth_env = env_req if require_auth is None else bool(require_auth)
        self.auto_prompt_setup = bool(auto_prompt_setup)
        self._failed_attempts: dict[str, list[float]] = {}
        self._lockout_until: dict[str, float] = {}

        init_pwd = initial_password if initial_password is not None else os.getenv("OTL_INITIAL_PASSWORD", "").strip()
        if init_pwd and not self.auth_file.exists():
            self.setup_password(init_pwd)

    def _load(self) -> dict[str, Any]:
        if not self.auth_file.exists():
            return {
                "initialized": False,
                "enabled": False,
                "salt_hex": "",
                "hash_hex": "",
                "secret_hex": "",
                "token_version": 1,
                "updated_ms": 0,
            }
        try:
            raw = json.loads(self.auth_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
        return {
            "initialized": False,
            "enabled": False,
            "salt_hex": "",
            "hash_hex": "",
            "secret_hex": "",
            "token_version": 1,
            "updated_ms": 0,
        }

    def _save(self, state: dict[str, Any]) -> None:
        payload = json.dumps(state, indent=2, ensure_ascii=False)
        tmp_path = self.auth_file.with_suffix(".json.tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        tmp_path.replace(self.auth_file)
        try:
            os.chmod(self.auth_file, 0o600)
        except OSError:
            pass

    def _check_rate_limit(self, client_key: str) -> None:
        now = time.time()
        until = self._lockout_until.get(client_key, 0.0)
        if until > now:
            raise AuthRateLimitError(int(until - now + 0.999))

    def _record_failure(self, client_key: str) -> int:
        now = time.time()
        window = [t for t in self._failed_attempts.get(client_key, []) if now - t <= 300.0]
        window.append(now)
        self._failed_attempts[client_key] = window
        if len(window) >= MAX_FAILED_ATTEMPTS:
            self._lockout_until[client_key] = now + LOCKOUT_DURATION_SEC
            self._failed_attempts[client_key] = []
            return 0
        return max(0, MAX_FAILED_ATTEMPTS - len(window))

    def _clear_failures(self, client_key: str) -> None:
        self._failed_attempts.pop(client_key, None)
        self._lockout_until.pop(client_key, None)

    def _verify_password_against_state(self, password: str, state: dict[str, Any]) -> bool:
        salt_hex = str(state.get("salt_hex") or "")
        hash_hex = str(state.get("hash_hex") or "")
        if not salt_hex or not hash_hex:
            return False
        try:
            salt = bytes.fromhex(salt_hex)
        except ValueError:
            return False
        candidate = _hash_password(password, salt)
        return hmac.compare_digest(candidate, hash_hex)

    def issue_session_token(self, *, remember: bool = True) -> tuple[str, int]:
        state = self._load()
        secret_hex = str(state.get("secret_hex") or "")
        if not secret_hex:
            secret_hex = secrets.token_hex(32)
            state["secret_hex"] = secret_hex
            self._save(state)
        version = int(state.get("token_version") or 1)
        ttl = REMEMBER_MAX_AGE_SEC if remember else SESSION_ONLY_TTL_SEC
        exp = int(time.time()) + ttl
        nonce = secrets.token_hex(8)
        body = f"v{version}.{exp}.{nonce}"
        sig = hmac.new(bytes.fromhex(secret_hex), body.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{body}.{sig}", ttl

    def verify_session_token(self, token: str | None) -> bool:
        if not token or not isinstance(token, str):
            return False
        parts = token.strip().split(".")
        if len(parts) != 4:
            return False
        ver_str, exp_str, nonce, sig = parts
        if not ver_str.startswith("v"):
            return False
        try:
            ver = int(ver_str[1:])
            exp = int(exp_str)
        except ValueError:
            return False
        if exp <= int(time.time()):
            return False

        state = self._load()
        if not state.get("enabled"):
            return True
        if ver != int(state.get("token_version") or 1):
            return False
        secret_hex = str(state.get("secret_hex") or "")
        if not secret_hex:
            return False
        body = f"v{ver}.{exp}.{nonce}"
        expected = hmac.new(bytes.fromhex(secret_hex), body.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)

    def get_status(self, session_token: str | None = None, *, demo_mode: bool = False) -> dict[str, Any]:
        state = self._load()
        initialized = bool(state.get("initialized"))
        has_hash = bool(state.get("salt_hex") and state.get("hash_hex"))
        enabled = bool(state.get("enabled")) and has_hash

        # Never bypass an active password lock or OTL_REQUIRE_AUTH=true, even when Demo Mode is active
        if enabled:
            is_authed = self.verify_session_token(session_token)
            return {
                "demo_bypass": False,
                "initialized": True,
                "enabled": True,
                "authenticated": is_authed,
                "require_setup": False,
                "can_skip": not self.require_auth_env,
            }

        if self.require_auth_env and not has_hash:
            return {
                "demo_bypass": False,
                "initialized": False,
                "enabled": False,
                "authenticated": False,
                "require_setup": True,
                "can_skip": False,
            }

        if demo_mode:
            return {
                "demo_bypass": True,
                "initialized": True,
                "enabled": False,
                "authenticated": True,
                "require_setup": False,
                "can_skip": True,
            }

        if not initialized:
            if not self.auto_prompt_setup and not self.require_auth_env:
                return {
                    "demo_bypass": False,
                    "initialized": False,
                    "enabled": False,
                    "authenticated": True,
                    "require_setup": False,
                    "can_skip": True,
                }
            return {
                "demo_bypass": False,
                "initialized": False,
                "enabled": False,
                "authenticated": False,
                "require_setup": True,
                "can_skip": not self.require_auth_env,
            }

        return {
            "demo_bypass": False,
            "initialized": True,
            "enabled": False,
            "authenticated": True,
            "require_setup": False,
            "can_skip": True,
        }

    def is_request_allowed(self, session_token: str | None, *, demo_mode: bool = False) -> bool:
        st = self.get_status(session_token, demo_mode=demo_mode)
        return bool(st["authenticated"]) and not bool(st["require_setup"])

    def setup_password(self, password: str) -> None:
        pwd = (password or "").strip()
        if len(pwd) < MIN_PASSWORD_LEN:
            raise AuthValidationError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
        prev = self._load()
        if prev.get("enabled") and prev.get("hash_hex"):
            raise AuthValidationError("Password is already enabled; use change-password instead")
        if prev.get("initialized") and not self.require_auth_env:
            raise AuthValidationError("Initial setup already completed; enable password via Settings")
        salt = secrets.token_bytes(16)
        state = {
            "initialized": True,
            "enabled": True,
            "salt_hex": salt.hex(),
            "hash_hex": _hash_password(pwd, salt),
            "secret_hex": secrets.token_hex(32),
            "token_version": int(prev.get("token_version") or 0) + 1,
            "updated_ms": int(time.time() * 1000),
        }
        self._save(state)

    def skip_setup(self) -> None:
        if self.require_auth_env:
            raise AuthValidationError("Cannot skip password setup when OTL_REQUIRE_AUTH=true")
        prev = self._load()
        if prev.get("enabled") and prev.get("hash_hex"):
            raise AuthValidationError("Password protection is already enabled")
        state = {
            "initialized": True,
            "enabled": False,
            "salt_hex": "",
            "hash_hex": "",
            "secret_hex": prev.get("secret_hex") or secrets.token_hex(32),
            "token_version": int(prev.get("token_version") or 1),
            "updated_ms": int(time.time() * 1000),
        }
        self._save(state)

    def authenticate(self, password: str, *, client_key: str = "local") -> bool:
        self._check_rate_limit(client_key)
        state = self._load()
        if not state.get("enabled"):
            return True
        if self._verify_password_against_state(password or "", state):
            self._clear_failures(client_key)
            return True
        remaining = self._record_failure(client_key)
        if remaining == 0:
            raise AuthRateLimitError(LOCKOUT_DURATION_SEC)
        raise AuthValidationError(f"Incorrect password ({remaining} attempts remaining before cooldown)")

    def change_password(self, current_password: str, new_password: str, *, client_key: str = "local") -> None:
        self._check_rate_limit(client_key)
        new_pwd = (new_password or "").strip()
        if len(new_pwd) < MIN_PASSWORD_LEN:
            raise AuthValidationError(f"New password must be at least {MIN_PASSWORD_LEN} characters")
        state = self._load()
        if state.get("enabled") and state.get("hash_hex"):
            if not self._verify_password_against_state(current_password or "", state):
                self._record_failure(client_key)
                raise AuthValidationError("Current password is incorrect")
        self._clear_failures(client_key)
        salt = secrets.token_bytes(16)
        state.update(
            {
                "initialized": True,
                "enabled": True,
                "salt_hex": salt.hex(),
                "hash_hex": _hash_password(new_pwd, salt),
                "secret_hex": secrets.token_hex(32),
                "token_version": int(state.get("token_version") or 0) + 1,
                "updated_ms": int(time.time() * 1000),
            }
        )
        self._save(state)

    def disable_password(self, current_password: str, *, client_key: str = "local") -> None:
        if self.require_auth_env:
            raise AuthValidationError("Cannot disable password when OTL_REQUIRE_AUTH=true")
        self._check_rate_limit(client_key)
        state = self._load()
        if state.get("enabled") and state.get("hash_hex"):
            if not self._verify_password_against_state(current_password or "", state):
                self._record_failure(client_key)
                raise AuthValidationError("Current password is incorrect")
        self._clear_failures(client_key)
        state.update(
            {
                "initialized": True,
                "enabled": False,
                "salt_hex": "",
                "hash_hex": "",
                "secret_hex": secrets.token_hex(32),
                "token_version": int(state.get("token_version") or 0) + 1,
                "updated_ms": int(time.time() * 1000),
            }
        )
        self._save(state)

    def reset_password(self) -> bool:
        """Emergency CLI reset: clears `.auth.json` so next web visit prompts fresh setup."""
        self._failed_attempts.clear()
        self._lockout_until.clear()
        if self.auth_file.exists():
            self.auth_file.unlink()
            return True
        return False
