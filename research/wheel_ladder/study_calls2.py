"""
study_calls2.py - study_calls.py with the defects a Codex adversarial review found in it
(2026-11-17). Same question, cases, rules and reading as study_calls.py; read that docstring
first. study_calls.py and its results are kept unchanged as the first run.

WHAT CHANGED, AND WHICH WAY EACH DEFECT LEANED
  1. Open calls are valued at no less than their intrinsic value. v1 used the last traded
     price, which can be days old; with a call still open at the 26-week mark that overstated
     the call rules after a rally. (v1 flattered calls.)
  2. Dividends are credited, on ex-dates, to the shares each side actually holds. v1 ignored
     them, which penalised HOLD after a call-away. (v1 flattered calls on dividend payers.)
  3. A strike must have traded on the decision day BEFORE it is ranked. v1 ranked the eight
     strikes nearest the target delta first, including ones not yet trading, so a tradable
     strike could be crowded out and the rule would wait. (Direction unclear.)
  4. Earnings: an expiry is also refused when the recorded earnings session is the session
     after it. The recorded session comes from filing times and can be one session late.
  5. The interval on each difference from HOLD now resamples blocks of six consecutive start
     dates, because 26-week windows that start a month apart overlap. (v1 intervals too narrow.)
  6. The split by what the stock did is by thirds of the HOLD return and is labelled that way,
     plus an explicit split at +10%.
  HAIRCUT_MULT=2 in the environment reruns everything at twice the bid-ask haircut.

NOT FIXED (no data to fix it with): fills assume the whole order trades at the day's opening
price whenever the day's volume covers it. Early assignment before ex-dividend dates is not
modelled.
"""
from __future__ import annotations

import json
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))        # wheel_ladder/sim.py, not options_farm/sim.py

from study_calls import H, MIN_PREMIUM, MIN_YIELD, RULES, SHARES, UNCOVERED_LOTS, UNDER, min_prem, start_indices  # noqa: E402

HAIRCUT_MULT = float(os.environ.get("HAIRCUT_MULT", "1"))
BLOCK = 6
N_BOOT = 4000
_DIV = None


def _env():
    import sim                                    # noqa: E402
    import calibrate                              # noqa: E402  (options_farm, on the path via sim)
    return sim, sim.M, sim.CAL, calibrate.Haircut(mult=HAIRCUT_MULT), sim.FEE


def dividends(u, CAL):
    """{session index: dollars per share} for ex-dates of u."""
    global _DIV
    if _DIV is None:
        d = pd.read_csv(HERE / "cache" / "dividends.csv", parse_dates=["ex_date"])
        _DIV = {n: g for n, g in d.groupby("name")}
    g = _DIV.get(u)
    out = {}
    if g is not None:
        for ts, amt in zip(g.ex_date, g.amount):
            if ts in CAL.pos:
                out[CAL.pos[ts]] = out.get(CAL.pos[ts], 0.0) + float(amt)
    return out


def tradable_calls(M, CAL, un, i, E, delta_target):
    """Calls of expiry E that TRADED on session i, nearest the target delta first."""
    sm = un.smile(i, E)
    S0 = un.raw_close[i]
    if sm is None or not np.isfinite(S0):
        return []
    mem = un.exp_members[E]
    mem = mem[un.is_call[mem]]
    rows = [(int(s), un.bar_at(int(s), i)) for s in mem]
    rows = [(s, r) for s, r in rows if r is not None and un.v[r] >= 1]
    if not rows:
        return []
    sids = np.array([s for s, _ in rows])
    K = un.strike[sids]
    iv = np.interp(np.log(K / S0), sm[0], sm[1])
    T = max((E - CAL.sessions[i + 1]).days, 0.5) / 365
    dl = np.abs(M.bs.delta(S0, K, T, iv, True))
    dist = np.abs(dl - delta_target)
    order = np.lexsort((K, dist))
    return [{"sid": int(sids[k]), "strike": float(K[k]), "close": float(un.c[rows[k][1]])}
            for k in order if dist[k] <= M.DELTA_TOL]


def pick_call(sim, M, CAL, un, u, i, dte, delta, floor):
    for d in [dte] + [x for x in (21, 14, 7) if x < dte]:
        E = un.choose_expiry(i, i + 1, d)
        if E is None:
            continue
        if sim.earnings_clash(u, i + 1, CAL.last_le(E) + 1):       # one session of margin
            continue
        need = min_prem(float(un.raw_close[i]), (E - CAL.sessions[i + 1]).days)
        for c in tradable_calls(M, CAL, un, i, E, delta):
            if c["close"] >= need and (floor is None or c["strike"] >= floor):
                return c["sid"], c["strike"]
        if floor is not None:                      # guide fallback: lowest tradable strike >= cost
            mem = un.exp_members[E]
            mem = mem[un.is_call[mem] & (un.strike[mem] >= floor)]
            for sid in mem[np.argsort(un.strike[mem])]:
                r = un.bar_at(int(sid), i)
                if r is not None and un.v[r] >= 1 and un.c[r] >= need:
                    return int(sid), float(un.strike[sid])
        return None
    return None


def simulate(sim, M, CAL, HC, FEE, u, i0, rule, basis_mult, div):
    un = M.U(u)
    px0 = float(un.raw_close[i0])
    basis = px0 * basis_mult
    shares, cash, prem = SHARES, 0.0, 0.0
    opt = pend = None
    called = below = 0
    vals = [SHARES * px0]
    weeks = set()
    dte, delta, use_floor = RULES[rule]
    last_px = px0
    for i in range(i0 + 1, i0 + H + 1):
        if i in div:                               # shares held at the prior close get the dividend
            cash += shares * div[i]
        if pend is not None:
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
        if opt is not None and opt["exp_i"] == i:
            if last_px > opt["K"]:
                cash += opt["K"] * 100 * opt["n"]
                shares -= 100 * opt["n"]
                called += 1
                below += int(opt["K"] < basis)
            opt = None
        v = cash + shares * last_px
        if opt is not None:                        # never below intrinsic, however old the last trade
            v -= max(un.mark(opt["sid"], i), last_px - opt["K"], 0.0) * 100 * opt["n"]
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
            "prem": float(prem / vals[0]), "called": called, "below": below, "weeks": len(weeks),
            "open_at_end": int(opt is not None)}


def run_name(u):
    sim, M, CAL, HC, FEE = _env()
    i_last = CAL.idx(pd.Timestamp("2026-10-09"))
    un = M.U(u)
    rows = []
    if not un.ok:
        return rows
    div = dividends(u, CAL)
    for i0 in start_indices(CAL, i_last):
        seg = un.raw_close[i0:i0 + H + 1]
        if not (np.isfinite(seg[0]) and np.isfinite(seg[-1])):
            continue
        cum = np.cumsum([div.get(i, 0.0) if i > i0 else 0.0 for i in range(i0, i0 + H + 1)])
        hold = pd.Series(seg).ffill().to_numpy() + cum
        base = {"name": u, "start": str(CAL.sessions[i0].date()),
                "hold_ret": float(hold[-1] / hold[0] - 1),
                "hold_dd": float((hold / np.maximum.accumulate(hold) - 1).min())}
        for rule, (_, _, use_floor) in RULES.items():
            for scen, mult in (("AT", 1.0), ("UNDER", UNDER)):
                if not use_floor and scen == "AT":
                    continue
                rows.append({**base, "rule": rule, "scen": scen,
                             **simulate(sim, M, CAL, HC, FEE, u, i0, rule, mult, div)})
    return rows


def boot_diff(g, rng):
    """Mean log-return difference from HOLD with a 90% interval from a circular block
    bootstrap over start dates (blocks of BLOCK consecutive starts)."""
    d = (g.lr - g.lh).to_numpy()
    starts = sorted(g.start.unique())
    idx = {s: np.where(g.start.to_numpy() == s)[0] for s in starts}
    n, means = len(starts), np.empty(N_BOOT)
    for b in range(N_BOOT):
        pick = []
        while len(pick) < n:
            a = int(rng.integers(0, n))
            pick += [starts[(a + j) % n] for j in range(BLOCK)]
        means[b] = d[np.concatenate([idx[s] for s in pick[:n]])].mean()
    return float(d.mean()), float(np.percentile(means, 5)), float(np.percentile(means, 95))


def summarize(df):
    rng = np.random.default_rng(20261117)
    one = df.drop_duplicates(["name", "start"])
    out = {"haircut_mult": HAIRCUT_MULT, "cases": int(len(one)), "names": int(df.name.nunique()),
           "starts": int(df.start.nunique()),
           "HOLD": {"mean_log": round(float(np.log1p(one.hold_ret).mean()), 4),
                    "mean_ret": round(float(one.hold_ret.mean()), 4),
                    "p10_ret": round(float(one.hold_ret.quantile(0.10)), 4),
                    "mean_dd": round(float(one.hold_dd.mean()), 4),
                    "share_up_more_than_10pct": round(float((one.hold_ret > 0.10).mean()), 3)},
           "rules": {}}
    for (rule, scen), g in df.groupby(["rule", "scen"]):
        g = g.assign(lr=np.log1p(g.ret), lh=np.log1p(g.hold_ret), x=g.ret - g.hold_ret)
        m, lo, hi = boot_diff(g, rng)
        third = pd.qcut(g.hold_ret, 3, labels=["lower_third", "middle_third", "upper_third"])
        out["rules"][f"{rule}|{scen}"] = {
            "mean_log": round(float(g.lr.mean()), 4), "mean_ret": round(float(g.ret.mean()), 4),
            "log_diff_vs_hold": round(m, 4), "diff_90pct": [round(lo, 4), round(hi, 4)],
            "beats_hold_share": round(float((g.ret > g.hold_ret).mean()), 3),
            "p10_ret": round(float(g.ret.quantile(0.10)), 4), "mean_dd": round(float(g.dd.mean()), 4),
            "premium_yield": round(float(g.prem.mean()), 4),
            "called_away_share": round(float((g.called > 0).mean()), 3),
            "called_below_cost_share": round(float((g.below > 0).mean()), 3),
            "open_at_end_share": round(float(g.open_at_end.mean()), 3),
            "minus_hold_when_stock_up_10pct_or_less": round(float(g.x[g.hold_ret <= 0.10].mean()), 4),
            "minus_hold_when_stock_up_more": round(float(g.x[g.hold_ret > 0.10].mean()), 4),
            "minus_hold_by_third_of_stock_return": {k: round(float(v), 4) for k, v in g.x.groupby(third, observed=True).mean().items()},
        }
    return out


def main():
    t0 = time.time()
    sim, M, CAL, HC, FEE = _env()
    with Pool(6) as p:
        rows = [r for part in p.map(run_name, list(sim.NAMES), chunksize=1) for r in part]
    df = pd.DataFrame(rows)
    tag = "" if HAIRCUT_MULT == 1 else f"_haircut{HAIRCUT_MULT:g}x"
    df.to_csv(HERE / "results" / f"study_calls2_cases{tag}.csv", index=False)
    out = summarize(df)
    (HERE / "results" / f"study_calls2{tag}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out))
    print(f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
