"""Local Fernet-encrypted credential vault with optional .env compatibility import."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import BaseModel, Field

PLACEHOLDER_SUBSTRINGS = ("your_", "example", "placeholder", "xxxx", "changeme")

DOTENV_VENUE_MAP: dict[str, tuple[str, str, str | None]] = {
    "binance": ("BINANCE_API_KEY", "BINANCE_API_SECRET", None),
    "okx": ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"),
    "bybit": ("BYBIT_API_KEY", "BYBIT_API_SECRET", None),
    "gate": ("GATE_API_KEY", "GATE_API_SECRET", None),
    "bitget": ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"),
    "mexc": ("MEXC_API_KEY", "MEXC_API_SECRET", None),
    "easicoin": ("EASICOIN_API_KEY", "EASICOIN_API_SECRET", None),
}


def mask_secret(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return f"{raw[:2]}***{raw[-2:]}"
    return f"{raw[:4]}...{raw[-4:]}"


def _is_placeholder(value: str) -> bool:
    low = (value or "").strip().lower()
    return (not low) or any(hint in low for hint in PLACEHOLDER_SUBSTRINGS)


def _chmod_600(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class CredentialEditConflict(RuntimeError):
    """A form was submitted after the corresponding credential changed."""


class ExchangeCredentialRecord(BaseModel):
    venue_id: str
    label: str = ""
    api_key: str
    api_secret: str
    passphrase: str = ""
    enabled: bool = True
    extra: dict[str, str] = Field(default_factory=dict)
    revision: str = "legacy"


class CredentialVault:
    """Stores exchange read-only credentials encrypted at rest using Fernet."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.key_path = self.data_dir / ".vault.key"
        self.vault_path = self.data_dir / "vault.enc"

    def _load_or_create_fernet(self) -> Fernet:
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            self.key_path.write_bytes(key)
            _chmod_600(self.key_path)
        return Fernet(key)

    def encrypt_secret_str(self, value: str) -> str:
        raw = value or ""
        if not raw:
            return ""
        if raw.startswith("enc:v1:"):
            return raw
        token = self._load_or_create_fernet().encrypt(raw.encode("utf-8")).decode("ascii")
        return f"enc:v1:{token}"

    def decrypt_secret_str(self, value: str) -> str:
        raw = value or ""
        if not raw or not raw.startswith("enc:v1:"):
            return raw
        token = raw[len("enc:v1:") :].encode("ascii")
        try:
            return self._load_or_create_fernet().decrypt(token).decode("utf-8")
        except Exception:
            return ""

    def _read_all(self) -> dict[str, dict[str, Any]]:
        if not self.vault_path.exists():
            return {}
        fernet = self._load_or_create_fernet()
        decrypted = fernet.decrypt(self.vault_path.read_bytes())
        data = json.loads(decrypted.decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def _write_all(self, payload: dict[str, dict[str, Any]]) -> None:
        fernet = self._load_or_create_fernet()
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        encrypted = fernet.encrypt(raw)
        tmp = self.vault_path.with_suffix(".enc.tmp")
        tmp.write_bytes(encrypted)
        _chmod_600(tmp)
        tmp.replace(self.vault_path)
        _chmod_600(self.vault_path)

    def revision(self, venue_id: str) -> str:
        existing = self.get_decrypted(venue_id)
        return existing.revision if existing is not None else "absent"

    def upsert(self, record: ExchangeCredentialRecord) -> dict[str, Any]:
        venue_id = record.venue_id.strip().lower()
        store = self._read_all()
        normalized = record.model_copy(update={"venue_id": venue_id, "revision": uuid4().hex})
        store[venue_id] = normalized.model_dump()
        self._write_all(store)
        return self._mask_record(normalized)

    def delete(self, venue_id: str) -> bool:
        vid = venue_id.strip().lower()
        store = self._read_all()
        if vid not in store:
            return False
        del store[vid]
        self._write_all(store)
        return True

    def get_decrypted(self, venue_id: str) -> ExchangeCredentialRecord | None:
        vid = venue_id.strip().lower()
        raw = self._read_all().get(vid)
        if not raw:
            return None
        return ExchangeCredentialRecord.model_validate(raw)

    def list_decrypted(self) -> list[ExchangeCredentialRecord]:
        store = self._read_all()
        return [ExchangeCredentialRecord.model_validate(v) for _, v in sorted(store.items())]

    def list_masked(self) -> list[dict[str, Any]]:
        return [self._mask_record(rec) for rec in self.list_decrypted()]

    @staticmethod
    def _mask_record(rec: ExchangeCredentialRecord) -> dict[str, Any]:
        return {
            "venue_id": rec.venue_id,
            "label": rec.label or rec.venue_id.upper(),
            "masked_api_key": mask_secret(rec.api_key),
            "revision": rec.revision,
            "has_passphrase": bool(rec.passphrase.strip()),
            "enabled": rec.enabled,
        }

    def dotenv_candidates(self, env_path: Path, overwrite: bool = False) -> list[ExchangeCredentialRecord]:
        """Read currently eligible credentials without mutating the encrypted vault."""
        if not env_path.exists():
            return []
        kv: dict[str, str] = {}
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            kv[k.strip()] = v.strip().strip('"').strip("'")

        candidates: list[ExchangeCredentialRecord] = []
        for venue_id, (k_key, k_sec, k_pass) in DOTENV_VENUE_MAP.items():
            if not overwrite and self.get_decrypted(venue_id) is not None:
                continue
            api_key = kv.get(k_key, "")
            api_secret = kv.get(k_sec, "")
            if venue_id == "gate" and (_is_placeholder(api_key) or _is_placeholder(api_secret)):
                api_key = kv.get("GATE_MANUAL_API_KEY", api_key)
                api_secret = kv.get("GATE_MANUAL_API_SECRET", api_secret)
            passphrase = kv.get(k_pass, "") if k_pass else ""
            if _is_placeholder(api_key) or _is_placeholder(api_secret):
                continue
            if k_pass and _is_placeholder(passphrase):
                passphrase = ""
            candidates.append(
                ExchangeCredentialRecord(
                    venue_id=venue_id,
                    label=venue_id.upper(),
                    api_key=api_key,
                    api_secret=api_secret,
                    passphrase=passphrase,
                    enabled=True,
                )
            )
        return candidates

    def import_from_dotenv(self, env_path: Path, overwrite: bool = False) -> list[str]:
        """Import local keys. EasiCoin requires verified sync before persistence."""
        imported = []
        for candidate in self.dotenv_candidates(env_path, overwrite):
            if candidate.venue_id != "easicoin":
                self.upsert(candidate)
                imported.append(candidate.venue_id)
        return imported
