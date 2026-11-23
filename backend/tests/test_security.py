import os
import stat
from pathlib import Path

import pytest

from opentradeledger.security.readonly_guard import ReadOnlyViolationError, assert_readonly_request
from opentradeledger.security.vault import CredentialVault, ExchangeCredentialRecord, mask_secret


def test_readonly_guard_blocks_non_get_and_unlisted_paths():
    assert_readonly_request("GET", "/api/v5/account/balance", ("/api/v5/account/",))
    assert_readonly_request("get", "/v5/execution/list?category=linear", ("/v5/execution/list",))

    for bad_method in ("POST", "PUT", "DELETE", "PATCH"):
        with pytest.raises(ReadOnlyViolationError):
            assert_readonly_request(bad_method, "/api/v5/account/balance", ("/api/v5/account/",))

    with pytest.raises(ReadOnlyViolationError):
        assert_readonly_request("GET", "/api/v5/trade/order", ("/api/v5/account/", "/api/v5/trade/fills"))


def test_credential_vault_encrypts_at_rest_and_masks_output(tmp_path: Path):
    vault = CredentialVault(tmp_path)
    secret_key = "demo_super_secret_value_for_unit_test_999"
    api_key = "demo_public_key_for_unit_test_12345"

    vault.upsert(
        ExchangeCredentialRecord(
            venue_id="okx",
            label="OKX Main",
            api_key=api_key,
            api_secret=secret_key,
            passphrase="demo_passphrase_value",
        )
    )

    # Key file must be chmod 0600 on POSIX
    key_mode = stat.S_IMODE(os.stat(tmp_path / ".vault.key").st_mode)
    assert key_mode == 0o600

    # Encrypted vault file must not expose plaintext secrets
    raw_bytes = (tmp_path / "vault.enc").read_bytes()
    assert secret_key.encode() not in raw_bytes
    assert api_key.encode() not in raw_bytes

    masked = vault.list_masked()
    assert len(masked) == 1
    assert masked[0]["venue_id"] == "okx"
    assert masked[0]["masked_api_key"] == mask_secret(api_key)
    assert masked[0]["has_passphrase"] is True
    assert "api_secret" not in masked[0]

    loaded = vault.get_decrypted("okx")
    assert loaded is not None
    assert loaded.api_secret == secret_key
    assert loaded.passphrase == "demo_passphrase_value"

    assert vault.delete("okx") is True
    assert vault.list_masked() == []


def test_credential_vault_imports_dotenv_for_all_supported_exchanges(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OKX_API_KEY=okx_demo_key_111111111111",
                "OKX_API_SECRET=okx_demo_secret_111111111111",
                "OKX_API_PASSPHRASE=okx_demo_pass",
                "BYBIT_API_KEY=bybit_demo_key_222222222222",
                "BYBIT_API_SECRET=bybit_demo_secret_222222222222",
                "GATE_API_KEY=your_readonly_gate_api_key",
                "GATE_API_SECRET=your_readonly_gate_api_secret",
            ]
        ),
        encoding="utf-8",
    )
    vault = CredentialVault(tmp_path)
    imported = vault.import_from_dotenv(env_file)
    assert sorted(imported) == ["bybit", "okx"]
    assert vault.get_decrypted("okx") is not None
    assert vault.get_decrypted("bybit") is not None
    # Placeholders must be ignored
    assert vault.get_decrypted("gate") is None
