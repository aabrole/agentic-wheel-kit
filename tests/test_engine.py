"""Tests for the proposal engine. Run:  python3 -m unittest -v test_engine   (from wheel_live/)"""
import copy
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "wheel"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))
import engine  # noqa: E402
import journal  # noqa: E402
import spec  # noqa: E402

FIX = json.loads((HERE / "fixtures" / "sample_account.json").read_text())


def rules(**over):
    r = types.SimpleNamespace(**{k: v for k, v in vars(spec).items() if k.isupper()})
    for k, v in over.items():
        setattr(r, k, v)
    return r


def snap(**over):
    s = copy.deepcopy(FIX)
    s.update(over)
    return s


def put(expiry, strike, bid, ask, delta, oi=500):
    return {"type": "put", "expiry": expiry, "strike": strike, "bid": bid, "ask": ask, "delta": -delta,
            "open_interest": oi, "id": f"p{strike}"}


class Calls(unittest.TestCase):
    def test_real_snapshot_takes_liquid_strike_at_or_above_cost(self):
        out = engine.propose(snap())
        self.assertEqual(out["blocked"], [])
        self.assertEqual(len(out["proposals"]), 1)
        p = out["proposals"][0]
        self.assertEqual((p["symbol"], p["strike"], p["contracts"], p["expiry"]), ("HOOD", 85.0, 2, "2026-12-24"))
        self.assertEqual((p["limit"], p["limit_floor"]), (1.71, 1.59))     # floor 1.5825 rounds UP
        self.assertAlmostEqual(p["credit_if_filled_at_limit"], 1.71 * 200 - 0.08, places=2)
        self.assertAlmostEqual(p["if_assigned"]["result_with_premium"], (85 - 82.0) * 200 + 341.92, places=2)
        self.assertGreaterEqual(p["strike"], p["net_cost_per_share"])
        self.assertTrue(all(r["strike"] >= 82.0 for r in p["rejected_strikes"]))   # nothing below cost considered
        self.assertFalse(any("qualified" in f for f in p["flags"]))                 # 37 days: qualified

    def test_one_lot_always_stays_uncovered(self):
        s = snap(short_options=[{"symbol": "HOOD", "type": "call", "strike": 90, "expiry": "2026-12-24", "contracts": 1}])
        self.assertEqual(engine.propose(s)["proposals"][0]["contracts"], 1)
        s["short_options"][0]["contracts"] = 2
        out = engine.propose(s)
        self.assertEqual(out["proposals"], [])
        self.assertTrue(any(k["symbol"] == "HOOD" and "no uncovered lot" in k["reason"] for k in out["skipped"]))

    def test_small_positions_never_get_a_call(self):
        out = engine.propose(snap())
        sk = {k["symbol"]: k["reason"] for k in out["skipped"]}
        self.assertIn("under one 100-share lot", sk["MSFT"])
        self.assertIn("under one 100-share lot", sk["AAPL"])

    def test_refuses_when_approvals_are_off_or_level_too_low(self):
        s = snap(); s["account"]["approvals_on"] = False
        out = engine.propose(s)
        self.assertTrue(out["blocked"]); self.assertEqual(out["proposals"], [])
        s = snap(); s["account"]["options_level"] = 0
        self.assertTrue(engine.propose(s)["blocked"])

    def test_unknown_earnings_date_blocks_a_stock_but_not_an_etf(self):
        out = engine.propose(snap(earnings={}))
        self.assertEqual(out["proposals"], [])
        self.assertTrue(any("earnings date unknown" in k["reason"] for k in out["skipped"]))
        s = snap(earnings={})
        s["positions"] = [{"symbol": "XLF", "shares": 200, "avg_cost": 80.0}]
        s["quotes"] = {"XLF": 68.88}
        s["chains"] = {"XLF": s["chains"]["HOOD"]}
        self.assertEqual(len(engine.propose(s)["proposals"]), 1)

    def test_earnings_inside_the_window_shortens_the_expiry_and_flags_tax(self):
        out = engine.propose(snap(earnings={"HOOD": "2026-12-10"}), rules(CALL_FLOOR="none"))
        p = out["proposals"][0]
        self.assertEqual(p["expiry"], "2026-11-27")
        self.assertTrue(any("earnings" in f for f in p["flags"]))
        self.assertTrue(any("not a qualified covered call" in f for f in p["flags"]))

    def test_earnings_before_every_usable_expiry_means_no_proposal(self):
        out = engine.propose(snap(earnings={"HOOD": "2026-11-20"}))
        self.assertEqual(out["proposals"], [])

    def test_without_the_cost_floor_it_picks_by_delta(self):
        p = engine.propose(snap(), rules(CALL_FLOOR="none"))["proposals"][0]
        self.assertEqual(p["strike"], 80.0)             # delta 0.288, the closest liquid strike to 0.30

    def test_kept_premium_lowers_the_floor(self):
        p = engine.propose(snap(net_premium={"HOOD": 2.0}))["proposals"][0]
        self.assertEqual(p["net_cost_per_share"], 80.0)
        self.assertEqual(p["strike"], 80.0)

    def test_quote_gates(self):
        self.assertEqual(engine.propose(snap(), rules(MAX_SPREAD=0.10))["proposals"], [])
        self.assertEqual(engine.propose(snap(), rules(MIN_OPEN_INTEREST=10_000))["proposals"], [])
        self.assertEqual(engine.propose(snap(), rules(MIN_YIELD_ANNUAL=5.0))["proposals"], [])

    def test_far_above_market_cost_takes_the_nearest_strike_or_nothing(self):
        s = snap(); s["positions"][0]["avg_cost"] = 89.5
        p = engine.propose(s)["proposals"][0]
        self.assertEqual(p["strike"], 90.0)             # only liquid strike at or above cost, delta 0.13
        self.assertTrue(any("below the delta band" in f for f in p["flags"]))
        s["positions"][0]["avg_cost"] = 120.0
        self.assertEqual(engine.propose(s)["proposals"], [])

    def test_limit_uses_five_cent_ticks_from_three_dollars(self):
        c = {"bid": 3.65, "ask": 4.50}
        self.assertEqual(engine._limit(c, spec), (4.05, 3.90))           # 3.8625 rounds up to 3.90
        self.assertEqual(engine._limit({"bid": 2.99, "ask": 3.01}, spec), (3.00, 3.00))

    def test_expiry_notice(self):
        s = snap(today="2026-12-23", short_options=[{"symbol": "HOOD", "type": "call", "strike": 69, "expiry": "2026-12-24", "contracts": 2}])
        n = engine.propose(s)["notices"]
        self.assertEqual(len(n), 1); self.assertIn("3:30 pm ET", n[0]["text"])


class Puts(unittest.TestCase):
    def base(self):
        s = snap()
        s["account"]["cash"] = 20_000.0
        s["quotes"]["XLF"] = 57.0
        s["chains"]["XLF"] = [put("2026-12-24", 54, 0.70, 0.80, 0.29), put("2026-12-24", 52, 0.40, 0.46, 0.18),
                              put("2026-12-24", 56, 1.20, 1.30, 0.42)]
        return s

    def test_puts_are_off_by_default(self):
        out = engine.propose(self.base())
        self.assertTrue(all(p["kind"] != "cash-secured put" for p in out["proposals"]))

    def test_put_is_cash_secured_and_capped_per_name(self):
        out = engine.propose(self.base(), rules(PUTS_ENABLED=True, PUT_ALLOW_LIST=["XLF"]))
        p = [x for x in out["proposals"] if x["kind"] == "cash-secured put"][0]
        self.assertEqual((p["strike"], p["contracts"]), (54.0, 1))       # 20% of ~$49.5k fits one contract
        self.assertLessEqual(p["cash_set_aside"], 20_000.0)
        self.assertAlmostEqual(p["max_loss"], 54 * 100 - p["credit_if_filled_at_limit"], places=2)   # fees count

    def test_put_blocked_when_cash_is_short_or_stress_too_high(self):
        s = self.base(); s["account"]["cash"] = 2_000.0
        out = engine.propose(s, rules(PUTS_ENABLED=True, PUT_ALLOW_LIST=["XLF"]))
        self.assertFalse([x for x in out["proposals"] if x["kind"] == "cash-secured put"])
        out = engine.propose(self.base(), rules(PUTS_ENABLED=True, PUT_ALLOW_LIST=["XLF"], STRESS_LIMIT=0.05))
        self.assertFalse([x for x in out["proposals"] if x["kind"] == "cash-secured put"])


class Journal(unittest.TestCase):
    def test_net_figures(self):
        with tempfile.TemporaryDirectory() as d:
            j = Path(d) / "j.jsonl"
            journal.append(j, {"event": "fill", "symbol": "HOOD", "type": "call", "side": "sell", "contracts": 2, "price": 1.71, "fees": 0.08})
            journal.append(j, {"event": "fill", "symbol": "HOOD", "type": "call", "side": "buy", "contracts": 2, "price": 0.50, "fees": 0.08})
            ev = journal.read(j)
            self.assertAlmostEqual(journal.net_option_cash(ev), (1.71 - 0.50) * 200 - 0.16, places=2)
            self.assertAlmostEqual(journal.net_call_premium_per_share(ev, "HOOD", 300), ((1.71 - 0.50) * 200 - 0.16) / 300, places=4)
            journal.append(j, {"event": "assigned", "symbol": "HOOD", "type": "call", "strike": 85, "contracts": 2, "shares_after": 100})
            kept = ((1.71 - 0.50) * 200 - 0.16) * 100 / 300         # the lot that stayed keeps its third
            self.assertAlmostEqual(journal.net_call_premium_per_share(journal.read(j), "HOOD", 100), kept / 100, places=4)
            journal.append(j, {"event": "assigned", "symbol": "HOOD", "type": "call", "strike": 90, "contracts": 1, "shares_after": 0})
            self.assertEqual(journal.net_call_premium_per_share(journal.read(j), "HOOD", 0), 0.0)

    def test_assignment_without_share_count_is_refused(self):
        ev = [{"event": "fill", "symbol": "X", "type": "call", "side": "sell", "contracts": 2, "price": 1, "fees": 0},
              {"event": "assigned", "symbol": "X", "type": "call", "contracts": 1, "strike": 100}]
        with self.assertRaises(ValueError):
            journal.net_call_premium_per_share(ev, "X", 100)

    def test_partial_assignment_keeps_the_rest(self):
        ev = [{"event": "fill", "symbol": "X", "type": "call", "side": "sell", "contracts": 2, "price": 1, "fees": 0},
              {"event": "assigned", "symbol": "X", "type": "call", "contracts": 1, "strike": 100, "shares_after": 100}]
        self.assertEqual(journal.net_call_premium_per_share(ev, "X", 100), 1.0)
        self.assertEqual(journal.net_option_cash(ev), 200.0)


class BadInput(unittest.TestCase):
    """Cases from the Codex adversarial review, 2026-11-17. Bad data must mean no proposal."""

    def blocked(self, s, needle):
        out = engine.propose(s)
        self.assertEqual(out["proposals"], [])
        self.assertTrue(any(needle in b for b in out["blocked"]), out["blocked"])

    def test_approvals_must_be_a_real_true(self):
        for bad in ("false", "true", 1, None):
            s = snap(); s["account"]["approvals_on"] = bad
            self.blocked(s, "approvals_on")

    def test_negative_or_fractional_short_counts_are_rejected(self):
        for n in (-1, 0, 1.5):
            s = snap(short_options=[{"symbol": "HOOD", "type": "call", "strike": 100, "expiry": "2026-12-24", "contracts": n}])
            self.blocked(s, "short contracts")

    def test_nan_premium_cannot_defeat_the_cost_floor(self):
        self.blocked(snap(net_premium={"HOOD": "NaN"}), "net_premium")
        self.blocked(snap(quotes={"HOOD": float("nan")}), "quote")

    def test_missing_inventory_is_not_read_as_nothing_covered(self):
        s = snap(); del s["short_options"]
        self.blocked(s, "missing 'short_options'")

    def test_repeated_position_rows_are_rejected(self):
        s = snap(); s["positions"].append(dict(s["positions"][0]))
        self.blocked(s, "repeated symbol")

    def test_numbers_arriving_as_text_are_normalised(self):
        s = snap(today="2026-12-23", quotes={"HOOD": "69.5"},
                 short_options=[{"symbol": "HOOD", "type": "call", "strike": "69", "expiry": "2026-12-24", "contracts": "2"}])
        s["positions"][0].update(shares="300.9", avg_cost="82.0")
        out = engine.propose(s)
        self.assertEqual(out["blocked"], [])
        self.assertEqual(len(out["notices"]), 1)

    def test_empty_or_stale_earnings_count_as_unknown(self):
        for bad in ("", False, None, "2026-10-27"):
            out = engine.propose(snap(earnings={"HOOD": bad}))
            self.assertEqual(out["proposals"], [], bad)
            self.assertTrue(any(k["symbol"] == "HOOD" for k in out["skipped"]))

    def test_minimum_premium_binds_on_the_price_actually_worked(self):
        s = snap()
        s["positions"] = [{"symbol": "XLF", "shares": 100, "avg_cost": 640.0}]
        s["quotes"] = {"XLF": 640.0}
        s["chains"] = {"XLF": [{"type": "call", "expiry": "2026-12-22", "strike": 640, "bid": 3.05, "ask": 3.10,
                                "delta": 0.30, "open_interest": 500}]}
        self.assertEqual(engine.propose(s)["proposals"], [])           # 3.05 is under the 3.07 minimum
        s["positions"][0]["avg_cost"] = 20.0; s["quotes"] = {"XLF": 20.0}
        s["chains"]["XLF"][0].update(strike=20, bid=0.09, ask=0.11)
        p = engine.propose(s)["proposals"][0]
        self.assertEqual((p["limit"], p["limit_floor"]), (0.10, 0.10))   # never worked below $0.10

    def test_put_loss_counts_fees(self):
        s = snap(); s["account"]["cash"] = 20_000.0; s["quotes"]["XLF"] = 57.0
        s["chains"]["XLF"] = [put("2026-12-24", 54, 0.70, 0.80, 0.29)]
        p = [x for x in engine.propose(s, rules(PUTS_ENABLED=True, PUT_ALLOW_LIST=["XLF"]))["proposals"] if x["kind"] == "cash-secured put"][0]
        self.assertAlmostEqual(p["max_loss"], 5400 - p["credit_if_filled_at_limit"], places=2)


if __name__ == "__main__":
    unittest.main()
