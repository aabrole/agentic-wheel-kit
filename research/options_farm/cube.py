"""
cube.py - the option-bar "cube": for every (underlying, expiry) the daily bars of every
standard contract whose strike sits in a band around the underlying's price while that
expiry is 0-80 calendar days out. Fixed BEFORE any strategy run; strategies read from it.

Expiries:  standard root only (root == underlying; adjusted deliverables like 1AAPL are
           dropped). One expiry per ISO week: the last listed expiry in that week (the Friday,
           or Thursday in a holiday week). Daily SPY/QQQ/IWM expiries are therefore NOT used.
Window:    bars from max(E - 80d, 2024-01-18) to min(E, 2026-10-09).
Band:      sigma = max(0.25, 1.25 x max 20d realized vol in window); T = window days / 365.
           calls: K in [Smin*0.90, Smax*exp(1.3*sigma*sqrt(T))]
           puts:  K in [Smin*exp(-1.3*sigma*sqrt(T)), Smax*1.10]
           (S = RAW, unadjusted closes, because strikes are never split-adjusted.)
FARM names: every expiry. PEAD-only names: only expiries with an earnings event in
           [E-80d, E] (on-demand around events).
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

import odata as D
from farm_universe import DATA_END, FARM_UNIVERSE, OPTIONS_START, PEAD_UNIVERSE

BARS = D.CACHE / "bars"
WINDOW_DAYS = 80


def weekly_expiries(con: pd.DataFrame, underlying: str) -> pd.DataFrame:
    con = con[con.root == underlying].copy()
    ex = pd.Series(sorted(con.expiry.unique()))
    iso = ex.dt.isocalendar()
    keep = ex.groupby([iso.year.values, iso.week.values]).max().values
    return con[con.expiry.isin(keep)]


def plan(underlying: str, only_expiries=None) -> pd.DataFrame:
    con = D.list_contracts(underlying, "2024-01-18", "2026-12-31")
    con = weekly_expiries(con, underlying)
    px = D.stock_daily(underlying, adjustment="raw")["close"]
    rv = np.log(px).diff().rolling(20).std() * np.sqrt(252)
    end = pd.Timestamp(DATA_END)
    rows = []
    for E, g in con.groupby("expiry"):
        if only_expiries is not None and E not in only_expiries:
            continue
        w0 = max(E - pd.Timedelta(days=WINDOW_DAYS), pd.Timestamp(OPTIONS_START))
        w1 = min(E, end)
        if w0 > w1 or E < pd.Timestamp("2024-01-22"):
            continue
        s = px[(px.index >= w0) & (px.index <= w1)]
        if s.empty:
            continue
        sig = max(0.25, 1.25 * float(rv[(rv.index >= w0) & (rv.index <= w1)].max()))
        T = max((E - w0).days, 1) / 365
        smin, smax = float(s.min()), float(s.max())
        up = smax * np.exp(1.3 * sig * np.sqrt(T)); dn = smin * np.exp(-1.3 * sig * np.sqrt(T))
        c = g[(g.cp == "C") & (g.strike >= smin * 0.90) & (g.strike <= up)]
        p = g[(g.cp == "P") & (g.strike >= dn) & (g.strike <= smax * 1.10)]
        sel = pd.concat([c, p])
        sel = sel.assign(w0=w0, w1=w1)
        rows.append(sel)
    return pd.concat(rows) if rows else pd.DataFrame()


def bars_path(underlying: str, E: pd.Timestamp) -> Path:
    return BARS / f"{underlying}_{E.date()}.parquet"


def fetch_expiry(underlying: str, E: pd.Timestamp, g: pd.DataFrame) -> int:
    p = bars_path(underlying, E)
    if p.exists():
        return 0
    w0, w1 = g.w0.iloc[0], g.w1.iloc[0]
    b = D.fetch_option_bars(sorted(g.symbol), str(w0.date()), str((w1 + pd.Timedelta(days=1)).date()))
    meta = g.set_index("symbol")[["strike", "cp"]]
    b = b.join(meta, on="symbol")
    b["expiry"] = E
    b.to_parquet(p)
    return len(b)


def build(names, only=None, workers: int = 2, time_limit: float = 1e9):
    import time as _t
    t_start = _t.time()
    jobs = []
    for u in names:
        pl = plan(u, None if only is None else only.get(u, set()))
        if pl.empty:
            continue
        for E, g in pl.groupby("expiry"):
            if not bars_path(u, E).exists():
                jobs.append((u, E, g))
    print(f"[cube] {len(jobs)} expiry groups to fetch")
    done = 0
    def guarded(u, E, g):
        if _t.time() - t_start > time_limit:
            return -1
        return fetch_expiry(u, E, g)
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(guarded, u, E, g) for u, E, g in jobs]
        for f in as_completed(futs):
            f.result()
            done += 1
            if done % 100 == 0:
                print(f"[cube] {done}/{len(jobs)}  calls so far {D.flush_calls()}", flush=True)
    print("[cube] done", D.flush_calls())


def load(underlying: str) -> pd.DataFrame:
    fs = sorted(BARS.glob(f"{underlying}_*.parquet"))
    if not fs:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)


def pead_only_expiries(events: pd.DataFrame):
    """Expiries for PEAD-only names that have an event entry within [E-80d, E]."""
    out = {}
    for u in sorted(set(PEAD_UNIVERSE) - set(FARM_UNIVERSE)):
        con = weekly_expiries(D.list_contracts(u, "2024-01-18", "2026-12-31"), u)
        ex = pd.Series(sorted(con.expiry.unique()))
        ev = events[events.ticker == u].entry_date
        keep = set()
        for t in ev:
            keep |= set(ex[(ex >= t) & (ex <= t + pd.Timedelta(days=WINDOW_DAYS))])
        out[u] = keep
    return out


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "estimate"
    if what == "estimate":
        tot = 0
        for u in FARM_UNIVERSE:
            pl = plan(u)
            n = len(pl); tot += n
            print(u, n, pl.expiry.nunique(), f"~{int(np.ceil(pl.groupby('expiry').size() / 100).sum())} req")
        print("farm contracts", tot)
    elif what == "farm":
        build(FARM_UNIVERSE, time_limit=float(sys.argv[2]) if len(sys.argv) > 2 else 1e9)
    elif what == "pead":
        import events as EV
        ev = EV.load_events(sorted(set(PEAD_UNIVERSE)), D.stock_daily("SPY").index)
        build(sorted(set(PEAD_UNIVERSE) - set(FARM_UNIVERSE)), only=pead_only_expiries(ev[ev.entry_date >= "2024-01-01"]), time_limit=540)
