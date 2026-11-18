"""
alpaca.py - Alpaca adapter. Standard library only (urllib).

Paper trading by default: orders go to paper-api.alpaca.markets and no real money moves.
Live trading needs ALPACA_LIVE=1 in .env on top of live keys.

Alpaca has no "approve on your phone" step. With this broker the Approve button on the
dashboard IS the approval, so keep the dashboard private.

What it cannot supply: earnings dates. Those come from wheel/earnings.py (Yahoo, if the
yfinance package is installed) or from state/earnings.json, which you can edit by hand. With no
date for a stock the engine refuses to propose a call on it.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request

from .. import config

DATA = "https://data.alpaca.markets"


def _base():
    return "https://api.alpaca.markets" if config.get("ALPACA_LIVE") == "1" else "https://paper-api.alpaca.markets"


def _headers():
    k, s = config.get("ALPACA_KEY") or config.get("ALPACA_PAPER_KEY"), config.get("ALPACA_SECRET") or config.get("ALPACA_PAPER_SECRET")
    if not k or not s:
        raise RuntimeError("Set ALPACA_KEY and ALPACA_SECRET in .env")
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s, "Content-Type": "application/json"}


def _call(method, url, params=None, body=None, http=None):
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    if http is not None:                                   # tests inject a fake transport
        return http(method, url, body)
    req = urllib.request.Request(url, method=method, headers=_headers(),
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Alpaca {method} {url.split('?')[0]} -> {e.code}: {e.read().decode()[:300]}") from None


def occ(symbol, expiry, kind, strike) -> str:
    d = dt.date.fromisoformat(str(expiry))
    return f"{symbol}{d:%y%m%d}{'C' if kind == 'call' else 'P'}{int(round(float(strike) * 1000)):08d}"


def parse_occ(s):
    root, tail = s[:-15], s[-15:]
    return root, f"20{tail[0:2]}-{tail[2:4]}-{tail[4:6]}", "call" if tail[6] == "C" else "put", int(tail[7:]) / 1000


def chain(symbol, today, max_days=60, http=None) -> list:
    """Call quotes for one stock out to max_days, in the engine's chain-row shape."""
    lo, hi = str(today), str(today + dt.timedelta(days=max_days))
    snaps, token = {}, None
    for _ in range(20):
        d = _call("GET", f"{DATA}/v1beta1/options/snapshots/{symbol}",
                  {"feed": "indicative", "type": "call", "expiration_date_gte": lo, "expiration_date_lte": hi,
                   "limit": 1000, "page_token": token}, http=http)
        snaps.update(d.get("snapshots") or {})
        token = d.get("next_page_token")
        if not token:
            break
    oi, token = {}, None
    for _ in range(20):
        d = _call("GET", f"{_base()}/v2/options/contracts",
                  {"underlying_symbols": symbol, "type": "call", "expiration_date_gte": lo,
                   "expiration_date_lte": hi, "limit": 1000, "page_token": token}, http=http)
        for c in d.get("option_contracts") or []:
            oi[c["symbol"]] = int(float(c.get("open_interest") or 0))
        token = d.get("next_page_token")
        if not token:
            break
    rows = []
    for sym, s in snaps.items():
        _, exp, kind, strike = parse_occ(sym)
        q, g = s.get("latestQuote") or {}, s.get("greeks") or {}
        rows.append({"type": kind, "expiry": exp, "strike": strike, "bid": q.get("bp") or 0, "ask": q.get("ap") or 0,
                     "delta": g.get("delta"), "open_interest": oi.get(sym, 0), "id": sym, "sell_floor_hint": None})
    return rows


def snapshot(extra_symbols=(), earnings=None, http=None) -> dict:
    acct = _call("GET", f"{_base()}/v2/account", http=http)
    pos = _call("GET", f"{_base()}/v2/positions", http=http)
    today = dt.date.today()
    positions, shorts, quotes = [], [], {}
    for p in pos:
        if p.get("asset_class") == "us_option":
            root, exp, kind, strike = parse_occ(p["symbol"])
            if float(p["qty"]) < 0:
                shorts.append({"symbol": root, "type": kind, "strike": strike, "expiry": exp,
                               "contracts": int(abs(float(p["qty"])))})
        else:
            positions.append({"symbol": p["symbol"], "shares": float(p["qty"]), "avg_cost": float(p["avg_entry_price"])})
            if p.get("current_price"):
                quotes[p["symbol"]] = float(p["current_price"])
    need = sorted({p["symbol"] for p in positions if p["shares"] >= 100} | {o["symbol"] for o in shorts} | set(extra_symbols))
    missing = [s for s in need if s not in quotes]
    if missing:
        d = _call("GET", f"{DATA}/v2/stocks/snapshots", {"symbols": ",".join(missing), "feed": "iex"}, http=http)
        for s, v in d.items():
            px = ((v or {}).get("latestTrade") or {}).get("p")
            if px:
                quotes[s] = float(px)
    chains = {s: chain(s, today, http=http) for s in need if s in quotes}
    return {"today": str(today), "broker": "alpaca" + ("" if config.get("ALPACA_LIVE") == "1" else " (paper)"),
            "account": {"cash": float(acct.get("cash") or 0), "options_level": int(acct.get("options_approved_level") or 0),
                        "approvals_on": True},          # the dashboard approval is the approval on Alpaca
            "positions": positions, "short_options": shorts, "quotes": quotes,
            "earnings": {s: (earnings or {}).get(s) for s in need}, "ex_dividend": {}, "chains": chains}


def _order_body(leg):
    if leg["instrument"] == "option":
        return {"symbol": leg.get("option_id") if str(leg.get("option_id", "")).startswith(leg["symbol"]) else occ(leg["symbol"], leg["expiry"], leg["type"], leg["strike"]),
                "qty": str(int(leg["contracts"])), "side": "sell" if leg["action"] == "SELL TO OPEN" else "buy",
                "type": "limit", "limit_price": f"{float(leg.get('limit_max') or leg['limit']):.2f}" if leg["action"] == "BUY TO CLOSE" else f"{float(leg['limit']):.2f}",
                "time_in_force": "day", "position_intent": "sell_to_open" if leg["action"] == "SELL TO OPEN" else "buy_to_close"}
    if leg.get("dollars"):
        return {"symbol": leg["symbol"], "notional": f"{float(leg['dollars']):.2f}", "side": leg["action"].lower(), "type": "market", "time_in_force": "day"}
    return {"symbol": leg["symbol"], "qty": str(leg["shares"]), "side": leg["action"].lower(), "type": "limit",
            "limit_price": f"{float(leg['limit']):.2f}", "time_in_force": "day"}


def send(order, http=None) -> dict:
    """Send the next unsent leg. A roll is two orders here: the buy-back goes first and the new
    call is only sent once the buy-back has filled (call send again after status says so)."""
    sent = order.get("broker_orders") or []
    i = len(sent)
    if i >= len(order["legs"]):
        return {"status": "sent", "broker_orders": sent}
    if i > 0 and sent[-1].get("state") != "filled":
        return {"status": "sent", "broker_orders": sent, "send_note": "Waiting for the first leg to fill before sending the next."}
    body = _order_body(order["legs"][i])
    body["client_order_id"] = f"wheel-{order['order_id']}-{i}"
    r = _call("POST", f"{_base()}/v2/orders", body=body, http=http)
    sent = sent + [{"id": r.get("id"), "leg": i, "state": r.get("status", "new")}]
    return {"status": "sent", "broker_orders": sent,
            "send_note": f"Sent to Alpaca{'' if config.get('ALPACA_LIVE') == '1' else ' (paper)'}: leg {i + 1} of {len(order['legs'])}."}


def status(order, http=None) -> dict:
    fills, out = [], []
    for b in order.get("broker_orders") or []:
        r = _call("GET", f"{_base()}/v2/orders/{b['id']}", http=http)
        st = r.get("status")
        state = "filled" if st == "filled" else ("cancelled" if st in ("canceled", "expired", "rejected") else "open")
        if state == "filled" and b.get("state") != "filled":
            fills.append({"leg": b["leg"], "price": float(r.get("filled_avg_price") or 0), "qty": float(r.get("filled_qty") or 0)})
        out.append({**b, "state": state})
    done = len(out) == len(order["legs"]) and all(b["state"] == "filled" for b in out)
    dead = any(b["state"] == "cancelled" for b in out)
    return {"status": "filled" if done else ("cancelled" if dead else "sent"), "broker_orders": out, "fills": fills}
