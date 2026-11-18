"""Alpaca adapter against a fake transport: no network, no orders."""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wheel.brokers import alpaca  # noqa: E402

EXP = str(dt.date.today() + dt.timedelta(days=36))
SYM = alpaca.occ("HOOD", EXP, "call", 85)


class Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        if url.endswith("/v2/account"):
            return {"cash": "2500.50", "options_approved_level": 2}
        if "/v2/positions" in url:
            return [{"symbol": "HOOD", "asset_class": "us_equity", "qty": "300", "avg_entry_price": "82", "current_price": "68.88"},
                    {"symbol": SYM, "asset_class": "us_option", "qty": "-1", "avg_entry_price": "1.7"}]
        if "/options/snapshots/" in url:
            return {"snapshots": {SYM: {"latestQuote": {"bp": 1.6, "ap": 1.8}, "greeks": {"delta": 0.21}}}}
        if "/options/contracts" in url:
            return {"option_contracts": [{"symbol": SYM, "open_interest": "150"}]}
        if method == "POST":
            return {"id": f"ord{len(self.calls)}", "status": "accepted"}
        if "/v2/orders/" in url:
            return {"status": "filled", "filled_avg_price": "1.70", "filled_qty": "2"}
        raise AssertionError(url)


class T(unittest.TestCase):
    def test_occ_round_trip(self):
        self.assertEqual(alpaca.occ("HOOD", "2026-12-24", "call", 85), "HOOD261224C00085000")
        self.assertEqual(alpaca.parse_occ("HOOD261224C00085000"), ("HOOD", "2026-12-24", "call", 85.0))
        self.assertEqual(alpaca.parse_occ(alpaca.occ("F", "2027-01-15", "put", 12.5)), ("F", "2027-01-15", "put", 12.5))

    def test_snapshot_shape_passes_engine_validation(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wheel"))
        import engine
        s = alpaca.snapshot(http=Fake(), earnings={"HOOD": "2027-02-09"})
        self.assertEqual(s["account"], {"cash": 2500.5, "options_level": 2, "approvals_on": True})
        self.assertEqual(s["short_options"], [{"symbol": "HOOD", "type": "call", "strike": 85.0, "expiry": EXP, "contracts": 1}])
        self.assertEqual(s["chains"]["HOOD"][0]["open_interest"], 150)
        clean, errors = engine.validate(s)
        self.assertEqual(errors, [])

    def test_send_is_limit_sell_to_open_and_roll_waits_for_first_leg(self):
        f = Fake()
        leg = {"instrument": "option", "action": "SELL TO OPEN", "symbol": "HOOD", "type": "call", "strike": 85, "expiry": "2026-12-24", "contracts": 2, "limit": 1.71}
        r = alpaca.send({"order_id": "abc", "legs": [leg]}, http=f)
        body = f.calls[-1][2]
        self.assertEqual((body["symbol"], body["qty"], body["side"], body["type"], body["limit_price"], body["position_intent"]),
                         ("HOOD261224C00085000", "2", "sell", "limit", "1.71", "sell_to_open"))
        self.assertEqual(r["status"], "sent")
        close = {"instrument": "option", "action": "BUY TO CLOSE", "symbol": "HOOD", "type": "call", "strike": 85, "expiry": "2026-11-20", "contracts": 2, "limit": 3.4, "limit_max": 3.5}
        o = {"order_id": "roll", "legs": [close, leg]}
        r1 = alpaca.send(o, http=f)
        self.assertEqual(f.calls[-1][2]["position_intent"], "buy_to_close")
        self.assertEqual(f.calls[-1][2]["limit_price"], "3.50")
        n = len(f.calls)
        r2 = alpaca.send({**o, **r1}, http=f)                      # first leg not filled yet
        self.assertEqual(len(f.calls), n)
        self.assertIn("Waiting", r2["send_note"])
        st = alpaca.status({**o, **r1}, http=f)
        self.assertEqual(st["fills"], [{"leg": 0, "price": 1.7, "qty": 2.0}])
        r3 = alpaca.send({**o, "broker_orders": st["broker_orders"]}, http=f)
        self.assertEqual(f.calls[-1][2]["position_intent"], "sell_to_open")
        self.assertEqual(len(r3["broker_orders"]), 2)

    def test_stock_orders(self):
        f = Fake()
        alpaca.send({"order_id": "s", "legs": [{"instrument": "stock", "action": "BUY", "symbol": "TGT", "shares": 100, "limit": 171.37}]}, http=f)
        self.assertEqual((f.calls[-1][2]["side"], f.calls[-1][2]["type"], f.calls[-1][2]["limit_price"]), ("buy", "limit", "171.37"))
        alpaca.send({"order_id": "d", "legs": [{"instrument": "stock", "action": "BUY", "symbol": "SPY", "dollars": 500}]}, http=f)
        self.assertEqual((f.calls[-1][2]["notional"], f.calls[-1][2]["type"]), ("500.00", "market"))


if __name__ == "__main__":
    unittest.main()
