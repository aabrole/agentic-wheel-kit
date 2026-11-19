"""candidates.py ATM IV helpers: pure function plus the threaded fetch against a fake transport."""
import datetime as dt
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wheel import candidates as C  # noqa: E402
from wheel.brokers import alpaca  # noqa: E402

TODAY = dt.date(2026, 11, 18)


def occ(sym, days, kind, strike):
    return alpaca.occ(sym, TODAY + dt.timedelta(days=days), kind, strike)


def test_picks_nearest_expiry_and_strike():
    snaps = {occ("X", 25, "call", 100): {"impliedVolatility": 0.9},
             occ("X", 38, "call", 95): {"impliedVolatility": 0.5},
             occ("X", 38, "call", 101): {"impliedVolatility": 0.62},
             occ("X", 38, "call", 110): {"impliedVolatility": 0.7}}
    assert C.atm_iv_from_snapshots(snaps, 100.0, TODAY) == 0.62


def test_ties_prefer_earlier_expiry_and_lower_strike():
    snaps = {occ("X", 30, "call", 100): {"impliedVolatility": 0.3},
             occ("X", 40, "call", 100): {"impliedVolatility": 0.4},
             occ("X", 30, "call", 90): {"impliedVolatility": 0.1},
             occ("X", 30, "call", 110): {"impliedVolatility": 0.2}}
    assert C.atm_iv_from_snapshots(snaps, 100.0, TODAY) == 0.3
    snaps = {k: v for k, v in snaps.items() if "00090000" in k or "00110000" in k}
    assert C.atm_iv_from_snapshots(snaps, 100.0, TODAY) == 0.1


def test_ignores_bad_iv_and_puts():
    snaps = {occ("X", 35, "call", 100): {"impliedVolatility": 0},
             occ("X", 35, "call", 101): {},
             occ("X", 35, "call", 102): {"impliedVolatility": float("nan")},
             occ("X", 35, "put", 100): {"impliedVolatility": 0.8},
             occ("X", 45, "call", 120): {"impliedVolatility": 0.55}}
    assert C.atm_iv_from_snapshots(snaps, 100.0, TODAY) == 0.55
    assert C.atm_iv_from_snapshots({k: v for k, v in snaps.items() if "00120000" not in k}, 100.0, TODAY) is None


def test_empty_is_none():
    assert C.atm_iv_from_snapshots({}, 100.0, TODAY) is None


def _row(sym, last, sma):
    return {"symbol": sym, "last": last, "sma200": sma, "atm_iv": None}


def test_alpaca_iv_only_trend_rows_and_errors_isolated():
    rows = [_row("UP", 50.0, 40.0), _row("DOWN", 30.0, 40.0), _row("PENNY", 5.0, 1.0),
            _row("NOSMA", 50.0, None), _row("BAD", 20.0, 10.0), _row("UP2", 80.0, 70.0)]
    seen = []

    def http(method, url, body):
        path = urllib.parse.urlparse(url)
        sym = path.path.rsplit("/", 1)[1]
        q = urllib.parse.parse_qs(path.query)
        seen.append(sym)
        assert q["feed"] == ["indicative"] and q["type"] == ["call"] and q["limit"] == ["1000"]
        assert q["expiration_date_gte"] == [str(TODAY + dt.timedelta(days=21))]
        if sym == "BAD":
            raise RuntimeError("boom")
        last = {"UP": 50, "UP2": 80}[sym]
        return {"snapshots": {occ(sym, 35, "call", last): {"impliedVolatility": 0.61234}}}

    C._alpaca_iv(rows, http=http, today=TODAY)
    assert sorted(set(seen)) == ["BAD", "UP", "UP2"] and seen.count("BAD") == 2   # one retry
    iv = {r["symbol"]: r["atm_iv"] for r in rows}
    assert iv == {"UP": 0.6123, "DOWN": None, "PENNY": None, "NOSMA": None, "BAD": None, "UP2": 0.6123}


def test_bad_quote_iv_is_ignored():
    snaps = {C._alp.occ("ABC", str(TODAY + dt.timedelta(days=35)), "call", 100): {"impliedVolatility": 50.0}}
    assert C.atm_iv_from_snapshots(snaps, 100.0, TODAY) is None


def test_partial_premium_pull_is_dropped(monkeypatch):
    bars = {s: [(f"d{i}", 50.0 + i * 0.1) for i in range(300)] for s in ("AAA", "BBB", "CCC")}
    monkeypatch.setattr(C, "_alpaca", lambda names: (bars, "fake"))
    monkeypatch.setattr(C.config, "get", lambda k, d=None: "x" if k == "ALPACA_KEY" else None)

    def fake_iv(rows):
        rows[0]["atm_iv"] = 0.5                                # 1 of 3 lookups worked
        return rows, 3, 1
    monkeypatch.setattr(C, "_alpaca_iv", fake_iv)
    out = C.build()
    assert out["iv_incomplete"] and all(r["atm_iv"] is None for r in out["candidates"])
