"""
study_managed.py - does ACTIVE MANAGEMENT rescue the covered call? Rules FROZEN 2026-11-17,
written before the first run. Same cases, data, fills, haircuts, fees, dividends, valuation
and bootstrap as study_calls2.py (read that docstring first); only the call rules differ.

Why: every call rule so far has trailed or tied holding. the owner asked whether an agent that
manages the position daily could do better. Two management rules were named in chat before
any run, and are tested here alone and together:

RULES COMPARED (four, fixed)
  BASE     C35-floor exactly as in study_calls2: 35 DTE, 0.30 delta, strike at or above net
           cost, left to expire. This is the live engine's rule and must reproduce
           study_calls2's numbers (checked in the output).
  ROLL     BASE + roll up and out. At the close, if an open call is in the money (close >
           strike) with 7 or fewer calendar days left, roll it: buy it back and sell a call in
           the expiry nearest 35 DTE that is later than the current one, at the HIGHEST strike
           above the current strike for which the estimated sale (decision-day close less
           half-spread) still covers the estimated buy-back (max of last price and intrinsic,
           plus half-spread) and both fees. The new strike must be strictly higher; if no such
           strike traded that day in the needed size, or the new option's life would contain
           an earnings session, there is no roll and the shares are called away as in BASE.
           Both legs fill at the next open with their own half-spread, whatever the net turns
           out to be (level 2 is single-leg: two market-style orders, no net-credit limit).
           If either leg has no bar or too little volume, retry each session until expiry.
           No limit on the number of rolls.
  IV       BASE + sell only when implied volatility is rich against the stock's own recent
           history. Measure: at-the-money implied vol of the expiry nearest 35 DTE, from that
           day's smile. A NEW call may be sold only if today's value is at or above the
           median of the values on the previous 126 sessions. With fewer than 20 prior
           values the filter is off (BASE behaviour). No value today = no sale.
  MANAGED  BASE + ROLL + IV. Rolls are not subject to the IV filter (a roll is defensive).

  Each rule under both cost scenarios of study_calls2: AT (basis = start price) and UNDER
  (basis = start price x 1.188, a holder about 16% below cost).

HOW IT IS READ (decided now)
  Primary: mean log return over 26 weeks, each rule against HOLD on the same cases, with the
  block-bootstrap 90% interval. A rule "beats holding" only if that interval is above zero.
  A rule "improves on BASE" only if the interval on (rule - BASE) is above zero.
  Secondary: beats-hold share, 10th-percentile return, mean worst drop, premium yield,
  called-away share, rolls per case, and the split at +10% stock return.
  This is the SIXTH look at the same 2.7 years. Anything that only just clears the bar here
  should be treated as not proven.
  HAIRCUT_MULT=2 reruns at twice the bid-ask haircut. Rolls cross the spread twice, so that
  run is the one to believe if the two disagree.

ADDED 2026-11-17, after the first results, at the owner's request ("can it capture more upside"):
  COVER_LOTS=1 in the environment covers ONE of the three lots instead of two. Nothing else
  changes. Expected before running: about half the upside given up and half the benefit.
  Results go to separate files (_cover1); the two-lot results above are untouched.
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
sys.path.insert(0, str(HERE))

import study_calls2 as S2  # noqa: E402
from study_calls import H, SHARES, UNCOVERED_LOTS, UNDER, start_indices  # noqa: E402

DTE, DELTA = 35, 0.30
ROLL_DTE = 7
IV_LOOKBACK, IV_MIN_OBS = 126, 20
MRULES = {"BASE": (False, False, False), "ROLL": (True, False, False), "IV": (False, True, False),
          "MANAGED": (True, True, False),
          # added 2026-11-18 at the owner's request, declared in news_days.py before any run:
          "BASE+NEWS": (False, False, True), "MANAGED+NEWS": (True, True, True)}
_NEWS = None


def news_sessions(CAL):
    global _NEWS
    if _NEWS is None:
        import news_days
        _NEWS = news_days.session_set(CAL)
    return _NEWS
COVER_LOTS = int(os.environ.get("COVER_LOTS", "2"))
_IV = {}


def atm_iv_series(un, CAL, u):
    """At-the-money implied vol of the expiry nearest 35 DTE, per session (nan if none)."""
    if u not in _IV:
        out = np.full(len(CAL.sessions), np.nan)
        for i in range(len(CAL.sessions) - 1):
            if not np.isfinite(un.raw_close[i]):
                continue
            E = un.choose_expiry(i, i + 1, DTE)
            if E is None:
                continue
            sm = un.smile(i, E)
            if sm is not None:
                out[i] = float(np.interp(0.0, sm[0], sm[1]))
        _IV[u] = out
    return _IV[u]


def iv_rich(iv, i):
    if not np.isfinite(iv[i]):
        return False
    prior = iv[max(0, i - IV_LOOKBACK):i]
    prior = prior[np.isfinite(prior)]
    if len(prior) < IV_MIN_OBS:
        return True
    return bool(iv[i] >= np.median(prior))


def roll_target(sim, CAL, HC, FEE, un, u, opt, i, last_px):
    E0 = un.expiry[opt["sid"]]
    E = un.choose_expiry(i, i + 1, DTE, min_expiry=E0 + pd.Timedelta(days=1))
    if E is None or E <= E0 or sim.earnings_clash(u, i + 1, CAL.last_le(E) + 1):
        return None
    m = max(un.mark(opt["sid"], i), last_px - opt["K"], 0.0)
    d_old = max((E0 - CAL.sessions[i]).days, 0)
    cost = m * (1 + HC(m, un.mny(opt["sid"], i), d_old))
    d_new = int((E - CAL.sessions[i + 1]).days)
    mem = un.exp_members[E]
    mem = mem[un.is_call[mem] & (un.strike[mem] > opt["K"])]
    for sid in mem[np.argsort(-un.strike[mem])]:               # highest strike first
        sid = int(sid)
        r = un.bar_at(sid, i)
        if r is None or un.v[r] < opt["n"]:
            continue
        est = float(un.c[r])
        if est * (1 - HC(est, un.mny(sid, i), d_new)) - cost - 2 * FEE / 100 >= 0:
            return {"old": opt["sid"], "new": sid, "K": float(un.strike[sid])}
    return None


def simulate(sim, M, CAL, HC, FEE, u, i0, rule, basis_mult, div, iv):
    un = M.U(u)
    do_roll, do_iv, do_news = MRULES[rule]
    news = news_sessions(CAL) if do_news else ()
    px0 = float(un.raw_close[i0])
    basis = px0 * basis_mult
    shares, cash, prem = SHARES, 0.0, 0.0
    opt = pend = proll = None
    called = below = rolls = 0
    vals = [SHARES * px0]
    last_px = px0
    for i in range(i0 + 1, i0 + H + 1):
        if i in div:
            cash += shares * div[i]
        if pend is not None and i not in news:
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
                opt = {"sid": pend["sid"], "K": pend["K"], "n": pend["n"], "exp_i": exp_i}
                pend = None
        if proll is not None and i not in news:
            if opt is None or opt["sid"] != proll["old"]:
                proll = None
            else:
                n = opt["n"]
                ro, rn = un.bar_at(proll["old"], i), un.bar_at(proll["new"], i)
                if ro is None or rn is None or un.v[ro] < n or un.v[rn] < n:
                    if i + 1 > opt["exp_i"]:
                        proll = None
                else:
                    po, pn = float(un.o[ro]), float(un.o[rn])
                    d_old = max((un.expiry[proll["old"]] - CAL.sessions[i]).days, 0)
                    d_new = max((un.expiry[proll["new"]] - CAL.sessions[i]).days, 0)
                    buy = po * (1 + HC(po, un.mny(proll["old"], i), d_old))
                    sell = pn * (1 - HC(pn, un.mny(proll["new"], i), d_new))
                    flow = (sell - buy) * 100 * n - 2 * FEE * n
                    cash += flow
                    prem += flow
                    opt = {"sid": proll["new"], "K": proll["K"], "n": n,
                           "exp_i": int(un.exp_sidx[proll["new"]])}
                    rolls += 1
                    proll = None
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
            proll = None
        v = cash + shares * last_px
        if opt is not None:
            v -= max(un.mark(opt["sid"], i), last_px - opt["K"], 0.0) * 100 * opt["n"]
        vals.append(v)
        if i >= i0 + H or (i + 1) in news:
            continue
        if do_roll and opt is not None and proll is None and opt["exp_i"] > i and last_px > opt["K"] \
                and (un.expiry[opt["sid"]] - CAL.sessions[i]).days <= ROLL_DTE:
            proll = roll_target(sim, CAL, HC, FEE, un, u, opt, i, last_px)
        if opt is None and pend is None:
            n = min(shares // 100 - UNCOVERED_LOTS, COVER_LOTS)
            if n >= 1 and (not do_iv or iv_rich(iv, i)):
                pk = S2.pick_call(sim, M, CAL, un, u, i, DTE, DELTA, basis - prem / SHARES)
                if pk:
                    pend = {"sid": pk[0], "K": pk[1], "n": n, "slipped": False}
    vals = np.array(vals)
    return {"ret": float(vals[-1] / vals[0] - 1),
            "dd": float((vals / np.maximum.accumulate(vals) - 1).min()),
            "prem": float(prem / vals[0]), "called": called, "below": below, "rolls": rolls,
            "weeks": 0, "open_at_end": int(opt is not None)}


def run_name(u):
    sim, M, CAL, HC, FEE = S2._env()
    i_last = CAL.idx(pd.Timestamp("2026-10-09"))
    un = M.U(u)
    rows = []
    if not un.ok:
        return rows
    div = S2.dividends(u, CAL)
    iv = atm_iv_series(un, CAL, u)
    for i0 in start_indices(CAL, i_last):
        seg = un.raw_close[i0:i0 + H + 1]
        if not (np.isfinite(seg[0]) and np.isfinite(seg[-1])):
            continue
        cum = np.cumsum([div.get(i, 0.0) if i > i0 else 0.0 for i in range(i0, i0 + H + 1)])
        hold = pd.Series(seg).ffill().to_numpy() + cum
        base = {"name": u, "start": str(CAL.sessions[i0].date()),
                "hold_ret": float(hold[-1] / hold[0] - 1),
                "hold_dd": float((hold / np.maximum.accumulate(hold) - 1).min())}
        for rule in MRULES:
            for scen, mult in (("AT", 1.0), ("UNDER", UNDER)):
                rows.append({**base, "rule": rule, "scen": scen,
                             **simulate(sim, M, CAL, HC, FEE, u, i0, rule, mult, div, iv)})
    return rows


def vs_base(df):
    """Interval on (rule - BASE) mean log return, same block bootstrap."""
    rng = np.random.default_rng(20261118)
    out = {}
    for scen, g in df.groupby("scen"):
        b = g[g.rule == "BASE"].set_index(["name", "start"]).ret
        for rule in ("ROLL", "IV", "MANAGED"):
            r = g[g.rule == rule].set_index(["name", "start"]).ret.reindex(b.index)
            t = pd.DataFrame({"lr": np.log1p(r.to_numpy()), "lh": np.log1p(b.to_numpy()),
                              "start": b.index.get_level_values("start")})
            m, lo, hi = S2.boot_diff(t, rng)
            out[f"{rule}|{scen}"] = {"log_diff_vs_base": round(m, 4), "diff_90pct": [round(lo, 4), round(hi, 4)],
                                     "beats_base_share": round(float((r.to_numpy() > b.to_numpy()).mean()), 3),
                                     "same_as_base_share": round(float(np.isclose(r.to_numpy(), b.to_numpy()).mean()), 3)}
    return out


def main():
    t0 = time.time()
    sim, M, CAL, HC, FEE = S2._env()
    with Pool(6) as p:
        rows = [r for part in p.map(run_name, list(sim.NAMES), chunksize=1) for r in part]
    df = pd.DataFrame(rows)
    tag = ("" if S2.HAIRCUT_MULT == 1 else f"_haircut{S2.HAIRCUT_MULT:g}x") + ("" if COVER_LOTS == 2 else f"_cover{COVER_LOTS}")
    df.to_csv(HERE / "results" / f"study_managed_cases{tag}.csv", index=False)
    out = S2.summarize(df)
    for (rule, scen), g in df.groupby(["rule", "scen"]):
        out["rules"][f"{rule}|{scen}"]["rolls_per_case"] = round(float(g.rolls.mean()), 2)
        out["rules"][f"{rule}|{scen}"]["cases_with_a_roll"] = round(float((g.rolls > 0).mean()), 3)
    out["vs_base"] = vs_base(df)
    (HERE / "results" / f"study_managed{tag}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    print(f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
