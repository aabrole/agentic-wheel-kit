"""
odata.py - option + underlying data for the farm, everything cached under cache/.

Sources (verified 2026-10-11):
  Alpaca  /v2/options/contracts?status=inactive|active   contract discovery (expired too,
          expirations from Jan 2024 on)
  Alpaca  /v1beta1/options/bars                          daily bars, from 2024-01-18,
          100 symbols per request, 200 req/min
  Alpaca  /v1beta1/options/snapshots/{u}?feed=indicative live bid/ask (OPRA not signed)
  Alpaca  SIP stock daily bars (raw AND split-adjusted)
  Massive /v2/aggs (trailing 2 years only, 5 req/min) - used only as an independent
          cross-check in the trade audit.

API calls are counted per endpoint in cache/api_calls.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import env  # noqa: F401  (loads keys, never prints them)

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
CACHE.mkdir(exist_ok=True)
(CACHE / "contracts").mkdir(exist_ok=True)
(CACHE / "bars").mkdir(exist_ok=True)
(CACHE / "stock").mkdir(exist_ok=True)
(CACHE / "massive").mkdir(exist_ok=True)
ET = "America/New_York"

_lock = threading.Lock()
_calls: Dict[str, int] = {}
_stamps: List[float] = []
_CALLS_FILE = CACHE / "api_calls.json"
if _CALLS_FILE.exists():
    _calls.update(json.loads(_CALLS_FILE.read_text()))


def _alpaca_headers() -> Dict[str, str]:
    return {"APCA-API-KEY-ID": os.environ["ALPACA_PAPER_KEY"],
            "APCA-API-SECRET-KEY": os.environ["ALPACA_PAPER_SECRET"]}


def _count(tag: str) -> None:
    with _lock:
        _calls[tag] = _calls.get(tag, 0) + 1
        if sum(_calls.values()) % 50 == 0:
            _CALLS_FILE.write_text(json.dumps(_calls, indent=1))


def flush_calls() -> Dict[str, int]:
    with _lock:
        _CALLS_FILE.write_text(json.dumps(_calls, indent=1))
        return dict(_calls)


def _throttle(per_min: int = 180) -> None:
    while True:
        with _lock:
            now = time.time()
            while _stamps and now - _stamps[0] > 60:
                _stamps.pop(0)
            if len(_stamps) < per_min:
                _stamps.append(now)
                return
            wait = 60 - (now - _stamps[0]) + 0.05
        time.sleep(max(wait, 0.05))


def alpaca_get(url: str, tag: str, params: Optional[dict] = None, retries: int = 6) -> dict:
    if params:
        url = url + "?" + urllib.parse.urlencode(params, safe=",")
    for k in range(retries):
        _throttle()
        _count(tag)
        try:
            req = urllib.request.Request(url, headers=_alpaca_headers())
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (k + 1))
                continue
            raise RuntimeError(f"HTTP {e.code} on {tag}: {e.read()[:200]!r}")
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(2 * (k + 1))
    raise RuntimeError(f"giving up on {tag}")


# --------------------------------------------------------------------------- contracts

def list_contracts(underlying: str, exp_gte: str, exp_lte: str) -> pd.DataFrame:
    """All contracts (expired + live) for an underlying with expiry in range, cached."""
    p = CACHE / "contracts" / f"{underlying}_{exp_gte}_{exp_lte}.parquet"
    if p.exists():
        return pd.read_parquet(p)
    rows = []
    for status in ("inactive", "active"):
        token = None
        while True:
            params = {"underlying_symbols": underlying, "status": status,
                      "expiration_date_gte": exp_gte, "expiration_date_lte": exp_lte, "limit": 10000}
            if token:
                params["page_token"] = token
            d = alpaca_get("https://paper-api.alpaca.markets/v2/options/contracts", "alpaca_contracts", params)
            for c in d.get("option_contracts", []):
                rows.append((c["symbol"], c["underlying_symbol"], c["root_symbol"], c["expiration_date"],
                             float(c["strike_price"]), c["type"][0].upper(), float(c.get("size") or 100),
                             status))
            token = d.get("next_page_token")
            if not token:
                break
    df = pd.DataFrame(rows, columns=["symbol", "underlying", "root", "expiry", "strike", "cp", "size", "status"])
    df = df.drop_duplicates("symbol")
    df["expiry"] = pd.to_datetime(df["expiry"])
    df.to_parquet(p)
    return df


# --------------------------------------------------------------------------- option bars

def fetch_option_bars(symbols: List[str], start: str, end: str) -> pd.DataFrame:
    """Daily bars for up to N symbols, chunked 100 per request, paginated. Not cached here."""
    out = []
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        token = None
        while True:
            params = {"symbols": ",".join(chunk), "timeframe": "1Day", "start": start, "end": end,
                      "limit": 10000}
            if token:
                params["page_token"] = token
            d = alpaca_get("https://data.alpaca.markets/v1beta1/options/bars", "alpaca_option_bars", params)
            for sym, bars in (d.get("bars") or {}).items():
                for b in bars:
                    out.append((sym, b["t"][:10], b["o"], b["h"], b["l"], b["c"], b["v"], b.get("n", 0)))
            token = d.get("next_page_token")
            if not token:
                break
    df = pd.DataFrame(out, columns=["symbol", "date", "o", "h", "l", "c", "v", "n"])
    df["date"] = pd.to_datetime(df["date"])
    return df


# --------------------------------------------------------------------------- stock bars

def stock_daily(symbol: str, start: str = "2022-12-01", end: str = "2026-10-10", adjustment: str = "raw") -> pd.DataFrame:
    """Daily SIP bars, tz-naive date index. adjustment raw|split."""
    p = CACHE / "stock" / f"{symbol}_{adjustment}_{start}_{end}.parquet"
    if p.exists():
        return pd.read_parquet(p)
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed, Adjustment
    cl = StockHistoricalDataClient(os.environ["ALPACA_PAPER_KEY"], os.environ["ALPACA_PAPER_SECRET"])
    req = StockBarsRequest(symbol_or_symbols=[symbol], timeframe=TimeFrame.Day,
                           start=pd.Timestamp(start).to_pydatetime(), end=pd.Timestamp(end).to_pydatetime(),
                           feed=DataFeed.SIP, adjustment=Adjustment.RAW if adjustment == "raw" else Adjustment.SPLIT)
    _throttle(); _count("alpaca_stock_bars")
    raw = cl.get_stock_bars(req).df
    df = raw.xs(symbol, level="symbol") if isinstance(raw.index, pd.MultiIndex) else raw
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET).tz_localize(None).normalize()
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df.to_parquet(p)
    return df


def split_dates(symbol: str) -> List[pd.Timestamp]:
    """Sessions where the raw/adjusted close ratio jumps (a split took effect)."""
    r = stock_daily(symbol, adjustment="raw")["close"]
    a = stock_daily(symbol, adjustment="split")["close"]
    ratio = (r / a).dropna()
    ch = ratio.pct_change().abs()
    return list(ch[ch > 0.05].index)


# --------------------------------------------------------------------------- massive (audit only)

def massive_aggs(occ: str, start: str, end: str) -> Optional[pd.DataFrame]:
    p = CACHE / "massive" / f"{occ}_{start}_{end}.json"
    if p.exists():
        d = json.loads(p.read_text())
    else:
        url = (f"https://api.massive.com/v2/aggs/ticker/O:{occ}/range/1/day/{start}/{end}"
               f"?adjusted=false&limit=500&apiKey={os.environ['MASSIVE_KEY']}")
        d = None
        for k in range(4):
            _count("massive_aggs")
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    d = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(13)
                    continue
                d = {"status": f"HTTP{e.code}", "results": []}
                break
        if d is None:
            return None
        p.write_text(json.dumps(d))
        time.sleep(12.5)          # free plan: 5 req/min
    res = d.get("results") or []
    if not res:
        return None
    df = pd.DataFrame(res)
    df["date"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(ET).dt.tz_localize(None).dt.normalize()
    return df.set_index("date")[["o", "h", "l", "c", "v"]]


def parse_occ(sym: str):
    import re
    m = re.match(r"^([A-Z]+\d?)(\d{6})([CP])(\d{8})$", sym)
    if not m:
        return None
    root, ymd, cp, k = m.groups()
    return root, dt.date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6])), cp, int(k) / 1000.0
