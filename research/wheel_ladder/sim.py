"""
sim.py - tier-ladder wheel on real option bars (rules in spec.py).

  python3 sim.py                 # ladder at $0 / $100 / $250 a month + SPY + V1
  python3 sim.py --monkeys 200   # + random-pick control at $100 a month
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "options_farm"))

import calibrate  # noqa: E402
import market as M  # noqa: E402
import odata as D  # noqa: E402
from farm_universe import FARM_UNIVERSE  # noqa: E402

import spec as S  # noqa: E402

CAL = M.CAL
HC = calibrate.Haircut()
FEE = M.FEE
I0 = CAL.last_le(S.SIM_START)
I1 = CAL.idx(S.SIM_END)
NAMES = [u for u in FARM_UNIVERSE if u not in S.EXCLUDE]


def _earnings():
    e = pd.read_parquet(D.CACHE / "earnings_events.parquet")
    out = {}
    for t, g in e.groupby("ticker"):
        out[t] = np.array(sorted({CAL.last_le(d) for d in pd.to_datetime(g.entry_date)}))
    return out


EARN = _earnings()


def earnings_clash(u: str, t_i: int, exp_i: int) -> bool:
    if u in S.ETFS:
        return False
    e = EARN.get(u)
    if e is None:
        return True
    k = np.searchsorted(e, t_i)
    return k < len(e) and e[k] <= exp_i


def tier_row(t):
    return S.TIERS[t]


class Run:
    def __init__(self, deposit: float, seed=None):
        self.dep = deposit
        self.rng = random.Random(seed) if seed is not None else None
        self.cash = S.START_EQUITY
        self.tier = 0
        self.above = 0
        self.wheels = {}        # u -> {"shares": int, "basis": float, "opt": pos|None, "cycle": float}
        self.pending = []       # orders to fill at the next open
        self.taps = 0
        self.assign = 0
        self.called = 0
        self.cycles = []        # finished wheel cycle P/L
        self.curve = []         # (session idx, equity, deposit that day)
        self.tier_log = [(CAL.sessions[I0].date().isoformat(), 0)]

    # -------------------------------------------------------------- helpers
    def collateral(self):
        c = 0.0
        for w in self.wheels.values():
            o = w["opt"]
            if o and o["kind"] == "P":
                c += o["K"] * 100 * o["n"]
        for od in self.pending:
            if od["type"] == "open" and od["kind"] == "P":
                c += od["K"] * 100 * od["n"]
        return c

    def equity(self, i):
        e = self.cash
        for u, w in self.wheels.items():
            un = M.U(u)
            px = un.raw_close[i]
            if not np.isfinite(px):
                px = un.raw_close[:i + 1][np.isfinite(un.raw_close[:i + 1])][-1]
            e += w["shares"] * px
            o = w["opt"]
            if o:
                e -= un.mark(o["sid"], i) * 100 * o["n"]
        return e

    # -------------------------------------------------------------- fills at the open
    def fill_pending(self, i):
        keep = []
        for od in self.pending:
            un = M.U(od["u"])
            sid = od["sid"]
            r = un.bar_at(sid, i)
            exp_i = int(un.exp_sidx[sid])
            if r is None:
                if od["type"] == "open":
                    if od.get("slipped") or i + 1 >= exp_i:
                        if not self.wheels[od["u"]]["shares"] and not self.wheels[od["u"]]["opt"]:
                            del self.wheels[od["u"]]
                        continue
                    od["slipped"] = True
                    keep.append(od)
                else:
                    if i < exp_i:
                        keep.append(od)          # keep trying to buy back until expiry
                continue
            o = float(un.o[r])
            dte = max((un.expiry[sid] - CAL.sessions[i]).days, 0)
            h = HC(o, un.mny(sid, i), dte)
            w = self.wheels[od["u"]]
            if od["type"] == "open":
                px = o * (1 - h)
                self.cash += px * 100 * od["n"] - FEE * od["n"]
                w["cycle"] += px * 100 * od["n"] - FEE * od["n"]
                w["opt"] = {"sid": sid, "kind": od["kind"], "K": od["K"], "n": od["n"], "credit": px,
                            "exp_i": exp_i, "closing": False}
            else:
                px = o * (1 + h)
                n = w["opt"]["n"]
                self.cash -= px * 100 * n + FEE * n
                w["cycle"] -= px * 100 * n + FEE * n
                w["opt"] = None
                if not w["shares"]:
                    self.cycles.append(w["cycle"])
                    del self.wheels[od["u"]]
            self.taps += 1
        self.pending = keep

    # -------------------------------------------------------------- expiries at the close
    def expire(self, i):
        for u in list(self.wheels):
            w = self.wheels[u]
            o = w["opt"]
            if not o or o["exp_i"] != i:
                continue
            un = M.U(u)
            Sx = un.raw_close[i]
            self.pending = [p for p in self.pending if not (p["u"] == u and p["type"] == "close")]
            if o["kind"] == "P" and Sx < o["K"]:
                cost = o["K"] * 100 * o["n"]
                self.cash -= cost
                w["cycle"] -= cost
                w["shares"] += 100 * o["n"]
                w["basis"] = o["K"]
                self.assign += 1
            elif o["kind"] == "C" and Sx > o["K"]:
                got = o["K"] * 100 * o["n"]
                self.cash += got
                w["cycle"] += got
                w["shares"] -= 100 * o["n"]
                self.called += 1
            w["opt"] = None
            if not w["shares"]:
                self.cycles.append(w["cycle"])
                del self.wheels[u]

    # -------------------------------------------------------------- tiers
    def update_tier(self, i, eq):
        t = self.tier
        if t + 1 < len(S.TIERS) and eq >= S.TIERS[t + 1][1]:
            self.above += 1
            if self.above >= S.PROMOTE_DAYS:
                self.tier += 1
                self.above = 0
                self.tier_log.append((CAL.sessions[i].date().isoformat(), self.tier))
        else:
            self.above = 0
            if t > 0 and eq < S.TIERS[t][1] * S.DEMOTE_CUSHION:
                self.tier -= 1
                self.tier_log.append((CAL.sessions[i].date().isoformat(), self.tier))

    # -------------------------------------------------------------- decisions at the close
    def manage(self, i):
        for u, w in self.wheels.items():
            o = w["opt"]
            if not o or o["closing"] or o["exp_i"] <= i:
                continue
            un = M.U(u)
            m = un.mark(o["sid"], i)
            dte = (un.expiry[o["sid"]] - CAL.sessions[i]).days
            Sx = un.raw_close[i]
            otm = (Sx > o["K"]) if o["kind"] == "P" else (Sx < o["K"])
            if m <= S.TAKE_PROFIT * o["credit"] or (dte <= S.MANAGE_DTE and otm):
                o["closing"] = True
                self.pending.append({"type": "close", "u": u, "sid": o["sid"]})

    def _valid(self, un, c, i, basis=None):
        r = un.bar_at(c["sid"], i)
        if r is None or un.v[r] < S.MIN_BAR_VOLUME or c["est_px"] < S.MIN_PREMIUM:
            return False
        if basis is not None and c["strike"] < basis:
            return False
        return True

    def covered_calls(self, i):
        for u, w in self.wheels.items():
            if not w["shares"] or w["opt"] or any(p["u"] == u for p in self.pending):
                continue
            un = M.U(u)
            pick = None
            for dte in (S.DTE_TARGET, 21, 14, 7):
                E = un.choose_expiry(i, i + 1, dte)
                if E is None or earnings_clash(u, i + 1, CAL.last_le(E)):
                    continue
                cs = [c for c in un.candidates(i, i + 1, True, dte, S.DELTA_TARGET)
                      if self._valid(un, c, i, w["basis"])]
                if not cs:
                    mem = un.exp_members[E]
                    mem = mem[un.is_call[mem] & (un.strike[mem] >= w["basis"])]
                    for sid in mem[np.argsort(un.strike[mem])]:
                        r = un.bar_at(int(sid), i)
                        if r is not None and un.c[r] >= S.MIN_PREMIUM:
                            cs = [{"sid": int(sid), "strike": float(un.strike[sid]), "expiry": E,
                                   "est_px": float(un.c[r])}]
                            break
                if cs:
                    pick = cs[0] if self.rng is None else self.rng.choice(cs)
                    break
            if pick:
                self.pending.append({"type": "open", "u": u, "sid": pick["sid"], "kind": "C",
                                     "K": pick["strike"], "n": w["shares"] // 100})

    def new_wheels(self, i, eq):
        _, _, _, (lo, hi), slots, cap = S.TIERS[self.tier]
        active = len(self.wheels)
        if active >= slots:
            return
        free = self.cash - self.collateral()
        for _ in range(slots - active):
            budget = free / (slots - len(self.wheels))
            options = []
            for u in NAMES:
                if u in self.wheels or (u in S.ETFS and self.tier < 3):
                    continue
                un = M.U(u)
                if not un.ok:
                    continue
                px = un.raw_close[i]
                if not (np.isfinite(px) and lo <= px <= hi):
                    continue
                cs = []
                for c in un.candidates(i, i + 1, False, S.DTE_TARGET, S.DELTA_TARGET):
                    if not self._valid(un, c, i) or c["strike"] * 100 > budget:
                        continue
                    if earnings_clash(u, i + 1, CAL.last_le(c["expiry"])):
                        continue
                    cs.append(c)
                if not cs:
                    continue
                if self.rng is not None:
                    options.append((0.0, u, self.rng.choice(cs)))
                    continue
                c = cs[0]
                h = HC(c["est_px"], un.mny(c["sid"], i), c["dte"])
                net = c["est_px"] * (1 - h) - FEE / 100
                options.append((net / c["strike"] * 365 / max(c["dte"], 1), u, c))
            if not options:
                return
            if self.rng is not None:
                _, u, c = self.rng.choice(options)
            else:
                _, u, c = max(options, key=lambda x: x[0])
            n = int(min(cap, budget // (c["strike"] * 100)))
            if n < 1:
                return
            self.wheels[u] = {"shares": 0, "basis": 0.0, "opt": None, "cycle": 0.0}
            self.pending.append({"type": "open", "u": u, "sid": c["sid"], "kind": "P",
                                 "K": c["strike"], "n": n})
            free -= c["strike"] * 100 * n

    # -------------------------------------------------------------- main loop
    def go(self):
        month = CAL.sessions[I0].month
        for i in range(I0, I1 + 1):
            self.fill_pending(i)
            self.expire(i)
            dep = 0.0
            if CAL.sessions[i].month != month:
                month = CAL.sessions[i].month
                dep = self.dep
                self.cash += dep
            eq = self.equity(i)
            self.curve.append((i, eq, dep))
            self.update_tier(i, eq)
            if i < I1:
                self.manage(i)
                self.covered_calls(i)
                self.new_wheels(i, eq)
        return self


# ------------------------------------------------------------------ metrics
def metrics(curve):
    """curve: list of (session idx, equity, deposit added that day)."""
    eq = np.array([c[1] for c in curve])
    dep = np.array([c[2] for c in curve])
    r = (eq[1:] - dep[1:]) / eq[:-1]
    idx = np.concatenate([[1.0], np.cumprod(r)])
    yrs = (CAL.sessions[curve[-1][0]] - CAL.sessions[curve[0][0]]).days / 365.25
    cagr = idx[-1] ** (1 / yrs) - 1
    dd = (idx / np.maximum.accumulate(idx) - 1).min()
    return {"final": round(float(eq[-1]), 2), "deposited": round(float(S.START_EQUITY + dep.sum()), 2),
            "twr_cagr": round(float(cagr), 4), "max_dd": round(float(dd), 4)}


def control_curves(deposit):
    import yfinance as yf
    px = yf.download(["SPY", "UPRO", "SGOV"], start="2023-10-01", end="2026-10-10", auto_adjust=True,
                     progress=False)["Close"]
    sess = CAL.sessions[I0:I1 + 1]
    px = px.reindex(sess.union(px.index)).ffill()
    spy_r = px["SPY"].pct_change()
    vol = spy_r.rolling(20).std() * math.sqrt(252)
    p = px.reindex(sess)
    month = sess[0].month
    # SPY buy and hold
    sh = S.START_EQUITY / p["SPY"].iloc[0]
    spy = []
    # V1
    w_up = min(1.0, 0.45 / (3 * vol.loc[sess[0]]))
    up = S.START_EQUITY * w_up / p["UPRO"].iloc[0]
    sg = S.START_EQUITY * (1 - w_up) / p["SGOV"].iloc[0]
    v1 = []
    for k, d in enumerate(sess):
        dep = 0.0
        if d.month != month:
            month = d.month
            dep = deposit
        sh += dep / p["SPY"].loc[d]
        spy.append((CAL.idx(d), sh * p["SPY"].loc[d], dep))
        tgt = min(1.0, 0.45 / (3 * vol.loc[d]))
        E = up * p["UPRO"].loc[d] + sg * p["SGOV"].loc[d] + dep
        cw = up * p["UPRO"].loc[d] / E
        if abs(cw - tgt) > 0.10 or dep > 0:
            if abs(cw - tgt) > 0.10:
                up, sg = E * tgt / p["UPRO"].loc[d], E * (1 - tgt) / p["SGOV"].loc[d]
            else:
                up += dep * tgt / p["UPRO"].loc[d]
                sg += dep * (1 - tgt) / p["SGOV"].loc[d]
        v1.append((CAL.idx(d), up * p["UPRO"].loc[d] + sg * p["SGOV"].loc[d], dep))
    return metrics(spy), metrics(v1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--monkeys", type=int, default=0)
    a = ap.parse_args()
    out = {"window": [S.SIM_START, S.SIM_END], "names": NAMES, "runs": {}}
    t0 = time.time()
    for dep in S.DEPOSITS:
        r = Run(dep).go()
        m = metrics(r.curve)
        m.update({"tiers": r.tier_log, "approval_taps": r.taps,
                  "taps_per_month": round(r.taps / ((I1 - I0) / 21), 1),
                  "assignments": r.assign, "called_away": r.called,
                  "cycles": len(r.cycles), "worst_cycle": round(min(r.cycles), 2) if r.cycles else None,
                  "best_cycle": round(max(r.cycles), 2) if r.cycles else None,
                  "open_at_end": {u: {"shares": w["shares"], "opt": bool(w["opt"])} for u, w in r.wheels.items()}})
        spy, v1 = control_curves(dep)
        out["runs"][f"${dep:.0f}/mo"] = {"ladder": m, "spy": spy, "v1": v1}
        print(f"${dep:.0f}/mo  ladder {m}\n          spy {spy}\n          v1  {v1}  ({time.time() - t0:.0f}s)",
              flush=True)
    if a.monkeys:
        ms = []
        for s in range(a.monkeys):
            ms.append(metrics(Run(100.0, seed=s).go().curve)["twr_cagr"])
            if (s + 1) % 20 == 0:
                print(f"  monkeys {s + 1}/{a.monkeys} ({time.time() - t0:.0f}s)", flush=True)
        ms = np.array(ms)
        lad = out["runs"]["$100/mo"]["ladder"]["twr_cagr"]
        out["monkeys_100"] = {"n": len(ms), "p50": round(float(np.percentile(ms, 50)), 4),
                              "p90": round(float(np.percentile(ms, 90)), 4),
                              "ladder_pct": round(float((ms < lad).mean() * 100), 1)}
        print("monkeys", out["monkeys_100"])
        spy = out["runs"]["$100/mo"]["spy"]["twr_cagr"]
        out["verdict"] = "PASS" if (lad > spy and lad >= out["monkeys_100"]["p90"]) else "FAIL"
        print("VERDICT", out["verdict"])
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "ladder.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
