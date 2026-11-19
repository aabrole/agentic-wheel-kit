"""Tests for the v2 additions (rolls, rotation). Run:  python3 -m pytest -q test_engine_v2.py"""
import copy
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "wheel"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))
import engine  # noqa: E402
import spec  # noqa: E402
import spec_v2  # noqa: E402


def rules(mod=spec_v2, **over):
    r = types.SimpleNamespace(**{k: v for k, v in vars(mod).items() if k.isupper()})
    for k, v in over.items():
        setattr(r, k, v)
    return r


def opt(expiry, strike, bid, ask, delta=0.3, oi=500, kind="call"):
    return {"type": kind, "expiry": expiry, "strike": strike, "bid": bid, "ask": ask, "delta": delta,
            "open_interest": oi, "id": f"{expiry}-{strike}", "sell_floor_hint": None}


def base(**over):
    s = {"today": "2026-11-18", "account": {"cash": 500.0, "options_level": 2, "approvals_on": True},
         "positions": [{"symbol": "AAA", "shares": 300, "avg_cost": 50.0}],
         "short_options": [{"symbol": "AAA", "type": "call", "strike": 55.0, "expiry": "2026-11-20", "contracts": 2}],
         "quotes": {"AAA": 58.0}, "earnings": {"AAA": "2027-02-01"}, "ex_dividend": {}, "net_premium": {},
         "chains": {"AAA": [opt("2026-11-20", 55.0, 3.00, 3.10, 0.9),
                            opt("2026-12-24", 56.0, 4.20, 4.40, 0.6), opt("2026-12-24", 57.5, 3.40, 3.60, 0.5),
                            opt("2026-12-24", 60.0, 2.30, 2.50, 0.4), opt("2026-12-24", 65.0, 0.90, 1.00, 0.2)]}}
    s.update(over)
    return s


CANDS = [{"symbol": "BIG", "last": 400.0, "sma200": 300.0, "ret_252": 2.0, "vol_252": 0.6},
         {"symbol": "RUN", "last": 40.0, "sma200": 30.0, "ret_252": 1.2, "vol_252": 0.5},
         {"symbol": "DOWN", "last": 20.0, "sma200": 25.0, "ret_252": 3.0, "vol_252": 0.3},
         {"symbol": "CALM", "last": 30.0, "sma200": 28.0, "ret_252": 0.1, "vol_252": 0.15},
         {"symbol": "PENNY", "last": 5.0, "sma200": 2.0, "ret_252": 5.0, "vol_252": 0.9},
         {"symbol": "AAA", "last": 58.0, "sma200": 40.0, "ret_252": 4.0, "vol_252": 0.4}]


def kinds(out):
    return [p["kind"] for p in out["proposals"]]


class Rolls(unittest.TestCase):
    def test_v1_never_rolls_or_rotates(self):
        s = base(candidates=CANDS)
        s["account"]["cash"] = 50_000.0
        out = engine.propose(s)                      # default rules = spec v1
        self.assertEqual(out["version"], spec.VERSION)
        self.assertNotIn("roll up and out", kinds(out))
        self.assertNotIn("rotation stock", kinds(out))

    def test_roll_takes_highest_strike_that_is_still_a_credit(self):
        out = engine.propose(base(), rules())
        r = [p for p in out["proposals"] if p["kind"] == "roll up and out"]
        self.assertEqual(len(r), 1)
        p = r[0]
        # buy-back worst price 3.10. Sale floors: 57.5 -> 3.45 (credit), 60 -> 2.35, 65 -> 0.95 (debits).
        self.assertEqual(p["strike"], 57.5)
        self.assertEqual(p["contracts"], 2)
        self.assertGreaterEqual(p["net_credit_worst_case"], 0)
        self.assertEqual([l["action"] for l in p["legs"]], ["BUY TO CLOSE", "SELL TO OPEN"])
        self.assertEqual(p["legs"][0]["limit_max"], 3.10)
        self.assertTrue(any(x["strike"] == 60.0 for x in p["rejected_strikes"]))

    def test_no_roll_when_only_debits(self):
        s = base()
        s["chains"]["AAA"][0] = opt("2026-11-20", 55.0, 5.00, 5.20, 0.95)     # buy-back too dear
        out = engine.propose(s, rules())
        self.assertNotIn("roll up and out", kinds(out))
        self.assertTrue(any("called away" in x["reason"] for x in out["skipped"]))

    def test_no_roll_out_of_the_money_or_far_from_expiry(self):
        s = base(quotes={"AAA": 54.0})
        self.assertNotIn("roll up and out", kinds(engine.propose(s, rules())))
        s = base()
        s["short_options"][0]["expiry"] = "2026-12-04"
        s["chains"]["AAA"][0] = opt("2026-12-04", 55.0, 3.0, 3.1, 0.9)
        self.assertNotIn("roll up and out", kinds(engine.propose(s, rules())))

    def test_no_roll_through_earnings_or_without_old_quote(self):
        out = engine.propose(base(earnings={"AAA": "2026-12-10"}), rules())
        self.assertNotIn("roll up and out", kinds(out))
        s = base()
        s["chains"]["AAA"] = s["chains"]["AAA"][1:]
        out = engine.propose(s, rules())
        self.assertNotIn("roll up and out", kinds(out))
        self.assertTrue(any("quote is not in the snapshot" in x["reason"] for x in out["skipped"]))

    def test_never_rolls_to_the_same_or_lower_strike(self):
        s = base()
        s["chains"]["AAA"] = [opt("2026-11-20", 55.0, 3.00, 3.10, 0.9), opt("2026-12-24", 55.0, 5.0, 5.2, 0.7),
                              opt("2026-12-24", 50.0, 9.0, 9.2, 0.9)]
        self.assertNotIn("roll up and out", kinds(engine.propose(s, rules())))


class Rotation(unittest.TestCase):
    def snap(self, cash, **over):
        s = base(candidates=CANDS, candidates_as_of="2026-11-17", short_options=[], **over)
        s["account"]["cash"] = cash
        return s

    def rot(self, out):
        return [p for p in out["proposals"] if p["kind"] in ("rotation stock", "idle cash to SPY")]

    def test_picks_strongest_trend_it_can_afford(self):
        out = engine.propose(self.snap(9000.0), rules(ROT_RULE="trend"))
        p = self.rot(out)[0]
        # BIG ranks first but 100 shares cost 40,000; DOWN is below its average; PENNY is under $10;
        # AAA is already held. RUN is the strongest that qualifies and fits.
        self.assertEqual(p["symbol"], "RUN")
        self.assertEqual(p["shares"], 200)
        self.assertLessEqual(p["cost"], 9000.0)
        self.assertTrue(any(x["symbol"] == "BIG" for x in p["rejected_strikes"]))
        self.assertTrue(any("BETA" in f for f in p["flags"]))

    def test_buys_the_top_name_when_cash_allows(self):
        p = self.rot(engine.propose(self.snap(100_000.0), rules(ROT_RULE="trend")))[0]
        self.assertEqual((p["symbol"], p["shares"]), ("BIG", 200))

    def test_nothing_when_cash_is_short(self):
        out = engine.propose(self.snap(2000.0), rules(ROT_RULE="trend", SWEEP_TO_SPY=False))
        self.assertEqual(self.rot(out), [])
        self.assertTrue(any("does not cover 100 shares" in x["reason"] for x in out["skipped"]))

    def test_cash_securing_puts_is_not_free(self):
        s = self.snap(9000.0)
        s["short_options"] = [{"symbol": "ZZZ", "type": "put", "strike": 60.0, "expiry": "2026-12-24", "contracts": 1}]
        self.assertEqual(self.rot(engine.propose(s, rules(ROT_RULE="trend", SWEEP_TO_SPY=False))), [])
        park = self.rot(engine.propose(s, rules(ROT_RULE="trend")))
        self.assertEqual([(p["symbol"], p["dollars"]) for p in park], [("SPY", 3000.0)])   # never the $6,000 behind the put

    # ---- leftover cash is parked in SPY, and parked SPY funds the next 100-share lot
    def park_snap(self, cash, spy_shares, parked):
        s = self.snap(cash)
        s["quotes"]["SPY"] = 600.0
        if spy_shares:
            s["positions"].append({"symbol": "SPY", "shares": spy_shares, "avg_cost": 590.0})
        s["parked_spy_shares"] = parked
        return s

    def test_leftover_cash_is_parked_in_spy(self):
        out = engine.propose(self.park_snap(2000.0, 0, 0), rules(ROT_RULE="trend"))
        p = self.rot(out)
        self.assertEqual([(x["action"], x["symbol"], x["dollars"], x.get("parked")) for x in p], [("BUY", "SPY", 2000.0, True)])
        self.assertEqual(self.rot(engine.propose(self.park_snap(10.0, 0, 0), rules(ROT_RULE="trend"))), [])  # under SWEEP_MIN

    def test_a_buy_from_cash_does_not_also_park(self):
        out = engine.propose(self.park_snap(9000.0, 0, 0), rules(ROT_RULE="trend"))
        self.assertEqual([(x["kind"], x["symbol"]) for x in self.rot(out)], [("rotation stock", "RUN")])

    def test_parked_spy_is_sold_to_fund_a_lot(self):
        # $1,000 cash + 6 parked SPY ($3,600) covers 100 RUN at about $4,008; cash alone does not
        out = engine.propose(self.park_snap(1000.0, 6.0, 6.0), rules(ROT_RULE="trend"))
        p = [x for x in out["proposals"] if x["kind"] == "release parked SPY"]
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0]["action"], p[0]["symbol"]), ("SELL", "SPY"))
        self.assertGreaterEqual(1000.0 + p[0]["dollars"], 4008.0)
        self.assertLessEqual(p[0]["dollars"], 3600.0)
        self.assertEqual(self.rot(out), [])                                  # the stock buy waits for the next run

    def test_spy_the_owner_bought_is_never_counted(self):
        # 6 SPY held but none of it was parked by the system: it is not buying power
        out = engine.propose(self.park_snap(1000.0, 6.0, 0.0), rules(ROT_RULE="trend"))
        self.assertFalse([x for x in out["proposals"] if x["kind"] == "release parked SPY"])
        # a parked figure larger than what is held is capped at what is held
        out = engine.propose(self.park_snap(1000.0, 1.0, 50.0), rules(ROT_RULE="trend"))
        self.assertFalse([x for x in out["proposals"] if x["kind"] == "release parked SPY"])

    def test_bad_parked_number_rejects_the_snapshot(self):
        s = self.park_snap(1000.0, 6.0, 6.0)
        s["parked_spy_shares"] = "lots"
        self.assertTrue(engine.propose(s, rules(ROT_RULE="trend"))["blocked"])

    def test_quality_rule_picks_calmest(self):
        p = self.rot(engine.propose(self.snap(9000.0), rules(ROT_RULE="quality")))[0]
        self.assertEqual(p["symbol"], "CALM")

    # ---- premium rule: uptrend, then highest option premium
    def psnap(self, cash, ivs, **over):
        s = self.snap(cash, **over)
        s["candidates"] = copy.deepcopy(CANDS)
        for c in s["candidates"]:
            c["atm_iv"] = ivs.get(c["symbol"])
        return s

    def test_premium_picks_highest_iv_in_uptrend(self):
        # DOWN has the highest IV but is below its 200-day average; BIG is unaffordable at 9,000
        s = self.psnap(9000.0, {"BIG": 0.8, "RUN": 0.55, "DOWN": 0.95, "CALM": 0.2, "PENNY": 0.99})
        p = self.rot(engine.propose(s, rules(ROT_RULE="premium")))[0]
        self.assertEqual((p["symbol"], p["rule"], p["atm_iv"]), ("RUN", "premium", 0.55))
        self.assertIn("highest option premium", p["why_this_stock"])
        self.assertIn("implied volatility 55%", p["why_this_stock"])
        self.assertTrue(any("BETA rule, a lead and not a finding" in f for f in p["flags"]))
        self.assertTrue(any("37%" in f for f in p["flags"]))
        self.assertTrue(any(f.startswith("RUN ranks 2 of") for f in p["flags"]))      # BIG ranked first, cost too much
        self.assertIn("option market is pricing", p["downside"])
        self.assertTrue(all("atm_iv" in r for r in p["ranked"]))
        self.assertTrue(any(x["symbol"] == "BIG" and x["atm_iv"] == 0.8 for x in p["rejected_strikes"]))
        self.assertNotIn("DOWN", [r["symbol"] for r in p["ranked"]])

    def test_premium_buys_top_name_when_affordable_and_skips_missing_iv(self):
        s = self.psnap(100_000.0, {"BIG": 0.5, "RUN": 0.7, "CALM": None})
        p = self.rot(engine.propose(s, rules(ROT_RULE="premium")))[0]
        self.assertEqual(p["symbol"], "RUN")
        self.assertEqual([r["symbol"] for r in p["ranked"]], ["RUN", "BIG"])          # CALM has no premium level
        self.assertEqual(p["eligible"], 2)

    def test_premium_with_no_iv_anywhere_proposes_no_stock_and_parks(self):
        s = self.psnap(9000.0, {})
        s["quotes"]["SPY"] = 600.0
        out = engine.propose(s, rules(ROT_RULE="premium"))
        self.assertFalse([p for p in out["proposals"] if p["kind"] == "rotation stock"])
        self.assertTrue(any("no option premium levels" in x["reason"] and "Alpaca keys" in x["reason"] for x in out["skipped"]))
        self.assertEqual([(p["symbol"], p["dollars"]) for p in self.rot(out)], [("SPY", 9000.0)])
        out = engine.propose(s, rules(ROT_RULE="premium", SWEEP_TO_SPY=False))
        self.assertEqual(self.rot(out), [])

    def test_premium_parks_leftover_and_releases_parked_spy(self):
        ivs = {"BIG": 0.8, "RUN": 0.55}
        s = self.psnap(2000.0, ivs)
        s["quotes"]["SPY"] = 600.0
        s["parked_spy_shares"] = 0
        out = engine.propose(s, rules(ROT_RULE="premium"))
        self.assertEqual([(x["symbol"], x["dollars"], x.get("parked")) for x in self.rot(out)], [("SPY", 2000.0, True)])
        s = self.psnap(1000.0, ivs)
        s["quotes"]["SPY"] = 600.0
        s["positions"].append({"symbol": "SPY", "shares": 6.0, "avg_cost": 590.0})
        s["parked_spy_shares"] = 6.0
        out = engine.propose(s, rules(ROT_RULE="premium"))
        rel = [x for x in out["proposals"] if x["kind"] == "release parked SPY"]
        self.assertEqual(len(rel), 1)
        self.assertIn("a lead, not a finding", " ".join(rel[0]["flags"]))

    def test_negative_atm_iv_fails_validation(self):
        for bad in (-0.2, 0, "abc"):
            s = self.psnap(9000.0, {"RUN": bad})
            out = engine.propose(s, rules(ROT_RULE="premium"))
            self.assertTrue(out["blocked"], bad)
            self.assertEqual(out["proposals"], [])

    def test_evidence_is_the_full_test_not_the_partial_one(self):
        s = self.psnap(9000.0, {"RUN": 0.5, "CALM": 0.2})
        for rule in ("trend", "premium", "quality"):
            p = self.rot(engine.propose(s, rules(ROT_RULE=rule)))[0]
            self.assertIn("198 names", p["evidence"])
            self.assertNotIn("Partial test", p["evidence"])

    def test_spy_rule_and_off(self):
        p = self.rot(engine.propose(self.snap(9000.0), rules(ROT_RULE="spy")))[0]
        self.assertEqual((p["symbol"], p["dollars"]), ("SPY", 9000.0))
        self.assertEqual(self.rot(engine.propose(self.snap(9000.0), rules(ROT_RULE="off"))), [])
        self.assertEqual(self.rot(engine.propose(self.snap(9000.0), rules(ROTATION_ENABLED=False))), [])

    def test_bad_candidate_rejects_the_snapshot(self):
        s = self.snap(9000.0)
        s["candidates"] = copy.deepcopy(CANDS)
        s["candidates"][1]["last"] = "abc"
        out = engine.propose(s, rules())
        self.assertEqual(out["proposals"], [])
        self.assertTrue(out["blocked"])

    def test_approvals_gate_still_blocks_everything(self):
        s = self.snap(100_000.0)
        s["account"]["approvals_on"] = False
        out = engine.propose(s, rules())
        self.assertEqual(out["proposals"], [])


if __name__ == "__main__":
    unittest.main()
