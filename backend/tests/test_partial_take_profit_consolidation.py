"""TDD suite verifying partial take-profit / scale-out consolidation across all 11 venues.

One position lifecycle (one entry -> multiple partial take-profit / stop-loss exits) must:
1. Consolidate into 1 closed trade record (`is_partial_close=True`) instead of N split rows;
2. Count the position's entry fee ONCE (`entry_leg.gross_fee_usd`), summing all exit batches into `exit_leg`;
3. Preserve closed-loop reconciliation (`0.00` drift), accurate Maker/Taker bps in `fee_matrix`, and legacy split journal notes;
4. Never double-count full entry fills when a position is still partially open in flight.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opentradeledger.adapters.bingx.client import normalize_bingx_closed_trades
from opentradeledger.adapters.bybit.client import normalize_bybit_closed_trades
from opentradeledger.adapters.htx.client import normalize_htx_closed_trades
from opentradeledger.adapters.hyperliquid.client import normalize_hyperliquid_closed_trades
from opentradeledger.adapters.kucoin.client import normalize_kucoin_closed_trades
from opentradeledger.adapters.okx.client import normalize_okx_closed_trades
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault


def test_gate_partial_take_profit_consolidates_into_single_order_and_single_entry_fee(tmp_path: Path) -> None:
    """Reproduces the live Gate ETH_USDT long (3.684 ETH opened once, closed in two 1.842 ETH TP/SL batches)."""
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    g_scope = store.venue_scope("gate")
    g_scope.set_kv(
        "contract_specs",
        {"ETH_USDT": {"name": "ETH_USDT", "quanto_multiplier": "0.01"}},
    )
    g_scope.set_kv(
        "live_bundle",
        {
            "wallet_total": {"total": {"amount": "5000.00"}},
            "futures_account": {"total": "5000.00", "available": "5000.00"},
        },
    )
    g_scope.upsert_records(
        "futures_closes",
        [
            {
                "contract": "ETH_USDT",
                "side": "long",
                "first_open_time": 1795705714,
                "time": 1795708860,
                "accum_size": "368.4",
                "max_size": "368.4",
                "long_price": "1670",
                "short_price": "1687.587592290988",
                "pnl_pnl": "64.79269",
                "pnl_fee": "-3.410624345",
                "pnl_fund": "-0.127445942748",
                "pnl": "61.254619712252",
                "order_id": 63894861940017131,
            }
        ],
        key_fn=lambda r: f"{r['contract']}_{r['first_open_time']}_{r['time']}_{r['side']}",
        ts_fn=lambda r: float(r["time"]),
    )
    g_scope.upsert_records(
        "futures_trades",
        [
            # Entry order (3 maker fills = 368.4 contracts = 3.684 ETH, total entry fee = 1.230456 U)
            {"id": "995959806", "order_id": "63894861928051411", "contract": "ETH_USDT", "size": "147.7", "close_size": "0", "price": "1670", "fee": "0.493318", "role": "maker", "create_time": 1795705714.2171, "text": "app"},
            {"id": "995959807", "order_id": "63894861928051411", "contract": "ETH_USDT", "size": "5.5", "close_size": "0", "price": "1670", "fee": "0.01837", "role": "maker", "create_time": 1795705714.2172, "text": "app"},
            {"id": "995959808", "order_id": "63894861928051411", "contract": "ETH_USDT", "size": "215.2", "close_size": "0", "price": "1670", "fee": "0.718768", "role": "maker", "create_time": 1795705714.2173, "text": "app"},
            # TP batch 1 (maker limit TP 184.2 contracts = 1.842 ETH @ 1680, fee = 0.618912 U)
            {"id": "995986886", "order_id": "63894861936890085", "contract": "ETH_USDT", "size": "-67.3", "close_size": "-67.3", "price": "1680", "fee": "0.226128", "role": "maker", "create_time": 1795706196.4941, "text": "app"},
            {"id": "995986961", "order_id": "63894861936890085", "contract": "ETH_USDT", "size": "-100", "close_size": "-100", "price": "1680", "fee": "0.336", "role": "maker", "create_time": 1795706198.7104, "text": "app"},
            {"id": "995986962", "order_id": "63894861936890085", "contract": "ETH_USDT", "size": "-16.9", "close_size": "-16.9", "price": "1680", "fee": "0.056784", "role": "maker", "create_time": 1795706198.9904, "text": "app"},
            # TP batch 2 (taker trailing/stop TP 184.2 contracts = 1.842 ETH @ ~1695.18, fee = 1.561256345 U)
            {"id": "996041240", "order_id": "63894861940017131", "contract": "ETH_USDT", "size": "-95.5", "close_size": "-95.5", "price": "1695.18", "fee": "0.80944845", "role": "taker", "create_time": 1795708860.5151, "text": "ao-2125982226721013760"},
            {"id": "996041241", "order_id": "63894861940017131", "contract": "ETH_USDT", "size": "-88.7", "close_size": "-88.7", "price": "1695.17", "fee": "0.751807895", "role": "taker", "create_time": 1795708860.5151, "text": "ao-2125982226721013760"},
        ],
        key_fn=lambda r: str(r["id"]),
        ts_fn=lambda r: float(r["create_time"]),
    )
    # Legacy journal saved on an old partial sub-record must survive consolidation
    store.save_journal(
        "fut_ETH_USDT_1795705714_1795706198_long_63894861936890085",
        tags=["clean_tp"],
        note="分批止盈第一半",
    )

    svc = LedgerService(store, vault, build_default_registry(), default_demo_mode=False)
    dash = svc.get_dashboard()

    assert len(dash["trades"]) == 1
    tr = dash["trades"][0]
    assert tr["trade_id"] == "fut_ETH_USDT_1795705714_1795708860_long"
    assert tr["is_partial_close"] is True
    assert tr["qty"] == pytest.approx(3.684)
    assert tr["entry_leg"]["gross_fee_usd"] == pytest.approx(1.2305, abs=1e-4)
    assert tr["exit_leg"]["gross_fee_usd"] == pytest.approx(2.1802, abs=1e-4)
    assert tr["exit_leg"]["mode_code"] == "MIXED"
    assert tr["total_fee_usd"] == pytest.approx(3.4106, abs=1e-4)
    assert tr["gross_pnl_usd"] == pytest.approx(64.7927, abs=1e-4)
    assert tr["funding_pnl_usd"] == pytest.approx(-0.1274, abs=1e-4)
    assert tr["tags"] == ["clean_tp"]
    assert "分批止盈第一半" in tr["note"]

    # Must retain the 2 individual exit batches inside the single consolidated order!
    batches = tr["exit_batches"]
    assert len(batches) == 2
    assert [b["batch_index"] for b in batches] == [1, 2]
    assert batches[0]["qty"] == pytest.approx(1.842)
    assert batches[0]["avg_price"] == pytest.approx(1680.0)
    assert batches[0]["mode_code"] == "LIMIT_MAKER"
    assert batches[0]["gross_fee_usd"] == pytest.approx(0.6189, abs=1e-4)
    assert batches[0]["gross_pnl_usd"] == pytest.approx(18.42, abs=1e-4)
    assert batches[1]["qty"] == pytest.approx(1.842)
    assert batches[1]["avg_price"] == pytest.approx(1695.1752, abs=1e-3)
    assert batches[1]["mode_code"] == "STOP_MARKET"
    assert batches[1]["gross_fee_usd"] == pytest.approx(1.5613, abs=1e-4)
    assert batches[1]["gross_pnl_usd"] == pytest.approx(46.3727, abs=1e-4)
    assert sum(b["gross_fee_usd"] for b in batches) == pytest.approx(tr["exit_leg"]["gross_fee_usd"], abs=1e-3)
    assert sum(b["gross_pnl_usd"] for b in batches) == pytest.approx(tr["gross_pnl_usd"], abs=1e-3)

    gate_fee = next(r for r in dash["fee_matrix"] if r["venue_id"] == "gate")
    assert gate_fee["trade_count"] == 1
    assert gate_fee["gross_fee_usd"] == pytest.approx(3.4106, abs=1e-3)
    assert gate_fee["maker_gross_bps"] == pytest.approx(2.0, abs=0.05)
    assert gate_fee["taker_gross_bps"] == pytest.approx(5.0, abs=0.05)


def test_bitget_mexc_binance_easicoin_partial_tp_consolidate_into_single_order(tmp_path: Path) -> None:
    """Bitget, MEXC, Binance, and EasiCoin must each emit 1 consolidated trade with 1 entry fee for multi-batch TP."""
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)

    # 1. Bitget: 1 positionId with 1 maker open (2.10 ETH @ 2430, fee=1.0206) and 2 taker TP orders (1.05 ETH each)
    bg = store.venue_scope("bitget")
    bg.set_kv("live_bundle", {"uid": "bg1", "uta_assets": {"accountEquity": "2000", "available": "2000"}})
    bg.upsert_records(
        "positions_history",
        [
            {
                "positionId": "pos_bg_1",
                "symbol": "ETHUSDT",
                "posSide": "long",
                "closeTotalPos": "2.10",
                "openAvgPrice": "2430",
                "closeAvgPrice": "2450",
                "pnl": "42.00",
                "openFee": "-1.0206",
                "closeFee": "-3.0870",
                "netProfit": "37.8924",
                "cTime": "1756731960000",
                "uTime": "1756733940000",
            }
        ],
        key_fn=lambda r: str(r["positionId"]),
        ts_fn=lambda r: float(r["uTime"]),
    )
    bg.upsert_records(
        "fills",
        [
            {"tradeId": "bg_f1", "orderId": "bg_o_open", "symbol": "ETHUSDT", "posSide": "long", "tradeSide": "open_long", "size": "2.10", "price": "2430", "feeDetail": [{"feeCoin": "USDT", "totalFee": "-1.0206"}], "tradeScope": "maker", "cTime": "1756731960000"},
            {"tradeId": "bg_f2", "orderId": "bg_o_tp1", "symbol": "ETHUSDT", "posSide": "long", "tradeSide": "close_long", "size": "1.05", "price": "2440", "profit": "10.50", "feeDetail": [{"feeCoin": "USDT", "totalFee": "-1.5372"}], "tradeScope": "taker", "cTime": "1756732620000"},
            {"tradeId": "bg_f3", "orderId": "bg_o_tp2", "symbol": "ETHUSDT", "posSide": "long", "tradeSide": "close_long", "size": "1.05", "price": "2460", "profit": "31.50", "feeDetail": [{"feeCoin": "USDT", "totalFee": "-1.5498"}], "tradeScope": "taker", "cTime": "1756733940000"},
        ],
        key_fn=lambda r: str(r["tradeId"]),
        ts_fn=lambda r: float(r["cTime"]),
    )

    # 2. MEXC: 1 history_position (200 contracts * 0.01 = 2 ETH) closed in 2 orders (100 contracts each)
    mx = store.venue_scope("mexc")
    mx.set_kv("live_bundle", {"refer_code": "mx1", "futures_assets": [{"currency": "USDT", "equity": "1500", "availableBalance": "1500"}]})
    mx.set_kv("contract_specs", {"ETH_USDT": {"symbol": "ETH_USDT", "contractSize": "0.01"}})
    mx.upsert_records(
        "positions_history",
        [
            {
                "positionId": "mx_pos_1",
                "symbol": "ETH_USDT",
                "positionType": 1,
                "closeVol": "200",
                "openAvgPrice": "2500",
                "closeAvgPrice": "2530",
                "closeProfitLoss": "60.0",
                "holdFee": "0",
                "fee": "-1.518",
                "createTime": 1756900000000,
                "updateTime": 1756903600000,
            }
        ],
        key_fn=lambda r: str(r["positionId"]),
        ts_fn=lambda r: float(r["updateTime"]),
    )
    mx.upsert_records(
        "deals",
        [
            {"id": "mxd1", "positionId": "mx_pos_1", "orderId": "mx_open", "symbol": "ETH_USDT", "side": 1, "vol": "200", "price": "2500", "fee": "0.50", "feeCurrency": "USDT", "taker": False, "profit": "0", "timestamp": 1756900000000},
            {"id": "mxd2", "positionId": "mx_pos_1", "orderId": "mx_tp1", "symbol": "ETH_USDT", "side": 4, "vol": "100", "price": "2520", "fee": "0.504", "feeCurrency": "USDT", "taker": True, "profit": "20.0", "timestamp": 1756901800000},
            {"id": "mxd3", "positionId": "mx_pos_1", "orderId": "mx_tp2", "symbol": "ETH_USDT", "side": 4, "vol": "100", "price": "2540", "fee": "0.508", "feeCurrency": "USDT", "taker": True, "profit": "40.0", "timestamp": 1756903600000},
        ],
        key_fn=lambda r: str(r["id"]),
        ts_fn=lambda r: float(r["timestamp"]),
    )

    # 3. Binance: 1 open fill (2 ETH @ 2000, fee=0.80) closed by 2 TP orders (1 ETH each, fees 1.01 + 1.02)
    bn = store.venue_scope("binance")
    bn.set_kv("live_bundle", {"uid": "bn1", "pm_account": {"accountEquity": "3000", "totalAvailableBalance": "3000"}})
    bn.upsert_records(
        "fills",
        [
            {"id": 1, "orderId": 10, "symbol": "ETHUSDT", "side": "BUY", "positionSide": "LONG", "qty": "2.0", "price": "2000", "quoteQty": "4000", "realizedPnl": "0", "commission": "0.80", "commissionAsset": "USDT", "maker": True, "time": 1757000000000},
            {"id": 2, "orderId": 11, "symbol": "ETHUSDT", "side": "SELL", "positionSide": "LONG", "qty": "1.0", "price": "2020", "quoteQty": "2020", "realizedPnl": "20.0", "commission": "1.01", "commissionAsset": "USDT", "maker": False, "time": 1757001800000},
            {"id": 3, "orderId": 12, "symbol": "ETHUSDT", "side": "SELL", "positionSide": "LONG", "qty": "1.0", "price": "2040", "quoteQty": "2040", "realizedPnl": "40.0", "commission": "1.02", "commissionAsset": "USDT", "maker": False, "time": 1757003600000},
        ],
        key_fn=lambda r: str(r["id"]),
        ts_fn=lambda r: float(r["time"]),
    )

    # 4. EasiCoin: 1 position (pz_link_id="pz_qnt_1", 11.42 QNT) closed in 2 orders (5.71 QNT each)
    ec = store.venue_scope("easicoin")
    ec.set_kv(
        "live_bundle",
        {"uid": "ec1", "futures_balances": [{"coin": "USDT", "equity": "1200", "available_balance": "1200", "unrealised_pnl": "0"}]},
    )
    ec.upsert_records(
        "closed_pnl",
        [
            {"order_id": "ec_tp1", "symbol": "QNTUSDT", "side": "Sell", "closed_size": "5.71", "avg_entry_price": "98.5", "avg_exit_price": "100.2", "cum_entry_value": "562.435", "cum_exit_value": "572.142", "closed_pnl": "8.5722", "created_at": 1760718420},
            {"order_id": "ec_tp2", "symbol": "QNTUSDT", "side": "Sell", "closed_size": "5.71", "avg_entry_price": "98.5", "avg_exit_price": "98.5", "cum_entry_value": "562.435", "cum_exit_value": "562.435", "closed_pnl": "-1.1248", "created_at": 1760728980},
        ],
        key_fn=lambda r: str(r["order_id"]),
        ts_fn=lambda r: float(r["created_at"]),
    )
    ec.upsert_records(
        "fills",
        [
            {"exec_id": "ef_open", "order_id": "ec_open", "pz_link_id": "pz_qnt_1", "symbol": "QNTUSDT", "exec_type": "Trade", "side": "Buy", "exec_qty": "11.42", "exec_price": "98.5", "exec_value": "1124.87", "exec_fee": "1.1248", "closed_size": "0", "liquidity_type": "maker", "exec_time": 1760712780},
            {"exec_id": "ef_tp1", "order_id": "ec_tp1", "pz_link_id": "pz_qnt_1", "symbol": "QNTUSDT", "exec_type": "Trade", "side": "Sell", "exec_qty": "5.71", "exec_price": "100.2", "exec_value": "572.142", "exec_fee": "0.5724", "closed_size": "5.71", "liquidity_type": "taker", "exec_time": 1760718420},
            {"exec_id": "ef_tp2", "order_id": "ec_tp2", "pz_link_id": "pz_qnt_1", "symbol": "QNTUSDT", "exec_type": "Trade", "side": "Sell", "exec_qty": "5.71", "exec_price": "98.5", "exec_value": "562.435", "exec_fee": "0.5624", "closed_size": "5.71", "liquidity_type": "taker", "exec_time": 1760728980},
        ],
        key_fn=lambda r: str(r["exec_id"]),
        ts_fn=lambda r: float(r["exec_time"]),
    )

    dash = LedgerService(store, vault, build_default_registry(), default_demo_mode=False).get_dashboard()
    by_venue = {t["venue_id"]: [x for x in dash["trades"] if x["venue_id"] == t["venue_id"]] for t in dash["trades"]}

    assert len(by_venue["bitget"]) == 1
    bg_tr = by_venue["bitget"][0]
    assert bg_tr["is_partial_close"] is True
    assert bg_tr["qty"] == pytest.approx(2.10)
    assert bg_tr["entry_leg"]["gross_fee_usd"] == pytest.approx(1.0206, abs=1e-4)
    assert bg_tr["exit_leg"]["gross_fee_usd"] == pytest.approx(3.0870, abs=1e-4)
    bg_matrix = next(r for r in dash["fee_matrix"] if r["venue_id"] == "bitget")
    assert bg_matrix["maker_gross_bps"] == pytest.approx(2.0, abs=0.05)

    assert len(by_venue["mexc"]) == 1
    mx_tr = by_venue["mexc"][0]
    assert mx_tr["is_partial_close"] is True
    assert mx_tr["qty"] == pytest.approx(2.0)
    assert mx_tr["entry_leg"]["gross_fee_usd"] == pytest.approx(0.50, abs=1e-4)
    assert mx_tr["exit_leg"]["gross_fee_usd"] == pytest.approx(1.012, abs=1e-4)

    assert len(by_venue["binance"]) == 1
    bn_tr = by_venue["binance"][0]
    assert bn_tr["is_partial_close"] is True
    assert bn_tr["qty"] == pytest.approx(2.0)
    assert bn_tr["entry_leg"]["gross_fee_usd"] == pytest.approx(0.80, abs=1e-4)
    assert bn_tr["exit_leg"]["gross_fee_usd"] == pytest.approx(2.03, abs=1e-4)

    assert len(by_venue["easicoin"]) == 1
    ec_tr = by_venue["easicoin"][0]
    assert ec_tr["is_partial_close"] is True
    assert ec_tr["qty"] == pytest.approx(11.42)
    assert ec_tr["entry_leg"]["gross_fee_usd"] == pytest.approx(1.1248, abs=1e-4)
    assert ec_tr["exit_leg"]["gross_fee_usd"] == pytest.approx(1.1348, abs=1e-4)

    for vid_chk in ("bitget", "mexc", "binance", "easicoin"):
        tr_chk = by_venue[vid_chk][0]
        assert len(tr_chk["exit_batches"]) == 2, f"{vid_chk} expected 2 exit_batches"
        assert sum(b["qty"] for b in tr_chk["exit_batches"]) == pytest.approx(tr_chk["qty"], abs=1e-4)
        assert sum(b["gross_fee_usd"] for b in tr_chk["exit_batches"]) == pytest.approx(
            tr_chk["exit_leg"]["gross_fee_usd"], abs=1e-3
        )

    assert dash["reconciliation"]["residual_usd"] == 0.0


def test_okx_bybit_kucoin_hl_htx_bingx_partial_tp_single_entry_fee() -> None:
    """Remaining 6 venues must never double-count entry fees on partial TP and must consolidate closed cycles."""
    # 1. OKX: intermediate type="2" snapshot superseded by final type="3" snapshot of same posId + cTime -> 1 trade;
    #    and an in-flight partial close (opened 100 contracts, closed 40 so far) allocates 40% of entry fee, not 100%!
    spec = [{"instId": "ETH-USDT-SWAP", "instType": "SWAP", "ctType": "linear", "ctVal": "0.1", "ctValCcy": "ETH"}]
    okx_trades = normalize_okx_closed_trades(
        positions_history=[
            {"posId": "p_same", "instId": "ETH-USDT-SWAP", "direction": "long", "type": "2", "cTime": "1760000000000", "uTime": "1760001000000", "openAvgPx": "2000", "closeAvgPx": "2020", "closeTotalPos": "10", "pnl": "20", "fee": "-1.2", "realizedPnl": "18.8"},
            {"posId": "p_same", "instId": "ETH-USDT-SWAP", "direction": "long", "type": "3", "cTime": "1760000000000", "uTime": "1760002000000", "openAvgPx": "2000", "closeAvgPx": "2030", "closeTotalPos": "20", "pnl": "60", "fee": "-2.8", "realizedPnl": "57.2"},
            # Separate still-open position p_inflight: opened 100 contracts (fee -4.0), only closed 40 contracts so far (exit fee -1.6, total pos fee -3.2)
            {"posId": "p_inflight", "instId": "ETH-USDT-SWAP", "direction": "long", "type": "2", "cTime": "1760010000000", "uTime": "1760011000000", "openAvgPx": "2000", "closeAvgPx": "2010", "openMaxPos": "100", "closeTotalPos": "40", "pnl": "40", "fee": "-3.2", "realizedPnl": "36.8"},
        ],
        fills=[
            {"billId": "of1", "ordId": "oo1", "posId": "p_same", "instId": "ETH-USDT-SWAP", "side": "buy", "execType": "M", "fillSz": "20", "fillPx": "2000", "fee": "-0.8", "feeCcy": "USDT", "ts": "1760000000000"},
            {"billId": "of2", "ordId": "oc1", "posId": "p_same", "instId": "ETH-USDT-SWAP", "side": "sell", "execType": "T", "fillSz": "10", "fillPx": "2020", "fee": "-1.0", "feeCcy": "USDT", "ts": "1760001000000"},
            {"billId": "of3", "ordId": "oc2", "posId": "p_same", "instId": "ETH-USDT-SWAP", "side": "sell", "execType": "T", "fillSz": "10", "fillPx": "2040", "fee": "-1.0", "feeCcy": "USDT", "ts": "1760002000000"},
            {"billId": "of4", "ordId": "oo2", "posId": "p_inflight", "instId": "ETH-USDT-SWAP", "side": "buy", "execType": "M", "fillSz": "100", "fillPx": "2000", "fee": "-4.0", "feeCcy": "USDT", "ts": "1760010000000"},
            {"billId": "of5", "ordId": "oc3", "posId": "p_inflight", "instId": "ETH-USDT-SWAP", "side": "sell", "execType": "T", "fillSz": "40", "fillPx": "2010", "fee": "-1.6", "feeCcy": "USDT", "ts": "1760011000000"},
        ],
        instruments=spec,
    )
    assert len(okx_trades) == 2
    p_same = next(t for t in okx_trades if "p_same" in t["trade_id"])
    assert p_same["is_partial_close"] is True
    assert p_same["contract_count"] == 20
    assert p_same["entry_leg"]["gross_fee_usd"] == pytest.approx(0.8, abs=1e-4)
    assert p_same["exit_leg"]["gross_fee_usd"] == pytest.approx(2.0, abs=1e-4)

    p_inf = next(t for t in okx_trades if "p_inflight" in t["trade_id"])
    assert p_inf["is_partial_close"] is True
    assert p_inf["entry_leg"]["gross_fee_usd"] == pytest.approx(1.6, abs=1e-4)
    assert p_inf["total_fee_usd"] == pytest.approx(3.2, abs=1e-4)
    assert p_inf["total_rebate_usd"] == pytest.approx(0.0, abs=1e-4)

    # 2. Bybit: 1 open execution (2 ETH @ 3000, execFee=1.20, no openFee on closed_pnl) closed by 2 TP orders (1 ETH each)
    bybit_trades = normalize_bybit_closed_trades(
        closed_pnl_rows=[
            {"orderId": "bb_tp1", "symbol": "ETHUSDT", "side": "Sell", "closedSize": "1", "qty": "1", "avgEntryPrice": "3000", "avgExitPrice": "3030", "cumEntryValue": "3000", "cumExitValue": "3030", "closedPnl": "27.885", "createdTime": "1760000100000", "updatedTime": "1760000600000"},
            {"orderId": "bb_tp2", "symbol": "ETHUSDT", "side": "Sell", "closedSize": "1", "qty": "1", "avgEntryPrice": "3000", "avgExitPrice": "3060", "cumEntryValue": "3000", "cumExitValue": "3060", "closedPnl": "57.87", "createdTime": "1760000200000", "updatedTime": "1760001200000"},
        ],
        executions=[
            {"execId": "be_open", "orderId": "bb_open", "symbol": "ETHUSDT", "side": "Buy", "execQty": "2", "execPrice": "3000", "execFee": "1.20", "isMaker": True, "closedSize": "0", "execTime": "1760000000000"},
            {"execId": "be_tp1", "orderId": "bb_tp1", "symbol": "ETHUSDT", "side": "Sell", "execQty": "1", "execPrice": "3030", "execFee": "1.515", "isMaker": False, "closedSize": "1", "execTime": "1760000600000"},
            {"execId": "be_tp2", "orderId": "bb_tp2", "symbol": "ETHUSDT", "side": "Sell", "execQty": "1", "execPrice": "3060", "execFee": "1.53", "isMaker": False, "closedSize": "1", "execTime": "1760001200000"},
        ],
    )
    assert len(bybit_trades) == 1
    assert bybit_trades[0]["is_partial_close"] is True
    assert bybit_trades[0]["qty"] == pytest.approx(2.0)
    assert bybit_trades[0]["entry_leg"]["gross_fee_usd"] == pytest.approx(1.20, abs=1e-4)
    assert bybit_trades[0]["exit_leg"]["gross_fee_usd"] == pytest.approx(3.045, abs=1e-4)

    # 3. KuCoin: 2 close rows sharing the same openTimestamp & entry fill -> consolidated into 1 trade with 1 entry fee
    kc_trades = normalize_kucoin_closed_trades(
        history_positions=[
            {"closeId": "kc_c1", "positionId": "kc_p1", "symbol": "ETHUSDTM", "side": "long", "closeSize": "1", "openPrice": "3000", "closePrice": "3030", "openTime": 1760000000000, "closeTime": 1760000600000, "realisedGrossPnl": "30"},
            {"closeId": "kc_c2", "positionId": "kc_p1", "symbol": "ETHUSDTM", "side": "long", "closeSize": "1", "openPrice": "3000", "closePrice": "3060", "openTime": 1760000000000, "closeTime": 1760001200000, "realisedGrossPnl": "60"},
        ],
        fills=[
            {"tradeId": "kf1", "orderId": "ko_open", "symbol": "ETHUSDTM", "side": "buy", "size": "2", "price": "3000", "fee": "1.20", "liquidity": "maker", "createdAt": 1760000000000},
            {"tradeId": "kf2", "orderId": "ko_tp1", "symbol": "ETHUSDTM", "side": "sell", "size": "1", "price": "3030", "fee": "1.515", "liquidity": "taker", "createdAt": 1760000600000},
            {"tradeId": "kf3", "orderId": "ko_tp2", "symbol": "ETHUSDTM", "side": "sell", "size": "1", "price": "3060", "fee": "1.53", "liquidity": "taker", "createdAt": 1760001200000},
        ],
    )
    assert len(kc_trades) == 1
    assert kc_trades[0]["is_partial_close"] is True
    assert kc_trades[0]["qty"] == pytest.approx(2.0)
    assert kc_trades[0]["entry_leg"]["gross_fee_usd"] == pytest.approx(1.20, abs=1e-4)
    assert kc_trades[0]["exit_leg"]["gross_fee_usd"] == pytest.approx(3.045, abs=1e-4)

    # 4. Hyperliquid: 1 Open Long (2 ETH, fee=0.40) + 2 Close Long orders (1 ETH each, fees 0.70 + 0.72) -> 1 trade
    hl_trades = normalize_hyperliquid_closed_trades(
        fills=[
            {"tid": 1, "oid": 100, "coin": "ETH", "dir": "Open Long", "side": "B", "px": "2000", "sz": "2.0", "fee": "0.40", "closedPnl": "0", "crossed": False, "time": 1760000000000},
            {"tid": 2, "oid": 101, "coin": "ETH", "dir": "Close Long", "side": "A", "px": "2020", "sz": "1.0", "fee": "0.70", "closedPnl": "20.0", "crossed": True, "time": 1760000600000},
            {"tid": 3, "oid": 102, "coin": "ETH", "dir": "Close Long", "side": "A", "px": "2040", "sz": "1.0", "fee": "0.72", "closedPnl": "40.0", "crossed": True, "time": 1760001200000},
        ]
    )
    assert len(hl_trades) == 1
    assert hl_trades[0]["is_partial_close"] is True
    assert hl_trades[0]["qty"] == pytest.approx(2.0)
    assert hl_trades[0]["entry_leg"]["gross_fee_usd"] == pytest.approx(0.40, abs=1e-4)
    assert hl_trades[0]["exit_leg"]["gross_fee_usd"] == pytest.approx(1.42, abs=1e-4)

    # 5. HTX: 1 open match (2 ETH, fee=-0.80) + 2 close matches (1 ETH each, fees -1.01, -1.02) -> 1 trade
    htx_trades = normalize_htx_closed_trades(
        matches=[
            {"id": 1, "order_id": 200, "contract_code": "ETH-USDT", "direction": "buy", "offset": "open", "trade_price": "2000", "trade_turnover": "4000", "trade_volume": "200", "trade_fee": "-0.80", "real_profit": "0", "role": "maker", "create_date": 1760000000000},
            {"id": 2, "order_id": 201, "contract_code": "ETH-USDT", "direction": "sell", "offset": "close", "trade_price": "2020", "trade_turnover": "2020", "trade_volume": "100", "trade_fee": "-1.01", "real_profit": "20", "role": "taker", "create_date": 1760000600000},
            {"id": 3, "order_id": 202, "contract_code": "ETH-USDT", "direction": "sell", "offset": "close", "trade_price": "2040", "trade_turnover": "2040", "trade_volume": "100", "trade_fee": "-1.02", "real_profit": "40", "role": "taker", "create_date": 1760001200000},
        ]
    )
    assert len(htx_trades) == 1
    assert htx_trades[0]["is_partial_close"] is True
    assert htx_trades[0]["qty"] == pytest.approx(2.0, abs=1e-3)
    assert htx_trades[0]["entry_leg"]["gross_fee_usd"] == pytest.approx(0.80, abs=1e-4)
    assert htx_trades[0]["exit_leg"]["gross_fee_usd"] == pytest.approx(2.03, abs=1e-4)

    # 6. BingX: 1 open fill (2 ETH, fee=-0.80) + 2 close fills (1 ETH each, fees -1.01, -1.02) -> 1 trade
    bx_trades = normalize_bingx_closed_trades(
        fills=[
            {"tradeId": "1", "orderId": "300", "symbol": "ETH-USDT", "side": "BUY", "positionSide": "LONG", "price": "2000", "qty": "2.0", "commission": "-0.80", "realizedPnl": "0", "role": "MAKER", "filledTm": 1760000000000},
            {"tradeId": "2", "orderId": "301", "symbol": "ETH-USDT", "side": "SELL", "positionSide": "LONG", "price": "2020", "qty": "1.0", "commission": "-1.01", "realizedPnl": "20.0", "role": "TAKER", "filledTm": 1760000600000},
            {"tradeId": "3", "orderId": "302", "symbol": "ETH-USDT", "side": "SELL", "positionSide": "LONG", "price": "2040", "qty": "1.0", "commission": "-1.02", "realizedPnl": "40.0", "role": "TAKER", "filledTm": 1760001200000},
        ]
    )
    assert len(bx_trades) == 1
    assert bx_trades[0]["is_partial_close"] is True
    assert bx_trades[0]["qty"] == pytest.approx(2.0)
    assert bx_trades[0]["entry_leg"]["gross_fee_usd"] == pytest.approx(0.80, abs=1e-4)
    assert bx_trades[0]["exit_leg"]["gross_fee_usd"] == pytest.approx(2.03, abs=1e-4)

    for label_chk, tr_obj in (
        ("okx", p_same),
        ("bybit", bybit_trades[0]),
        ("kucoin", kc_trades[0]),
        ("hyperliquid", hl_trades[0]),
        ("htx", htx_trades[0]),
        ("bingx", bx_trades[0]),
    ):
        assert len(tr_obj["exit_batches"]) == 2, f"{label_chk} expected 2 exit_batches"
        assert sum(b["gross_fee_usd"] for b in tr_obj["exit_batches"]) == pytest.approx(
            tr_obj["exit_leg"]["gross_fee_usd"], abs=1e-3
        )
