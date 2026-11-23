from pathlib import Path

from opentradeledger.config import AppConfig, UserPreferences
from privacy_audit import run_privacy_audit


def test_default_user_preferences_contain_no_personal_hardcodes():
    prefs = UserPreferences()
    assert prefs.locale == "zh-CN"
    assert prefs.theme == "obsidian"
    assert prefs.polarity == "cn"
    assert prefs.privacy_mode is False
    assert prefs.goal_enabled is False
    assert prefs.goal_target_usd is None
    assert prefs.ai_base_url == ""
    assert prefs.ai_model == ""
    assert prefs.copy_watcher_targets == []
    assert prefs.modules.copy_trading is True
    assert prefs.modules.stocks is True


def test_app_config_defaults():
    cfg = AppConfig()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8012
    assert cfg.demo_mode is False


def test_privacy_audit_detects_private_leaks_and_passes_clean_tree(tmp_path: Path):
    clean_file = tmp_path / "clean.py"
    clean_file.write_text('API_KEY = "your_readonly_binance_api_key"\n', encoding="utf-8")
    assert run_privacy_audit(tmp_path) == []

    leaky_ip = tmp_path / "bad_ip.py"
    leaky_ip.write_text('URL = "http://192.168.1.50:8317/v1"\n', encoding="utf-8")
    findings = run_privacy_audit(tmp_path)
    assert any("private IP" in item for item in findings)
    leaky_ip.unlink()

    leaky_secret = tmp_path / "bad_key.env"
    leaky_secret.write_text("BINANCE_API_KEY=AbCdEfGhIjKlMnOpQrStUvWxYz1234567890ABCD\n", encoding="utf-8")
    findings = run_privacy_audit(tmp_path)
    assert any("credential" in item.lower() for item in findings)
