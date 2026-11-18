"""
sim7.py - the full wheel (puts, assignment, calls) from cash on the EXPANDED universe, in the
style of the r/Optionswheel "Week 46: $537 in premium" post the owner shared. Rules written
2026-11-18 before the new names' data was on disk.

FROZEN RULES (spec3's engine and rules, unchanged: 35 DTE 0.30 delta puts, 50% take-profit,
roll down and out for a credit, 8 DTE 0.30 delta calls at or above net cost after assignment,
puts plus assigned stock <= 50% of equity, same fills, haircuts and fees)
  $50,000 cash, 2024-01-22 .. 2026-10-09, names = universe2 names with option bars.
  Two ways of picking names, each at two position sizes:
    PREMIUM  highest annualized net put premium first, no filter. This is the poster's style:
             the 5% size ($2,500 a name) can only hold one contract of a stock under $25,
             which is his table (one contract each, small volatile names).
    QUALITY  spec3's filter (price >= $10, realized vol <= 40%, above the 200-day average),
             calmest first.
    Sizes: 5% and 10% of equity per name. Cash earns 0%.
  Compared with $50,000 in SPY.
  NOT modelled: the poster rolls calls DOWN below cost when a stock falls (his NVDA rows).
  The guide's rule (never sell a call below net cost) is kept, which is kinder to the result.

HOW IT IS READ (decided now): same four questions as spec3 for each run: at least 10% a year?
beat SPY? smaller worst drop than SPY? and premium collected next to actual profit.
One path over 2.7 years each, so this describes; the 891-style case studies are the evidence.
"""
import json
import sys
import warnings

import numpy as np

import env2
import sim
import sim3
import spec3 as S3
from sim3 import CAL, M, FEE, HC, I0, I1, Run3

sim3.NAMES[:] = sys.argv[1].split(",") if len(sys.argv) > 1 else env2.NAMES2
NAMES = sim3.NAMES


class Run7(Run3):
    def __init__(self, mode, cap):
        super().__init__(cap, 0.0)
        self.mode = mode

    def new_wheels(self, i, eq):
        if self.mode == "QUALITY":
            return super().new_wheels(i, eq)
        while True:
            coll = self.collateral()
            room = min(self.cash - coll, S3.DEPLOY_MAX * eq - coll - self.stock_at_basis(), self.cap * eq)
            if room <= 0:
                return
            options = []
            for u in NAMES:
                if u in self.wheels or not M.U(u).ok or not np.isfinite(M.U(u).raw_close[i]):
                    continue
                cs = self._puts(u, i, room)
                if not cs:
                    continue
                c = cs[0]
                net = c["est_px"] * (1 - HC(c["est_px"], M.U(u).mny(c["sid"], i), c["dte"])) - FEE / 100
                options.append((net / c["strike"] * 365 / max(c["dte"], 1), u, c))
            if not options:
                return
            _, u, c = max(options, key=lambda x: (x[0], x[1]))
            n = int(room // (c["strike"] * 100))
            if n < 1:
                return
            self.wheels[u] = {"shares": 0, "basis": 0.0, "opt": None, "cycle": 0.0}
            self.pending.append({"type": "open", "u": u, "sid": c["sid"], "kind": "P", "K": c["strike"], "n": n})


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    out = {"names": len(NAMES), "spy": sim3.spy_control(), "runs": {}}
    print("SPY", out["spy"], flush=True)
    weeks = (CAL.sessions[I1] - CAL.sessions[I0]).days / 7
    for mode in ("PREMIUM", "QUALITY"):
        for cap in (0.05, 0.10):
            r = Run7(mode, cap).go()
            m = sim3.summarize(r)
            m["premium_per_week"] = round(m["premium_collected"] / weeks)
            m["net_option_cash"] = round(r.premium - r.debits)
            m["profit_per_week"] = round(m["profit"] / weeks)
            m["names_traded_count"] = len(m["names_traded"])
            m["names_traded"] = m["names_traded"][:25]
            m["Q1_ge_10pct"] = m["cagr"] >= 0.10
            m["Q2_beats_spy"] = m["cagr"] > out["spy"]["cagr"]
            m["Q3_smaller_dd"] = m["max_dd"] > out["spy"]["max_dd"]
            out["runs"][f"{mode}_cap{int(cap * 100)}"] = m
            print(mode, cap, {k: m[k] for k in ("final", "cagr", "max_dd", "profit", "premium_collected", "net_option_cash", "assignments", "rolls", "names_traded_count")}, flush=True)
            print("   held at end:", m["open_at_end"], flush=True)
    (sim.HERE / "results" / "wheel_v7.json").write_text(json.dumps(out, indent=1))
