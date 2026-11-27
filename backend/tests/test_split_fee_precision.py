"""The same source execution costs must reach batches, legs and the parent trade."""
from decimal import Decimal

import pytest

from opentradeledger.core.store import LedgerStore
from test_native_position_costs import add_position, trades
from test_scaleout_cycle_evidence import VENUES, _record_native_fills, _snapshot


# Charged USD amounts observed on the two Binance DEXE exits and their entry.
DEXE_FEES = ('0.22457672', '0.101325', '0.08614651')
BITGET_ETH_FEES = ('1.0206', '1.5391341', '1.5334389')


def write_fees(store, venue, fees):
    actions = [(2, 100, 0), (-1, 110, 10), (-1, 120, 20)]
    scope = store.venue_scope(venue)
    if venue in VENUES:
        snap = _snapshot(venue, actions)
        rows_key, fee_key, sign = {
            'bybit': ('executions', 'execFee', 1), 'kucoin': ('fills', 'fee', 1),
            'htx': ('matches', 'trade_fee', -1), 'bingx': ('fills', 'commission', -1),
            'hyperliquid': ('fills', 'fee', 1),
        }[venue]
        for row, fee in zip(snap[rows_key], fees, strict=True):
            row[fee_key] = str(Decimal(fee) * sign)
        store.upsert_plugin_snapshot(venue, snap)
        return

    if venue in ('binance', 'easicoin'):
        _record_native_fills(store, venue, actions, native_history=True)
    else:
        add_position(store, venue, [(0, 2, 100, 'o', None), (1000, -1, 110, 'a', 10),
                                    (2000, -1, 120, 'b', 20)], reported_gross=30)
    if venue == 'okx':
        snap = scope.get_kv('snapshot')
        for row, fee in zip(sorted(snap['fills'], key=lambda f: f['ts']), fees, strict=True):
            row['fee'] = '-' + fee
        scope.set_kv('snapshot', snap)
        return
    stream, id_key, ts_key, fee_key = {
        'bitget': ('fills', 'tradeId', 'cTime', 'feeDetail'),
        'gate': ('futures_trades', 'id', 'create_time', 'fee'),
        'mexc': ('deals', 'id', 'timestamp', 'fee'),
        'binance': ('fills', 'id', 'time', 'commission'),
        'easicoin': ('fills', 'exec_id', 'exec_time', 'exec_fee'),
    }[venue]
    rows = sorted(scope.load_records(stream), key=lambda r: r[ts_key])
    for row, fee in zip(rows, fees, strict=True):
        row[fee_key] = [{'feeCoin': 'USDT', 'fee': fee}] if venue == 'bitget' else fee
    scope.upsert_records(stream, rows, key_fn=lambda r: str(r[id_key]), ts_fn=lambda r: r[ts_key])


@pytest.mark.parametrize('venue', ['binance', 'bitget', 'gate', 'mexc', 'easicoin', 'okx', *VENUES])
def test_native_fee_precision_across_split_close(tmp_path, venue):
    store = LedgerStore(tmp_path)
    fees = BITGET_ETH_FEES if venue == 'bitget' else DEXE_FEES
    write_fees(store, venue, fees)
    trade, = trades(store, venue)
    entry, first, last = map(Decimal, fees)
    for field in ('gross_fee_usd', 'net_fee_usd'):
        actual = [Decimal(str(b[field])) for b in trade['exit_batches']]
        assert actual == [first, last]
        assert Decimal(str(trade['entry_leg'][field])) == entry
        assert Decimal(str(trade['exit_leg'][field])) == first + last
    assert Decimal(str(trade['total_fee_usd'])) == entry + first + last
    assert Decimal(str(trade['net_fee_usd'])) == entry + first + last


@pytest.mark.parametrize('venue', ['okx', *VENUES])
def test_aggregating_decimal_charges_introduces_no_binary_digits(tmp_path, venue):
    store = LedgerStore(tmp_path)
    write_fees(store, venue, ('0.3', '0.1', '0.2'))
    trade, = trades(store, venue)
    assert [b['gross_fee_usd'] for b in trade['exit_batches']] == [.1, .2]
    assert trade['exit_leg']['gross_fee_usd'] == .3
    assert trade['total_fee_usd'] == .6
