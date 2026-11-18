"""
env2.py - point the existing engine (sim.py / market.py) at the expanded universe.
Importing this module: adds the new names' earnings dates to sim.EARN, marks every fund in
universe2.ETFS2 as an ETF (no earnings), and exposes NAMES2 = universe2 names that have option
bars on disk. Old names keep the earnings dates and dividends the earlier studies used.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sim  # noqa: E402
import universe2 as U2  # noqa: E402

M, CAL = sim.M, sim.CAL
sim.S.ETFS = set(sim.S.ETFS) | set(U2.ETFS2)

_p = HERE / "cache" / "earnings_events2.parquet"
NO_EARNINGS = []
if _p.exists():
    _e = pd.read_parquet(_p)
    for _t, _g in _e.groupby("ticker"):
        if _t not in sim.EARN:
            sim.EARN[_t] = np.array(sorted({CAL.last_le(d) for d in pd.to_datetime(_g.entry_date)}))
NO_EARNINGS = [t for t in U2.UNIVERSE2 if t not in sim.S.ETFS and t not in sim.EARN]

import cube  # noqa: E402
_have = {f.name.rsplit("_", 1)[0] for f in cube.BARS.glob("*.parquet")}
OLD = [t for t in sim.NAMES]                                   # the 33 names of the earlier studies
NAMES2 = [t for t in U2.UNIVERSE2 if t in _have and t not in NO_EARNINGS and (t in OLD or t not in U2.FARM)]
GROUP = {}
for _n, _l in (("active", U2.ACTIVE), ("etf", U2.ETFS2), ("sp100", U2.SP100), ("reddit", U2.REDDIT), ("orig33", OLD)):
    for _t in _l:
        GROUP[_t] = _n                                          # later groups win: orig33 > reddit > sp100 > etf > active


def dividends(u):
    """{session index: $ per share}. Old names from the file the earlier studies used."""
    if not hasattr(dividends, "d"):
        a = pd.read_csv(HERE / "cache" / "dividends.csv", parse_dates=["ex_date"])
        b = pd.read_csv(HERE / "cache" / "dividends2.csv", parse_dates=["ex_date"])
        b = b[~b.name.isin(OLD)]
        dividends.d = {n: g for n, g in pd.concat([a, b]).groupby("name")}
    g = dividends.d.get(u)
    out = {}
    if g is not None:
        for ts, amt in zip(g.ex_date, g.amount):
            if ts in CAL.pos:
                out[CAL.pos[ts]] = out.get(CAL.pos[ts], 0.0) + float(amt)
    return out
