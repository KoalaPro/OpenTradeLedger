# Contributing to OpenTradeLedger

Thank you for your interest in contributing to **OpenTradeLedger (交易账本)**!

This project is designed around two core principles:
1. **Strict Read-Only Safety**: Every exchange client must only issue `GET` requests against explicitly allowlisted read-only endpoints.
2. **Zero-Hardcode Extensibility**: Adding a new exchange must not require editing hardcoded counts or UI lists in the frontend.

---

## Adding a New Read-Only Exchange Adapter

You can add support for a new exchange (for example, Hyperliquid, Kraken, or KuCoin) in **4 steps**:

### 1. Create the Read-Only API Client
Create `backend/src/opentradeledger/adapters/<venue_id>/client.py`:
- Define an explicit `ALLOWED_READONLY_PATHS` allowlist of read-only endpoints for that exchange.
- Before sending any HTTP request, **always** call:
  ```python
  from opentradeledger.security.readonly_guard import assert_readonly_request

  assert_readonly_request("GET", path, allowed_paths=ALLOWED_READONLY_PATHS)
  ```
- Implement a normalizer function that produces per-leg fee disclosure (`entry_leg` and `exit_leg`) whenever the venue returns open/close fills separately.

### 2. Implement `BaseExchangeAdapter`
In `backend/src/opentradeledger/adapters/registry.py`, subclass `BaseExchangeAdapter` and declare its `AdapterMeta`:

```python
class ExampleAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="example",
        display_name="Example Exchange",
        display_name_zh="示例交易所",
        color_token="example",
        requires_passphrase=False,
        capabilities=ExchangeCapabilities(futures=True, rebates=True),
        api_base_url="https://api.example.com",
        docs_url="https://docs.example.com",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> ExampleReadOnlyClient:
        ...

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        ...
```

Register the adapter in `AdapterRegistry.default()`.

### 3. Optional: Add Env Var Mapping & Brand Color
- Add `EXAMPLE_API_KEY` / `EXAMPLE_API_SECRET` placeholders to `.env.example` and `vault.py`'s `import_from_dotenv()`.
- Optionally add a brand accent CSS variable `--venue-example` in `web/src/styles/theme.css` (unconfigured venues automatically fall back to a clean neutral badge).

### 4. Write Tests & Run the Quality Gate
Add unit tests in `backend/tests/test_adapters_registry.py` covering:
- Registry discovery of the new adapter
- Signature / request formatting (using mocked HTTP transport — no live network calls in tests)
- Strict rejection of `POST` / order / withdrawal paths by `assert_readonly_request`
- Per-leg entry & exit fee normalization

Before opening a Pull Request, run all checks:

```bash
python scripts/privacy_audit.py
pytest -q backend/tests
cd web && npm run typecheck && npm run test && npm run build
```

---

## Privacy & Security Rules for Contributors

- **Never** commit real API keys, account UIDs, personal balances, database files (`.db`), `.env` files, or local absolute filesystem paths.
- `scripts/privacy_audit.py` must return `OK (0 findings)` on every commit.
- Never relax `assert_readonly_request()` or add write/order/withdrawal endpoints to any exchange client.
