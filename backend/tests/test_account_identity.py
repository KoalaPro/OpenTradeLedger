"""Account ownership and optimistic edits across the live API and durable storage."""
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.api.app import create_app
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault, ExchangeCredentialRecord


def closed(uid):
    return {"order_id": f"fixture-{uid}", "symbol": "BTCUSDT" if uid == "A" else "ETHUSDT", "created_at": 1000, "closed_pnl": 10}


class AccountClient:
    def __init__(self, cred):
        self.uid = cred.api_key.split("-")[0]

    async def fetch_live_overview_bundle(self):
        return {"userid": "" if self.uid == "unknown" else self.uid, "futures_balances": []}

    async def fetch_closed_pnl(self, **kwargs):
        return [closed(self.uid)]

    async def fetch_trade_fills(self, **kwargs):
        return []

    async def fetch_orders_history(self, **kwargs):
        return []

    async def fetch_transfers(self, **kwargs):
        return []

    async def aclose(self):
        pass


def make_app(data_dir):
    registry = build_default_registry()
    registry.get("easicoin").create_client = AccountClient
    return create_app(data_dir=data_dir, registry=registry, auto_prompt_setup=False, enable_background_sync=False)


def save(client, key, *, path="/api/v1/sync", revision=None):
    cred = {"venue_id": "easicoin", "api_key": key, "api_secret": "test-secret"}
    if revision is not None:
        cred["expected_revision"] = revision
    return client.post(path, json={"candidate_credential": cred} if path.endswith("/sync") else cred)


@pytest.mark.parametrize('path', ['/api/v1/sync', '/api/v1/exchanges'])
def test_rotation_rebind_and_direct_save_never_mix_accounts(tmp_path, path):
    http = TestClient(make_app(tmp_path))
    save(http, "A-one").raise_for_status()
    save(http, "A-two", path=path).raise_for_status()
    store = LedgerStore(tmp_path)
    before = store.snapshot_venue_partition('easicoin')
    vault = CredentialVault(tmp_path)
    for key, code in [('B-other', 'ACCOUNT_UID_MISMATCH'), ('unknown-key', 'ACCOUNT_UID_UNAVAILABLE')]:
        rejected = save(http, key, path=path)
        assert rejected.status_code == 409
        assert rejected.json()['error_code'] == code
        assert rejected.json()['committed'] is False
        assert vault.get_decrypted('easicoin').api_key == 'A-two'
        assert store.snapshot_venue_partition('easicoin') == before

    http.delete('/api/v1/exchanges/easicoin?keep_data=true').raise_for_status()
    http = TestClient(make_app(tmp_path))
    assert save(http, 'B-other', path=path).status_code == 409
    assert vault.get_decrypted('easicoin') is None
    assert store.snapshot_venue_partition('easicoin') == before
    save(http, 'A-three', path=path).raise_for_status()
    http.delete('/api/v1/exchanges/easicoin?keep_data=false').raise_for_status()
    save(http, 'B-other', path=path).raise_for_status()
    assert store.venue_scope('easicoin').load_records('closed_pnl') == [closed('B')]


def test_unknown_uid_on_first_connect_keeps_vault_and_partition_empty(tmp_path):
    http = TestClient(make_app(tmp_path))
    response = save(http, 'unknown-key')
    assert response.status_code == 409
    assert not CredentialVault(tmp_path).list_decrypted()
    assert not LedgerStore(tmp_path).snapshot_venue_partition('easicoin')['kv']


def test_background_sync_with_changed_upstream_identity_preserves_partition(tmp_path):
    http = TestClient(make_app(tmp_path))
    save(http, 'A-one').raise_for_status()
    store = LedgerStore(tmp_path)
    before = store.snapshot_venue_partition('easicoin')
    CredentialVault(tmp_path).upsert(ExchangeCredentialRecord(venue_id='easicoin', api_key='B-other', api_secret='fixture'))
    result = http.post('/api/v1/sync', json={'venue_id': 'easicoin'}).json()['venues']['easicoin']
    assert result['ok'] is False
    assert result['error_code'] == 'ACCOUNT_UID_MISMATCH'
    assert store.snapshot_venue_partition('easicoin') == before


@pytest.mark.parametrize('mixed', [True, False])
def test_legacy_rows_require_independent_ownership_evidence(tmp_path, mixed):
    store = LedgerStore(tmp_path)
    scope = store.venue_scope('easicoin')
    scope.set_kv('live_bundle', {'userid': 'B'})
    scope.upsert_records('closed_pnl', [closed('A'), closed('B')] if mixed else [closed('B')],
                         key_fn=lambda row: row['order_id'], ts_fn=lambda row: row['created_at'])
    before = store.snapshot_venue_partition('easicoin')
    http = TestClient(make_app(tmp_path))
    response = save(http, 'B-owner')
    if mixed:
        assert response.status_code == 409
        assert response.json()['error_code'] == 'ARCHIVE_OWNER_UNVERIFIED'
        assert store.snapshot_venue_partition('easicoin') == before
        assert not CredentialVault(tmp_path).list_decrypted()
    else:
        response.raise_for_status()
        assert save(http, 'A-other').status_code == 409


@pytest.mark.parametrize('path', ['/api/v1/sync', '/api/v1/exchanges'])
def test_stale_edit_preserves_newer_same_account_key(tmp_path, path):
    http = TestClient(make_app(tmp_path))
    initial = save(http, 'A-first', revision='absent').json()['credential']
    list_row = next(row for row in http.get('/api/v1/exchanges').json()['adapters'] if row['venue_id'] == 'easicoin')
    assert list_row['credential']['revision'] == initial['revision']
    winning = save(http, 'A-new', revision=initial['revision'], path=path)
    winning.raise_for_status()
    assert winning.json()['credential']['revision'] != initial['revision']
    stale = save(http, 'A-stale', revision=initial['revision'], path=path)
    assert stale.status_code == 409
    assert stale.json()['error_code'] == 'CREDENTIAL_EDIT_CONFLICT'
    assert stale.json()['detail'] and stale.json()['detail_zh']
    assert CredentialVault(tmp_path).get_decrypted('easicoin').api_key == 'A-new'
    assert save(http, 'A-stale', revision='absent', path=path).status_code == 409


def test_dotenv_cannot_bind_different_account_to_archived_history(tmp_path: Path):
    http = TestClient(make_app(tmp_path))
    save(http, 'A-first').raise_for_status()
    http.delete('/api/v1/exchanges/easicoin?keep_data=true').raise_for_status()
    (tmp_path / '.env').write_text('EASICOIN_API_KEY=B-other\nEASICOIN_API_SECRET=fixture\n')
    with TestClient(make_app(tmp_path)):
        assert not CredentialVault(tmp_path).list_decrypted()
    assert LedgerStore(tmp_path).venue_scope('easicoin').load_records('closed_pnl') == [closed('A')]


@pytest.mark.parametrize('mode', ['added-field', 'order-changed', 'id-collision', 'old-signed', 'old-unverified', 'foreign-row-uid'])
def test_upgrade_uses_immutable_exchange_evidence_and_preserves_unverified_rows(tmp_path, monkeypatch, mode):
    store = LedgerStore(tmp_path)
    scope = store.venue_scope('easicoin')
    scope.set_kv('live_bundle', {'userid': 'A'})
    stream = 'closed_pnl'
    archive = {**closed('A'), 'side': 'Sell', 'qty': '1.000'}
    remote = [{**archive, 'qty': 1, 'new_response_field': 'new'}]
    if mode == 'order-changed':
        stream = 'orders'
        archive = {'order_id': 'order1', 'symbol': 'BTCUSDT', 'side': 'Buy', 'created_time': 1000, 'status': 'New'}
        remote = [{**archive, 'status': 'Filled', 'execution_count': 2}]
    elif mode == 'id-collision':
        remote = [{**archive, 'qty': 2}]
    elif mode == 'old-signed':
        archive['userid'] = 'A'
        remote = []
    elif mode == 'old-unverified':
        remote = []
    elif mode == 'foreign-row-uid':
        archive['userid'] = 'B'
        remote = [archive]
    scope.upsert_records(stream, [archive], key_fn=lambda row: row['order_id'], ts_fn=lambda row: 1000)

    async def refetch(self, **kwargs):
        return remote

    monkeypatch.setattr(AccountClient, 'fetch_orders_history' if stream == 'orders' else 'fetch_closed_pnl', refetch)
    before = store.snapshot_venue_partition('easicoin')
    res = save(TestClient(make_app(tmp_path)), 'A-new')
    if mode in {'added-field', 'order-changed', 'old-signed'}:
        res.raise_for_status()
        assert scope.load_records(stream) == (remote or [archive])
    else:
        assert res.status_code == 409
        assert stream in res.json()['detail'] and '1' in res.json()['detail']
        assert store.snapshot_venue_partition('easicoin') == before
        assert not CredentialVault(tmp_path).list_decrypted()


@pytest.mark.asyncio
async def test_concurrent_first_sync_can_only_bind_one_uid(tmp_path):
    import asyncio
    from opentradeledger.adapters.easicoin.sync import sync_easicoin_venue

    class WaitingClient(AccountClient):
        async def fetch_closed_pnl(self, **kwargs):
            return [] if self.uid == 'A' else [closed('B')]

        async def fetch_transfers(self, **kwargs):
            await ready.wait()
            return []

    ready = asyncio.Event()
    store = LedgerStore(tmp_path)
    async def fetch(uid):
        client = WaitingClient(ExchangeCredentialRecord(venue_id='easicoin', api_key=uid, api_secret='test'))
        await sync_easicoin_venue(client, store, now_s=2000)
    tasks = [asyncio.create_task(fetch(uid)) for uid in ('A', 'B')]
    await asyncio.sleep(0)
    ready.set()
    attempts = await asyncio.gather(*tasks, return_exceptions=True)
    assert sum(error is None for error in attempts) == 1
    assert store.venue_scope('easicoin').get_kv('live_bundle')['userid'] == 'A'


def test_legacy_direct_save_keeps_explicit_demo_selected(tmp_path):
    http = TestClient(make_app(tmp_path))
    http.post('/api/v1/demo', json={'enabled': True}).raise_for_status()
    save(http, 'A-one', path='/api/v1/exchanges').raise_for_status()
    assert http.get('/api/v1/dashboard').json()['explicit_demo_mode']


@pytest.mark.asyncio
@pytest.mark.parametrize('data', [[], [1], 'malformed', {'userid': True}, {'userid': 'null'}])
async def test_invalid_signed_uid_prevents_history_requests(tmp_path, data):
    import httpx
    from opentradeledger.adapters.compat_creds import EasiCoinCredentials
    from opentradeledger.adapters.easicoin.client import EasiCoinReadOnlyClient
    from opentradeledger.adapters.easicoin.identity import EasiCoinIdentityError
    from opentradeledger.adapters.easicoin.sync import sync_easicoin_venue

    requested = []
    def handle(request):
        requested.append(request.url.path)
        if request.url.path.endswith('/userid'):
            assert request.headers['Access-Key'] == 'fixture'
            return httpx.Response(200, json={'code': 0, 'data': data})
        return httpx.Response(200, json={'code': 0})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
        client = EasiCoinReadOnlyClient(EasiCoinCredentials('fixture', 'fixture'), client=transport, clock_ms=lambda: 1000)
        with pytest.raises(EasiCoinIdentityError) as error:
            await sync_easicoin_venue(client, LedgerStore(tmp_path), now_s=2000)
    assert error.value.code == 'ACCOUNT_UID_UNAVAILABLE'
    assert not any(path.endswith(('/closed-pnl', '/fills')) for path in requested)


def test_legacy_unattributed_balances_do_not_block_proven_history_or_become_live(tmp_path):
    store = LedgerStore(tmp_path)
    store.legacy.record_equity_snapshot(ts_sec=1000, total_equity=23, unified_equity=23, quant_equity=0, stock_equity=0, easicoin_equity=765)
    scope = store.venue_scope('easicoin')
    old_live = {'futures_balances': [{'coin': 'USDT', 'equity': '777'}]}
    scope.set_kv('live_bundle', old_live)
    scope.upsert_records('closed_pnl', [closed('A')], key_fn=lambda row: row['order_id'], ts_fn=lambda row: 1000)
    save(TestClient(make_app(tmp_path)), 'A-key').raise_for_status()
    assert scope.get_kv('live_bundle')['userid'] == 'A'
    assert scope.get_kv('unattributed_live_bundle') == old_live
    assert store.legacy.load_equity_snapshots()[0]['easicoin_equity'] == 765


@pytest.mark.parametrize('mode', ['same', 'different-wallet', 'missing-wallet', 'ambiguous-ids'])
def test_legacy_transfer_without_id_requires_matching_unique_exchange_composite(tmp_path, monkeypatch, mode):
    store = LedgerStore(tmp_path)
    scope = store.venue_scope('easicoin')
    scope.set_kv('live_bundle', {'userid': 'A'})
    archived = {'coin': 'USDT', 'amount': '20', 'from_wallet': 'FUNDING', 'to_wallet': 'CONTRACT', 'time': 123456}
    if mode == 'missing-wallet':
        archived.pop('from_wallet')
    store.legacy.upsert_easicoin_transfers([archived])
    fetched = [{**archived, 'transfer_status': 'ok'}]
    if mode == 'different-wallet':
        fetched[0]['from_wallet'] = 'SPOT'
    if mode == 'ambiguous-ids':
        fetched = [{**fetched[0], 'id': 'transfer1'}, {**fetched[0], 'id': 'transfer2'}]

    async def get_transfers(self, **kwargs):
        return fetched
    monkeypatch.setattr(AccountClient, 'fetch_transfers', get_transfers)
    http = TestClient(make_app(tmp_path))
    before = store.snapshot_venue_partition('easicoin')
    res = save(http, 'A-key')
    if mode == 'same':
        res.raise_for_status()
        assert scope.load_records('transfers') == fetched
    else:
        assert res.status_code == 409
        assert store.snapshot_venue_partition('easicoin') == before


def test_failed_first_binding_releases_uid_and_restores_unattributed_snapshot(tmp_path, monkeypatch):
    store = LedgerStore(tmp_path)
    scope = store.venue_scope('easicoin')
    scope.set_kv('live_bundle', {'futures_balances': [{'equity': 100}]})
    scope.upsert_records('closed_pnl', [closed('A')], key_fn=lambda row: row['order_id'], ts_fn=lambda row: 1000)
    http = TestClient(make_app(tmp_path))
    before = store.snapshot_venue_partition('easicoin')
    def disk_full(self, record):
        raise OSError('injected local save failure')
    with monkeypatch.context() as m:
        m.setattr(CredentialVault, 'upsert', disk_full)
        res = save(http, 'A-owner')
    assert res.status_code == 500 and res.json()['error_code'] == 'LOCAL_SAVE_FAILED'
    assert store.snapshot_venue_partition('easicoin') == before
    assert not CredentialVault(tmp_path).list_decrypted()
    save(http, 'A-owner').raise_for_status()
    assert scope.get_kv('account_uid') == 'A'


def test_pre_revision_vault_can_be_edited_once_by_open_forms(tmp_path):
    vault = CredentialVault(tmp_path)
    vault._write_all({'gate': {'venue_id': 'gate', 'api_key': 'first', 'api_secret': 'first-secret'}})
    http = TestClient(make_app(tmp_path))
    version = http.get('/api/v1/exchanges').json()['configured'][0]['revision']
    edit = {'venue_id': 'gate', 'api_key': 'next', 'api_secret': 'next-secret', 'expected_revision': version}
    http.post('/api/v1/exchanges', json=edit).raise_for_status()
    assert http.post('/api/v1/exchanges', json={**edit, 'api_key': 'stale'}).status_code == 409
    assert vault.get_decrypted('gate').api_key == 'next'


def test_dotenv_same_archived_uid_still_imports(tmp_path):
    http = TestClient(make_app(tmp_path))
    save(http, 'A-first').raise_for_status()
    http.delete('/api/v1/exchanges/easicoin?keep_data=true').raise_for_status()
    (tmp_path / '.env').write_text('EASICOIN_API_KEY=A-env\nEASICOIN_API_SECRET=fixture\n')
    with TestClient(make_app(tmp_path)) as restarted:
        row = restarted.get('/api/v1/exchanges').json()['configured']
        assert len(row) == 1
    assert CredentialVault(tmp_path).get_decrypted('easicoin').api_key == 'A-env'
