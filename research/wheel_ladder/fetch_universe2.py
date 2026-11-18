"""
fetch_universe2.py - download everything the expanded universe (universe2.py) needs.
Resumable: every step skips what is already cached, so it can be stopped and rerun.

  python3 fetch_universe2.py check       # are the Alpaca keys accepted? (no downloads)
  python3 fetch_universe2.py dividends   # Yahoo, no keys needed
  python3 fetch_universe2.py earnings    # SEC EDGAR 8-K item 2.02, no Alpaca keys needed.
                                         # EDGAR requires a contact User-Agent; this reuses
                                         # news_catalyst_options/edgar.py; set SEC_CONTACT in .env.
  python3 fetch_universe2.py stock       # Alpaca: daily bars, splits, contract lists
  python3 fetch_universe2.py estimate    # Alpaca: how many option-bar requests the cube needs
  python3 fetch_universe2.py cube [sec]  # Alpaca: option bars (hours; optional time limit)
  python3 fetch_universe2.py status      # what is on disk per name

New-name earnings and dividends go to wheel_ladder/cache/*2.* so the files the earlier
studies read (options_farm/cache/earnings_events.parquet, cache/dividends.csv) never change.
"""
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "options_farm"))

import universe2 as U2  # noqa: E402

CACHE2 = HERE / "cache"
DIV2 = CACHE2 / "dividends2.csv"
EARN2 = CACHE2 / "earnings_events2.parquet"
STOCKS = [t for t in U2.UNIVERSE2 if t not in U2.ETFS2]
ALL = sorted(set(U2.UNIVERSE2) | {t for t in U2.FARM if t not in U2.NO_HISTORY})   # a fresh clone has no data at all


def check():
    import urllib.request
    import env
    import os
    env.load_env()
    req = urllib.request.Request("https://paper-api.alpaca.markets/v2/account", headers={
        "APCA-API-KEY-ID": os.environ["ALPACA_PAPER_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_PAPER_SECRET"]})
    try:
        urllib.request.urlopen(req, timeout=15)
        print("Alpaca keys accepted")
        return True
    except Exception as e:
        print("Alpaca keys REJECTED:", e)
        return False


def dividends():
    import warnings
    import yfinance as yf
    warnings.filterwarnings("ignore")
    px = yf.download(U2.UNIVERSE2, start="2024-01-01", end="2026-10-10", auto_adjust=False,
                     actions=True, progress=False, threads=8)["Dividends"]
    rows = [{"name": t, "ex_date": d.date(), "amount": float(a)}
            for t in U2.UNIVERSE2 for d, a in px[t].dropna().items() if a > 0]
    pd.DataFrame(rows).to_csv(DIV2, index=False)
    print(f"dividends: {len(rows)} rows, {len({r['name'] for r in rows})} names -> {DIV2}")


def earnings():
    sys.path.insert(0, str(HERE.parent / "news_catalyst_options"))
    import edgar
    import events as EV
    import odata as D
    sessions = D.stock_daily("SPY", adjustment="raw").index
    rows, none = [], []
    for t in STOCKS:
        try:
            e = edgar.earnings_releases(t, "2023-06-01")
        except Exception as ex:
            none.append(t)
            print(f"[edgar] {t}: no events ({type(ex).__name__})")
            continue
        if e.empty:
            none.append(t)
            continue
        e["accepted"] = pd.to_datetime(e["accepted"], utc=True)
        e["entry_date"] = EV.first_session(e["accepted"], sessions)
        rows.append(e[["ticker", "accepted", "release_slot", "entry_date"]])
    ev = pd.concat(rows, ignore_index=True).sort_values("accepted").drop_duplicates(["ticker", "entry_date"])
    ev.reset_index(drop=True).to_parquet(EARN2)
    print(f"earnings: {ev.ticker.nunique()} names with dates, {len(none)} without: {none}")


def stock():
    import odata as D
    splits = {}
    for s in ALL:
        D.stock_daily(s, adjustment="raw")
        D.stock_daily(s, adjustment="split")
        sd = D.split_dates(s)
        if sd:
            splits[s] = [str(x.date()) for x in sd]
        c = D.list_contracts(s, "2024-01-18", "2026-12-31")
        print(s, len(c), c.expiry.nunique() if len(c) else 0, flush=True)
    (CACHE2 / "splits2.json").write_text(json.dumps(splits, indent=1))
    print("splits seen by Alpaca (should be empty):", splits)
    print(D.flush_calls())


def estimate():
    import numpy as np
    import cube
    tot = 0
    for u in ALL:
        pl = cube.plan(u)
        if pl.empty:
            print(u, "no contracts")
            continue
        req = int(np.ceil(pl.groupby("expiry").size() / 100).sum())
        tot += req
        print(u, len(pl), f"~{req} req")
    print(f"total ~{tot} requests, ~{tot / 180 / 60:.1f} hours at 180 a minute")


def build(limit):
    import cube
    cube.build(ALL, time_limit=limit)


def status():
    import cube
    import odata as D
    have = {}
    for f in cube.BARS.glob("*.parquet"):
        have[f.name.rsplit("_", 1)[0]] = have.get(f.name.rsplit("_", 1)[0], 0) + 1
    stock_ok = [t for t in ALL if list((D.CACHE / "stock").glob(f"{t}_raw_*"))]
    cube_ok = [t for t in ALL if have.get(t)]
    print(f"names {len(ALL)}: stock bars {len(stock_ok)}, option bars {len(cube_ok)}")
    print("dividends2:", DIV2.exists(), " earnings2:", EARN2.exists())
    print("missing option bars:", [t for t in ALL if t not in cube_ok][:40])


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "status"
    if what == "check":
        check()
    elif what == "dividends":
        dividends()
    elif what == "earnings":
        earnings()
    elif what in ("stock", "estimate", "cube"):
        if not check():
            sys.exit(1)
        {"stock": stock, "estimate": estimate}.get(what, lambda: build(float(sys.argv[2]) if len(sys.argv) > 2 else 1e9))()
    else:
        status()
