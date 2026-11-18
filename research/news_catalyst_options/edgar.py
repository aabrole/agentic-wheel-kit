"""
edgar.py
========
Corporate events with exact timestamps, from SEC EDGAR's free submissions API.

Why EDGAR and not a news vendor for the EVENT itself: a filing's
`acceptanceDateTime` is the second it became public. It cannot be revised, it
has no editorial delay, and it is the same for every market participant. That
makes it the right anchor for "what happened after the earnings came out".

  8-K item 2.02   results of operations (the earnings release)
  8-K item 7.01 / 8.01   regulation FD / other events (guidance, deals)
  Form 4         insider transactions (the "insider edge" stream; needs the
                 XML for transaction code P = open-market purchase)

Etiquette: EDGAR requires a descriptive User-Agent and asks for <=10 req/s.
Everything is cached to disk; a universe of 50 tickers is ~100 requests once.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

CACHE = Path(__file__).resolve().parent / "cache" / "edgar"
CACHE.mkdir(parents=True, exist_ok=True)
import os
if not os.environ.get("SEC_CONTACT"):
    raise SystemExit("Set SEC_CONTACT in .env (your name and a contact address). The SEC requires automated readers to identify themselves.")
UA = {"User-" + "Agent": os.environ["SEC_CONTACT"]}
_last = [0.0]


def _get(url: str) -> bytes:
    wait = 0.12 - (time.time() - _last[0])          # stay under 10 req/s
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read()


def cik_map() -> Dict[str, str]:
    p = CACHE / "company_tickers.json"
    if not p.exists() or time.time() - p.stat().st_mtime > 7 * 86400:
        p.write_bytes(_get("https://www.sec.gov/files/company_tickers.json"))
    raw = json.loads(p.read_text())
    return {v["ticker"].upper(): f"{int(v['cik_str']):010d}" for v in raw.values()}


def submissions(ticker: str, refresh: bool = False) -> pd.DataFrame:
    """Every filing for a ticker, newest first, with acceptance timestamp (UTC)."""
    cik = cik_map()[ticker.upper()]
    p = CACHE / f"sub_{cik}.json"
    if refresh or not p.exists() or time.time() - p.stat().st_mtime > 86400:
        p.write_bytes(_get(f"https://data.sec.gov/submissions/CIK{cik}.json"))
    d = json.loads(p.read_text())
    frames = [pd.DataFrame(d["filings"]["recent"])]
    for extra in d["filings"].get("files", []):        # older filings live in side files
        q = CACHE / extra["name"]
        if not q.exists():
            q.write_bytes(_get(f"https://data.sec.gov/submissions/{extra['name']}"))
        frames.append(pd.DataFrame(json.loads(q.read_text())))
    f = pd.concat(frames, ignore_index=True)
    f["accepted"] = pd.to_datetime(f["acceptanceDateTime"], utc=True)
    f["ticker"] = ticker.upper()
    f["cik"] = cik
    return f.sort_values("accepted", ascending=False).reset_index(drop=True)


def earnings_releases(ticker: str, start: Optional[str] = None) -> pd.DataFrame:
    """8-K filings carrying item 2.02, i.e. the earnings press release, with timestamps."""
    f = submissions(ticker)
    f = f[(f["form"] == "8-K") & f["items"].fillna("").str.contains("2.02")]
    if start:
        f = f[f["accepted"] >= pd.Timestamp(start, tz="UTC")]
    out = f[["ticker", "accepted", "items", "accessionNumber", "primaryDocument"]].copy()
    et = out["accepted"].dt.tz_convert("America/New_York")
    # when, relative to the cash session, the release became public
    out["release_slot"] = pd.cut(et.dt.hour + et.dt.minute / 60, [-1, 9.5, 16, 25],
                                 labels=["premarket", "intraday", "afterhours"]).astype(str)
    out["first_tradable_session"] = (et.dt.normalize() + pd.to_timedelta(
        (et.dt.hour + et.dt.minute / 60 >= 16).astype(int), unit="D")).dt.date
    return out.sort_values("accepted").reset_index(drop=True)


def insider_filings(ticker: str, start: Optional[str] = None) -> pd.DataFrame:
    """Form 4 filings (metadata only; transaction detail needs the XML)."""
    f = submissions(ticker)
    f = f[f["form"] == "4"]
    if start:
        f = f[f["accepted"] >= pd.Timestamp(start, tz="UTC")]
    return f[["ticker", "accepted", "accessionNumber", "primaryDocument", "cik"]].sort_values("accepted").reset_index(drop=True)


def universe_events(tickers: List[str], start: str, verbose: bool = True) -> pd.DataFrame:
    frames = []
    for i, t in enumerate(tickers, 1):
        try:
            e = earnings_releases(t, start)
        except Exception as ex:
            if verbose:
                print(f"[edgar] {t}: {type(ex).__name__} {str(ex)[:60]}")
            continue
        frames.append(e)
        if verbose:
            print(f"[edgar] {i}/{len(tickers)} {t}: {len(e)} earnings releases since {start}")
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    e = universe_events(["AAPL", "MSFT", "NVDA", "TSLA", "JPM"], "2023-01-01")
    print(f"\n{len(e)} events")
    print(e.release_slot.value_counts().to_string())
    print(e.head(6).to_string())
