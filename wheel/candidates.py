"""
candidates.py - the list the rotation rule ranks: every name in universe.py with its last price,
200-day average, one-year return and one-year volatility. Market data only, read-only.
Uses Alpaca daily bars when Alpaca keys are set, otherwise Yahoo through the yfinance package.

    python3 -m wheel.candidates        # print the top of the ranking

With Alpaca keys, each trend-passing name also gets "atm_iv": the implied volatility of the
at-the-money call about 35 days out (Alpaca indicative option snapshots), or None when unknown.
"""
import datetime as dt
import time
import json
import math
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request

import numpy as np

from . import config
from .brokers import alpaca as _alp
from .universe import ETFS, UNIVERSE


IV_MIN_COVERAGE = 0.9             # share of uptrend names that must have a premium level
IV_MAX = 5.0                      # an implied volatility above 500% is a bad quote, not a signal


def _row(sym, closes, day):
    c = np.asarray(closes, float)
    if len(c) < 60:
        return None
    lr = np.diff(np.log(c))
    return {"symbol": sym, "last": round(float(c[-1]), 2),
            "sma200": round(float(c[-200:].mean()), 2) if len(c) >= 200 else None,
            "ret_252": round(float(c[-1] / c[-253] - 1), 4) if len(c) >= 253 else None,
            "vol_252": round(float(lr[-252:].std() * np.sqrt(252)), 4) if len(lr) >= 252 else None,
            "last_bar": str(day), "etf": sym in ETFS, "atm_iv": None}


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


def _trend_ok(r):
    return r["last"] >= 10 and r["sma200"] is not None and r["last"] > r["sma200"]


def atm_iv_from_snapshots(snaps, last, today, target_days=35):
    """IV of the call nearest `last` in the expiry nearest target_days out; None if nothing usable."""
    best = {}
    for sym, s in (snaps or {}).items():
        try:
            _, exp, kind, strike = _alp.parse_occ(sym)
            iv = float((s or {}).get("impliedVolatility"))
            days = (dt.date.fromisoformat(exp) - today).days
        except (TypeError, ValueError, KeyError):
            continue
        if kind != "call" or not math.isfinite(iv) or iv <= 0 or iv > IV_MAX:
            continue
        best.setdefault(exp, []).append((strike, iv, days))
    if not best:
        return None
    exp = min(best, key=lambda e: (abs(best[e][0][2] - target_days), e))
    strike, iv, _ = min(best[exp], key=lambda t: (abs(t[0] - last), t[0]))
    return round(iv, 4)


def _alpaca_iv(rows, http=None, today=None):
    """Fill row["atm_iv"] for trend-passing rows, 8 requests at a time. Never raises."""
    today = today or dt.date.today()

    def one(r):
        for attempt in (0, 1):                                 # one retry: a rate limit or timeout is usually brief
            try:
                d = _alp._call("GET", f"{_alp.DATA}/v1beta1/options/snapshots/{r['symbol']}",
                               {"feed": "indicative", "type": "call",
                                "expiration_date_gte": str(today + dt.timedelta(days=21)),
                                "expiration_date_lte": str(today + dt.timedelta(days=50)),
                                "strike_price_gte": round(r["last"] * 0.9, 2),
                                "strike_price_lte": round(r["last"] * 1.1, 2), "limit": 1000}, http=http)
                return atm_iv_from_snapshots(d.get("snapshots") or {}, r["last"], today)
            except Exception:
                if attempt == 0 and http is None:
                    time.sleep(1.0)
        return None

    todo = [r for r in rows if _trend_ok(r)]
    with ThreadPoolExecutor(max_workers=8) as ex:
        for r, iv in zip(todo, ex.map(one, todo)):
            r["atm_iv"] = iv
    return rows, len(todo), sum(1 for r in todo if r["atm_iv"] is not None)


def build() -> dict:
    names = list(UNIVERSE)
    have_alpaca = bool(config.get("ALPACA_KEY") or config.get("ALPACA_PAPER_KEY"))
    bars, source = _alpaca(names) if have_alpaca else _yahoo(names)
    as_of = max((b[-1][0] for b in bars.values() if b), default="")
    rows = [r for r in (_row(s, [c for _, c in b], b[-1][0]) for s, b in bars.items() if b) if r and r["last_bar"] == as_of]
    wanted = got = 0
    if have_alpaca:
        _, wanted, got = _alpaca_iv(rows)
    # A ranking by premium over a partial list would call the wrong stock "highest premium". If too many
    # lookups failed, drop them all: the premium rule then proposes nothing instead of something wrong.
    incomplete = bool(wanted) and got < IV_MIN_COVERAGE * wanted
    if incomplete:
        for r in rows:
            r["atm_iv"] = None
    return {"candidates_as_of": as_of, "source": source, "candidates": sorted(rows, key=lambda r: r["symbol"]),
            "iv_source": "Alpaca indicative option snapshots" if have_alpaca else None,
            "iv_wanted": wanted, "iv_got": got, "iv_incomplete": incomplete}


if __name__ == "__main__":
    out = build()
    ok = [r for r in out["candidates"] if r["sma200"] and r["ret_252"] is not None and _trend_ok(r)]
    print(f"{len(out['candidates'])} names as of {out['candidates_as_of']} ({out['source']}); {len(ok)} pass the trend filter")
    for r in sorted(ok, key=lambda r: -r["ret_252"])[:10]:
        print(f"  {r['symbol']:6} {r['last']:>9.2f}  1y {r['ret_252']:+.0%}  200d {r['sma200']:.2f}")
    iv = [r for r in ok if r.get("atm_iv")]
    print(f"{len(iv)} of {len(ok)} have an ATM IV ({out['iv_source']}); top 10 by IV:")
    for r in sorted(iv, key=lambda r: -r["atm_iv"])[:10]:
        print(f"  {r['symbol']:6} {r['last']:>9.2f}  IV {r['atm_iv']:.1%}  1y {r['ret_252']:+.0%}")
