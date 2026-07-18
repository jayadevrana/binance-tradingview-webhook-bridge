"""Offline tests: parsing + executor sizing/flow against a fake Binance client.

Run:  python -m pytest -q      (or)      python tests/test_bridge.py
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Settings
from app.executor import Executor
from app.models import parse_alert


# ── a fake client that records calls instead of hitting Binance ───────────
class FakeClient:
    def __init__(self, price="2000", position="0"):
        self.price = Decimal(price)
        self.position = Decimal(position)
        self.orders = []
        self.leverage_calls = []
        self.margin_calls = []
        self._filters = {
            "step": Decimal("0.001"), "min_qty": Decimal("0.001"),
            "max_qty": Decimal("10000"), "min_notional": Decimal("5"),
            "qty_precision": 3,
        }

    def get_price(self, symbol):
        return self.price

    def get_symbol_filters(self, symbol):
        return self._filters

    def round_qty(self, symbol, raw):
        step = self._filters["step"]
        return (raw / step).to_integral_value(rounding="ROUND_DOWN") * step

    def get_position_amt(self, symbol):
        return self.position

    def set_leverage(self, symbol, leverage):
        self.leverage_calls.append((symbol, leverage))

    def set_margin_type(self, symbol, margin_type):
        self.margin_calls.append((symbol, margin_type))

    def place_market_order(self, symbol, side, quantity, reduce_only=False, position_side="BOTH"):
        self.orders.append({"symbol": symbol, "side": side, "qty": Decimal(str(quantity)),
                            "reduceOnly": reduce_only, "positionSide": position_side})
        return {"orderId": len(self.orders), "avgPrice": str(self.price),
                "executedQty": str(quantity)}


def live_settings(**over):
    s = Settings()
    s.dry_run = False
    s.fund_mode = "margin"
    s.set_margin_type = "OFF"
    s.max_notional_usdt = 5000.0
    for k, v in over.items():
        setattr(s, k, v)
    return s


def test_parse_buy_alert():
    raw = ('[{"MODE":"ONEWAY","EXIT":"EXIT","TS":"ETHUSDT","B":"BINANCEFUTURE"},'
           '{"MODE":"ONEWAY","TS":"ETHUSDT","TT":"BUY","OT":"MARKET",'
           '"FUND":"100","LVG":"20","B":"BINANCEFUTURE"}]')
    cmds = parse_alert(raw)
    assert len(cmds) == 2
    assert cmds[0].action == "EXIT" and cmds[0].symbol == "ETHUSDT"
    assert cmds[1].action == "ENTRY" and cmds[1].side == "BUY"
    assert cmds[1].fund == 100 and cmds[1].leverage == 20
    assert cmds[1].position_side == "BOTH"
    print("ok  parse_buy_alert")


def test_margin_mode_sizing():
    # FUND=100, LVG=20, price=2000  ->  notional=2000, qty=1.0
    c = FakeClient(price="2000", position="0")
    ex = Executor(c, live_settings())
    cmds = parse_alert('{"MODE":"ONEWAY","TS":"ETHUSDT","TT":"BUY","OT":"MARKET",'
                       '"FUND":"100","LVG":"20","B":"BINANCEFUTURE"}')
    out = ex.handle(cmds, "test")
    assert out["ok"], out
    assert c.leverage_calls == [("ETHUSDT", 20)]
    assert c.margin_calls == []                    # SET_MARGIN_TYPE=off -> untouched
    assert c.orders[0]["side"] == "BUY"
    assert c.orders[0]["qty"] == Decimal("1.000")  # 100*20/2000
    print("ok  margin_mode_sizing (qty=1.0)")


def test_notional_mode_sizing():
    c = FakeClient(price="2000", position="0")
    ex = Executor(c, live_settings(fund_mode="notional"))
    cmds = parse_alert('{"TS":"ETHUSDT","TT":"BUY","OT":"MARKET",'
                       '"FUND":"100","LVG":"20","B":"BINANCEFUTURE"}')
    out = ex.handle(cmds, "test")
    assert out["ok"], out
    assert c.orders[0]["qty"] == Decimal("0.050")  # 100/2000
    print("ok  notional_mode_sizing (qty=0.05)")


def test_exit_closes_long():
    c = FakeClient(price="2000", position="1.5")   # long 1.5
    ex = Executor(c, live_settings())
    out = ex.handle(parse_alert('{"EXIT":"EXIT","TS":"ETHUSDT","B":"BINANCEFUTURE"}'), "t")
    assert out["ok"], out
    assert c.orders[0]["side"] == "SELL"           # opposite side to close
    assert c.orders[0]["reduceOnly"] is True
    assert c.orders[0]["qty"] == Decimal("1.5")
    print("ok  exit_closes_long (SELL 1.5 reduceOnly)")


def test_exit_flat_is_noop():
    c = FakeClient(position="0")
    ex = Executor(c, live_settings())
    out = ex.handle(parse_alert('{"EXIT":"EXIT","TS":"ETHUSDT","B":"BINANCEFUTURE"}'), "t")
    assert out["ok"] and not c.orders
    print("ok  exit_flat_is_noop")


def test_max_notional_cap_blocks():
    c = FakeClient(price="2000")
    ex = Executor(c, live_settings(max_notional_usdt=1000))  # cap below 2000
    out = ex.handle(parse_alert('{"TS":"ETHUSDT","TT":"BUY","OT":"MARKET",'
                                '"FUND":"100","LVG":"20","B":"BINANCEFUTURE"}'), "t")
    assert not out["ok"]
    assert not c.orders                            # nothing placed
    assert "exceeds MAX_NOTIONAL" in out["results"][0]["error"]
    print("ok  max_notional_cap_blocks")


def test_dry_run_places_nothing():
    c = FakeClient(price="2000")
    ex = Executor(c, live_settings(dry_run=True))
    out = ex.handle(parse_alert('{"TS":"ETHUSDT","TT":"BUY","OT":"MARKET",'
                                '"FUND":"100","LVG":"20","B":"BINANCEFUTURE"}'), "t")
    assert out["ok"]
    assert out["results"][0]["status"] == "dry_run"
    assert not c.orders and not c.leverage_calls
    print("ok  dry_run_places_nothing")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} tests passed.")
