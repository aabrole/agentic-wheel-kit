"""
study_u2.py - the covered-call study on the EXPANDED universe (universe2.py).
Rules FROZEN 2026-11-18, written while the new names' option data was still downloading.

Identical to study_managed.py in every rule, fill, haircut, fee, valuation and bootstrap:
cases are (name, first session of a month 2024-02..2026-04), 126 sessions, 300 shares, calls on
two of three lots (COVER_LOTS=1 for one), rules BASE / ROLL / IV / MANAGED, cost scenarios AT
and UNDER. The only change is the list of names: every universe2 name that has option bars and
(for stocks) earnings dates. A late-listed name only has cases from its first full month.

HOW IT IS READ (decided now)
  Primary: MANAGED and BASE against HOLD on ALL names, mean log return, 90% block-bootstrap
  interval. Same bar as before: a rule beats holding only if the interval is above zero.
  Then the same numbers for the NEW names only (never tested before; the original 33 are a
  seventh look at old data and are shown only to confirm they reproduce study_managed.py).
  Then by group (sp100, etf, reddit, active), descriptive only: no claims from one group
  unless its interval excludes zero AND the all-names result agrees in sign.
  The bid-ask haircut was calibrated on the original names. Small caps are wider, so for the
  reddit and active groups the HAIRCUT_MULT=2 run is the one to believe.
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
sys.path.insert(0, str(HERE))

import study_calls2 as S2  # noqa: E402
import study_managed as SM  # noqa: E402
from study_calls import H, UNDER, start_indices  # noqa: E402


def run_name(u):
    import env2
    sim, M, CAL, HC, FEE = S2._env()
    i_last = CAL.idx(pd.Timestamp("2026-10-09"))
    un = M.U(u)
    rows = []
    if not un.ok:
        return rows
    div = env2.dividends(u)
    iv = SM.atm_iv_series(un, CAL, u)
    first_bar = int(un.b_di.min())
    for i0 in start_indices(CAL, i_last):
        seg = un.raw_close[i0:i0 + H + 1]
        if i0 < first_bar or not (np.isfinite(seg[0]) and np.isfinite(seg[-1])):
            continue
        cum = np.cumsum([div.get(i, 0.0) if i > i0 else 0.0 for i in range(i0, i0 + H + 1)])
        hold = pd.Series(seg).ffill().to_numpy() + cum
        base = {"name": u, "group": env2.GROUP.get(u, "?"), "start": str(CAL.sessions[i0].date()),
                "hold_ret": float(hold[-1] / hold[0] - 1),
                "hold_dd": float((hold / np.maximum.accumulate(hold) - 1).min())}
        for rule in SM.MRULES:
            for scen, mult in (("AT", 1.0), ("UNDER", UNDER)):
                rows.append({**base, "rule": rule, "scen": scen,
                             **SM.simulate(sim, M, CAL, HC, FEE, u, i0, rule, mult, div, iv)})
    return rows


def news_effect(df):
    """Interval on (rule+NEWS minus the same rule without it), mean log return."""
    rng = np.random.default_rng(20261119)
    out = {}
    for scen, g in df.groupby("scen"):
        for rule in ("BASE", "MANAGED"):
            b = g[g.rule == rule].set_index(["name", "start"]).ret
            r = g[g.rule == rule + "+NEWS"].set_index(["name", "start"]).ret.reindex(b.index)
            t = pd.DataFrame({"lr": np.log1p(r.to_numpy()), "lh": np.log1p(b.to_numpy()),
                              "start": b.index.get_level_values("start")})
            m, lo, hi = S2.boot_diff(t, rng)
            out[f"{rule}|{scen}"] = {"news_minus_plain_log": round(m, 4), "diff_90pct": [round(lo, 4), round(hi, 4)],
                                     "better_share": round(float((r.to_numpy() > b.to_numpy()).mean()), 3),
                                     "same_share": round(float(np.isclose(r.to_numpy(), b.to_numpy()).mean()), 3)}
    return out


def block(df):
    if df.empty or df.start.nunique() < 8:
        return None
    out = S2.summarize(df)
    out["vs_base"] = SM.vs_base(df)
    out["news_guardrail"] = news_effect(df)
    return out


def main():
    import env2
    t0 = time.time()
    names = sys.argv[1].split(",") if len(sys.argv) > 1 else env2.NAMES2
    with Pool(6) as p:
        rows = [r for part in p.map(run_name, names, chunksize=1) for r in part]
    df = pd.DataFrame(rows)
    tag = ("" if S2.HAIRCUT_MULT == 1 else f"_haircut{S2.HAIRCUT_MULT:g}x") + ("" if SM.COVER_LOTS == 2 else f"_cover{SM.COVER_LOTS}")
    df.to_csv(HERE / "results" / f"study_u2_cases{tag}.csv", index=False)
    out = {"names_used": len(names), "no_earnings_dates": env2.NO_EARNINGS,
           "ALL": block(df), "NEW": block(df[df.group != "orig33"]), "ORIG": block(df[df.group == "orig33"]),
           "by_group": {g: block(d) for g, d in df.groupby("group")}}
    (HERE / "results" / f"study_u2{tag}.json").write_text(json.dumps(out, indent=1))
    for k in ("ALL", "NEW", "ORIG"):
        b = out[k]
        if b:
            print(k, b["cases"], "cases", b["names"], "names  HOLD", b["HOLD"]["mean_ret"])
            print("   news guardrail", b["news_guardrail"])
            for r in ("BASE|AT", "MANAGED|AT", "BASE|UNDER", "MANAGED|UNDER", "MANAGED+NEWS|AT", "MANAGED+NEWS|UNDER"):
                v = b["rules"][r]
                print("  ", r, v["mean_ret"], v["log_diff_vs_hold"], v["diff_90pct"], v["beats_hold_share"], v["mean_dd"])
    print(f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
