"""
earnings.py - next earnings date per stock, for brokers that do not supply one (Alpaca).
Order of trust: state/earnings.json (edit it by hand: {"HOOD": "2027-02-09"}), then Yahoo through
the optional yfinance package. No date means the engine will not propose a call on that stock.
Yahoo dates can be estimates; check a date before a call that would span it.
"""
import datetime as dt
import json
from pathlib import Path

from . import config

FILE = Path(config.get("WHEEL_STATE_DIR", str(config.ROOT / "state"))) / "earnings.json"


def lookup(symbols, etfs=()) -> dict:
    saved = json.loads(FILE.read_text()) if FILE.exists() else {}
    today, out = dt.date.today(), {}
    for s in symbols:
        if s in etfs:
            continue
        d = saved.get(s)
        if d and dt.date.fromisoformat(d) >= today:
            out[s] = d
            continue
        try:
            import yfinance as yf
            cal = yf.Ticker(s).calendar or {}
            dates = [x for x in (cal.get("Earnings Date") or []) if x >= today]
            out[s] = str(min(dates)) if dates else None
        except Exception:
            out[s] = None
    return out
