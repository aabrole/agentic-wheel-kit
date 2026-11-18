"""
sim3.py - wheel v3, the guide's wheel (rules in spec3.py), on the same engine as sim.py.

  python3 sim3.py                # 2 caps x 2 cash rates + SPY + HOOD arm
  python3 sim3.py --monkeys 100  # + random-pick control (10% cap, 0% cash)
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

import sim
import spec3 as S3
from sim import CAL, FEE, HC, M, earnings_clash
from farm_universe import FARM_UNIVERSE

I0 = CAL.last_le(S3.SIM_START)
I1 = CAL.idx(S3.SIM_END)
NAMES = [u for u in FARM_UNIVERSE if u not in S3.EXCLUDE]

_Q = {}


def quality(u, i):
    """(passes, realized vol). Uses split-adjusted closes up to and including session i."""
    if u not in _Q:
        c = M.U(u).adj.close
        lr = np.log(c).diff()
        _Q[u] = (c.to_numpy(), c.rolling(S3.SMA_N, min_periods=S3.SMA_N).mean().to_numpy(),
                 (lr.rolling(252, min_periods=252).std() * np.sqrt(252)).to_numpy())
    c, s, v = _Q[u]
    px = M.U(u).raw_close[i]
    ok = (np.isfinite(px) and px >= S3.MIN_PRICE and np.isfinite(s[i]) and c[i] > s[i]
          and np.isfinite(v[i]) and v[i] <= S3.MAX_REALIZED_VOL)
    return bool(ok), float(v[i]) if np.isfinite(v[i]) else 9.9


class Run3(sim.Run):
    def __init__(self, cap, rate, seed=None, hood=False):
        super().__init__(0.0, seed)
        self.cap, self.rate, self.hood = cap, rate, hood
        self.cash = S3.START_CASH
        self.rolls = 0
        self.premium = 0.0          # gross credits from every option sold
        self.debits = 0.0           # paid to buy options back
        if hood:
            px = float(M.U("HOOD").raw_close[I0])
            self.cash = 0.0
            self.start_value = S3.HOOD_SHARES * px
            self.wheels["HOOD"] = {"shares": S3.HOOD_SHARES, "basis": px, "opt": None,
                                   "cycle": -S3.HOOD_SHARES * px}
        else:
            self.start_value = S3.START_CASH

    def stock_at_basis(self):
        return sum(w["shares"] * w["basis"] for w in self.wheels.values())

    # ---------------------------------------------------------- fills at the open
    def fill_pending(self, i):
        rolls = [p for p in self.pending if p["type"] == "roll"]
        self.pending = [p for p in self.pending if p["type"] != "roll"]
        before = {u: (w["opt"]["sid"] if w["opt"] else None) for u, w in self.wheels.items()}
        cash0 = self.cash
        super().fill_pending(i)
        for u, w in self.wheels.items():                       # premium bookkeeping
            o = w["opt"]
            if o and before.get(u) != o["sid"]:
                self.premium += o["credit"] * 100 * o["n"]
        for od in rolls:
            u = od["u"]
            w = self.wheels.get(u)
            if w is None or w["opt"] is None or w["opt"]["sid"] != od["old"]:
                continue
            o = w["opt"]
            un = M.U(u)
            r_old, r_new = un.bar_at(od["old"], i), un.bar_at(od["new"], i)
            if r_old is None or r_new is None:
                if i + 1 < o["exp_i"]:
                    self.pending.append(od)
                else:
                    o["closing"] = False
                continue
            po, pn = float(un.o[r_old]), float(un.o[r_new])
            d_old = max((un.expiry[od["old"]] - CAL.sessions[i]).days, 0)
            d_new = max((un.expiry[od["new"]] - CAL.sessions[i]).days, 0)
            buy = po * (1 + HC(po, un.mny(od["old"], i), d_old))
            sell = pn * (1 - HC(pn, un.mny(od["new"], i), d_new))
            n = o["n"]
            flow = (sell - buy) * 100 * n - 2 * FEE * n
            self.cash += flow
            w["cycle"] += flow
            self.premium += sell * 100 * n
            self.debits += buy * 100 * n
            w["opt"] = {"sid": od["new"], "kind": "P", "K": od["K"], "n": n, "credit": sell,
                        "exp_i": int(un.exp_sidx[od["new"]]), "closing": False}
            self.taps += 2
            self.rolls += 1

    # ---------------------------------------------------------- decisions at the close
    def _roll_target(self, u, o, i):
        un = M.U(u)
        E = un.choose_expiry(i, i + 1, S3.DTE_TARGET,
                             min_expiry=un.expiry[o["sid"]] + pd.Timedelta(days=1))
        if E is None or E <= un.expiry[o["sid"]] or earnings_clash(u, i + 1, CAL.last_le(E)):
            return None
        m = un.mark(o["sid"], i)
        d_old = max((un.expiry[o["sid"]] - CAL.sessions[i]).days, 0)
        cost = m * (1 + HC(m, un.mny(o["sid"], i), d_old))
        d_new = int((E - CAL.sessions[i + 1]).days)
        mem = un.exp_members[E]
        mem = mem[(~un.is_call[mem]) & (un.strike[mem] <= o["K"])]
        for sid in mem[np.argsort(un.strike[mem])]:            # lowest strike first
            sid = int(sid)
            r = un.bar_at(sid, i)
            if r is None or un.v[r] < S3.MIN_BAR_VOLUME:
                continue
            est = float(un.c[r])
            net = est * (1 - HC(est, un.mny(sid, i), d_new)) - cost - 2 * FEE / 100
            if net > 0:
                return {"type": "roll", "u": u, "old": o["sid"], "new": sid,
                        "K": float(un.strike[sid])}
        return None

    def manage(self, i):
        for u, w in self.wheels.items():
            o = w["opt"]
            if not o or o["closing"] or o["exp_i"] <= i or o["kind"] != "P":
                continue
            un = M.U(u)
            m = un.mark(o["sid"], i)
            if m <= S3.TAKE_PROFIT * o["credit"]:
                o["closing"] = True
                self.pending.append({"type": "close", "u": u, "sid": o["sid"]})
                continue
            dte = (un.expiry[o["sid"]] - CAL.sessions[i]).days
            if un.raw_close[i] < o["K"] and dte <= S3.ROLL_DTE:
                od = self._roll_target(u, o, i)
                if od:
                    o["closing"] = True
                    self.pending.append(od)

    def covered_calls(self, i):
        for u, w in self.wheels.items():
            if not w["shares"] or w["opt"] or any(p["u"] == u for p in self.pending):
                continue
            un = M.U(u)
            floor = -w["cycle"] / w["shares"]                  # net stock cost per share
            E = un.choose_expiry(i, i + 1, S3.CC_DTE)
            if E is None or earnings_clash(u, i + 1, CAL.last_le(E)):
                continue
            cs = []
            for c in un.candidates(i, i + 1, True, S3.CC_DTE, S3.CC_DELTA):
                r = un.bar_at(c["sid"], i)
                if (r is not None and un.v[r] >= S3.MIN_BAR_VOLUME and c["est_px"] >= S3.MIN_PREMIUM
                        and c["strike"] >= floor):
                    cs.append(c)
            if not cs:
                mem = un.exp_members[E]
                mem = mem[un.is_call[mem] & (un.strike[mem] >= floor)]
                for sid in mem[np.argsort(un.strike[mem])]:
                    r = un.bar_at(int(sid), i)
                    if r is not None and un.v[r] >= S3.MIN_BAR_VOLUME and un.c[r] >= S3.MIN_PREMIUM:
                        cs = [{"sid": int(sid), "strike": float(un.strike[sid])}]
                        break
            if cs:
                pick = cs[0] if self.rng is None else self.rng.choice(cs)
                n = self._cc_n(u, w)
                if n >= 1:
                    self.pending.append({"type": "open", "u": u, "sid": pick["sid"], "kind": "C",
                                         "K": pick["strike"], "n": n})

    def _cc_n(self, u, w):
        return w["shares"] // 100

    def _puts(self, u, i, room):
        un = M.U(u)
        out = []
        for c in un.candidates(i, i + 1, False, S3.DTE_TARGET, S3.DELTA_TARGET):
            r = un.bar_at(c["sid"], i)
            if r is None or un.v[r] < S3.MIN_BAR_VOLUME or c["est_px"] < S3.MIN_PREMIUM:
                continue
            if c["strike"] * 100 > room or earnings_clash(u, i + 1, CAL.last_le(c["expiry"])):
                continue
            out.append(c)
        return out

    def new_wheels(self, i, eq):
        if self.hood:
            if "HOOD" in self.wheels or not M.U("HOOD").ok:
                return
            free = self.cash - self.collateral()
            cs = self._puts("HOOD", i, free)
            if not cs:
                return
            c = cs[0]
            n = int(min(S3.HOOD_SHARES // 100, free // (c["strike"] * 100)))
            if n >= 1:
                self.wheels["HOOD"] = {"shares": 0, "basis": 0.0, "opt": None, "cycle": 0.0}
                self.pending.append({"type": "open", "u": "HOOD", "sid": c["sid"], "kind": "P",
                                     "K": c["strike"], "n": n})
            return
        while True:
            coll = self.collateral()
            free = self.cash - coll
            per = self.cap * eq
            room = min(free, S3.DEPLOY_MAX * eq - coll - self.stock_at_basis(), per)
            if room <= 0:
                return
            options = []
            for u in NAMES:
                if u in self.wheels:
                    continue
                un = M.U(u)
                if not un.ok or not np.isfinite(un.raw_close[i]):
                    continue
                if self.rng is None:
                    ok, vol = quality(u, i)
                    if not ok:
                        continue
                else:
                    vol = 0.0
                cs = self._puts(u, i, room)
                if not cs:
                    continue
                options.append((vol, u, cs[0] if self.rng is None else self.rng.choice(cs)))
            if not options:
                return
            _, u, c = min(options, key=lambda x: (x[0], x[1])) if self.rng is None \
                else self.rng.choice(options)
            n = int(room // (c["strike"] * 100))
            if n < 1:
                return
            self.wheels[u] = {"shares": 0, "basis": 0.0, "opt": None, "cycle": 0.0}
            self.pending.append({"type": "open", "u": u, "sid": c["sid"], "kind": "P",
                                 "K": c["strike"], "n": n})

    # ---------------------------------------------------------- main loop
    def go(self):
        self.held = {}
        for i in range(getattr(self, "i_start", I0 - 1) + 1, I1 + 1):
            self.fill_pending(i)
            self.expire(i)
            if self.rate and self.cash > 0:
                self.cash += self.cash * self.rate / 252
            eq = self.equity(i)
            self.curve.append((i, eq))
            for u in self.wheels:
                self.held[u] = self.held.get(u, 0) + 1
            if i < I1:
                self.manage(i)
                self.covered_calls(i)
                self.new_wheels(i, eq)
        return self


def stats(eq, i_first=I0, i_last=I1):
    eq = np.asarray(eq, float)
    yrs = (CAL.sessions[i_last] - CAL.sessions[i_first]).days / 365.25
    return {"final": round(float(eq[-1]), 2), "cagr": round(float((eq[-1] / eq[0]) ** (1 / yrs) - 1), 4),
            "max_dd": round(float((eq / np.maximum.accumulate(eq) - 1).min()), 4)}


def summarize(r):
    eq = [r.start_value] + [e for _, e in r.curve]
    m = stats(eq)
    m.update({"profit": round(m["final"] - r.start_value, 2),
              "premium_collected": round(r.premium, 2),
              "assignments": r.assign, "called_away": r.called, "rolls": r.rolls,
              "orders": r.taps, "cycles": len(r.cycles),
              "worst_cycle": round(min(r.cycles), 2) if r.cycles else None,
              "names_traded": sorted(r.held, key=lambda u: -r.held[u]),
              "open_at_end": {u: {"shares": w["shares"], "basis": round(w["basis"], 2),
                                  "last": round(float(M.U(u).raw_close[I1]), 2)}
                              for u, w in r.wheels.items() if w["shares"]}})
    return m


def spy_control():
    import yfinance as yf
    px = yf.download("SPY", start="2023-12-01", end="2026-10-10", auto_adjust=True, progress=False)["Close"]
    px = px.squeeze().reindex(CAL.sessions[I0:I1 + 1]).ffill()
    return stats(S3.START_CASH * px / px.iloc[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--monkeys", type=int, default=0)
    a = ap.parse_args()
    t0 = time.time()
    out = {"window": [S3.SIM_START, S3.SIM_END], "universe": NAMES, "runs": {}}
    out["spy"] = spy_control()
    print("SPY", out["spy"], flush=True)
    for cap in S3.NAME_CAPS:
        for rate in S3.CASH_RATES:
            m = summarize(Run3(cap, rate).go())
            m["Q1_ge_10pct"] = m["cagr"] >= 0.10
            m["Q2_beats_spy"] = m["cagr"] > out["spy"]["cagr"]
            m["Q3_smaller_dd"] = m["max_dd"] > out["spy"]["max_dd"]
            out["runs"][f"cap{int(cap * 100)}_cash{int(rate * 100)}"] = m
            print(f"cap {cap:.0%} cash {rate:.0%}: {m}  ({time.time() - t0:.0f}s)", flush=True)
    h = Run3(1.0, 0.0, hood=True).go()
    hm = summarize(h)
    un = M.U("HOOD")
    hold = stats(S3.HOOD_SHARES * un.adj.close.to_numpy()[I0:I1 + 1])
    out["hood"] = {"covered_calls": hm, "hold_300_shares": hold}
    print("HOOD cc  ", hm, "\nHOOD hold", hold, flush=True)
    if a.monkeys:
        ms = []
        for s in range(a.monkeys):
            r = Run3(0.10, 0.0, seed=s).go()
            ms.append(stats([r.start_value] + [e for _, e in r.curve])["cagr"])
            if (s + 1) % 20 == 0:
                print(f"  monkeys {s + 1}/{a.monkeys} ({time.time() - t0:.0f}s)", flush=True)
        ms = np.array(ms)
        mine = out["runs"]["cap10_cash0"]["cagr"]
        out["monkeys"] = {"n": len(ms), "p10": round(float(np.percentile(ms, 10)), 4),
                          "p50": round(float(np.percentile(ms, 50)), 4),
                          "p90": round(float(np.percentile(ms, 90)), 4),
                          "guide_percentile": round(float((ms < mine).mean() * 100), 1),
                          "Q4_at_or_above_p90": bool(mine >= np.percentile(ms, 90))}
        print("monkeys", out["monkeys"], flush=True)
    (sim.HERE / "results" / "wheel_v3.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
