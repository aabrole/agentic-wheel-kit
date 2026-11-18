"""
calibrate.py - half-spread haircut from LIVE Alpaca option snapshots (indicative feed).

For every FARM + PEAD name: contracts expiring within 80 days, strikes within +-35% of
spot. Relative spread = (ask - bid) / mid, two-sided quotes only (bid > 0). Buckets:
  premium (mid):  <0.25, 0.25-0.50, 0.50-1, 1-2, 2-5, >=5
  moneyness x:    ln(K/S) for calls, ln(S/K) for puts (positive = OTM):
                  ITM (< -0.02), ATM [-0.02, 0.02), OTM1 [0.02, 0.07), OTM2 [0.07, 0.15), DEEP (>= 0.15)
  DTE:            0-10, 11-21, 22-45, 46-80
The haircut for a fill is median(rel spread)/2 of its bucket; buckets with < 20 quotes fall
back to (premium, DTE), then (premium). Output: cache/spread_table.parquet + .md table.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

import odata as D
from farm_universe import FARM_UNIVERSE, PEAD_UNIVERSE

PREM_BINS = [0, 0.25, 0.5, 1, 2, 5, 1e9]
PREM_LBL = ["<0.25", "0.25-0.5", "0.5-1", "1-2", "2-5", ">=5"]
MNY_BINS = [-1e9, -0.02, 0.02, 0.07, 0.15, 1e9]
MNY_LBL = ["ITM", "ATM", "OTM1", "OTM2", "DEEP"]
DTE_BINS = [-1, 10, 21, 45, 1e9]
DTE_LBL = ["0-10", "11-21", "22-45", "46-80"]
OUT = D.CACHE / "spread_table.parquet"
RAW = D.CACHE / "snapshots_raw.parquet"


def pull() -> pd.DataFrame:
    if RAW.exists():
        return pd.read_parquet(RAW)
    today = dt.date(2026, 10, 9)
    rows = []
    for u in sorted(set(FARM_UNIVERSE) | set(PEAD_UNIVERSE)):
        spot = float(D.stock_daily(u, adjustment="raw")["close"].iloc[-1])
        token = None
        while True:
            params = {"feed": "indicative", "limit": 1000,
                      "expiration_date_lte": str(today + dt.timedelta(days=80)),
                      "strike_price_gte": round(spot * 0.65, 2), "strike_price_lte": round(spot * 1.35, 2)}
            if token:
                params["page_token"] = token
            d = D.alpaca_get(f"https://data.alpaca.markets/v1beta1/options/snapshots/{u}",
                             "alpaca_snapshots", params)
            for sym, s in (d.get("snapshots") or {}).items():
                q = s.get("latestQuote") or {}
                p = D.parse_occ(sym)
                if not p or not q:
                    continue
                rows.append((u, sym, p[1], p[2], p[3], spot, q.get("bp"), q.get("ap"), q.get("t"),
                             (s.get("dailyBar") or {}).get("v", 0)))
            token = d.get("next_page_token")
            if not token:
                break
    df = pd.DataFrame(rows, columns=["underlying", "symbol", "expiry", "cp", "strike", "spot", "bid", "ask",
                                     "quote_t", "day_vol"])
    df.to_parquet(RAW)
    return df


def bucketize(prem, mny, dte):
    return (pd.cut(np.asarray(prem, float), PREM_BINS, labels=PREM_LBL, right=False).astype(str),
            pd.cut(np.asarray(mny, float), MNY_BINS, labels=MNY_LBL, right=False).astype(str),
            pd.cut(np.asarray(dte, float), DTE_BINS, labels=DTE_LBL, right=True).astype(str))


def build() -> pd.DataFrame:
    df = pull()
    df["expiry"] = pd.to_datetime(df["expiry"])
    df["dte"] = (df.expiry - pd.Timestamp("2026-10-09")).dt.days
    df = df[(df.dte >= 0)]
    n_all = len(df)
    df["one_sided"] = (df.bid.fillna(0) <= 0) & (df.ask.fillna(0) > 0)
    two = df[(df.bid > 0) & (df.ask > df.bid)].copy()
    two["mid"] = (two.bid + two.ask) / 2
    two["rel"] = (two.ask - two.bid) / two.mid
    x = np.log(two.strike / two.spot)
    two["mny"] = np.where(two.cp == "C", x, -x)
    two["pb"], two["mb"], two["db"] = bucketize(two.mid, two.mny, two.dte)
    full = two.groupby(["pb", "mb", "db"]).rel.agg(["median", "count"]).reset_index()
    pd_ = two.groupby(["pb", "db"]).rel.agg(["median", "count"]).reset_index()
    p_ = two.groupby(["pb"]).rel.agg(["median", "count"]).reset_index()
    full.attrs = {}
    full.to_parquet(OUT)
    pd_.to_parquet(D.CACHE / "spread_table_pd.parquet")
    p_.to_parquet(D.CACHE / "spread_table_p.parquet")
    stats = {"quotes": n_all, "two_sided": len(two), "one_sided_bid0": int(df.one_sided.sum()),
             "underlyings": int(df.underlying.nunique()),
             "quote_time_max": str(pd.to_datetime(df.quote_t, errors="coerce").max())}
    pd.Series(stats).to_json(D.CACHE / "spread_stats.json")
    return two


class Haircut:
    """h(premium, moneyness, dte) = bucket median relative spread / 2, x stress multiplier."""

    def __init__(self, mult: float = 1.0):
        self.mult = mult
        f = pd.read_parquet(OUT); pdt = pd.read_parquet(D.CACHE / "spread_table_pd.parquet")
        p = pd.read_parquet(D.CACHE / "spread_table_p.parquet")
        self.full = {(r.pb, r.mb, r.db): r["median"] for _, r in f.iterrows() if r["count"] >= 20}
        self.pd = {(r.pb, r.db): r["median"] for _, r in pdt.iterrows() if r["count"] >= 20}
        self.p = {r.pb: r["median"] for _, r in p.iterrows()}
        self._memo = {}

    def __call__(self, prem: float, mny: float, dte: float) -> float:
        pb, mb, db = (str(v[0]) for v in bucketize([prem], [mny], [dte]))
        k = (pb, mb, db)
        if k in self._memo:
            return self._memo[k] * self.mult
        v = self.full.get(k)
        if v is None:
            v = self.pd.get((pb, db))
        if v is None:
            v = self.p.get(pb, 0.10)
        v = min(float(v) / 2.0, 0.5)
        self._memo[k] = v
        return v * self.mult


if __name__ == "__main__":
    two = build()
    t = pd.read_parquet(OUT)
    print(pd.read_json(D.CACHE / "spread_stats.json", typ="series"))
    piv = two.groupby(["pb", "db"]).rel.median().unstack().reindex(PREM_LBL)[DTE_LBL]
    print((piv * 50).round(1).to_string())    # half-spread in %
