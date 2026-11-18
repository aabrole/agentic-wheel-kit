"""Tests for the dashboard logic. Run from wheel_live/dashboard:  python3 -m pytest -q test_core.py"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "wheel"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))
import core  # noqa: E402
import scenarios  # noqa: E402


class Base(unittest.TestCase):
    scenario = "real"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = core.state_dir
        core.state_dir = lambda mode: (self.tmp / mode).mkdir(parents=True, exist_ok=True) or (self.tmp / mode)
        self.assertTrue(scenarios.load(self.scenario)["ok"])

    def tearDown(self):
        core.state_dir = self._old
        shutil.rmtree(self.tmp)

    def st(self):
        return core.full_state("staging")

    def events(self, kind):
        return [e for e in core._lines(self.tmp / "staging" / "journal.jsonl") if e.get("event") == kind]


class Real(Base):
    def test_real_account_proposes_a_call_first(self):
        s = self.st()
        kinds = [p["kind"] for p in s["engine"]["proposals"]]
        self.assertEqual(kinds[0], "covered call")
        p = s["engine"]["proposals"][0]
        self.assertEqual((p["symbol"], p["contracts"]), ("HOOD", 2))
        self.assertGreaterEqual(p["strike"], 82.0)
        self.assertFalse(s["send_enabled"])
        for r in (x for x in s["engine"]["proposals"] if x["kind"] == "rotation stock"):
            self.assertLessEqual(r["cost"], s["portfolio"]["cash"])          # small cash: only what it can afford
            self.assertTrue(r["rank"] == 1 or any("ranks" in f for f in r["flags"]))

    def test_approve_queues_then_simulated_fill_updates_everything(self):
        p = self.st()["engine"]["proposals"][0]
        r = core.decide("staging", p["id"], "approve", "looks right")
        self.assertTrue(r["ok"])
        s = self.st()
        self.assertEqual(s["outbox"][0]["status"], "queued")
        self.assertIn("STAGING", s["outbox"][0]["send_note"])
        cash0 = s["portfolio"]["cash"]
        self.assertTrue(core.simulate_fill("staging", s["outbox"][0]["order_id"])["ok"])
        s = self.st()
        self.assertEqual(s["outbox"][0]["status"], "filled")
        self.assertEqual(s["portfolio"]["calls_out"], 2)
        self.assertAlmostEqual(s["portfolio"]["cash"], cash0 + p["credit_if_filled_at_limit"], places=2)
        self.assertEqual(s["scoreboard"]["premium_collected"], round(p["limit"] * 200, 2))
        self.assertNotIn("covered call", [x["kind"] for x in s["engine"]["proposals"]])   # nothing left to cover
        hood = next(x for x in s["portfolio"]["positions"] if x["symbol"] == "HOOD")
        self.assertLess(hood["net_cost"], 82.0)                                  # premium lowers net cost
        self.assertTrue(self.events("fill")[0]["simulated"])

    def test_decline_is_logged_and_nothing_is_queued(self):
        p = self.st()["engine"]["proposals"][0]
        core.decide("staging", p["id"], "decline", "not today")
        s = self.st()
        self.assertEqual(s["outbox"], [])
        self.assertEqual(s["engine"]["proposals"][0]["status"], "declined")
        self.assertEqual(self.events("decision")[0]["decision"], "declined")

    def test_edit_below_cost_needs_an_override_and_records_it(self):
        p = self.st()["engine"]["proposals"][0]
        r = core.decide("staging", p["id"], "approve", edits={"strike": 75})
        self.assertFalse(r["ok"])
        self.assertTrue(r["needs_override"])
        self.assertTrue(any("below your net cost" in w for w in r["warnings"]))
        r = core.decide("staging", p["id"], "approve", edits={"strike": 75}, override=True)
        self.assertTrue(r["ok"])
        self.assertTrue(any("below your net cost" in w for w in r["order"]["overrides"]))

    def test_manual_orders_that_would_be_naked_or_unaffordable_are_blocked(self):
        leg = {"action": "SELL TO OPEN", "type": "call", "symbol": "HOOD", "strike": 90, "expiry": "2026-12-24", "contracts": 4, "limit": 1.0}
        r = core.manual("staging", leg, override=True)
        self.assertFalse(r["ok"])
        self.assertIn("uncovered call is not allowed", r["error"])
        r = core.manual("staging", {"action": "BUY", "symbol": "AMD", "shares": 100, "limit": 500}, override=True)
        self.assertFalse(r["ok"])
        r = core.manual("staging", {"action": "SELL TO OPEN", "type": "put", "symbol": "HOOD", "strike": 60, "expiry": "2026-12-24", "contracts": 1, "limit": 1}, override=True)
        self.assertFalse(r["ok"])

    def test_covering_the_reserved_lot_warns(self):
        leg = {"action": "SELL TO OPEN", "type": "call", "symbol": "HOOD", "strike": 85, "expiry": "2026-12-24", "contracts": 3, "limit": 1.9}
        r = core.manual("staging", leg)
        self.assertTrue(r.get("needs_override"))
        self.assertTrue(any("set aside" in w for w in r["warnings"]))

    def test_cannot_sell_shares_out_from_under_open_calls(self):
        p = self.st()["engine"]["proposals"][0]
        core.decide("staging", p["id"], "approve")
        core.simulate_fill("staging", self.st()["outbox"][0]["order_id"])
        r = core.manual("staging", {"action": "SELL", "symbol": "HOOD", "shares": 200, "limit": 90}, override=True)
        self.assertFalse(r["ok"])
        self.assertIn("uncovered", r["error"])

    def test_settings_are_bounded_logged_and_change_proposals(self):
        self.assertFalse(core.change_settings("staging", {"rules": {"CALL_FLOOR": "none"}})["ok"])
        self.assertFalse(core.change_settings("staging", {"rules": {"CALL_DTE_TARGET": 7}})["ok"])
        self.assertFalse(core.change_settings("staging", {"rules": {"PUTS_ENABLED": True}})["ok"])
        pv = core.preview("staging", {"uncovered_lots": {"HOOD": 2}})
        self.assertTrue(pv["changed"])
        self.assertEqual(core.load_settings("staging")["uncovered_lots"], {})        # preview saved nothing
        self.assertTrue(core.change_settings("staging", {"uncovered_lots": {"HOOD": 2}}, "keep more upside")["ok"])
        self.assertEqual(self.st()["engine"]["proposals"][0]["contracts"], 1)
        self.assertEqual(len(self.events("settings")), 1)

    def test_cancel(self):
        p = self.st()["engine"]["proposals"][0]
        oid = core.decide("staging", p["id"], "approve")["order"]["order_id"]
        self.assertTrue(core.cancel("staging", oid)["ok"])
        self.assertFalse(core.simulate_fill("staging", oid)["ok"])
        self.assertFalse(core.cancel("staging", oid)["ok"])

    def test_live_mode_has_no_simulated_fills(self):
        self.assertFalse(core.simulate_fill("live", "x")["ok"])
        self.assertFalse(core.simulate_expiry("live", "HOOD", 85, "2026-12-24", "assigned")["ok"])


class Loop(Base):
    def test_goals_notes_and_suggestions(self):
        core.set_goals("staging", "Income over growth. No drop over 25%.")
        self.assertIn("Income over growth", core.goals("staging"))
        self.assertTrue(core.add_note("staging", "Week 1: one call sold, nothing called away.")["ok"])
        self.assertEqual(len(core.notes("staging")), 1)
        bad = core.add_suggestion("staging", "Sell below cost", "more premium", {"rules": {"CALL_FLOOR": "none"}})
        self.assertFalse(bad["ok"])                                         # locked rules cannot even be proposed
        self.assertFalse(core.add_suggestion("staging", "Short calls", "x", {"rules": {"CALL_DTE_TARGET": 7}})["ok"])
        ok = core.add_suggestion("staging", "Aim for 0.25 delta", "called away twice in a month", {"rules": {"CALL_DELTA_TARGET": 0.25}})
        self.assertTrue(ok["ok"])
        st = self.st()["loop"]
        self.assertEqual((len(st["suggestions"]), st["suggestions"][0]["status"]), (1, "open"))

    def test_assistant_cannot_adopt_unless_switched_on_and_owner_can(self):
        sid = core.add_suggestion("staging", "Keep two lots free", "more upside", {"uncovered_lots": {"HOOD": 2}})["id"]
        r = core.suggestion_decide("staging", sid, "adopt", source="assistant")
        self.assertFalse(r["ok"])
        self.assertEqual(self.st()["engine"]["proposals"][0]["contracts"], 2)       # unchanged
        core.suggestion_tested("staging", sid, "On 891 cases: +1.1 points, drop 1 point deeper.")
        self.assertTrue(core.suggestion_decide("staging", sid, "adopt", "matches my goals")["ok"])
        self.assertEqual(self.st()["engine"]["proposals"][0]["contracts"], 1)       # applied
        s = self.st()["loop"]["suggestions"][0]
        self.assertEqual((s["status"], s["decided_by"]), ("adopted", "you"))
        self.assertIn("891 cases", s["test_result"])
        self.assertFalse(core.suggestion_decide("staging", sid, "reject")["ok"])   # already decided

    def test_assistant_can_tune_only_inside_bounds_when_switched_on(self):
        old = core.AGENT_CAN_TUNE
        core.AGENT_CAN_TUNE = True
        try:
            sid = core.add_suggestion("staging", "Keep two lots free", "more upside", {"uncovered_lots": {"HOOD": 2}})["id"]
            self.assertTrue(core.suggestion_decide("staging", sid, "adopt", source="assistant")["ok"])
            self.assertEqual(self.events("settings")[-1]["after"]["uncovered_lots"], {"HOOD": 2})
            note_only = core.add_suggestion("staging", "Watch earnings week", "calls shortened twice")["id"]
            self.assertFalse(core.suggestion_decide("staging", note_only, "adopt", source="assistant")["ok"])
        finally:
            core.AGENT_CAN_TUNE = old

    def test_open_suggestions_are_capped(self):
        for i in range(3):
            self.assertTrue(core.add_suggestion("staging", f"idea {i}", "why")["ok"])
        self.assertFalse(core.add_suggestion("staging", "idea 4", "why")["ok"])


class Rotation(Base):
    scenario = "after_call_away"

    def test_rotation_buy_then_fill(self):
        s = self.st()
        rot = [p for p in s["engine"]["proposals"] if p["kind"] == "rotation stock"]
        self.assertEqual(len(rot), 1)
        p = rot[0]
        self.assertLessEqual(p["cost"], s["portfolio"]["cash"])
        self.assertTrue(any("BETA" in f for f in p["flags"]))
        oid = core.decide("staging", p["id"], "approve")["order"]["order_id"]
        self.assertTrue(core.simulate_fill("staging", oid)["ok"])
        s = self.st()
        self.assertIn(p["symbol"], [x["symbol"] for x in s["portfolio"]["positions"]])
        self.assertTrue(any(x["symbol"] == p["symbol"] and "no option chain" in x["reason"] for x in s["engine"]["skipped"]))

    def test_rule_switch_changes_the_pick(self):
        a = next(p for p in self.st()["engine"]["proposals"] if p["kind"] == "rotation stock")["symbol"]
        core.change_settings("staging", {"rules": {"ROT_RULE": "quality"}})
        b = next(p for p in self.st()["engine"]["proposals"] if p["kind"] == "rotation stock")["symbol"]
        self.assertNotEqual(a, b)
        core.change_settings("staging", {"rules": {"ROT_RULE": "off"}})
        self.assertFalse([p for p in self.st()["engine"]["proposals"] if p["kind"] == "rotation stock"])


class Roll(Base):
    scenario = "roll_due"

    def test_roll_two_legs_and_assignment(self):
        s = self.st()
        p = next(x for x in s["engine"]["proposals"] if x["kind"] == "roll up and out")
        self.assertEqual(p["strike"], 90.0)
        self.assertEqual(len(p["legs_to_send"]), 2)
        oid = core.decide("staging", p["id"], "approve")["order"]["order_id"]
        self.assertTrue(core.simulate_fill("staging", oid)["ok"])
        s = self.st()
        self.assertEqual([(o["strike"], o["expiry"]) for o in s["portfolio"]["short_options"]], [(90.0, "2026-12-24")])
        self.assertEqual(len(self.events("fill")), 2)
        self.assertTrue(core.simulate_expiry("staging", "HOOD", 90.0, "2026-12-24", "assigned")["ok"])
        s = self.st()
        hood = next(x for x in s["portfolio"]["positions"] if x["symbol"] == "HOOD")
        self.assertAlmostEqual(hood["shares"], 100.0, places=4)
        self.assertEqual(s["scoreboard"]["call_aways"], 1)
        self.assertGreater(s["portfolio"]["cash"], 18000)


if __name__ == "__main__":
    unittest.main()
