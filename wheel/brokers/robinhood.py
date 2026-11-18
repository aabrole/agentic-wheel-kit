"""
robinhood.py - Robinhood agent-account adapter, over Robinhood's official agent connection.

Connect once with `python3 -m wheel.agent connect` (sign in on Robinhood's own page, pick the
account the agent may use). Tokens stay in state/robinhood/ on this machine.

The engine refuses to propose anything unless trade approvals are ON for the account, and this
adapter never tries to change that setting. Every order it sends lands in the Robinhood app for
the owner to approve or decline.

STATUS: written from the tool responses Robinhood returned on 2026-11-18 and built on the
connection code that ran the author's stock bot. The option order path has NOT yet been run end
to end from this kit. Test with one small order and approvals ON before relying on it.
"""
from __future__ import annotations

import asyncio
import datetime as dt

from . import rh_client as rh


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


async def _chain(s, symbol, today, max_days=60):
    ch = (await rh.call(s, "get_option_chains", {"underlying_symbol": symbol})).get("chains") or []
    rows = []
    for c in ch:
        exps = [e for e in c.get("expiration_dates") or [] if 0 < (dt.date.fromisoformat(e) - today).days <= max_days]
        if not exps:
            continue
        ins = await rh.pages(s, "get_option_instruments", {"chain_id": c["id"], "expiration_dates": ",".join(exps),
                                                           "type": "call", "tradability": "tradable"}, "instruments")
        meta = {i["id"]: i for i in ins}
        ids = list(meta)
        for k in range(0, len(ids), 20):
            res = (await rh.call(s, "get_option_quotes", {"instrument_ids": ids[k:k + 20]})).get("results") or []
            for r in res:
                q = r.get("quote") or {}
                m = meta.get(q.get("instrument_id"))
                if not m:
                    continue
                rows.append({"type": m["type"], "expiry": m["expiration_date"], "strike": _f(m["strike_price"]),
                             "bid": _f(q.get("bid_price")), "ask": _f(q.get("ask_price")),
                             "delta": _f(q.get("delta"), None) if q.get("delta") is not None else None,
                             "open_interest": int(q.get("open_interest") or 0), "id": m["id"],
                             "sell_floor_hint": _f(q.get("high_fill_rate_sell_price"), None) if q.get("high_fill_rate_sell_price") else None})
    return rows


async def _snapshot(extra_symbols):
    today = dt.date.today()
    async with rh.session() as s:
        a = rh.bound_account((await rh.call(s, "get_accounts", {}))["accounts"])
        acct = a["account_number"]
        approval = (await rh.call(s, "get_trade_approval_setting", {"account_number": acct}))["setting"]
        port = await rh.call(s, "get_portfolio", {"account_number": acct})
        eq = await rh.pages(s, "get_equity_positions", {"account_number": acct}, "positions")
        op = await rh.pages(s, "get_option_positions", {"account_number": acct, "nonzero": True}, "positions")
        positions = [{"symbol": p["symbol"], "shares": _f(p.get("quantity")), "avg_cost": _f(p.get("average_buy_price"))}
                     for p in eq if _f(p.get("quantity")) > 0 and _f(p.get("average_buy_price")) > 0]
        shorts = []
        short_ids = [p.get("option_id") for p in op if p.get("type") == "short" and p.get("option_id")]
        if short_ids:
            ins = await rh.pages(s, "get_option_instruments", {"ids": ",".join(short_ids)}, "instruments")
            meta = {i["id"]: i for i in ins}
            for p in op:
                m = meta.get(p.get("option_id"))
                if m and p.get("type") == "short":
                    shorts.append({"symbol": m["chain_symbol"], "type": m["type"], "strike": _f(m["strike_price"]),
                                   "expiry": m["expiration_date"], "contracts": int(abs(_f(p.get("quantity"))))})
        need = sorted({p["symbol"] for p in positions} | {o["symbol"] for o in shorts} | set(extra_symbols))
        quotes = {}
        for k in range(0, len(need), 20):
            q = await rh.call(s, "get_equity_quotes", {"symbols": need[k:k + 20]})
            for r in q.get("results") or []:
                x = r.get("quote") or {}
                if x.get("symbol") and x.get("last_trade_price"):
                    quotes[x["symbol"]] = _f(x["last_trade_price"])
        lots = sorted({p["symbol"] for p in positions if p["shares"] >= 100} | {o["symbol"] for o in shorts})
        chains, earnings = {}, {}
        for sym in lots:
            chains[sym] = await _chain(s, sym, today)
            res = (await rh.call(s, "get_earnings_results", {"symbol": sym})).get("results") or []
            nxt = [r["report"]["date"] for r in res if (r.get("eps") or {}).get("actual") is None and (r.get("report") or {}).get("date")]
            earnings[sym] = min(nxt) if nxt else None
    return {"today": str(today), "broker": "robinhood", "account_last4": acct[-4:],
            "account": {"cash": _f(port.get("cash")), "options_level": int(str(a.get("option_level") or "0")[-1] or 0)
                        if str(a.get("option_level") or "").startswith("option_level_") else 0,
                        "approvals_on": approval.get("human_must_approve_trades") is True},
            "positions": positions, "short_options": shorts, "quotes": quotes, "earnings": earnings,
            "ex_dividend": {}, "chains": chains}


def snapshot(extra_symbols=(), earnings=None, http=None) -> dict:
    return asyncio.run(_snapshot(extra_symbols))


def _option_args(acct, order):
    legs = order["legs"]
    rl = [{"option_id": l["option_id"], "side": "sell" if l["action"] == "SELL TO OPEN" else "buy",
           "position_effect": "open" if l["action"] == "SELL TO OPEN" else "close"} for l in legs]
    if any(not l.get("option_id") for l in legs):
        raise ValueError("This order has no Robinhood contract id (it was edited or entered by hand). "
                         "Refresh the account and approve the engine's own proposal, or place it in the app.")
    args = {"account_number": acct, "legs": rl, "quantity": str(int(legs[0]["contracts"])), "type": "limit", "time_in_force": "gfd"}
    if len(legs) == 1:
        args["price"] = f"{float(legs[0]['limit']):.2f}"
    else:                                                   # a roll: one two-leg order for a net credit
        net = float(legs[1]["limit"]) - float(legs[0]["limit"])
        if net <= 0:
            raise ValueError("Roll is not a net credit at the limits; not sending.")
        args.update({"direction": "credit", "price": f"{net:.2f}"})
    return args


async def _send(order):
    async with rh.session() as s:
        a = rh.bound_account((await rh.call(s, "get_accounts", {}))["accounts"])
        acct = a["account_number"]
        setting = (await rh.call(s, "get_trade_approval_setting", {"account_number": acct}))["setting"]
        if setting.get("human_must_approve_trades") is not True:
            return {"status": "queued", "send_note": "NOT SENT: trade approvals are off on the Robinhood account. Turn them on in the app."}
        ref = f"wheel-{order['order_id']}"
        if order["legs"][0]["instrument"] == "option":
            args = _option_args(acct, order)
            review = await rh.call(s, "review_option_order", args, allow_orders=True)
            if review.get("order_checks"):
                return {"status": "queued", "send_note": "NOT SENT: Robinhood's review raised: " + str(review["order_checks"])[:400]}
            placed = await rh.call(s, "place_option_order", {**args, "ref_id": ref}, allow_orders=True)
        else:
            leg = order["legs"][0]
            args = {"account_number": acct, "symbol": leg["symbol"], "side": leg["action"].lower(),
                    "market_hours": "regular_hours", "time_in_force": "gfd"}
            args.update({"type": "market", "dollar_amount": f"{float(leg['dollars']):.2f}"} if leg.get("dollars")
                        else {"type": "limit", "quantity": str(int(float(leg["shares"]))), "limit_price": f"{float(leg['limit']):.2f}"})
            review = await rh.call(s, "review_equity_order", args, allow_orders=True)
            if review.get("order_checks"):
                return {"status": "queued", "send_note": "NOT SENT: Robinhood's review raised: " + str(review["order_checks"])[:400]}
            placed = await rh.call(s, "place_equity_order", {**args, "ref_id": ref}, allow_orders=True)
    return {"status": "sent", "broker_orders": [{"id": ref, "leg": 0, "state": "open", "response_keys": sorted(placed)[:12]}],
            "send_note": "Sent to Robinhood. Approve or decline it in the Robinhood app."}


def send(order, http=None) -> dict:
    return asyncio.run(_send(order))


def status(order, http=None) -> dict:
    """Fills are picked up from the next account read: once the position shows up, mark the order
    filled on the dashboard. Reading order status from Robinhood is not wired up yet."""
    return {"status": order.get("status", "sent"), "broker_orders": order.get("broker_orders") or [], "fills": []}
