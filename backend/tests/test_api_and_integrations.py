from pathlib import Path

from fastapi.testclient import TestClient

from opentradeledger.api.app import create_app


def test_full_api_lifecycle_demo_exchanges_settings_and_ai(tmp_path: Path):
    app = create_app(data_dir=tmp_path, demo_mode=True)
    client = TestClient(app)

    # 1. Health check enforces read-only posture
    h = client.get("/api/v1/health")
    assert h.status_code == 200
    assert h.json()["read_only"] is True
    assert h.json()["trading_enabled"] is False
    assert "adaptive_polling" not in h.json()

    # 2. Dashboard returns dynamic 6-venue demo payload with OKX & Bybit
    d = client.get("/api/v1/dashboard")
    assert d.status_code == 200
    payload = d.json()
    assert payload["demo_mode"] is True
    vids = {e["venue_id"] for e in payload["exchanges"]}
    assert {"binance", "okx", "bybit", "gate", "bitget", "mexc"}.issubset(vids)
    assert payload["trades"][0]["entry_leg"]["gross_fee_usd"] > 0
    assert payload["trades"][0]["exit_leg"]["gross_fee_usd"] > 0

    # 3. Exchanges endpoint lists all 11 registered adapters and encrypts new OKX/Bybit keys
    ex = client.get("/api/v1/exchanges").json()
    assert len(ex["adapters"]) == 11
    assert {a["venue_id"] for a in ex["adapters"]} == {
        "binance",
        "okx",
        "bybit",
        "gate",
        "bitget",
        "mexc",
        "hyperliquid",
        "kucoin",
        "bingx",
        "htx",
        "easicoin",
    }

    # OKX requires passphrase
    bad_okx = client.post(
        "/api/v1/exchanges",
        json={"venue_id": "okx", "api_key": "okx_k_1111111111", "api_secret": "okx_s_1111111111", "passphrase": ""},
    )
    assert bad_okx.status_code == 400

    good_okx = client.post(
        "/api/v1/exchanges",
        json={
            "venue_id": "okx",
            "label": "OKX ReadOnly",
            "api_key": "okx_k_1111111111",
            "api_secret": "okx_s_1111111111",
            "passphrase": "okx_pass_value",
        },
    )
    assert good_okx.status_code == 200
    assert "api_secret" not in good_okx.json()["credential"]

    good_bybit = client.post(
        "/api/v1/exchanges",
        json={"venue_id": "bybit", "api_key": "byb_k_2222222222", "api_secret": "byb_s_2222222222"},
    )
    assert good_bybit.status_code == 200

    # 4. Settings update preserves secrets and hides raw tokens on GET
    s_put = client.put(
        "/api/v1/settings",
        json={
            "locale": "en",
            "theme": "light",
            "polarity": "global",
            "goal_enabled": True,
            "goal_target_usd": 50000,
            "ai_base_url": "https://api.openai.com/v1",
            "ai_api_key": "sk-demo-secret-key-for-test",
            "ai_model": "gpt-4o-mini",
        },
    )
    assert s_put.status_code == 200
    s_data = s_put.json()
    assert s_data["locale"] == "en"
    assert s_data["theme"] == "light"
    assert s_data["polarity"] == "global"
    assert s_data["ai_api_key"] == ""
    assert s_data["ai_api_key_set"] is True

    # 5. AI review falls back cleanly when remote call is unreachable or unconfigured
    client.put("/api/v1/settings", json={"ai_base_url": ""})
    rev = client.post("/api/v1/ai-review", json={"range_key": "all"})
    assert rev.status_code == 200
    assert rev.json()["model"] == "local-actuarial"
    assert len(rev.json()["highlights"]) == 3

    # 6. Delete exchange credential works cleanly
    assert client.delete("/api/v1/exchanges/bybit").status_code == 200
    assert client.delete("/api/v1/exchanges/bybit").status_code == 404
