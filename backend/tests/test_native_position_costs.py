"""Per-fill lifecycle and close-order attribution for history-backed venues."""

from pathlib import Path

import pytest

from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.store import LedgerStore


BASE = 1_770_000_000_000


def add_position(store, venue, fills, *, seq=0, start=BASE, reported_gross=20, position_ids=True, short=False):
    """(ms offset, signed base qty, price, order, fill PnL) tuples in position-relative direction."""
    scope = store.venue_scope(venue)
    pid = str(seq + 1)
    close_qty = sum(-q for _, q, *_ in fills if q < 0)
    open_qty = sum(q for _, q, *_ in fills if q > 0)
    open_avg = sum(q * px for _, q, px, *_ in fills if q > 0) / open_qty
    close_avg = sum(-q * px for _, q, px, *_ in fills if q < 0) / close_qty
    end = start + fills[-1][0]
    side = 'short' if short else 'long'
    rows = []
    for n, (dt, q, px, order, pnl) in enumerate(fills):
        t = start + dt
        uid = str(100 * seq + n + 1)
        oid = f'{pid}-{order}' if order else ''
        fee = abs(q) / 10
        if venue == 'gate':
            size = -q if short else q
            row = dict(id=uid, contract='ETH_USDT', size=size, close_size=size if q < 0 else 0,
                       order_id=oid, price=px, fee=fee, create_time=t / 1000)
        elif venue == 'bitget':
            row = dict(tradeId=uid, symbol='ETHUSDT', posSide=side, orderId=oid,
                       tradeSide=f'{"open" if q > 0 else "close"}_{side}', size=abs(q),
                       price=px, feeDetail=[dict(feeCoin='USDT', totalFee=-fee)], cTime=t)
            if pnl is not None:
                row['execPnl'] = pnl
            if position_ids:
                row['positionId'] = pid
        elif venue == 'mexc':
            row = dict(id=uid, positionId=pid, orderId=oid, symbol='ETH_USDT',
                       side=(3 if short else 1) if q > 0 else (2 if short else 4),
                       vol=abs(q) * 100, price=px, fee=fee, feeCurrency='USDT', timestamp=t)
            if pnl is not None:
                row['profit'] = pnl
        else:
            row = dict(billId=uid, posId=pid, ordId=oid, instId='ETH-USDT-SWAP',
                       side='buy' if ((q > 0) != short) else 'sell', fillPx=px,
                       fillSz=10 * abs(q), fee=-fee, feeCcy='USDT', ts=t)
            if pnl is not None:
                row['fillPnl'] = pnl
        rows.append(row)

    if venue == 'okx':
        spec = dict(instId='ETH-USDT-SWAP', ctType='linear', ctVal='.1', ctValCcy='ETH')
        hist = dict(posId=pid, instId=spec['instId'], direction=side, type='3',
                    openAvgPx=open_avg, closeAvgPx=close_avg, closeTotalPos=10 * close_qty,
                    pnl=reported_gross, cTime=start, uTime=end)
        store.upsert_plugin_snapshot(venue, dict(instruments=[spec], fills=list(reversed(rows)), positions_history=[hist]))
        return
    if venue == 'gate':
        hist = dict(contract='ETH_USDT', side=side, first_open_time=start // 1000, time=end // 1000,
                    long_price=close_avg if short else open_avg, short_price=open_avg if short else close_avg,
                    accum_size=close_qty, max_size=open_qty, pnl_pnl=reported_gross, pnl=reported_gross)
        scope.set_kv('contract_specs', {'ETH_USDT': {'quanto_multiplier': '1'}})
        streams = 'futures_trades', 'futures_closes'
    elif venue == 'bitget':
        hist = dict(positionId=pid, symbol='ETHUSDT', posSide=side, openAvgPrice=open_avg,
                    closeAvgPrice=close_avg, closeTotalPos=close_qty, pnl=reported_gross, cTime=start, uTime=end)
        streams = 'fills', 'positions_history'
    else:
        hist = dict(positionId=pid, symbol='ETH_USDT', positionType=2 if short else 1,
                    openAvgPrice=open_avg, closeAvgPrice=close_avg, closeVol=100 * close_qty,
                    closeProfitLoss=reported_gross, createTime=start, updateTime=end)
        scope.set_kv('contract_specs', {'ETH_USDT': {'contractSize': '.01'}})
        streams = 'deals', 'positions_history'
    scope.upsert_records(streams[0], rows, key_fn=lambda r: str(r.get('id') or r.get('tradeId')),
                         ts_fn=lambda r: float(r.get('create_time') or r.get('cTime') or r.get('timestamp')))
    scope.upsert_records(streams[1], [hist], key_fn=lambda r: pid, ts_fn=lambda r: end)


def trades(store, venue):
    bundle = build_default_registry().get(venue).build_venue_ledger(store, journals={}, now_s=BASE / 1000 + 60)
    return sorted(bundle.trades, key=lambda row: row['close_time_ms'])


@pytest.mark.parametrize('venue', ['gate', 'bitget', 'mexc', 'okx'])
@pytest.mark.parametrize('short', [False, True])
def test_fill_cost_after_partial_and_add(tmp_path: Path, venue, short):
    sign = -1 if short else 1
    fills = [(0, 1, 100, 'o', None), (10, 1, 100, 'o', None),
             (1000, -1, 100 + sign * 10, 'c1', None),
             (2000, 1, 100 + sign * 30, 'add', None),
             (3000, -2, 100 + sign * 20, 'c2', None)]
    store = LedgerStore(tmp_path)
    add_position(store, venue, fills, short=short)
    trade, = trades(store, venue)
    assert [b['gross_pnl_usd'] for b in trade['exit_batches']] == [10, 10]
    events = trade['position_events']
    assert [e['remaining_qty'] for e in events] == [1, 2, 1, 2, 0]
    assert [e['cost_basis_price'] for e in events if e['gross_pnl_usd'] is not None][-2:] == [100, 100 + sign * 15]
    assert [e['order_id'] for e in events] == ['1-o', '1-o', '1-c1', '1-add', '1-c2']


@pytest.mark.parametrize('venue', ['bitget', 'mexc', 'okx'])
@pytest.mark.parametrize('first,last', [(0, 40), (40, 0), (None, 0), (0, None)])
def test_native_zero_and_missing_pnl(tmp_path: Path, venue, first, last):
    # Prices differ from the supplied PnL to detect both accidental re-pricing and zero-as-missing.
    store = LedgerStore(tmp_path)
    add_position(store, venue, [(0, 2, 100, 'o', None), (1000, -1, 110, 'a', first),
                                (2000, -1, 130, 'b', last)], reported_gross=40)
    trade, = trades(store, venue)
    expected = [first if first is not None else 40 - last, last if last is not None else 40 - first]
    assert [b['gross_pnl_usd'] for b in trade['exit_batches']] == expected


@pytest.mark.parametrize('venue,position_ids', [('gate', False), ('bitget', False), ('bitget', True)])
def test_one_second_reopen_claims_own_fills(tmp_path: Path, venue, position_ids):
    store = LedgerStore(tmp_path)
    rows = [(0, 2, 100, 'o', None), (1000, -1, 110, 'a', 10), (2000, -1, 130, 'b', 30)]
    for seq in (0, 1):
        add_position(store, venue, rows, seq=seq, start=BASE + seq * 3000, reported_gross=40, position_ids=position_ids)
    parents = trades(store, venue)
    assert len(parents) == 2
    for trade in parents:
        assert [b['qty'] for b in trade['exit_batches']] == [1, 1]
        assert trade['entry_leg']['gross_fee_usd'] == pytest.approx(0.2)


@pytest.mark.parametrize('venue', ['gate', 'mexc', 'okx'])
def test_missing_contract_spec_preserves_contract_units(tmp_path: Path, venue):
    store = LedgerStore(tmp_path)
    add_position(store, venue, [(0, 2, 100, 'o', None), (1000, -1, 110, 'a', None),
                                (2000, -1, 130, 'b', None)], reported_gross=40)
    scope = store.venue_scope(venue)
    if venue == 'okx':
        snapshot = scope.get_kv('snapshot')
        snapshot.pop('instruments')
        scope.set_kv('snapshot', snapshot)
    else:
        scope.set_kv('contract_specs', {})
    trade, = trades(store, venue)
    events = trade['position_events']
    assert {e['qty_unit'] for e in events} == {'contracts'}
    assert [e['remaining_qty'] for e in events] == {
        'gate': [2, 1, 0], 'mexc': [200, 100, 0], 'okx': [20, 10, 0]}[venue]
    assert [b['gross_pnl_usd'] for b in trade['exit_batches']] == [None, None]


def test_okx_decimal_contract_sizes_close_exactly(tmp_path: Path):
    store = LedgerStore(tmp_path)
    add_position(store, 'okx', [(0, .3, 100, 'o', None), (1000, -.1, 110, 'a', 1),
                                (2000, -.2, 130, 'b', 6)], reported_gross=7)
    trade, = trades(store, 'okx')
    last = trade['position_events'][-1]
    assert last['action'] == 'CLOSE'
    assert last['remaining_qty'] == 0


@pytest.mark.parametrize('has_partial_history', [False, True])
def test_okx_live_reductions_and_final_history_share_journal(tmp_path, has_partial_history):
    store = LedgerStore(tmp_path)
    fills = [(0, 2, 100, 'o', None), (1000, -1, 110, 'first', 10),
             (2000, 1, 98, 'add', None), (3000, -1, 109, 'second', 10)]
    add_position(store, 'okx', fills)
    scope = store.venue_scope('okx')
    snap = scope.get_kv('snapshot')
    pos = snap['positions_history'][0]
    snap['positions'] = [dict(posId='1', instId=pos['instId'], posSide='long', pos='10',
                              avgPx='99', markPx='109', cTime=BASE, uTime=BASE + 3000)]
    snap['positions_history'] = [
        {**pos, 'type': '2', 'uTime': BASE + 1000, 'closeTotalPos': 10, 'pnl': 10,
         'fee': '-.2', 'realizedPnl': '9.8'}
    ] if has_partial_history else []
    scope.set_kv('snapshot', snap)
    row, = trades(store, 'okx')
    assert [e['action'] for e in row['position_events']] == ['OPEN', 'REDUCE', 'ADD', 'REDUCE']
    assert row['gross_pnl_usd'] == 20
    assert row['total_fee_usd'] == pytest.approx(.5)
    assert row['net_pnl_usd'] == pytest.approx(19.5)
    provisional_id = row['trade_id']
    add_position(store, 'okx', fills + [(4000, -1, 120, 'last', 21)], reported_gross=41)
    snap = scope.get_kv('snapshot')
    snap['positions'] = []
    scope.set_kv('snapshot', snap)
    adapter = build_default_registry().get('okx')
    final, = adapter.build_venue_ledger(
        store, journals={provisional_id: {'note': 'scaled out', 'tags': ['one']}}, now_s=BASE / 1000 + 100,
    ).trades
    assert final['note'] == 'scaled out'
    assert final['tags'] == ['one']
    assert final['position_events'][-1]['action'] == 'CLOSE'


@pytest.mark.parametrize('venue', ['gate', 'bitget', 'mexc', 'okx'])
def test_native_maker_credit_dates_and_signs(tmp_path, venue):
    store = LedgerStore(tmp_path)
    add_position(store, venue, [(0, 2, 100, 'o', None), (86_400_000, -2, 120, 'x', 40)], reported_gross=40)
    scope = store.venue_scope(venue)
    if venue == 'okx':
        snap = scope.get_kv('snapshot')
        next(f for f in snap['fills'] if f['side'] == 'buy')['fee'] = 0.2
        scope.set_kv('snapshot', snap)
    elif venue == 'gate':
        f = scope.load_records('futures_trades')[0]
        f['fee'] = -0.2
        scope.upsert_records('futures_trades', [f], key_fn=lambda r: r['id'], ts_fn=lambda r: r['create_time'])
    elif venue == 'mexc':
        f = scope.load_records('deals')[0]
        f['fee'] = -0.2
        scope.upsert_records('deals', [f], key_fn=lambda r: r['id'], ts_fn=lambda r: r['timestamp'])
    else:
        f = scope.load_records('fills')[0]
        f.update(execId=f['tradeId'], feeDetail=[dict(feeCoin='USDT', fee=-0.2)])
        scope.upsert_records('fills', [f], key_fn=lambda r: r['execId'], ts_fn=lambda r: r['cTime'])
    row, = trades(store, venue)
    assert row['position_events'][0]['fee_usd'] == pytest.approx(-.2)
    assert row['position_events'][1]['allocated_entry_fee_usd'] == pytest.approx(-.2)


def test_okx_partial_native_type_and_live_identity(tmp_path):
    store = LedgerStore(tmp_path)
    add_position(store, 'okx', [(0, 2, 100, 'o', None), (1000, -1, 110, 'x', 10)], reported_gross=10)
    scope = store.venue_scope('okx')
    snap = scope.get_kv('snapshot')
    snap['positions_history'][0]['type'] = '1'
    scope.set_kv('snapshot', snap)
    partial, = trades(store, 'okx')
    assert partial['is_partial_close']
    assert partial['position_events'][-1]['remaining_qty'] == 1
    add_position(store, 'okx', [(0, 2, 100, 'o', None), (1000, -1, 110, 'x', 10),
                                (2000, -1, 120, 'last', 20)], reported_gross=30)
    final, = trades(store, 'okx')
    assert partial['trade_id'] == final['trade_id']


@pytest.mark.parametrize('venue', ['gate', 'bitget', 'mexc', 'okx'])
def test_two_exit_orders_can_interleave_their_executions(tmp_path, venue):
    store = LedgerStore(tmp_path)
    add_position(store, venue, [(0, 3, 100, 'o', None), (1000, -1, 110, 'a', 10),
                                (2000, -1, 120, 'b', 20), (3000, -1, 130, 'a', 30)], reported_gross=60)
    row, = trades(store, venue)
    assert [(b.get('order_id'), b['qty'], b['gross_pnl_usd']) for b in row['exit_batches']] == [
        ('1-b', 1, 20), ('1-a', 2, 40)]


@pytest.mark.parametrize('venue', ['gate', 'bitget', 'mexc', 'okx'])
def test_missing_order_ids_preserve_fills_without_inventing_batches(tmp_path, venue):
    store = LedgerStore(tmp_path)
    add_position(store, venue, [(0, 3, 100, 'o', None), (1000, -1, 110, 'a', 10),
                                (2000, -1, 120, '', 20), (3000, -1, 130, 'b', 30)], reported_gross=60)
    row, = trades(store, venue)
    assert [e['order_id'] for e in row['position_events']] == ['1-o', '1-a', '', '1-b']
    assert row['exit_batches'] == []


def test_gate_mixed_exit_order_modes_preserve_per_fill_cost(tmp_path):
    store = LedgerStore(tmp_path)
    add_position(store, 'gate', [(0, 2, 100, 'o', None), (1000, -1, 110, 'tp', 10),
                                 (2000, -1, 130, 'x', 30)], reported_gross=40)
    scope = store.venue_scope('gate')
    rows = scope.load_records('futures_trades')
    for fill in rows:
        fill['role'] = 'maker' if fill['size'] > 0 else 'taker'
        fill['text'] = 'ao-test' if fill['order_id'] == '1-tp' else 'app'
    scope.upsert_records('futures_trades', rows, key_fn=lambda r: r['id'], ts_fn=lambda r: r['create_time'])
    trade, = trades(store, 'gate')
    assert trade['exit_leg']['mode_code'] == 'MIXED'
    assert [b['mode_code'] for b in trade['exit_batches']] == ['TP_MARKET', 'MARKET_TAKER']
    assert [e['net_fee_usd'] for e in trade['position_events']] == pytest.approx([.2, .1, .1])


def test_gate_split_fill_precision_through_public_legs_batches_and_totals(tmp_path):
    store = LedgerStore(tmp_path)
    add_position(store, 'gate', [(0, 3, 100, 'o', None), (1000, -1, 110, 'a', 10),
                                 (2000, -1, 120, 'b', 20), (86401000, -1, 130, 'b', 30)],
                 reported_gross=60)
    scope = store.venue_scope('gate')
    fees = ['.111111111', '.618912', '.80944845', '.751807895']
    fills = scope.load_records('futures_trades')
    for fill in fills:
        fill['fee'] = fees[int(fill['id']) - 1]
    scope.upsert_records('futures_trades', fills, key_fn=lambda r: r['id'], ts_fn=lambda r: r['create_time'])
    bundle = build_default_registry().get('gate').build_venue_ledger(store, journals={}, now_s=BASE / 1000 + 2 * 86400)
    trade, = bundle.trades
    for field in ('gross_fee_usd', 'net_fee_usd'):
        assert [b[field] for b in trade['exit_batches']] == [.618912, 1.561256345]
        assert trade['entry_leg'][field] == .111111111
        assert trade['exit_leg'][field] == 2.180168345
    assert trade['total_fee_usd'] == trade['net_fee_usd'] == 2.291279456
    assert [e['fee_usd'] for e in trade['position_events']] == [float(f) for f in fees]
    assert sum(float(parts.get('manual_futures', 0)) for parts in bundle.daily_components.values()) == pytest.approx(60)
