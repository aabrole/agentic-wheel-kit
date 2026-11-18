"""Earnings events from EDGAR 8-K item 2.02 (reuses news_catalyst_options/edgar.py and its cache).
Foreign filers (BABA, NIO) file 6-K, so they have no events here; ETFs have none."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "news_catalyst_options"))
CACHE = Path(__file__).resolve().parent / "cache" / "earnings_events.parquet"


def first_session(accepted_utc: pd.Series, idx: pd.DatetimeIndex) -> pd.Series:
    """Same rule as swing_study.first_session: same day if accepted before 09:30 ET, else next."""
    et = accepted_utc.dt.tz_convert("America/New_York")
    same = (et.dt.hour * 60 + et.dt.minute) < 9 * 60 + 30
    day = et.dt.tz_localize(None).dt.normalize()
    anchor = day.where(same, day + pd.Timedelta(days=1))
    pos = np.clip(idx.searchsorted(anchor.values), 0, len(idx) - 1)
    return pd.Series(idx[pos], index=accepted_utc.index)


def load_events(tickers, sessions: pd.DatetimeIndex, start="2023-06-01") -> pd.DataFrame:
    if CACHE.exists():
        ev = pd.read_parquet(CACHE)
        if set(tickers) <= set(ev.attrs.get("tickers", ev.ticker.unique())) | set(ev.ticker.unique()):
            return ev[ev.ticker.isin(tickers)]
    import edgar
    rows = []
    for t in tickers:
        try:
            e = edgar.earnings_releases(t, start)
        except Exception as ex:
            print(f"[edgar] {t}: no events ({type(ex).__name__})")
            continue
        if e.empty:
            print(f"[edgar] {t}: 0 8-K 2.02 filings")
            continue
        e["accepted"] = pd.to_datetime(e["accepted"], utc=True)
        e["entry_date"] = first_session(e["accepted"], sessions)       # first session after release
        rows.append(e[["ticker", "accepted", "release_slot", "entry_date"]])
    ev = pd.concat(rows, ignore_index=True)
    # drop duplicate releases mapped to the same session (amended 8-Ks etc.)
    ev = ev.sort_values("accepted").drop_duplicates(["ticker", "entry_date"]).reset_index(drop=True)
    ev.to_parquet(CACHE)
    return ev
