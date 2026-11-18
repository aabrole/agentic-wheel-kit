"""
study_rotate.py - after a call-away, does moving the cash into ANOTHER stock and writing calls
there compound? Rules FROZEN 2026-11-18, written before the new names' data was on disk.

The idea, in the owner's words: when the shares are called away you take the profit, you do not go
back into the same stock, you buy something else and do the same thing, and over time you have
more calls out. The earlier studies stop at the call-away with the cash idle.

SETUP (same data, fills, haircuts, fees, dividends and valuation as study_calls2.py)
  Case     = (name, first session of a month 2024-02 .. 2025-10), every universe2 name with
             option bars. Horizon 252 sessions (one year; 126 is too short to rotate twice).
  Start    = 300 shares of the name at the start close, no cash. Calls on two of the three
             lots: 35 DTE, 0.30 delta, strike at or above net cost, left to expire (the BASE
             rule). Cost scenarios AT and UNDER as before.
  Cash     = call premium, dividends and call-away proceeds. What happens to it is the arm:
    IDLE   stays in cash at 0% (the earlier studies, at one year).
    SPY    every dollar is moved into SPY at the next open (0.05% half-spread), no calls.
    ROTQ   whenever cash covers at least 100 shares of a QUALITY name, buy as many 100-share
           lots of the best one as the cash allows, at the next open (0.05% half-spread), and
           write BASE calls on every lot of it. Quality = spec3's filter (price >= $10, one-year
           realized vol <= 40%, above its 200-day average), calmest first. Never the start
           name, never a name already held. Leftover cash stays idle. Repeats without limit.
    ROTP   the same, but the name is the one with the highest at-the-money implied vol (35 DTE)
           that day and price >= $5: where the premium is, the way premium sellers pick.
  ADDED 2026-11-18 after the first partial results, at the owner's request ("not calmest, highest
  quality; I want gains and compounding"). Two readings of that, both declared before running:
    ROTM   strongest trend: price >= $10 and above its 200-day average (no volatility cap),
           highest trailing 252-session return first.
    ROTT   trend plus premium: same two conditions, highest at-the-money implied vol first.
  ADDED 2026-11-18 (evening), AFTER the full-list run showed ROTM failing because it bought names
  that had already gone vertical (RGTI, QUBT, UMAC, RKLB, WULF). Written before running:
    ROTF   ROTM with one more exclusion: skip any name whose trailing 252-session return is
           above +200% (it has more than tripled). Highest remaining return first.
  This is the 7th pick rule on the same cases and was designed after seeing why ROTM failed.
  A win is a lead for forward paper trading, not a finding. It "holds" only if its interval
  is above zero against BOTH HOLD and the SPY arm at 1x; one run, no tuning of the 200% cutoff.
  ADDED 2026-11-18 (night) at the owner's request: "most committed to the wheel, leftover cash
  that cannot be split into 100 shares gets held in SPY". Written before running:
    <ARM>+S  the same pick rule as <ARM>, but cash is never idle. Each morning any cash is moved
           into SPY (0.05% half-spread). Buying power for the pick = cash + SPY value. When a
           100-share lot is affordable from that, enough SPY is sold at the next open (0.05%
           half-spread) to fund it. Run for ROTM, ROTT and ROTP. This changes where leftovers
           sit, not which stocks are picked; it is expected to add roughly the SPY return on the
           idle share of the account and nothing else.
  Quality by fundamentals (profits, margins) is not tested: no point-in-time fundamentals on
  disk, and today's numbers would leak the future. These are the 3rd and 4th pick rules tried
  on the same cases, so a win here needs the full list to confirm it.
  A bought name is sold only by being called away. HOLD = the 300 start shares, untouched.

HOW IT IS READ (decided now)
  Primary: mean log return over the year, each arm against HOLD and against SPY-arm, on the
  same cases, 90% block-bootstrap interval over start dates (blocks of six). "Rotation
  compounds" means ROTQ or ROTP beats the SPY arm with an interval above zero: the cash did
  better in the wheel than in the index. Beating IDLE alone only shows cash should not sit.
  Also reported: lots held at the end against the 3 at the start (his "more calls out"),
  premium collected against profit, worst drop, names rotated into.
  HAIRCUT_MULT=2 reruns at twice the option spread.
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
from study_calls import SHARES, UNDER, H as H126  # noqa: E402

import os
H = int(os.environ.get("ROT_H", "252"))      # ROT_H=630 (2.5 years) added 2026-11-18 at the owner's request:
STARTS = ("2024-02-01", "2025-10-31")        # "over years it is exponential". Descriptive only: 3 overlapping starts.
DTE, DELTA = 35, 0.30
STK_HC = 0.0005
ARMS = ("IDLE", "SPY", "ROTQ", "ROTP", "ROTM", "ROTT", "ROTF")
if os.environ.get("ROT_ARMS"):          # speed only: run a subset of arms (results per arm are unchanged)
    ARMS = tuple(os.environ["ROT_ARMS"].split(","))
ROTF_MAX_MOM = 2.0
TAB = SPYT = None                # set in each worker by _init()


def start_indices(CAL, i_last):
    s = CAL.sessions
    m = pd.Series(np.arange(len(s)), index=s)
    m = m[(s >= STARTS[0]) & (s <= STARTS[1])]
    first = m.groupby([m.index.year, m.index.month]).first().to_numpy()
    return [int(i) for i in first if i + H <= i_last]


def name_table(u):
    """Per-session arrays for one name: raw close/open, quality flag, realized vol, ATM IV."""
    import env2
    import study_managed as SM
    sim, M, CAL = env2.sim, env2.M, env2.CAL
    un = M.U(u)
    if not un.ok:
        return u, None
    c = un.adj.close
    lr = np.log(c).diff()
    sma = c.rolling(200, min_periods=200).mean().to_numpy()
    vol = (lr.rolling(252, min_periods=252).std() * np.sqrt(252)).to_numpy()
    px = un.raw_close
    q = np.isfinite(px) & (px >= 10) & np.isfinite(sma) & (c.to_numpy() > sma) & np.isfinite(vol) & (vol <= 0.40)
    ac = c.to_numpy()
    mom = np.full(len(ac), np.nan)
    mom[252:] = ac[252:] / ac[:-252] - 1
    trend = np.isfinite(px) & (px >= 10) & np.isfinite(sma) & (ac > sma)
    return u, {"mom": mom, "trend": trend, "close": px, "open": un.raw_open, "q": q, "vol": vol, "iv": SM.atm_iv_series(un, CAL, u).copy(),
               "first_bar": int(un.b_di.min())}


def pick_name(arm, i, cash, skip):
    arm = arm.split("+")[0]
    best = None
    for u, t in TAB.items():
        if u in skip or i < t["first_bar"]:
            continue
        px = t["close"][i]
        if not np.isfinite(px) or cash < px * 1.02 * 100:
            continue
        if arm == "ROTQ":
            if not t["q"][i]:
                continue
            key = (t["vol"][i], u)
        elif arm == "ROTM":
            if not t["trend"][i] or not np.isfinite(t["mom"][i]):
                continue
            key = (-t["mom"][i], u)
        elif arm == "ROTF":
            if not t["trend"][i] or not np.isfinite(t["mom"][i]) or t["mom"][i] > ROTF_MAX_MOM:
                continue
            key = (-t["mom"][i], u)
        elif arm == "ROTT":
            if not t["trend"][i] or not np.isfinite(t["iv"][i]):
                continue
            key = (-t["iv"][i], u)
        else:
            if px < 5 or not np.isfinite(t["iv"][i]):
                continue
            key = (-t["iv"][i], u)
        if best is None or key < best[0]:
            best = (key, u, int(cash // (px * 1.02 * 100)))
    return best


def simulate(env2, HC, FEE, u0, i0, arm, basis_mult, spy):
    sim, M, CAL = env2.sim, env2.M, env2.CAL
    px0 = float(TAB[u0]["close"][i0])
    pos = {u0: {"shares": SHARES, "basis": px0 * basis_mult, "prem": 0.0, "n0": SHARES, "free": 1,
                "opt": None, "pend": None, "last": px0}}
    div = {u0: env2.dividends(u0)}
    cash = spy_sh = prem_total = 0.0
    buy = None                                   # pending stock purchase
    called = 0
    names = []
    vals = [SHARES * px0]
    sweep = arm.endswith("+S")
    for i in range(i0 + 1, i0 + H + 1):
        for u, p in pos.items():
            if i in div[u]:
                cash += p["shares"] * div[u][i]
        so = spy["open"][i]
        if buy is not None:
            o = TAB[buy["u"]]["open"][i]
            if np.isfinite(o):
                bp = cash + (spy_sh * so * (1 - STK_HC) if sweep and np.isfinite(so) else 0.0)
                n = int(min(buy["lots"], bp // (o * (1 + STK_HC) * 100)))
                if n >= 1:
                    need = n * 100 * o * (1 + STK_HC) - cash
                    if need > 0:                       # sell just enough SPY to fund the lot(s)
                        spy_sh -= need / (so * (1 - STK_HC))
                        cash += need
                    cash -= n * 100 * o * (1 + STK_HC)
                    pos[buy["u"]] = {"shares": n * 100, "basis": o * (1 + STK_HC), "prem": 0.0, "n0": n * 100,
                                     "free": 0, "opt": None, "pend": None, "last": float(o)}
                    div[buy["u"]] = env2.dividends(buy["u"])
                    names.append(buy["u"])
            buy = None
        if (arm == "SPY" or sweep) and cash > 0 and np.isfinite(spy["open"][i]):
            spy_sh += cash / (spy["open"][i] * (1 + STK_HC))
            cash = 0.0
        for u in list(pos):
            p = pos[u]
            un = M.U(u)
            pend = p["pend"]
            if pend is not None:
                r = un.bar_at(pend["sid"], i)
                exp_i = int(un.exp_sidx[pend["sid"]])
                if r is None or un.v[r] < pend["n"]:
                    if pend["slipped"] or i + 1 >= exp_i:
                        p["pend"] = None
                    else:
                        pend["slipped"] = True
                else:
                    o = float(un.o[r])
                    d = max((un.expiry[pend["sid"]] - CAL.sessions[i]).days, 0)
                    got = o * (1 - HC(o, un.mny(pend["sid"], i), d)) * 100 * pend["n"] - FEE * pend["n"]
                    cash += got
                    p["prem"] += got
                    prem_total += got
                    p["opt"] = {"sid": pend["sid"], "K": pend["K"], "n": pend["n"], "exp_i": exp_i}
                    p["pend"] = None
            c = un.raw_close[i]
            if np.isfinite(c):
                p["last"] = float(c)
            o = p["opt"]
            if o is not None and o["exp_i"] == i:
                if p["last"] > o["K"]:
                    cash += o["K"] * 100 * o["n"]
                    p["shares"] -= 100 * o["n"]
                    called += 1
                p["opt"] = None
                if p["shares"] == 0:
                    del pos[u]
        v = cash + spy_sh * (spy["close"][i] if spy_sh else 0.0)
        for u, p in pos.items():
            v += p["shares"] * p["last"]
            if p["opt"] is not None:
                v -= max(M.U(u).mark(p["opt"]["sid"], i), p["last"] - p["opt"]["K"], 0.0) * 100 * p["opt"]["n"]
        vals.append(v)
        if i >= i0 + H:
            continue
        for u, p in pos.items():
            if p["opt"] is None and p["pend"] is None:
                n = p["shares"] // 100 - p["free"]
                if n >= 1:
                    pk = S2.pick_call(sim, M, CAL, M.U(u), u, i, DTE, DELTA, p["basis"] - p["prem"] / p["n0"])
                    if pk:
                        p["pend"] = {"sid": pk[0], "K": pk[1], "n": n, "slipped": False}
        if arm.startswith("ROT") and buy is None:
            b = pick_name(arm, i, cash + (spy_sh * spy["close"][i] * (1 - STK_HC) if sweep and spy_sh > 0 else 0.0),
                          set(pos) | {u0})
            if b:
                buy = {"u": b[1], "lots": b[2]}
    vals = np.array(vals)
    return {"ret": float(vals[-1] / vals[0] - 1), "dd": float((vals / np.maximum.accumulate(vals) - 1).min()),
            "prem": float(prem_total / vals[0]), "called": called,
            "lots_end": int(sum(p["shares"] for p in pos.values()) // 100),
            "names": "|".join(names), "n_rot": len(names)}


def run_name(u0):
    import env2
    sim, M, CAL, HC, FEE = S2._env()
    i_last = CAL.idx(pd.Timestamp("2026-10-09"))
    t = TAB[u0]
    div = env2.dividends(u0)
    spy = SPYT
    rows = []
    for i0 in start_indices(CAL, i_last):
        seg = t["close"][i0:i0 + H + 1]
        if i0 < t["first_bar"] or not (np.isfinite(seg[0]) and np.isfinite(seg[-1])):
            continue
        cum = np.cumsum([div.get(i, 0.0) if i > i0 else 0.0 for i in range(i0, i0 + H + 1)])
        hold = pd.Series(seg).ffill().to_numpy() + cum
        base = {"name": u0, "group": env2.GROUP.get(u0, "?"), "start": str(CAL.sessions[i0].date()),
                "hold_ret": float(hold[-1] / hold[0] - 1),
                "hold_dd": float((hold / np.maximum.accumulate(hold) - 1).min())}
        for arm in ARMS:
            for scen, mult in (("AT", 1.0), ("UNDER", UNDER)):
                rows.append({**base, "arm": arm, "scen": scen, **simulate(env2, HC, FEE, u0, i0, arm, mult, spy)})
    return rows


def diff(df, a, b, rng):
    x = df[df.arm == a].set_index(["name", "start"]).ret
    y = (df[df.arm == b].set_index(["name", "start"]).ret if b != "HOLD"
         else df[df.arm == a].set_index(["name", "start"]).hold_ret).reindex(x.index)
    t = pd.DataFrame({"lr": np.log1p(x.to_numpy()), "lh": np.log1p(y.to_numpy()),
                      "start": x.index.get_level_values("start")})
    m, lo, hi = S2.boot_diff(t, rng)
    return {"log_diff": round(m, 4), "diff_90pct": [round(lo, 4), round(hi, 4)],
            "better_share": round(float((x.to_numpy() > y.to_numpy()).mean()), 3)}


def summarize(df):
    rng = np.random.default_rng(20261120)
    out = {}
    for scen, g in df.groupby("scen"):
        one = g.drop_duplicates(["name", "start"])
        o = {"cases": int(len(one)), "names": int(one.name.nunique()),
             "HOLD": {"mean_ret": round(float(one.hold_ret.mean()), 4), "mean_dd": round(float(one.hold_dd.mean()), 4),
                      "p10_ret": round(float(one.hold_ret.quantile(0.1)), 4)}}
        for arm in ARMS:
            a = g[g.arm == arm]
            o[arm] = {"mean_ret": round(float(a.ret.mean()), 4), "p10_ret": round(float(a.ret.quantile(0.1)), 4),
                      "mean_dd": round(float(a.dd.mean()), 4), "premium_yield": round(float(a.prem.mean()), 4),
                      "called_away_share": round(float((a.called > 0).mean()), 3),
                      "mean_lots_end": round(float(a.lots_end.mean()), 2),
                      "share_with_more_than_3_lots": round(float((a.lots_end > 3).mean()), 3),
                      "rotations_per_case": round(float(a.n_rot.mean()), 2),
                      "vs_HOLD": diff(g, arm, "HOLD", rng)}
            if arm != "SPY":
                o[arm]["vs_SPY_arm"] = diff(g, arm, "SPY", rng)
            if arm.startswith("ROT"):
                top = pd.Series([n for s in a.names for n in s.split("|") if n]).value_counts().head(12)
                o[arm]["most_rotated_into"] = {k: int(v) for k, v in top.items()}
        out[scen] = o
    return out


def _init(tab, spy):
    global TAB, SPYT
    TAB, SPYT = tab, spy


def main():
    import env2
    t0 = time.time()
    names = sys.argv[1].split(",") if len(sys.argv) > 1 else env2.NAMES2
    with Pool(6, maxtasksperchild=4) as p:
        tab = {u: t for u, t in p.map(name_table, names, chunksize=1) if t is not None}
    import odata as D
    spy = D.stock_daily("SPY", adjustment="split").reindex(env2.CAL.sessions)
    spyt = {"open": spy.open.to_numpy(), "close": spy.close.to_numpy()}
    print(f"tables for {len(tab)} names ({time.time() - t0:.0f}s)", flush=True)
    with Pool(6, initializer=_init, initargs=(tab, spyt), maxtasksperchild=6) as p:
        rows = [r for part in p.map(run_name, list(tab), chunksize=1) for r in part]
    df = pd.DataFrame(rows)
    tag = ("" if S2.HAIRCUT_MULT == 1 else f"_haircut{S2.HAIRCUT_MULT:g}x") + ("" if H == 252 else f"_h{H}") + os.environ.get("ROT_TAG", "")
    df.to_csv(HERE / "results" / f"study_rotate_cases{tag}.csv", index=False)
    out = summarize(df)
    (HERE / "results" / f"study_rotate{tag}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    print(f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
