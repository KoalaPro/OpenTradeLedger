"""A reduction must be visible before the exchange publishes the closing summary."""
import pytest

from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.security.vault import CredentialVault


@pytest.mark.parametrize("venue", ["gate", "bitget", "mexc"])
def test_trade_list_before_and_after_last_exit(tmp_path, venue):
    store = LedgerStore(tmp_path)
    scope = store.venue_scope(venue)
    ledger = LedgerService(store, CredentialVault(tmp_path), build_default_registry(), default_demo_mode=False)
    t = 1_794_916_800
    symbol = "ETH_USDT"
    scope.set_kv("contract_specs", {symbol: {"quanto_multiplier": "1", "contractSize": "1"}})
    if venue == "gate":
        live_pos = dict(contract=symbol, size=1, entry_price=99)
        wallets = {"futures_account": {"total": "100", "available": "50"}}
    elif venue == "mexc":
        live_pos = dict(symbol=symbol, positionId="P", positionType=1, holdVol=1, holdAvgPrice=99)
        wallets = {"futures_assets": [{"currency": "USDT", "equity": 100}]}
    else:
        live_pos = dict(symbol="ETHUSDT", positionId="P", posSide="long", size=1, avgPrice=99)
        wallets = {"uta_assets": {"accountEquity": 100, "available": 50}}
    scope.set_kv("live_bundle", {**wallets, "futures_positions": [live_pos]})

    def save(stream, row, key, ts):
        scope.upsert_records(stream, [row], key_fn=lambda r: str(key), ts_fn=lambda r: ts)

    def add(idx, signed, price, pnl, fee):
        ts = t + idx * 60
        oid = str(100+idx)
        if venue == "gate":
            save("futures_trades", dict(id=idx, order_id=oid, contract=symbol, size=signed, price=price,
                 close_size=min(signed, 0), fee=fee, create_time=ts, role="maker"), idx, ts)
        elif venue == "mexc":
            save("deals", dict(id=idx, positionId="P", orderId=oid, symbol=symbol, side=1 if signed>0 else 4,
                 vol=abs(signed), profit=pnl, price=price, fee=fee, feeCurrency="USDT", timestamp=ts*1000), idx, ts)
        else:
            save("fills", dict(tradeId=str(idx), orderId=oid, positionId="P", symbol="ETHUSDT", posSide="long",
                 tradeSide="open_long" if signed>0 else "close_long", size=abs(signed), price=price,
                 execPnl=pnl, feeDetail=[dict(feeCoin="USDT", totalFee=-fee)], cTime=ts*1000), idx, ts)

    add(0, 2, 100, 0, .1)
    add(1, -1, 110, 10, .055)
    add(2, 1, 98, 0, .049)
    add(3, -1, 109, 10, .0545)
    rows = ledger.get_dashboard()["trades"]
    assert len(rows) == 1
    row = rows[0]
    assert [e["action"] for e in row["position_events"]] == ["OPEN", "REDUCE", "ADD", "REDUCE"]
    assert row["position_events"][-1]["remaining_qty"] == 1
    assert row["total_fee_usd"] == pytest.approx(.2585)
    ledger.save_journal(row["trade_id"], ["review"], "第一次减仓")
    add(4, -1, 115, 16, .0575)
    scope.set_kv("live_bundle", {**wallets, "futures_positions": []})
    end = t + 240
    if venue == "gate":
        save("futures_closes", dict(contract=symbol, side="long", first_open_time=t, time=end,
             accum_size=3, max_size=2, long_price=298/3, short_price=334/3, pnl_pnl=36,
             pnl_fee=-.316, pnl_fund=0, pnl=35.684), "P", end)
    elif venue == "bitget":
        save("positions_history", dict(positionId="P", symbol="ETHUSDT", posSide="long", closeTotalPos=3,
             openAvgPrice=298/3, closeAvgPrice=334/3, pnl=36, openFee=-.149, closeFee=-.167,
             netProfit=35.684, cTime=t*1000, uTime=end*1000), "P", end)
    else:
        save("positions_history", dict(positionId="P", symbol=symbol, positionType=1, closeVol=3,
             openAvgPrice=298/3, closeAvgPrice=334/3, closeProfitLoss=36, fee=-.316,
             createTime=t*1000, updateTime=end*1000), "P", end)
    for _ in range(2):
        rows = ledger.get_dashboard()["trades"]
        assert len(rows) == 1
        assert rows[0]["position_events"][-1]["action"] == "CLOSE"
        assert rows[0]["note"] == "第一次减仓"
        assert [e["gross_pnl_usd"] for e in rows[0]["position_events"] if e["action"] in ("CLOSE", "REDUCE")] == [10, 10, 16]
