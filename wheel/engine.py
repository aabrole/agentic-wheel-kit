"""
engine.py - turns an account snapshot into proposals. Pure functions, no network, no orders.

    result = propose(snapshot)            # rules default to spec.py

Snapshot (plain dict; the caller builds it from the broker's read-only tools):
  today          "YYYY-MM-DD"
  account        {"cash": float, "options_level": int, "approvals_on": bool}
  positions      [{"symbol", "shares", "avg_cost"}]
  short_options  [{"symbol", "type": "call"|"put", "strike", "expiry", "contracts"}]
  quotes         {symbol: last price}
  earnings       {symbol: "YYYY-MM-DD" | None}     next earnings date; ETFs need none
  ex_dividend    {symbol: "YYYY-MM-DD"}            optional
  net_premium    {symbol: dollars per share}       call premium kept so far (from the journal)
  chains         {symbol: [{"type", "expiry", "strike", "bid", "ask", "delta",
                            "open_interest", "id", "sell_floor_hint"}]}
  candidates     [{"symbol", "last", "sma200", "ret_252", "vol_252"}]   optional; stocks the
                 rotation rule may buy (spec_v2), with the date they were measured in
                 candidates_as_of

Result: {"blocked": [...], "proposals": [...], "notices": [...], "skipped": [...]}
Every proposal carries the numbers a person needs to say yes or no: what they are agreeing
to, what happens if it is assigned, what it pays, and which checks it passed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
from typing import Optional

import spec as DEFAULT_RULES


def _d(s) -> dt.date:
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


def _tick(px: float, R) -> float:
    return R.TICK_BELOW_3 if px < 3.0 else R.TICK_ABOVE_3


def _round_down(px: float, R) -> float:
    t = _tick(px, R)
    return round(math.floor(px / t + 1e-9) * t, 2)


def min_premium(spot: float, dte: int, R) -> float:
    return max(R.MIN_PREMIUM, R.MIN_YIELD_ANNUAL * spot * max(dte, 1) / 365)


def choose_expiry(expiries, today, earnings, lo, hi, target, R):
    """(expiry, note) or (None, reason). Expiries are date objects."""
    dte = {e: (e - today).days for e in expiries}
    window = [e for e in expiries if lo <= dte[e] <= hi]
    if not window:
        return None, f"no expiry {lo}-{hi} days out"
    pick = min(window, key=lambda e: (abs(dte[e] - target), -dte[e]))
    if earnings is not None and today <= earnings <= pick:
        before = [e for e in expiries if e < earnings and R.PRE_EARNINGS_MIN_DTE <= dte[e] <= hi]
        if not before:
            return None, f"earnings on {earnings} fall inside every usable expiry"
        pick = max(before)
        return pick, f"shortened to end before earnings on {earnings}"
    return pick, ""


def _quote_ok(c, spot, dte, R):
    """None if the contract passes the quote gates, else the reason it does not."""
    bid, ask = float(c.get("bid") or 0), float(c.get("ask") or 0)
    if bid <= 0 or ask < bid:
        return "no two-sided quote"
    mid = (bid + ask) / 2
    if (ask - bid) / mid > R.MAX_SPREAD:
        return f"spread {(ask - bid) / mid:.0%} of mid"
    if int(c.get("open_interest") or 0) < R.MIN_OPEN_INTEREST:
        return f"open interest {int(c.get('open_interest') or 0)}"
    if _limit(c, R, min_premium(spot, dte, R))[0] is None:
        return f"premium {mid:.2f} cannot meet the {min_premium(spot, dte, R):.2f} minimum at a valid price"
    if c.get("delta") is None:
        return "no delta"
    return None


def pick_contract(contracts, kind, expiry, spot, today, R, floor=None, ceiling=None,
                  target=None, band=None):
    """Strike selection shared by calls and puts. Returns (contract, rejected, note)."""
    dte = (expiry - today).days
    pool, rejected = [], []
    for c in contracts:
        if c.get("type") != kind or _d(c["expiry"]) != expiry:
            continue
        k = float(c["strike"])
        if floor is not None and k < floor:
            continue
        if ceiling is not None and k > ceiling:
            continue
        why = _quote_ok(c, spot, dte, R)
        if why:
            rejected.append({"strike": k, "reason": why})
        else:
            pool.append(c)
    if not pool:
        return None, rejected, "no strike passed the quote checks"
    in_band = [c for c in pool if band[0] <= abs(float(c["delta"])) <= band[1]]
    if in_band:
        best = min(in_band, key=lambda c: (abs(abs(float(c["delta"])) - target),
                                           (float(c["ask"]) - float(c["bid"]))))
        return best, rejected, ""
    if kind == "call" and floor is not None:
        far = [c for c in pool if abs(float(c["delta"])) < band[0]]
        if far:                                    # cost is far above the market: nearest strike
            return min(far, key=lambda c: float(c["strike"])), rejected, \
                "every strike at or above cost is below the delta band; nearest one taken"
    return None, rejected, "no strike in the delta band"


def _round_up(px: float, R) -> float:
    t = _tick(px, R)
    return round(math.ceil(px / t - 1e-9) * t, 2)


def _limit(c, R, need=0.0):
    """(limit, floor) for a sale, both on valid ticks: start at the midpoint rounded DOWN, never
    work below the floor rounded UP. The floor is the highest of bid + a quarter of the spread,
    the broker's fill hint, and the minimum premium. (None, None) if no valid price meets it."""
    bid, ask = float(c["bid"]), float(c["ask"])
    mid = (bid + ask) / 2
    limit = _round_down(mid, R)
    raw = bid + R.LIMIT_FLOOR_FRACTION * (ask - bid)
    hint = c.get("sell_floor_hint")
    if hint:
        raw = max(raw, min(float(hint), mid))
    floor = max(_round_up(raw, R), _round_up(need, R) if need else 0.0)
    if limit < need or floor > limit and _round_up(need, R) > limit:
        return None, None
    return limit, min(floor, limit)


def _pid(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _account_value(snap) -> float:
    q = snap.get("quotes", {})
    return float(snap["account"]["cash"]) + sum(
        float(p["shares"]) * float(q.get(p["symbol"], p.get("avg_cost", 0))) for p in snap["positions"])


def _num(x, what, errors, positive=False, allow_none=False):
    """Finite float or an error. Strings holding numbers are accepted; booleans and NaN are not."""
    if x is None and allow_none:
        return None
    try:
        if isinstance(x, bool):
            raise ValueError
        v = float(x)
        if not math.isfinite(v) or (positive and v <= 0):
            raise ValueError
        return v
    except (TypeError, ValueError):
        errors.append(f"{what}: {x!r} is not a usable number")
        return None


def _date(x, what, errors):
    try:
        return _d(x)
    except (TypeError, ValueError):
        errors.append(f"{what}: {x!r} is not a date")
        return None


def validate(snap: dict):
    """(clean snapshot, errors). Any error means nothing is proposed: a snapshot that is
    incomplete or malformed says nothing reliable about what is already covered."""
    E, out = [], {}
    for key in ("today", "account", "positions", "short_options", "quotes", "chains"):
        if key not in snap:
            E.append(f"snapshot is missing '{key}'")
    if E:
        return None, E
    today = _date(snap["today"], "today", E)
    acct = snap["account"] if isinstance(snap["account"], dict) else {}
    if not isinstance(acct.get("approvals_on"), bool):
        E.append(f"account.approvals_on: {acct.get('approvals_on')!r} must be true or false")
    lvl = _num(acct.get("options_level"), "account.options_level", E)
    out["account"] = {"cash": _num(acct.get("cash"), "account.cash", E), "options_level": int(lvl or 0),
                      "approvals_on": acct.get("approvals_on") is True}
    out["today"] = str(today)
    seen, pos = set(), []
    for p in snap["positions"]:
        sym = p.get("symbol")
        if not sym or sym in seen:
            E.append(f"positions: missing or repeated symbol {sym!r}")
            continue
        seen.add(sym)
        sh = _num(p.get("shares"), f"{sym} shares", E)
        if sh is not None and sh < 0:
            E.append(f"{sym} shares: negative")
        pos.append({"symbol": sym, "shares": sh, "avg_cost": _num(p.get("avg_cost"), f"{sym} avg_cost", E, positive=True)})
    out["positions"] = pos
    shorts = []
    if not isinstance(snap["short_options"], list):
        E.append("short_options must be a list (empty if there are none)")
    else:
        for o in snap["short_options"]:
            sym = o.get("symbol")
            n = _num(o.get("contracts"), f"{sym} short contracts", E, positive=True)
            if n is not None and n != int(n):
                E.append(f"{sym} short contracts: {n} is not a whole number")
            if o.get("type") not in ("call", "put"):
                E.append(f"{sym} short option type: {o.get('type')!r}")
            shorts.append({"symbol": sym, "type": o.get("type"), "contracts": int(n or 0),
                           "strike": _num(o.get("strike"), f"{sym} short strike", E, positive=True),
                           "expiry": str(_date(o.get("expiry"), f"{sym} short expiry", E))})
    out["short_options"] = shorts
    out["quotes"] = {s: _num(v, f"{s} quote", E, positive=True) for s, v in snap["quotes"].items()}
    out["net_premium"] = {s: _num(v, f"{s} net_premium", E) for s, v in (snap.get("net_premium") or {}).items()}
    earn, notes = {}, []
    for s, v in (snap.get("earnings") or {}).items():
        d = _date(v, f"{s} earnings", E) if v not in (None, "", False) else None
        if d is not None and today is not None and d < today:
            notes.append(f"{s}: the earnings date supplied ({d}) is in the past, so it is treated as unknown")
            d = None
        earn[s] = str(d) if d else None
    out["earnings"], out["notes"] = earn, notes
    out["ex_dividend"] = {s: str(_date(v, f"{s} ex_dividend", E)) for s, v in (snap.get("ex_dividend") or {}).items() if v}
    chains = {}
    for s, rows in snap["chains"].items():
        clean = []
        for c in rows:
            where = f"{s} {c.get('expiry')} {c.get('strike')}"
            if c.get("type") not in ("call", "put"):
                E.append(f"{where}: option type {c.get('type')!r}")
            clean.append({"type": c.get("type"), "expiry": str(_date(c.get("expiry"), where + " expiry", E)),
                          "strike": _num(c.get("strike"), where + " strike", E, positive=True),
                          "bid": _num(c.get("bid"), where + " bid", E, allow_none=True) or 0.0,
                          "ask": _num(c.get("ask"), where + " ask", E, allow_none=True) or 0.0,
                          "delta": _num(c.get("delta"), where + " delta", E, allow_none=True),
                          "open_interest": int(_num(c.get("open_interest"), where + " open interest", E, allow_none=True) or 0),
                          "id": c.get("id"),
                          "sell_floor_hint": _num(c.get("sell_floor_hint"), where + " hint", E, allow_none=True)})
        chains[s] = clean
    out["chains"] = chains
    cands = []
    for c in snap.get("candidates") or []:
        sym = c.get("symbol")
        if not sym:
            E.append("candidates: entry without a symbol")
            continue
        cands.append({"symbol": sym, "last": _num(c.get("last"), f"{sym} candidate price", E, positive=True),
                      "sma200": _num(c.get("sma200"), f"{sym} 200-day average", E, allow_none=True),
                      "ret_252": _num(c.get("ret_252"), f"{sym} one-year return", E, allow_none=True),
                      "vol_252": _num(c.get("vol_252"), f"{sym} one-year volatility", E, allow_none=True)})
    out["candidates"] = cands
    out["candidates_as_of"] = str(snap.get("candidates_as_of") or "")
    return (None, E) if E else (out, [])


def _blocks(snap, R):
    a, out = snap["account"], []
    if R.REQUIRE_APPROVALS_ON and a.get("approvals_on") is not True:
        out.append("Trade approvals are off on this account. Turn them on in the Robinhood app first.")
    if int(a.get("options_level") or 0) < R.MIN_OPTIONS_LEVEL:
        out.append(f"Options level {a.get('options_level')} is below level {R.MIN_OPTIONS_LEVEL}.")
    return out


def _short(snap, symbol, kind):
    return sum(int(o["contracts"]) for o in snap.get("short_options", [])
               if o["symbol"] == symbol and o["type"] == kind)


def call_proposals(snap, R):
    today = _d(snap["today"])
    props, skipped = [], []
    for p in snap["positions"]:
        sym, shares, cost = p["symbol"], float(p["shares"]), float(p["avg_cost"])
        lots = int(shares // 100)
        if lots < 1:
            skipped.append({"symbol": sym, "reason": f"{shares:g} shares is under one 100-share lot"})
            continue
        keep = R.UNCOVERED_LOTS.get(sym, R.DEFAULT_UNCOVERED_LOTS)
        n = lots - keep - _short(snap, sym, "call")
        if n < 1:
            skipped.append({"symbol": sym, "reason": "no uncovered lot left to write against"})
            continue
        chain = snap.get("chains", {}).get(sym)
        spot = snap.get("quotes", {}).get(sym)
        if not chain or not spot:
            skipped.append({"symbol": sym, "reason": "no option chain or price in the snapshot"})
            continue
        earn = snap.get("earnings", {}).get(sym)
        if earn is None and sym not in R.ETFS:
            skipped.append({"symbol": sym, "reason": "next earnings date unknown; not proposing blind"})
            continue
        earn = _d(earn) if earn else None
        expiries = sorted({_d(c["expiry"]) for c in chain if c.get("type") == "call"})
        expiry, note = choose_expiry(expiries, today, earn, R.CALL_DTE_MIN, R.CALL_DTE_MAX,
                                     R.CALL_DTE_TARGET, R)
        if expiry is None:
            skipped.append({"symbol": sym, "reason": note})
            continue
        net_cost = cost - float(snap.get("net_premium", {}).get(sym, 0.0))
        floor = net_cost if R.CALL_FLOOR == "cost" else None
        c, rejected, why = pick_contract(chain, "call", expiry, float(spot), today, R, floor=floor,
                                         target=R.CALL_DELTA_TARGET, band=R.CALL_DELTA_BAND)
        if c is None:
            skipped.append({"symbol": sym, "reason": why, "rejected": rejected[:6]})
            continue
        k, dte = float(c["strike"]), (expiry - today).days
        limit, limit_floor = _limit(c, R, min_premium(float(spot), dte, R))
        credit = round(limit * 100 * n - R.FEE_PER_CONTRACT * n, 2)
        flags = [x for x in (note, why) if x]
        if dte <= 30:
            flags.append("30 days or fewer to expiry: not a qualified covered call for US tax; "
                         "it can stop the holding-period clock on shares held under a year")
        exd = snap.get("ex_dividend", {}).get(sym)
        if exd and today <= _d(exd) <= expiry:
            flags.append(f"ex-dividend {exd} falls before expiry: early assignment risk if it goes in the money")
        props.append({
            "id": _pid(R.VERSION, snap["today"], sym, "call", expiry, k, n),
            "action": "SELL TO OPEN", "kind": "covered call", "symbol": sym, "contracts": n,
            "expiry": str(expiry), "dte": dte, "strike": k, "limit": limit, "limit_floor": limit_floor,
            "credit_if_filled_at_limit": credit, "option_id": c.get("id"),
            "delta": round(abs(float(c["delta"])), 3),
            "spread_pct": round((float(c["ask"]) - float(c["bid"])) / ((float(c["ask"]) + float(c["bid"])) / 2), 3),
            "open_interest": int(c.get("open_interest") or 0), "spot": float(spot),
            "net_cost_per_share": round(net_cost, 2),
            "agreeing_to": f"sell {100 * n} {sym} shares at ${k:g} any time through {expiry}",
            "if_assigned": {"shares_sold": 100 * n, "proceeds": round(k * 100 * n, 2),
                            "per_share_vs_net_cost": round(k - net_cost, 2),
                            "result_with_premium": round((k - net_cost) * 100 * n + credit, 2)},
            "if_expires": {"shares_kept": 100 * n, "premium_kept": credit},
            "holding_does_better_above": round(k + limit, 2),
            "downside": "unchanged: you still hold the shares; the premium offsets "
                        f"${limit:.2f} a share of any fall",
            "evidence": "In 891 test cases this rule beat holding 61% of the time: +4 points on average when "
                        "the stock ended 26 weeks up 10% or less, -15 points when it rose more.",
            "flags": flags, "rejected_strikes": rejected[:6],
        })
    return props, skipped


def notices(snap, R):
    today, out = _d(snap["today"]), []
    for o in snap.get("short_options", []):
        sym, k, exp = o["symbol"], float(o["strike"]), _d(o["expiry"])
        spot = snap.get("quotes", {}).get(sym)
        dte = (exp - today).days
        if spot is None:
            continue
        itm = spot > k if o["type"] == "call" else spot < k
        near = abs(spot - k) / k <= R.NEAR_STRIKE
        if dte <= R.EXPIRY_NOTICE_DTE and (itm or near):
            what = "your shares will be sold at the strike" if o["type"] == "call" else "you will buy the shares at the strike"
            out.append({"symbol": sym, "kind": "expiry", "text":
                        f"{sym} {k:g} {o['type']} expires {exp} with the stock at {spot:g}: if it finishes "
                        f"in the money {what}. Choices: let it be assigned, or buy it back before 3:30 pm ET "
                        "on expiry day (Robinhood starts closing at-risk positions then). Nothing is automatic."})
        exd = snap.get("ex_dividend", {}).get(sym)
        if o["type"] == "call" and itm and exd and 0 <= (_d(exd) - today).days <= 1:
            out.append({"symbol": sym, "kind": "ex-dividend", "text":
                        f"{sym} {k:g} call is in the money and {sym} goes ex-dividend {exd}: early "
                        "assignment is likely. Buying the call back today keeps the shares and the dividend."})
    return out


def _find(chain, kind, expiry, strike):
    for c in chain or []:
        if c.get("type") == kind and _d(c["expiry"]) == expiry and abs(float(c["strike"]) - strike) < 1e-9:
            return c
    return None


def roll_proposals(snap, R):
    """Roll an in-the-money call up and out, only for a net credit at the worst worked prices."""
    if not getattr(R, "ROLLS_ENABLED", False):
        return [], []
    today, props, skipped = _d(snap["today"]), [], []
    for o in snap.get("short_options", []):
        if o["type"] != "call":
            continue
        sym, k0, e0, n = o["symbol"], float(o["strike"]), _d(o["expiry"]), int(o["contracts"])
        spot = snap.get("quotes", {}).get(sym)
        dte0 = (e0 - today).days
        if spot is None or spot <= k0 or dte0 < 0 or dte0 > R.ROLL_DTE:
            continue
        chain = snap.get("chains", {}).get(sym)
        old = _find(chain, "call", e0, k0)
        if not old or float(old.get("ask") or 0) <= 0:
            skipped.append({"symbol": sym, "reason": f"${k0:g} call is in the money with {dte0} days left, "
                            "but its quote is not in the snapshot, so no roll can be priced"})
            continue
        earn = snap.get("earnings", {}).get(sym)
        if earn is None and sym not in R.ETFS:
            skipped.append({"symbol": sym, "reason": "roll not proposed: next earnings date unknown"})
            continue
        earn = _d(earn) if earn else None
        later = sorted(e for e in {_d(c["expiry"]) for c in chain if c.get("type") == "call"}
                       if e > e0 and R.CALL_DTE_MIN <= (e - today).days <= R.CALL_DTE_MAX
                       and not (earn is not None and today <= earn <= e))
        if not later:
            skipped.append({"symbol": sym, "reason": f"roll not proposed: no later expiry {R.CALL_DTE_MIN}-"
                            f"{R.CALL_DTE_MAX} days out that ends before earnings; the shares will be called away at ${k0:g}"})
            continue
        e1 = min(later, key=lambda e: (abs((e - today).days - R.CALL_DTE_TARGET), -(e - today).days))
        dte1 = (e1 - today).days
        buy_max = _round_up(float(old["ask"]), R)
        buy_limit = _round_up((float(old["bid"] or 0) + float(old["ask"])) / 2, R)
        best, tried = None, []
        for c in sorted((c for c in chain if c.get("type") == "call" and _d(c["expiry"]) == e1
                         and float(c["strike"]) > k0), key=lambda c: -float(c["strike"])):
            why = _quote_ok(c, float(spot), dte1, R)
            if why:
                tried.append({"strike": float(c["strike"]), "reason": why})
                continue
            limit, floor = _limit(c, R, min_premium(float(spot), dte1, R))
            worst = floor - buy_max - 2 * R.FEE_PER_CONTRACT / 100
            if worst >= 0:
                best = (c, limit, floor, worst)
                break
            tried.append({"strike": float(c["strike"]), "reason": f"net debit of ${-worst:.2f} a share at the worst prices"})
        if best is None:
            skipped.append({"symbol": sym, "reason": f"roll not proposed: no higher strike on {e1} pays for buying "
                            f"back the ${k0:g} call; the shares will be called away at ${k0:g}", "rejected": tried[:6]})
            continue
        c, limit, floor, worst = best
        k1 = float(c["strike"])
        net_at_limits = round((limit - buy_limit) * 100 * n - 2 * R.FEE_PER_CONTRACT * n, 2)
        props.append({
            "id": _pid(R.VERSION, snap["today"], sym, "roll", e0, k0, e1, k1, n),
            "action": "ROLL", "kind": "roll up and out", "symbol": sym, "contracts": n,
            "expiry": str(e1), "dte": dte1, "strike": k1, "limit": limit, "limit_floor": floor, "spot": float(spot),
            "legs": [{"action": "BUY TO CLOSE", "type": "call", "strike": k0, "expiry": str(e0), "contracts": n,
                      "limit": buy_limit, "limit_max": buy_max, "option_id": old.get("id")},
                     {"action": "SELL TO OPEN", "type": "call", "strike": k1, "expiry": str(e1), "contracts": n,
                      "limit": limit, "limit_floor": floor, "option_id": c.get("id")}],
            "credit_if_filled_at_limit": net_at_limits,
            "net_credit_worst_case": round(worst * 100 * n, 2),
            "agreeing_to": f"give up the sale at ${k0:g} on {e0} and instead agree to sell {100 * n} {sym} "
                           f"shares at ${k1:g} any time through {e1}",
            "if_assigned": {"shares_sold": 100 * n, "proceeds": round(k1 * 100 * n, 2),
                            "extra_vs_old_strike": round((k1 - k0) * 100 * n, 2)},
            "if_not_rolled": f"{100 * n} shares are sold at ${k0:g} on {e0} (the stock is at ${float(spot):g})",
            "evidence": "In 891 test cases a roll like this was possible in 10% to 27% of cases and added "
                        "a small amount (about 0.1 to 0.3 points per 26 weeks).",
            "flags": ["two separate orders: buy the old call back first, then sell the new one"],
            "rejected_strikes": tried[:6],
        })
    return props, skipped


def rotation_proposals(snap, R, reserved=0.0):
    """BETA (spec_v2): put free cash into the strongest-trend eligible stock, 100-share lots."""
    rule = getattr(R, "ROT_RULE", "off") if getattr(R, "ROTATION_ENABLED", False) else "off"
    if rule == "off":
        return [], []
    secured = sum(float(o["strike"]) * 100 * int(o["contracts"]) for o in snap.get("short_options", [])
                  if o["type"] == "put")
    free = float(snap["account"]["cash"]) - secured - reserved
    held = {p["symbol"] for p in snap["positions"] if float(p["shares"]) > 0}
    cands = snap.get("candidates") or []
    as_of = snap.get("candidates_as_of") or "unknown date"
    value = _account_value(snap)
    if rule == "spy":
        if free < 1:
            return [], [{"symbol": "SPY", "reason": "no free cash to move into SPY"}]
        return [{"id": _pid(R.VERSION, snap["today"], "SPY", "cash", round(free)),
                 "action": "BUY", "kind": "idle cash to SPY", "symbol": "SPY", "dollars": round(free, 2),
                 "agreeing_to": f"buy ${free:,.2f} of SPY (fractional shares) with idle cash",
                 "evidence": "In the plan test, idle cash in SPY was worth about $10,000 more than idle cash at 0% "
                             "over 16 months, more than any options rule added.",
                 "flags": [], "rejected_strikes": []}], []
    if not cands:
        return [], [{"symbol": "*", "reason": "rotation: no candidate list in the snapshot"}]
    ranked, passed_over = [], []
    for c in cands:
        sym, last = c["symbol"], c["last"]
        if sym in held:
            continue
        if last < R.ROT_MIN_PRICE or c["sma200"] is None or last <= c["sma200"]:
            continue
        if rule == "trend":
            if c["ret_252"] is None:
                continue
            ranked.append((-c["ret_252"], sym, c))
        else:                                                   # "quality": calmest first
            if c["vol_252"] is None or c["vol_252"] > R.ROT_QUALITY_MAX_VOL:
                continue
            ranked.append((c["vol_252"], sym, c))
    ranked.sort(key=lambda x: (x[0], x[1]))
    pick = None
    for rank, (_, sym, c) in enumerate(ranked, 1):
        lots = int(free // (c["last"] * (1 + R.ROT_CASH_BUFFER) * 100))
        if lots >= 1:
            pick = (sym, c, lots, rank)
            break
        if len(passed_over) < R.ROT_SHOW_RANKS:
            passed_over.append({"symbol": sym, "reason": f"100 shares cost ${c['last'] * 100:,.0f}, free cash is ${free:,.0f}",
                                "ret_252": c["ret_252"], "last": c["last"]})
    if pick is None:
        why = (f"rotation: free cash ${free:,.0f} does not cover 100 shares of any eligible stock"
               if ranked else "rotation: no stock passes the trend filter")
        return [], [{"symbol": "*", "reason": why, "rejected": passed_over}]
    sym, c, lots, rank = pick
    limit = round(c["last"] * (1 + R.ROT_LIMIT_PAD), 2)
    cost = round(limit * 100 * lots, 2)
    what = ("strongest one-year gain among stocks above their 200-day average" if rule == "trend"
            else "calmest stock passing the quality filter")
    flags = ["BETA rule, not confirmed: on the full 198-name test it did not clearly beat holding or putting this cash in SPY",
             f"candidate list measured {as_of}",
             f"this purchase is {cost / value:.0%} of the account in one stock"]
    if rank > 1:
        flags.insert(0, f"{sym} ranks {rank} of {len(ranked)} eligible stocks: the {rank - 1} ranked above it "
                        f"cost more than the ${free:,.0f} of free cash for 100 shares")
    if snap.get("earnings", {}).get(sym) is None and sym not in R.ETFS:
        flags.append(f"next earnings date for {sym} is not in the snapshot: calls will not be proposed on it until it is")
    return [{"id": _pid(R.VERSION, snap["today"], sym, "rotate", lots),
             "action": "BUY", "kind": "rotation stock", "symbol": sym, "shares": 100 * lots, "lots": lots,
             "limit": limit, "cost": cost, "cash_after": round(free - cost, 2), "spot": c["last"],
             "ret_252": c["ret_252"], "sma200": c["sma200"], "rule": rule, "rank": rank, "eligible": len(ranked),
             "agreeing_to": f"buy {100 * lots} {sym} shares at up to ${limit:,.2f} (${cost:,.2f}); calls are proposed on them next run",
             "why_this_stock": f"{what}: {sym} is {c['ret_252']:+.0%} over a year, ${c['last']:,.2f} against a 200-day average of ${c['sma200']:,.2f}"
                               if rule == "trend" else f"{what}: {sym} one-year volatility {c['vol_252']:.0%}",
             "ranked": [{"symbol": s2, "ret_252": c2["ret_252"], "vol_252": c2["vol_252"], "last": c2["last"]}
                        for _, s2, c2 in ranked[:R.ROT_SHOW_RANKS]],
             "downside": "full stock risk: the shares can fall, and stocks that have risen the most can fall the most",
             "evidence": "Partial test (47 names): over one year 37.8% against 39.7% holding and 26.6% with the cash in SPY; "
                         "over 2.5 years it beat holding in 78% of 141 cases. It bought that period's biggest winners." if rule == "trend"
                         else "Partial test (47 names): about the same return as putting the cash in SPY (24.2% against 26.6% over one year).",
             "flags": flags, "rejected_strikes": passed_over}], []


def put_proposals(snap, R):
    if not R.PUTS_ENABLED:
        return [], [{"symbol": "*", "reason": "puts are off in this version (not yet tested on ETF data)"}]
    today = _d(snap["today"])
    value = _account_value(snap)
    secured = sum(float(o["strike"]) * 100 * int(o["contracts"]) for o in snap.get("short_options", [])
                  if o["type"] == "put")
    free = float(snap["account"]["cash"]) - secured
    held = sum(float(p["shares"]) * float(snap.get("quotes", {}).get(p["symbol"], p["avg_cost"]))
               for p in snap["positions"])
    props, skipped = [], []
    for sym in R.PUT_ALLOW_LIST:
        chain, spot = snap.get("chains", {}).get(sym), snap.get("quotes", {}).get(sym)
        if not chain or not spot:
            skipped.append({"symbol": sym, "reason": "no option chain or price in the snapshot"})
            continue
        if _short(snap, sym, "put") or any(p["symbol"] == sym and float(p["shares"]) >= 100 for p in snap["positions"]):
            skipped.append({"symbol": sym, "reason": "already holding shares or a short put on it"})
            continue
        expiries = sorted({_d(c["expiry"]) for c in chain if c.get("type") == "put"})
        expiry, note = choose_expiry(expiries, today, None, R.PUT_DTE_MIN, R.PUT_DTE_MAX, R.PUT_DTE_TARGET, R)
        if expiry is None:
            skipped.append({"symbol": sym, "reason": note})
            continue
        budget = min(free, R.PUT_NAME_CAP * value)
        c, rejected, why = pick_contract(chain, "put", expiry, float(spot), today, R, ceiling=budget / 100,
                                         target=R.PUT_DELTA_TARGET, band=R.PUT_DELTA_BAND)
        if c is None:
            skipped.append({"symbol": sym, "reason": why or "no strike fits the cash available", "rejected": rejected[:6]})
            continue
        k = float(c["strike"])
        n = int(budget // (k * 100))
        stress = R.STRESS_DROP * (held + secured + k * 100 * n)
        if n < 1 or stress > R.STRESS_LIMIT * value:
            skipped.append({"symbol": sym, "reason": "fails the cash or stress check"})
            continue
        limit, limit_floor = _limit(c, R, min_premium(float(spot), (expiry - today).days, R))
        credit = round(limit * 100 * n - R.FEE_PER_CONTRACT * n, 2)
        props.append({
            "id": _pid(R.VERSION, snap["today"], sym, "put", expiry, k, n),
            "action": "SELL TO OPEN", "kind": "cash-secured put", "symbol": sym, "contracts": n,
            "expiry": str(expiry), "dte": (expiry - today).days, "strike": k, "limit": limit,
            "limit_floor": limit_floor, "credit_if_filled_at_limit": credit, "option_id": c.get("id"),
            "delta": round(abs(float(c["delta"])), 3), "spot": float(spot),
            "agreeing_to": f"buy {100 * n} {sym} shares at ${k:g} any time through {expiry}",
            "cash_set_aside": round(k * 100 * n, 2),
            "if_assigned": {"shares_bought": 100 * n, "cost": round(k * 100 * n, 2),
                            "net_cost_per_share": round(k - credit / (100 * n), 4)},
            "max_loss": round(k * 100 * n - credit, 2),
            "stress_loss_whole_account": round(stress, 2), "flags": [x for x in (note, why) if x],
            "rejected_strikes": rejected[:6],
        })
        free -= k * 100 * n
        secured += k * 100 * n
    return props, skipped


def propose(snap: dict, rules=None) -> dict:
    R = rules or DEFAULT_RULES
    snap, errors = validate(snap)
    if errors:
        return {"version": R.VERSION, "blocked": ["Snapshot rejected: " + e for e in errors],
                "proposals": [], "notices": [], "skipped": []}
    blocked = _blocks(snap, R)
    if blocked:
        return {"version": R.VERSION, "blocked": blocked, "proposals": [], "notices": notices(snap, R), "skipped": []}
    rolls, s0 = roll_proposals(snap, R)
    calls, s1 = call_proposals(snap, R)
    s1 = s1 + [{"symbol": n.split(":")[0], "reason": n.split(": ", 1)[1]} for n in snap.get("notes", [])]
    puts, s2 = put_proposals(snap, R)
    reserved = sum(p.get("cash_set_aside", 0.0) for p in puts)
    rot, s3 = rotation_proposals(snap, R, reserved)
    allp = rolls + calls + rot + puts
    extra = [{"symbol": p["symbol"], "reason": "over the per-run proposal limit"} for p in allp[R.MAX_PROPOSALS:]]
    return {"version": R.VERSION, "blocked": [], "proposals": allp[:R.MAX_PROPOSALS],
            "notices": notices(snap, R), "skipped": s0 + s1 + s2 + s3 + extra,
            "account_value": round(_account_value(snap), 2)}
