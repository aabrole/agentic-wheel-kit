"""
study_calls.py - which covered-call rule costs a holder the least? Rules FROZEN 2026-11-17,
written before the first run.

Why: the live plan starts from 300 shares of one stock held about 16% below cost. sim4/sim5
tested call rules on HOOD's single path. This asks the same question across every name and
many start dates, so the answer does not hang on one stock's history.

SETUP (same data, fills, half-spreads and fees as spec3: decisions at the close, fills at the
next open less the calibrated half-spread, $0.03 per contract)
  Case      = (name, start). Names: the 33 tradable names of the option cube. Starts: the
              first session of each month, 2024-02 .. 2026-04. Horizon: 126 sessions (26 weeks).
  Holding   = 300 shares bought at the start close. Calls are written on 2 of the 3 lots
              (the owner's stated plan); the third lot is never covered.
  Calls     = left to expire. No call whose life contains an earnings session: when the target
              expiry does, use the longest of 21/14/7 DTE that ends before earnings, else wait.
              A call is sold only if its premium is at least $0.10 and at least 5% a year of the
              share price; otherwise the lot stays uncovered that day. (The 2026-11-17 audit
              found the earlier engine's penny calls far out of the money fill about 30% below
              their quoted close.) An order fills only if the bar traded at least its size.
              "8 DTE" resolves to the weekly expiry nearest 8 days from the fill, usually 11.
  After a call-away the cash sits idle at 0% and no more calls are written (nothing left to
  cover). This isolates the call decision; where the cash goes next is a separate question.

RULES COMPARED (six, fixed)
  C8-floor   8 DTE,  0.30 delta, strike at or above net cost (the r/Optionswheel guide)
  C35-floor  35 DTE, 0.30 delta, strike at or above net cost
  C8-d30     8 DTE,  0.30 delta, cost ignored
  C35-d30    35 DTE, 0.30 delta, cost ignored
  C8-d15     8 DTE,  0.15 delta, cost ignored
  C35-d15    35 DTE, 0.15 delta, cost ignored
  plus HOLD (no calls).
  Net cost = cost basis minus net call premium collected so far, per share. Two cost-basis
  scenarios for the floor rules: AT (basis = start price) and UNDER (basis = start price x
  1.188, a holder sitting about 16% below cost). Rules that ignore cost are identical in
  both; for them UNDER only adds the count of call-aways below cost.

HOW IT IS READ (decided now)
  Primary: mean log return over the 26 weeks across all cases. That is the compounding
  criterion: the rule with the highest mean log return grows fastest when repeated.
  Each rule is compared with HOLD on the same cases; the interval on the difference is a
  bootstrap that resamples whole start dates (cases that share a start move together).
  "Best call rule" = highest mean log return among the six. If every call rule is below HOLD
  with an interval that excludes zero, that is the finding and it is reported as such.
  Secondary, reported for every rule: 10th-percentile return, mean worst drop, net premium as a
  share of starting value, share of cases called away, and (UNDER) called away below cost.
  The window is mostly a bull market, which is the worst case for covered calls; results are
  also split by what the stock itself did (fell / flat / rose) so that is visible.
"""
from __future__ import annotations

import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))        # wheel_ladder/sim.py, not options_farm/sim.py

H = 126
SHARES = 300
UNCOVERED_LOTS = 1
UNDER = 81.83 / 68.88          # 1.188: a holding bought about 16% above the start price
MIN_PREMIUM = 0.10
MIN_YIELD = 0.05               # premium / share price, annualized


def min_prem(spot, days):
    return max(MIN_PREMIUM, MIN_YIELD * spot * max(days, 1) / 365)

RULES = {                       # name: (dte, delta, floor?)
    "C8-floor": (8, 0.30, True),
    "C35-floor": (35, 0.30, True),
    "C8-d30": (8, 0.30, False),
    "C35-d30": (35, 0.30, False),
    "C8-d15": (8, 0.15, False),
    "C35-d15": (35, 0.15, False),
}
STARTS = ("2024-02-01", "2026-04-30")
N_BOOT = 4000


def _env():
    import sim                                   # noqa: E402  (loads calendar, haircut, earnings)
    return sim, sim.M, sim.CAL, sim.HC, sim.FEE


def start_indices(CAL, i_last):
    s = CAL.sessions
    m = pd.Series(np.arange(len(s)), index=s)
    m = m[(s >= STARTS[0]) & (s <= STARTS[1])]
    first = m.groupby([m.index.year, m.index.month]).first().to_numpy()
    return [int(i) for i in first if i + H <= i_last]


def pick_call(sim, M, CAL, un, u, i, dte, delta, floor):
    """Contract to sell at the open of i+1, decided at the close of i. None = wait."""
    for d in [dte] + [x for x in (21, 14, 7) if x < dte]:
        E = un.choose_expiry(i, i + 1, d)
        if E is None:
            continue
        if sim.earnings_clash(u, i + 1, CAL.last_le(E)):
            continue
        cs = []
        need = min_prem(float(un.raw_close[i]), (E - CAL.sessions[i + 1]).days)
        for c in un.candidates(i, i + 1, True, d, delta):
            r = un.bar_at(c["sid"], i)
            if r is None or un.v[r] < 1 or c["est_px"] < need:
                continue
            if floor is not None and c["strike"] < floor:
                continue
            cs.append(c)
        if cs:
            return cs[0]["sid"], float(cs[0]["strike"])
        if floor is not None:                     # guide fallback: lowest tradable strike >= cost
            mem = un.exp_members[E]
            mem = mem[un.is_call[mem] & (un.strike[mem] >= floor)]
            for sid in mem[np.argsort(un.strike[mem])]:
                r = un.bar_at(int(sid), i)
                if r is not None and un.v[r] >= 1 and un.c[r] >= need:
                    return int(sid), float(un.strike[sid])
        return None                               # an eligible expiry existed, nothing to sell
    return None


def simulate(sim, M, CAL, HC, FEE, u, i0, rule, basis_mult):
    un = M.U(u)
    px0 = float(un.raw_close[i0])
    basis = px0 * basis_mult
    shares, cash, prem = SHARES, 0.0, 0.0
    opt = None                                    # {"sid","K","n","exp_i"}
    pend = None                                   # {"sid","K","n","slipped"}
    called, below = 0, 0
    vals = [SHARES * px0]
    weeks = set()
    dte, delta, use_floor = RULES[rule]
    last_px = px0
    for i in range(i0 + 1, i0 + H + 1):
        if pend is not None:                      # fill at the open
            r = un.bar_at(pend["sid"], i)
            exp_i = int(un.exp_sidx[pend["sid"]])
            if r is None or un.v[r] < pend["n"]:
                if pend["slipped"] or i + 1 >= exp_i:
                    pend = None
                else:
                    pend["slipped"] = True
            else:
                o = float(un.o[r])
                d = max((un.expiry[pend["sid"]] - CAL.sessions[i]).days, 0)
                px = o * (1 - HC(o, un.mny(pend["sid"], i), d))
                got = px * 100 * pend["n"] - FEE * pend["n"]
                cash += got
                prem += got
                weeks.add(CAL.sessions[i].strftime("%G-%V"))
                opt = {"sid": pend["sid"], "K": pend["K"], "n": pend["n"], "exp_i": exp_i}
                pend = None
        c = un.raw_close[i]
        if np.isfinite(c):
            last_px = float(c)
        if opt is not None and opt["exp_i"] == i:  # expiry at the close
            if last_px > opt["K"]:
                cash += opt["K"] * 100 * opt["n"]
                shares -= 100 * opt["n"]
                called += 1
                below += int(opt["K"] < basis)
            opt = None
        v = cash + shares * last_px
        if opt is not None:
            v -= un.mark(opt["sid"], i) * 100 * opt["n"]
        vals.append(v)
        if i < i0 + H and opt is None and pend is None:
            n = shares // 100 - UNCOVERED_LOTS
            if n >= 1:
                floor = basis - prem / SHARES if use_floor else None
                pk = pick_call(sim, M, CAL, un, u, i, dte, delta, floor)
                if pk:
                    pend = {"sid": pk[0], "K": pk[1], "n": n, "slipped": False}
    vals = np.array(vals)
    return {"ret": float(vals[-1] / vals[0] - 1),
            "dd": float((vals / np.maximum.accumulate(vals) - 1).min()),
            "prem": float(prem / vals[0]), "called": called, "below": below, "weeks": len(weeks)}


def run_name(u):
    sim, M, CAL, HC, FEE = _env()
    i_last = CAL.idx(pd.Timestamp("2026-10-09"))
    un = M.U(u)
    rows = []
    if not un.ok:
        return rows
    for i0 in start_indices(CAL, i_last):
        seg = un.raw_close[i0:i0 + H + 1]
        if not (np.isfinite(seg[0]) and np.isfinite(seg[-1])):
            continue
        hold = seg[np.isfinite(seg)]
        base = {"name": u, "start": str(CAL.sessions[i0].date()),
                "hold_ret": float(seg[-1] / seg[0] - 1),
                "hold_dd": float((hold / np.maximum.accumulate(hold) - 1).min())}
        for rule, (_, _, use_floor) in RULES.items():
            for scen, mult in (("AT", 1.0), ("UNDER", UNDER)):
                if not use_floor and scen == "AT":
                    continue                      # identical to UNDER apart from the below-cost count
                rows.append({**base, "rule": rule, "scen": scen,
                             **simulate(sim, M, CAL, HC, FEE, u, i0, rule, mult)})
    return rows


def boot_diff(df, col_a, col_b, rng):
    """Mean of (a - b) with a 90% interval, resampling whole start dates."""
    d = (df[col_a] - df[col_b]).to_numpy()
    groups = [g.to_numpy() for _, g in pd.Series(np.arange(len(df))).groupby(df["start"].to_numpy())]
    means = np.empty(N_BOOT)
    for b in range(N_BOOT):
        pick = rng.integers(0, len(groups), len(groups))
        idx = np.concatenate([groups[k] for k in pick])
        means[b] = d[idx].mean()
    return float(d.mean()), float(np.percentile(means, 5)), float(np.percentile(means, 95))


def summarize(df):
    rng = np.random.default_rng(20261117)
    out = {"cases": int(df.groupby(["name", "start"]).ngroups), "names": int(df.name.nunique()),
           "starts": int(df.start.nunique()), "rules": {}}
    one = df.drop_duplicates(["name", "start"])
    out["HOLD"] = {"mean_log": round(float(np.log1p(one.hold_ret).mean()), 4),
                   "mean_ret": round(float(one.hold_ret.mean()), 4),
                   "p10_ret": round(float(one.hold_ret.quantile(0.10)), 4),
                   "mean_dd": round(float(one.hold_dd.mean()), 4)}
    for (rule, scen), g in df.groupby(["rule", "scen"]):
        g = g.assign(lr=np.log1p(g.ret), lh=np.log1p(g.hold_ret))
        m, lo, hi = boot_diff(g, "lr", "lh", rng)
        t = g.assign(t=pd.qcut(g.hold_ret, 3, labels=["fell", "flat", "rose"]))
        out["rules"][f"{rule}|{scen}"] = {
            "mean_log": round(float(g.lr.mean()), 4), "mean_ret": round(float(g.ret.mean()), 4),
            "log_diff_vs_hold": round(m, 4), "diff_90pct": [round(lo, 4), round(hi, 4)],
            "beats_hold_share": round(float((g.ret > g.hold_ret).mean()), 3),
            "p10_ret": round(float(g.ret.quantile(0.10)), 4), "mean_dd": round(float(g.dd.mean()), 4),
            "premium_yield": round(float(g.prem.mean()), 4), "weeks_with_premium": round(float(g.weeks.mean()), 1),
            "called_away_share": round(float((g.called > 0).mean()), 3),
            "called_below_cost_share": round(float((g.below > 0).mean()), 3),
            "by_stock_outcome": {k: round(float((v.ret - v.hold_ret).mean()), 4) for k, v in t.groupby("t", observed=True)},
        }
    return out


def main():
    t0 = time.time()
    sim, M, CAL, HC, FEE = _env()
    names = list(sim.NAMES)
    with Pool(6) as p:
        rows = [r for part in p.map(run_name, names, chunksize=1) for r in part]
    df = pd.DataFrame(rows)
    (HERE / "results").mkdir(exist_ok=True)
    df.to_csv(HERE / "results" / "study_calls_cases.csv", index=False)
    out = summarize(df)
    hood = df[df.name == "HOOD"]
    if len(hood):
        out["HOOD_only"] = {f"{r}|{s}": {"mean_ret": round(float(g.ret.mean()), 4),
                                          "mean_minus_hold": round(float((g.ret - g.hold_ret).mean()), 4),
                                          "called_away_share": round(float((g.called > 0).mean()), 3)}
                            for (r, s), g in hood.groupby(["rule", "scen"])}
        out["HOOD_only"]["HOLD_mean_ret"] = round(float(hood.drop_duplicates("start").hold_ret.mean()), 4)
    (HERE / "results" / "study_calls.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    print(f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
