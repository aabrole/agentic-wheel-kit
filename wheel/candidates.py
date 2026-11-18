"""
candidates.py - the list the rotation rule ranks: every name in universe.py with its last price,
200-day average, one-year return and one-year volatility. Market data only, read-only.
Uses Alpaca daily bars when Alpaca keys are set, otherwise Yahoo through the yfinance package.

    python3 -m wheel.candidates        # print the top of the ranking
"""
import datetime as dt
import json
import urllib.parse
import urllib.request

import numpy as np

from . import config
from .universe import ETFS, UNIVERSE


def _row(sym, closes, day):
    c = np.asarray(closes, float)
    if len(c) < 60:
        return None
    lr = np.diff(np.log(c))
    return {"symbol": sym, "last": round(float(c[-1]), 2),
            "sma200": round(float(c[-200:].mean()), 2) if len(c) >= 200 else None,
            "ret_252": round(float(c[-1] / c[-253] - 1), 4) if len(c) >= 253 else None,
            "vol_252": round(float(lr[-252:].std() * np.sqrt(252)), 4) if len(lr) >= 252 else None,
            "last_bar": str(day), "etf": sym in ETFS}


def _alpaca(names):
    k, s = config.get("ALPACA_KEY") or config.get("ALPACA_PAPER_KEY"), config.get("ALPACA_SECRET") or config.get("ALPACA_PAPER_SECRET")
    end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)
    start = end - dt.timedelta(days=560)
    bars = {}
    for i in range(0, len(names), 50):
        token = None
        while True:
            q = {"symbols": ",".join(names[i:i + 50]), "timeframe": "1Day", "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "adjustment": "split", "feed": "iex", "limit": 10000}
            if token:
                q["page_token"] = token
            req = urllib.request.Request("https://data.alpaca.markets/v2/stocks/bars?" + urllib.parse.urlencode(q),
                                         headers={"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s})
            d = json.loads(urllib.request.urlopen(req, timeout=60).read())
            for sym, rows in (d.get("bars") or {}).items():
                bars.setdefault(sym, []).extend((r["t"][:10], r["c"]) for r in rows)
            token = d.get("next_page_token")
            if not token:
                break
    return bars, "Alpaca daily bars (IEX feed), split-adjusted"


def _yahoo(names):
    import yfinance as yf
    px = yf.download(names, period="3y", auto_adjust=False, progress=False, threads=8)["Close"]
    return {s: [(str(d.date()), float(v)) for d, v in px[s].dropna().items()] for s in names if s in px}, "Yahoo daily closes"


def build() -> dict:
    names = list(UNIVERSE)
    have_alpaca = bool(config.get("ALPACA_KEY") or config.get("ALPACA_PAPER_KEY"))
    bars, source = _alpaca(names) if have_alpaca else _yahoo(names)
    as_of = max((b[-1][0] for b in bars.values() if b), default="")
    rows = [r for r in (_row(s, [c for _, c in b], b[-1][0]) for s, b in bars.items() if b) if r and r["last_bar"] == as_of]
    return {"candidates_as_of": as_of, "source": source, "candidates": sorted(rows, key=lambda r: r["symbol"])}


if __name__ == "__main__":
    out = build()
    ok = [r for r in out["candidates"] if r["sma200"] and r["ret_252"] is not None and r["last"] >= 10 and r["last"] > r["sma200"]]
    print(f"{len(out['candidates'])} names as of {out['candidates_as_of']} ({out['source']}); {len(ok)} pass the trend filter")
    for r in sorted(ok, key=lambda r: -r["ret_252"])[:10]:
        print(f"  {r['symbol']:6} {r['last']:>9.2f}  1y {r['ret_252']:+.0%}  200d {r['sma200']:.2f}")
