"""
scenarios.py - snapshots the STAGING dashboard can start from. All are made-up accounts built on real option quotes so every button can be exercised. Loading one
archives the current staging state and starts a fresh journal.
"""
import copy
import json
import shutil
import datetime as dt
from pathlib import Path

import core

FIX = core.ROOT / "fixtures"


def _candidates():
    files = sorted(FIX.glob("candidates_*.json"))
    return json.loads(files[-1].read_text()) if files else {"candidates": [], "candidates_as_of": ""}


def _real():
    s = json.loads((FIX / "sample_account.json").read_text())
    c = _candidates()
    s["candidates"] = [{k: r.get(k) for k in ("symbol", "last", "sma200", "ret_252", "vol_252", "atm_iv")} for r in c["candidates"]]
    s["candidates_as_of"] = c["candidates_as_of"]
    return s


def real():
    s = _real()
    s["_note"] = ("SAMPLE: a made-up account (300 HOOD, a few MSFT and AAPL shares, $2,000 cash). The HOOD option quotes "
                  f"are real quotes from 2026-11-17 and the rotation candidates are market data from {s['candidates_as_of']}.")
    return s


def after_call_away():
    s = _real()
    for p in s["positions"]:
        if p["symbol"] == "HOOD":
            p["shares"] = round(p["shares"] - 200, 6)
    s["account"]["cash"] = round(s["account"]["cash"] + 200 * 85.0, 2)
    s["_note"] = ("MADE UP: the sample account, as if 200 HOOD shares had been called away at $85. "
                  "Use it to walk through rotation: free cash, a stock purchase, then calls.")
    return s


def roll_due():
    s = _real()
    s["quotes"]["HOOD"] = 88.0
    s["short_options"] = [{"symbol": "HOOD", "type": "call", "strike": 85.0, "expiry": "2026-11-20", "contracts": 2}]
    q = lambda e, k, b, a, d: {"type": "call", "expiry": e, "strike": k, "bid": b, "ask": a, "delta": d,
                               "open_interest": 800, "id": f"made-up-{e}-{k}", "sell_floor_hint": None}
    s["chains"]["HOOD"] = [q("2026-11-20", 85.0, 3.30, 3.50, 0.85), q("2026-12-24", 87.0, 5.20, 5.50, 0.58),
                           q("2026-12-24", 90.0, 3.90, 4.20, 0.47), q("2026-12-24", 95.0, 2.30, 2.50, 0.33),
                           q("2026-12-24", 100.0, 1.30, 1.45, 0.22)]
    s["_note"] = ("MADE UP: HOOD at $88 with two $85 calls three days from expiry, and invented option quotes. "
                  "Use it to walk through a roll up and out.")
    return s


SCENARIOS = {
    "real": ("Sample account", real),
    "after_call_away": ("Made up: 200 HOOD called away at $85", after_call_away),
    "roll_due": ("Made up: HOOD calls in the money, roll due", roll_due),
}


def load(name: str) -> dict:
    if name not in SCENARIOS:
        return {"ok": False, "error": f"No scenario called {name}."}
    d = core.state_dir("staging")
    old = [p for p in d.iterdir() if p.is_file()]
    if old:
        arch = d / "archive" / dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        arch.mkdir(parents=True, exist_ok=True)
        for p in old:
            shutil.move(str(p), arch / p.name)
    snap = SCENARIOS[name][1]()
    (d / "snapshot.json").write_text(json.dumps(snap, indent=1))
    (d / "base.json").write_text(json.dumps(copy.deepcopy(snap), indent=1))
    core.log("staging", {"event": "scenario", "name": SCENARIOS[name][0], "note": snap["_note"]})
    core.record_run("staging", "scenario loaded")
    return {"ok": True}
