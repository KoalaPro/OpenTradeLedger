"""A single position may remain live across several entry and exit orders."""
from __future__ import annotations

import pytest

from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.store import LedgerStore

START = 1_790_000_000_000
VENUES = ("bybit", "kucoin", "hyperliquid", "htx", "bingx")


def _snapshot(venue, actions, *, start=START, first_id=1, split_day=False):
    fills, closes = [], []
    for i, (signed_qty, px, gross) in enumerate(actions, first_id):
        opening = signed_qty > 0
        qty = abs(signed_qty)
        t = start + (i - first_id) * 60_000 + (86_400_000 if split_day and i >= 3 else 0)
        oid = str(100 + i)
        side = "buy" if opening else "sell"
        fee = 0.1 * qty
        if venue == "bybit":
            fills.append(dict(symbol="ETHUSDT", execId=str(i), orderId=oid, side=side.title(),
                              execQty=qty, execPrice=px, execFee=fee, execTime=t, closedSize=0 if opening else qty))
            if not opening:
                closes.append(dict(symbol="ETHUSDT", orderId=oid, side="Sell", qty=qty, closedSize=qty,
                                   avgEntryPrice=px - gross / qty, avgExitPrice=px, closedPnl=gross - 2 * fee,
                                   createdTime=t, updatedTime=t))
        elif venue == "kucoin":
            fills.append(dict(symbol="ETHUSDTM", tradeId=str(i), orderId=oid, side=side, size=qty, price=px,
                              fee=fee, createdAt=t))
            if not opening:
                closes.append(dict(symbol="ETHUSDTM", closeId=oid, positionId=str(start), side="long",
                                   closeSize=qty, multiplier=1, openPrice=px - gross / qty, closePrice=px,
                                   openTime=start, closeTime=t, realisedGrossPnl=gross))
        elif venue == "hyperliquid":
            fills.append(dict(coin="ETH", tid=i, oid=int(oid), dir="Open Long" if opening else "Close Long",
                              side="B" if opening else "A", sz=qty, px=px, fee=fee, closedPnl=gross, time=t))
        elif venue == "htx":
            fills.append(dict(contract_code="ETH-USDT", id=i, order_id=oid, direction=side,
                              offset="open" if opening else "close", trade_price=px, trade_turnover=qty * px,
                              trade_volume=100 * qty, trade_fee=-fee, real_profit=gross, create_date=t))
        elif venue == "bingx":
            fills.append(dict(symbol="ETH-USDT", tradeId=str(i), orderId=oid, side=side.upper(),
                              positionSide="LONG", qty=qty, price=px, commission=-fee, realizedPnl=gross, filledTm=t))
    if venue == "bybit":
        return {"executions": fills, "closed_pnl": closes}
    if venue == "kucoin":
        return {"fills": fills, "history_positions": closes}
    return {"matches" if venue == "htx" else "fills": fills}


@pytest.mark.parametrize("venue", VENUES)
def test_partial_then_final_carries_old_journals_and_events(tmp_path, venue):
    store = LedgerStore(tmp_path)
    adapter = build_default_registry().get(venue)
    actions = [(1, 100, 0), (1, 100, 0), (-1, 110, 10), (1, 130, 0), (-2, 120, 10)]

    def records():
        bundle = adapter.build_venue_ledger(store, journals=store.load_journals(), now_s=START / 1000 + 86400)
        return bundle.trades

    store.upsert_plugin_snapshot(venue, _snapshot(venue, actions[:3]))
    partial, = records()
    assert partial["is_partial_close"]
    assert len(partial["position_events"]) == 3
    store.save_journal(partial["trade_id"], ["first"], "during scale-out")
    store.upsert_plugin_snapshot(venue, _snapshot(venue, actions))
    final, = records()
    assert final["trade_id"] == partial["trade_id"]
    assert final["note"] == "during scale-out"
    assert len(final["position_events"]) == 5
    assert final["qty"] == 3
    assert [b["gross_pnl_usd"] for b in final["exit_batches"]] == [10, 10]

    def old_key(i):
        oid = str(100 + i)
        prefix = {"bybit": f"bybit_{oid}", "kucoin": f"kucoin_{START}", "hyperliquid": f"hl_{i}",
                  "htx": f"htx_{i}", "bingx": f"bingx_{oid}"}[venue]
        return f"{prefix}_{START + (i - 1) * 60_000}"

    other = LedgerStore(tmp_path / "older")
    other.upsert_plugin_snapshot(venue, _snapshot(venue, actions))
    other.save_journal(old_key(3), ["a"], "old first")
    other.save_journal(old_key(5), ["b"], "old final")
    other.save_journal(f"another_account__{old_key(3)}", [], "other account")
    store = other
    recovered, = records()
    assert recovered["tags"] == ["a", "b"]
    assert recovered["note"] == "old first\nold final"
    store.save_journal(recovered["trade_id"], [], "")
    assert records()[0]["note"] == ""


def test_bybit_flat_then_reopen_keeps_cycles_separate(tmp_path):
    store = LedgerStore(tmp_path)
    adapter = build_default_registry().get("bybit")
    first = _snapshot("bybit", [(1, 100, 0), (1, 102, 0), (-1, 104, 3), (-1, 105, 4)])
    second = _snapshot("bybit", [(1, 99, 0), (-1, 100, 1)], start=START + 181_000, first_id=11)
    store.upsert_plugin_snapshot("bybit", {k: first[k] + second[k] for k in first})
    newer, older = adapter.build_venue_ledger(store, journals={}, now_s=START / 1000 + 86400).trades
    assert [newer["qty"], older["qty"]] == [1, 2]
    assert [len(newer["position_events"]), len(older["position_events"])] == [2, 4]


def _record_native_fills(store, venue, actions, *, native_history, split_day=False):
    if venue in ("bybit", "kucoin"):
        snap = _snapshot(venue, actions, split_day=split_day)
        if not native_history:
            snap["closed_pnl" if venue == "bybit" else "history_positions"] = []
        if actions[-1][0] > 0:
            if venue == "bybit":
                snap["positions"] = [dict(symbol="ETHUSDT", side="Buy", size=2, avgPrice=115)]
            else:
                snap["positions"] = [dict(symbol="ETHUSDTM", currentQty=2, avgEntryPrice=115,
                                          openingTimestamp=START, multiplier=1)]
        else:
            snap["positions"] = []
        store.upsert_plugin_snapshot(venue, snap)
        return

    scope = store.venue_scope(venue)
    ap = actions[-1][0] > 0
    live_pos = (dict(symbol="ETHUSDT", positionSide="LONG", positionAmt=2, entryPrice=115) if venue == "binance"
                else dict(symbol="ETHUSDT", side="Buy", size=2, entry_price=115))
    scope.set_kv("live_bundle", {"futures_positions": [live_pos] if ap else [],
                                "futures_balances": [{"coin": "USDT", "equity": "1000"}],
                                "pm_account": {"accountEquity": "1000"}})
    fills, closes = [], []
    for i, (qty, px, gross) in enumerate(actions, 1):
        ts = START + i * 60_000 + (86_400_000 if split_day and i >= 3 else 0)
        side = "Buy" if qty > 0 else "Sell"
        q = abs(qty)
        if venue == "binance":
            fills.append(dict(id=i, orderId=i, symbol="ETHUSDT", side=side.upper(), positionSide="LONG",
                              qty=q, price=px, realizedPnl=gross, commission=.1*q, commissionAsset="USDT", time=ts))
        else:
            fills.append(dict(exec_id=i, order_id=i, symbol="ETHUSDT", exec_type="Trade", side=side,
                              exec_qty=q, exec_price=px, exec_fee=.1*q, pz_link_id="p",
                              closed_size=0 if qty > 0 else q, exec_time=ts))
            if qty < 0:
                closes.append(dict(order_id=i, symbol="ETHUSDT", side=side, closed_size=q,
                                   avg_entry_price=px-gross/q, avg_exit_price=px, closed_pnl=gross-.2*q,
                                   created_at=ts))
    scope.upsert_records("fills", fills, key_fn=lambda f: str(f.get("id") or f["exec_id"]),
                         ts_fn=lambda f: float(f.get("time") or f["exec_time"]))
    if native_history and closes:
        scope.upsert_records("closed_pnl", closes, key_fn=lambda r: str(r["order_id"]), ts_fn=lambda r: float(r["created_at"]))


@pytest.mark.parametrize("venue", ("binance", "easicoin", "bybit", "kucoin"))
def test_live_add_after_reduction_then_native_final(tmp_path, venue):
    actions = [(2, 100, 0), (-1, 110, 10), (1, 130, 0)]
    store = LedgerStore(tmp_path)
    adapter = build_default_registry().get(venue)

    def trade():
        record, = adapter.build_venue_ledger(store, journals=store.load_journals(), now_s=START / 1000 + 86400).trades
        return record

    _record_native_fills(store, venue, actions, native_history=False)
    partial = trade()
    assert [e["action"] for e in partial["position_events"]] == ["OPEN", "REDUCE", "ADD"]
    assert partial["position_events"][-1]["remaining_qty"] == 2
    assert partial["position_events"][-1]["avg_entry_price"] == 115
    store.save_journal(partial["trade_id"], ["open"], "saved before exchange history")
    _record_native_fills(store, venue, actions, native_history=True)
    matched = trade()
    assert matched["note"] == "saved before exchange history"
    assert [e["action"] for e in matched["position_events"]] == ["OPEN", "REDUCE", "ADD"]
    _record_native_fills(store, venue, actions + [(-2, 120, 10)], native_history=True)
    final = trade()
    assert final["note"] == "saved before exchange history"
    assert final["position_events"][-1]["action"] == "CLOSE"
    assert final["position_events"][-1]["net_pnl_usd"] == pytest.approx(9.6)


def test_bybit_missing_close_fills_still_separate_inventory(tmp_path):
    first = _snapshot("bybit", [(1, 100, 0), (-1, 110, 10)])
    first["executions"].pop()
    second = _snapshot("bybit", [(1, 200, 0), (-1, 203, 3)], start=START + 121_000, first_id=11)
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("bybit", {k: first[k] + second[k] for k in first})
    newer, older = build_default_registry().get("bybit").build_venue_ledger(
        store, journals={}, now_s=START / 1000 + 86400,
    ).trades
    assert not older["position_events"]
    assert [(e["action"], e["price"]) for e in newer["position_events"]] == [("OPEN", 200), ("CLOSE", 203)]


def test_kucoin_linear_contract_count_does_not_invent_usd_pnl(tmp_path):
    snap = _snapshot("kucoin", [(2, 100, 0), (-1, 110, 10), (1, 130, 0), (-2, 120, 10)])
    for row in snap["history_positions"]:
        del row["multiplier"]
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("kucoin", snap)
    record, = build_default_registry().get("kucoin").build_venue_ledger(
        store, journals={}, now_s=START / 1000 + 86400,
    ).trades
    events = record["position_events"]
    assert all(e["qty_unit"] == "contracts" for e in events)
    assert events[2]["avg_entry_price"] == 115
    assert [e["gross_pnl_usd"] for e in events if e["action"] in {"REDUCE", "CLOSE"}] == [10, 10]


def test_bybit_each_exit_releases_its_share_of_mixed_entry_fees():
    from opentradeledger.adapters.bybit.client import normalize_bybit_closed_trades

    snap = _snapshot("bybit", [(1, 100, 0), (1, 100, 0), (-1, 110, 10), (-1, 120, 20)])
    snap["executions"][0]["execFee"] = ".1"
    snap["executions"][1]["execFee"] = ".9"
    for row, gross in zip(snap["closed_pnl"], [10, 20]):
        row["closedPnl"] = gross - .5 - .1
    partial, = normalize_bybit_closed_trades(snap["closed_pnl"][:1], snap["executions"][:3])
    assert partial["entry_leg"]["gross_fee_usd"] == pytest.approx(.5)
    final, = normalize_bybit_closed_trades(snap["closed_pnl"], snap["executions"])
    assert [b["gross_pnl_usd"] for b in final["exit_batches"]] == [10, 20]


def test_bingx_zero_native_pnl_and_signed_fee_credit():
    from opentradeledger.adapters.bingx.client import normalize_bingx_closed_trades

    fills = _snapshot("bingx", [(2, 100, 0), (-1, 100, 0), (-1, 110, 10)])["fills"]
    fills[0]["commission"] = ".2"
    fills[1]["profit"] = "99"
    trade, = normalize_bingx_closed_trades(fills)
    assert [e["fee_usd"] for e in trade["position_events"]] == [-.2, .1, .1]
    assert trade["position_events"][1]["gross_pnl_usd"] == 0
    assert trade["net_pnl_usd"] == 10
    assert trade["total_fee_usd"] == .2
    assert trade["total_rebate_usd"] == .2
    assert [b["order_id"] for b in trade["exit_batches"]] == ["102", "103"]


def test_binance_native_maker_credit_is_received_once(tmp_path):
    store = LedgerStore(tmp_path)
    _record_native_fills(store, "binance", [(2, 100, 0), (-1, 110, 10), (-1, 110, 10)], native_history=True)
    scope = store.venue_scope("binance")
    rows = scope.load_records("fills")
    opening = next(r for r in rows if r["side"] == "BUY")
    scope.upsert_records("fills", [{**opening, "maker": True, "commission": "-0.2"}],
                         key_fn=lambda f: str(f["id"]), ts_fn=lambda f: float(f["time"]))
    trade, = build_default_registry().get("binance").build_venue_ledger(store, journals={}, now_s=START / 1000 + 86400).trades
    assert trade["position_events"][0]["fee_usd"] == -.2
    assert trade["net_pnl_usd"] == 20
    assert trade["total_fee_usd"] == .2
    assert trade["total_rebate_usd"] == .2


@pytest.mark.parametrize("venue,asset_key", [("bingx", "commissionAsset"), ("bybit", "feeCurrency"),
                                             ("kucoin", "feeCurrency"), ("hyperliquid", "feeToken"), ("htx", "fee_asset")])
def test_lifecycle_unknown_fee_currency_keeps_exit_net_unknown(tmp_path, venue, asset_key):
    store = LedgerStore(tmp_path)
    snap = _snapshot(venue, [(2, 100, 0), (-1, 100, 0), (-1, 110, 10)])
    items = snap.get("fills") or snap.get("executions") or snap["matches"]
    items[0][asset_key] = "BTC"
    store.upsert_plugin_snapshot(venue, snap)
    trade, = build_default_registry().get(venue).build_venue_ledger(store, journals={}, now_s=START / 1000 + 86400).trades
    events = trade["position_events"]
    assert events[0]["fee_usd"] is None
    assert events[1]["net_pnl_usd"] is None
    assert events[1]["gross_pnl_usd"] == 0


@pytest.mark.parametrize("venue,key", [("htx", "real_profit"), ("hyperliquid", "closedPnl"), ("bingx", "realizedPnl")])
def test_missing_fill_pnl_uses_replayed_linear_cost_without_overwriting_zero(tmp_path, venue, key):
    snap = _snapshot(venue, [(2, 100, 0), (-1, 110, 0), (1, 130, 0), (-2, 120, 10)])
    fills = snap.get("fills") or snap["matches"]
    del fills[-1][key]
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot(venue, snap)
    trade, = build_default_registry().get(venue).build_venue_ledger(store, journals={}, now_s=START / 1000 + 86400).trades
    assert [b["gross_pnl_usd"] for b in trade["exit_batches"]] == [0, 10]
    assert trade["gross_pnl_usd"] == 10


@pytest.mark.parametrize(("venue", "extra_fee"), [
    ("bybit", 0), ("kucoin", 0), ("easicoin", 0), ("bybit", .2), ("kucoin", .2),
])
def test_new_reduction_precedes_next_native_summary(tmp_path, venue, extra_fee):
    store = LedgerStore(tmp_path)
    adapter = build_default_registry().get(venue)
    actions = [(2, 100, 0), (-1, 110, 10), (1, 98, 0), (-1, 109, 10)]
    scope = store.venue_scope(venue)
    _record_native_fills(store, venue, actions[:2], native_history=True)
    if venue == "easicoin":
        native, = scope.load_records("closed_pnl")
        native["closed_pnl"] += 1.5
        scope.upsert_records("closed_pnl", [native], key_fn=lambda r: str(r["order_id"]), ts_fn=lambda r: r["created_at"])
    else:
        first = scope.get_kv("snapshot")
        if venue == "kucoin":
            first["history_positions"][0]["fundingFee"] = 1.5
            first["history_positions"][0]["tradeFee"] = .2 + extra_fee
        else:
            first["closed_pnl"][0]["closeFee"] = .1 + extra_fee
            first["closed_pnl"][0]["closedPnl"] -= extra_fee
            first["tx_logs"] = [{"type": "SETTLEMENT", "id": "one-receipt", "symbol": "ETHUSDT",
                                 "transactionTime": START + 30_000, "funding": 1.5}]
        scope.set_kv("snapshot", first)
    first, = adapter.build_venue_ledger(store, journals={}, now_s=START / 1000 + 86400).trades
    store.save_journal(first["trade_id"], ["native"], "first verified")
    if venue == "easicoin":
        _record_native_fills(store, venue, actions, native_history=False, split_day=True)
        scope.set_kv("live_bundle", {"userid": "test", "futures_positions": [
            {"symbol": "ETHUSDT", "side": "Buy", "size": 1, "entry_price": 99}
        ]})
    else:
        updated = _snapshot(venue, actions, split_day=True)
        previous = scope.get_kv("snapshot")
        if venue == "kucoin":
            updated["history_positions"] = previous["history_positions"]
            updated["positions"] = [{"symbol": "ETHUSDTM", "currentQty": 1, "avgEntryPrice": 99,
                                     "multiplier": 1, "openingTimestamp": START}]
        else:
            updated["closed_pnl"] = previous["closed_pnl"]
            updated["tx_logs"] = previous["tx_logs"]
            updated["positions"] = [{"symbol": "ETHUSDT", "side": "Buy", "size": 1, "avgPrice": 99}]
        scope.set_kv("snapshot", updated)
    bundle = adapter.build_venue_ledger(store, journals=store.load_journals(), now_s=START / 1000 + 86400)
    row, = bundle.trades
    assert [e["action"] for e in row["position_events"]] == ["OPEN", "REDUCE", "ADD", "REDUCE"]
    assert row["position_events"][-1]["remaining_qty"] == 1
    assert row["position_events"][-1]["avg_entry_price"] == 99
    assert [b["gross_pnl_usd"] for b in row["exit_batches"]] == [10, 10]
    assert row["funding_pnl_usd"] == pytest.approx(0 if venue == "easicoin" else 1.5)
    if venue == "easicoin":
        assert row["settlement_adjustment_usd"] == pytest.approx(1.5)
    assert row["net_pnl_usd"] == pytest.approx(21.0 - extra_fee)  # +20 gross +1.5 funding -0.5 in actual fills and any statement override
    assert row["note"] == "first verified"
    assert sum(float(c.get(f"{venue}_futures", 0)) for c in bundle.daily_components.values()) == pytest.approx(21.0 - extra_fee)
    daily = [float(c[f"{venue}_futures"]) for _, c in sorted(bundle.daily_components.items()) if f"{venue}_futures" in c]
    assert daily == pytest.approx([11.2 - extra_fee, 9.8])
    entry_adjustment = extra_fee / 2 if venue == "kucoin" else 0
    assert row["entry_leg"]["gross_fee_usd"] == pytest.approx(.3 + entry_adjustment)
    assert row["exit_leg"]["gross_fee_usd"] == pytest.approx(.2 + extra_fee - entry_adjustment)
    assert row["exit_batches"][0]["gross_fee_usd"] == pytest.approx(.1 + extra_fee - entry_adjustment)
    assert row["exit_batches"][0]["net_pnl_usd"] == pytest.approx(9.8 - extra_fee)
    _record_native_fills(store, venue, actions + [(-1, 111, 12)], native_history=True, split_day=True)
    final, = adapter.build_venue_ledger(store, journals=store.load_journals(), now_s=START / 1000 + 86400).trades
    assert final["position_events"][-1]["action"] == "CLOSE"
    assert final["note"] == "first verified"


@pytest.mark.parametrize("venue,fee_key,native_credit", [
    ("kucoin", "fee", -.2), ("bybit", "execFee", -.2), ("hyperliquid", "fee", -.2),
    ("htx", "trade_fee", .2), ("bingx", "commission", .2),
])
def test_native_maker_credit_reaches_leg_and_parent(tmp_path, venue, fee_key, native_credit):
    snap = _snapshot(venue, [(2, 100, 0), (-1, 110, 10), (-1, 130, 30)], split_day=True)
    rows = snap.get("fills") or snap.get("executions") or snap["matches"]
    rows[0][fee_key] = native_credit
    if venue == "bybit":
        for record, pnl in zip(snap["closed_pnl"], [10, 30]):
            record["closedPnl"] = pnl
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot(venue, snap)
    trade, = build_default_registry().get(venue).build_venue_ledger(store, journals={}, now_s=START / 1000 + 86400).trades
    assert trade["entry_leg"]["net_fee_usd"] == pytest.approx(-.2)
    assert trade["net_pnl_usd"] == pytest.approx(40)
    assert trade["net_fee_usd"] == pytest.approx(0)


def test_kucoin_no_multiplier_uses_native_values_and_preserves_contracts():
    from opentradeledger.adapters.kucoin.client import normalize_kucoin_closed_trades
    snap = _snapshot("kucoin", [(2, 100, 0), (-1, 110, 10), (-1, 130, 30)])
    for row in snap["history_positions"]:
        del row["multiplier"]
        row.update(openValue=row["openPrice"], closeValue=row["closePrice"], closeSize=100)
    for fill in snap["fills"]:
        fill["size"] *= 100
    trade, = normalize_kucoin_closed_trades(snap["history_positions"], snap["fills"])
    assert trade["qty"] == 2 and trade["contract_count"] == 200
    assert trade["entry_leg"]["notional_usd"] == 200
    assert trade["entry_leg"]["fee_bps"] == 10
    for row in snap["history_positions"]:
        del row["openValue"], row["closeValue"], row["realisedGrossPnl"]
    trade, = normalize_kucoin_closed_trades(snap["history_positions"], snap["fills"])
    assert trade["qty"] is None and trade["contract_count"] == 200
    assert trade["entry_leg"]["fee_bps"] is None
    for fill in snap["fills"]:
        fill["value"] = fill["size"] * .01 * fill["price"]
    trade, = normalize_kucoin_closed_trades(snap["history_positions"], snap["fills"])
    assert trade["qty"] == 2 and trade["contract_count"] == 200
    assert trade["exit_leg"]["notional_usd"] == 240


def test_bybit_mnt_without_settlement_fee_does_not_become_dollars():
    from opentradeledger.adapters.bybit.client import normalize_bybit_closed_trades
    snap = _snapshot("bybit", [(2, 100, 0), (-1, 110, 10), (-1, 130, 30)])
    snap["executions"][0].update(feeCurrency="MNT", execFee=.002)
    trade, = normalize_bybit_closed_trades(snap["closed_pnl"], snap["executions"])
    assert trade["entry_leg"]["fee_known"] is False
    assert trade["total_fee_known"] is False
    assert trade["entry_leg"]["discount_saved_usd"] == 0
    assert trade["gross_pnl_usd"] == 40
    assert [b["gross_pnl_usd"] for b in trade["exit_batches"]] == [10, 30]
    assert trade["net_pnl_usd"] == 39.6


@pytest.mark.parametrize("venue,key", [("kucoin", "feeCurrency"), ("bingx", "commissionAsset"),
                                       ("hyperliquid", "feeToken"), ("htx", "fee_asset"), ("bybit", "feeCurrency")])
def test_foreign_fee_preserves_only_verified_net_and_dates(tmp_path, venue, key):
    snap = _snapshot(venue, [(2, 100, 0), (-1, 110, 10), (-1, 130, 30)], split_day=True)
    fills = snap.get("executions") or snap.get("fills") or snap["matches"]
    fills[0][key] = "ETH"
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot(venue, snap)
    bundle = build_default_registry().get(venue).build_venue_ledger(store, journals={}, now_s=START / 1000 + 86400)
    trade, = bundle.trades
    assert trade["total_fee_known"] is False
    assert trade["entry_leg"]["fee_known"] is False
    assert trade["exit_leg"]["fee_known"] is True
    if venue == "bybit":
        assert trade["net_pnl_usd"] == pytest.approx(39.6)
        assert [float(parts["bybit_futures"]) for _, parts in sorted(bundle.daily_components.items())] == pytest.approx([9.8, 29.8])
    else:
        assert trade["net_pnl_known"] is False
        assert trade["net_pnl_usd"] == 0
        assert not any(sum(parts.values()) for parts in bundle.daily_components.values())


def test_binance_foreign_commission_is_not_subtracted_as_usd(tmp_path):
    store = LedgerStore(tmp_path)
    _record_native_fills(store, "binance", [(2, 100, 0), (-1, 110, 10), (-1, 130, 30)], native_history=True)
    scope = store.venue_scope("binance")
    opener = next(r for r in scope.load_records("fills") if r["side"] == "BUY")
    opener.update(commissionAsset="ETH", commission=.002, maker=True)
    scope.upsert_records("fills", [opener], key_fn=lambda r: str(r["id"]), ts_fn=lambda r: r["time"])
    trade, = build_default_registry().get("binance").build_venue_ledger(store, journals={}, now_s=START / 1000 + 86400).trades
    assert trade["entry_leg"]["fee_known"] is False
    assert trade["net_pnl_known"] is False
    assert trade["position_events"][0]["role"] == "MAKER"
    assert trade["position_events"][0]["fee_usd"] is None
    assert trade["position_events"][1]["net_fee_usd"] == pytest.approx(.1)
    assert trade["position_events"][1]["rebate_status"] == "SETTLED"


def test_easicoin_maker_credit_is_not_funding(tmp_path):
    store = LedgerStore(tmp_path)
    _record_native_fills(store, "easicoin", [(2, 100, 0), (-1, 110, 10), (-1, 130, 30)], native_history=True)
    scope = store.venue_scope("easicoin")
    opener = next(r for r in scope.load_records("fills") if r["side"] == "Buy")
    opener.update(exec_fee=-.2, liquidity_type="maker")
    scope.upsert_records("fills", [opener], key_fn=lambda r: str(r["exec_id"]), ts_fn=lambda r: r["exec_time"])
    rows = scope.load_records("closed_pnl")
    for row in rows:
        row["closed_pnl"] += .2
    scope.upsert_records("closed_pnl", rows, key_fn=lambda r: str(r["order_id"]), ts_fn=lambda r: r["created_at"])
    trade, = build_default_registry().get("easicoin").build_venue_ledger(store, journals={}, now_s=START / 1000 + 86400).trades
    assert trade["entry_leg"]["net_fee_usd"] == pytest.approx(-.2)
    assert trade["funding_pnl_usd"] == 0
    assert trade["settlement_adjustment_usd"] == pytest.approx(0)
    assert trade["net_pnl_usd"] == pytest.approx(40)
