"""
core.py - everything the wheel dashboard does, as plain functions over a state folder.
No network and no broker calls: this module never sends an order anywhere.

State folder (wheel_live/state/<mode>/):
  snapshot.json   the account snapshot the engine reads (in staging, simulated fills edit it)
  base.json       the snapshot the scenario started from (for the "just held" comparison)
  journal.jsonl   append-only log: runs, proposals, decisions, orders, fills, setting changes
  outbox.jsonl    orders approved on the dashboard, waiting to be sent to the broker
  settings.json   the owner's overrides of the adjustable rules

Modes: "staging" works on saved or made-up snapshots; fills are simulated by a button and say so.
"live" reads snapshots written by the account reader; orders wait in the outbox for the sender.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import sys
import types
from pathlib import Path

import os

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # wheel/
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))     # repo root, for `wheel.config`

from wheel import config  # noqa: E402

import engine  # noqa: E402
import journal  # noqa: E402
import spec_v2  # noqa: E402

SEND_ENABLED = config.SEND       # WHEEL_SEND=1 in .env. Off: approved orders stop in the outbox.

# name: (kind, low, high, plain description). Anything not listed here cannot be changed from the page.
ADJUSTABLE = {
    "CALL_DTE_TARGET": ("int", 31, 45, "Days to expiry aimed for on new calls"),
    "CALL_DELTA_TARGET": ("float", 0.20, 0.40, "Delta aimed for on new calls (higher = closer to the price, more premium, called away more)"),
    "ROLLS_ENABLED": ("bool", None, None, "Propose rolling in-the-money calls up and out for a credit"),
    "ROT_RULE": ("choice", ["trend", "premium", "quality", "spy", "off"], None,
                 "Where free cash goes: trend (beta: strongest one-year gain), premium (beta: uptrend, then highest option premium), quality (calmest), spy, or off"),
    "SWEEP_TO_SPY": ("bool", None, None, "With trend, premium or quality: park cash that cannot buy 100 shares in SPY, and sell it when it can"),
    "MAX_PROPOSALS": ("int", 1, 8, "Most proposals shown per run"),
}
LOCKED = [
    ("Calls are never proposed below your net cost", "CALL_FLOOR = cost"),
    ("Nothing is proposed if Robinhood trade approvals are off", "REQUIRE_APPROVALS_ON"),
    ("No option whose life contains an earnings date", "earnings rule"),
    ("Cash-secured puts are off", "PUTS_ENABLED = False"),
    ("Every order needs your approval in Robinhood", "account setting"),
]


def state_dir(mode: str) -> Path:
    d = Path(os.environ.get("WHEEL_STATE_DIR", str(ROOT.parent / "state"))) / mode
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def _lines(p: Path) -> list:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def log(mode, event: dict) -> None:
    journal.append(state_dir(mode) / "journal.jsonl", event)


# ------------------------------------------------------------------ rules
def load_settings(mode) -> dict:
    return _read(state_dir(mode) / "settings.json", {"rules": {}, "uncovered_lots": {}})


def clean_settings(changes: dict) -> tuple:
    """(clean dict in the settings.json shape, errors). Only ADJUSTABLE names and uncovered lots."""
    out, errors = {"rules": {}, "uncovered_lots": {}}, []
    for k, v in (changes.get("rules") or {}).items():
        if k not in ADJUSTABLE:
            errors.append(f"{k} cannot be changed from the dashboard")
            continue
        kind, lo, hi, _ = ADJUSTABLE[k]
        try:
            if kind == "bool":
                if not isinstance(v, bool):
                    raise ValueError
            elif kind == "choice":
                if v not in lo:
                    raise ValueError
            else:
                v = int(v) if kind == "int" else float(v)
                if not (lo <= v <= hi):
                    raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{k}: {v!r} is outside what is allowed ({lo}" + (f" to {hi})" if hi is not None else ")"))
            continue
        out["rules"][k] = v
    for sym, v in (changes.get("uncovered_lots") or {}).items():
        try:
            n = int(v)
            if n < 0 or n > 50:
                raise ValueError
            out["uncovered_lots"][str(sym).upper()] = n
        except (TypeError, ValueError):
            errors.append(f"uncovered lots for {sym}: {v!r} must be a whole number, 0 or more")
    return out, errors


def rules_for(settings: dict):
    r = types.SimpleNamespace(**{k: v for k, v in vars(spec_v2).items() if k.isupper()})
    for k, v in settings.get("rules", {}).items():
        setattr(r, k, v)
    r.UNCOVERED_LOTS = {**spec_v2.UNCOVERED_LOTS, **settings.get("uncovered_lots", {})}
    r.ROTATION_ENABLED = r.ROT_RULE != "off"
    return r


def settings_view(settings: dict) -> dict:
    R = rules_for(settings)
    return {"version": R.VERSION,
            "adjustable": [{"name": k, "kind": a[0], "low": a[1], "high": a[2], "about": a[3],
                            "value": getattr(R, k), "default": getattr(spec_v2, k),
                            "changed": getattr(R, k) != getattr(spec_v2, k)} for k, a in ADJUSTABLE.items()],
            "uncovered_lots": R.UNCOVERED_LOTS, "locked": [{"rule": a, "setting": b} for a, b in LOCKED]}


# ------------------------------------------------------------------ snapshot and proposals
def load_snapshot(mode) -> dict:
    return _read(state_dir(mode) / "snapshot.json", None)


def with_premium(mode, snap: dict) -> dict:
    """Snapshot plus the net call premium kept per share, from the journal."""
    s = copy.deepcopy(snap)
    ev = _lines(state_dir(mode) / "journal.jsonl")
    np_ = dict(s.get("net_premium") or {})
    for p in s["positions"]:
        try:
            v = journal.net_call_premium_per_share(ev, p["symbol"], float(p["shares"]))
        except (ValueError, KeyError):
            v = 0.0
        if v:
            np_[p["symbol"]] = round(float(np_.get(p["symbol"], 0.0)) + v, 4)
    s["net_premium"] = np_
    s["parked_spy_shares"] = parked_shares(mode, s)
    return s


def parked_shares(mode, snap) -> float:
    """SPY shares this system bought to park leftover cash, less what it sold back. Never more
    than the SPY actually held, so SPY the owner bought separately is not counted."""
    n = 0.0
    for e in _lines(state_dir(mode) / "journal.jsonl"):
        if e.get("event") == "stock_fill" and e.get("parked") and e.get("symbol") == spec_v2.PARK_SYMBOL:
            n += float(e.get("shares") or 0.0) * (1 if e.get("side") == "buy" else -1)
    held = sum(float(p["shares"]) for p in snap["positions"] if p["symbol"] == spec_v2.PARK_SYMBOL)
    return round(max(0.0, min(n, held)), 6)


def run_engine(mode, settings=None) -> dict:
    snap = load_snapshot(mode)
    if snap is None:
        return {"version": spec_v2.VERSION, "blocked": ["No account snapshot yet."], "proposals": [],
                "notices": [], "skipped": []}
    return engine.propose(with_premium(mode, snap), rules_for(settings or load_settings(mode)))


def _chain_mid(snap, sym, kind, strike, expiry):
    for c in (snap.get("chains") or {}).get(sym, []):
        if c.get("type") == kind and str(c.get("expiry")) == str(expiry) and abs(float(c["strike"]) - float(strike)) < 1e-9:
            b, a = float(c.get("bid") or 0), float(c.get("ask") or 0)
            if a > 0:
                return round((a + b) / 2, 2), a
    return None, None


def portfolio(mode, snap: dict, R) -> dict:
    s = with_premium(mode, snap)
    q = s.get("quotes", {})
    rows, stock_value = [], 0.0
    for p in s["positions"]:
        sym, sh, cost = p["symbol"], float(p["shares"]), float(p["avg_cost"])
        px = q.get(sym)
        lots = int(sh // 100)
        short = sum(int(o["contracts"]) for o in s["short_options"] if o["symbol"] == sym and o["type"] == "call")
        keep = R.UNCOVERED_LOTS.get(sym, R.DEFAULT_UNCOVERED_LOTS)
        prem = float(s["net_premium"].get(sym, 0.0))
        val = sh * px if px else None
        stock_value += val or sh * cost
        rows.append({"symbol": sym, "shares": sh, "avg_cost": cost, "net_cost": round(cost - prem, 2),
                     "premium_kept_per_share": prem, "price": px, "value": round(val, 2) if val else None,
                     "gain": round((px - cost) * sh, 2) if px else None,
                     "gain_pct": round(px / cost - 1, 4) if px else None,
                     "lots": lots, "calls_out": short, "kept_free": min(keep, lots),
                     "free_to_cover": max(lots - keep - short, 0),
                     "earnings": (s.get("earnings") or {}).get(sym)})
    opts, liability = [], 0.0
    today = dt.date.fromisoformat(s["today"])
    for o in s["short_options"]:
        mid, ask = _chain_mid(s, o["symbol"], o["type"], o["strike"], o["expiry"])
        px = q.get(o["symbol"])
        intrinsic = max((px - float(o["strike"])) if o["type"] == "call" else (float(o["strike"]) - px), 0.0) if px else 0.0
        mark = mid if mid is not None else intrinsic
        liability += mark * 100 * int(o["contracts"])
        opts.append({**o, "dte": (dt.date.fromisoformat(o["expiry"]) - today).days, "price": px,
                     "in_the_money": bool(px and ((px > o["strike"]) if o["type"] == "call" else (px < o["strike"]))),
                     "cost_to_close": round(mark * 100 * int(o["contracts"]), 2), "quoted": mid is not None})
    cash = float(s["account"]["cash"])
    return {"today": s["today"], "cash": cash, "stock_value": round(stock_value, 2),
            "open_option_liability": round(liability, 2),
            "account_value": round(cash + stock_value - liability, 2),
            "positions": rows, "short_options": opts, "account": s["account"],
            "lots_total": sum(r["lots"] for r in rows), "calls_out": sum(r["calls_out"] for r in rows),
            "candidates_as_of": s.get("candidates_as_of"), "note": s.get("_note")}


# ------------------------------------------------------------------ guardrails for owner-made orders
def check_order(mode, snap: dict, R, leg: dict) -> dict:
    """{"blocks": [...], "warnings": [...]}. Blocks cannot be overridden; warnings can, on the record."""
    s = with_premium(mode, snap)
    blocks, warns = [], []
    sym = str(leg.get("symbol", "")).upper()
    act = leg.get("action")
    pos = next((p for p in s["positions"] if p["symbol"] == sym), None)
    today = dt.date.fromisoformat(s["today"])
    if not s["account"].get("approvals_on"):
        blocks.append("Trade approvals are off on the account.")
    try:
        if act == "SELL TO OPEN":
            n, k = int(leg["contracts"]), float(leg["strike"])
            exp = dt.date.fromisoformat(str(leg["expiry"]))
            if leg.get("type") != "call":
                blocks.append("Only covered calls can be sold from the dashboard (puts are off).")
            lots = int(float(pos["shares"]) // 100) if pos else 0
            short = sum(int(o["contracts"]) for o in s["short_options"] if o["symbol"] == sym and o["type"] == "call")
            if n < 1 or n > lots - short:
                blocks.append(f"{n} contracts need {100 * n} uncovered {sym} shares; you have {max(lots - short, 0) * 100}. "
                              "An uncovered call is not allowed on this account.")
            elif n > lots - short - R.UNCOVERED_LOTS.get(sym, R.DEFAULT_UNCOVERED_LOTS):
                warns.append(f"This covers a lot you set aside to stay uncovered on {sym}.")
            if exp <= today:
                blocks.append("That expiry is not in the future.")
            if pos:
                net = float(pos["avg_cost"]) - float(s["net_premium"].get(sym, 0.0))
                if k < net:
                    warns.append(f"Strike ${k:g} is below your net cost of ${net:.2f}: if assigned you lock in a loss of "
                                 f"${(net - k) * 100 * n:,.0f} before premium. In testing, calls below cost were clearly worse than holding.")
            earn = (s.get("earnings") or {}).get(sym)
            if earn is None and sym not in R.ETFS:
                warns.append(f"Next earnings date for {sym} is unknown.")
            elif earn and today <= dt.date.fromisoformat(earn) <= exp:
                warns.append(f"Earnings on {earn} fall before this call expires.")
            dte = (exp - today).days
            if 0 < dte <= 30:
                warns.append("30 days or fewer to expiry: not a qualified covered call for US tax.")
            mid, _ = _chain_mid(s, sym, "call", k, exp)
            if mid is None:
                warns.append("No quote for this contract in the snapshot, so the price cannot be checked.")
            elif float(leg.get("limit") or 0) < mid * 0.75:
                warns.append(f"Limit ${float(leg.get('limit') or 0):.2f} is well under the quoted midpoint ${mid:.2f}.")
        elif act == "BUY TO CLOSE":
            n = int(leg["contracts"])
            have = sum(int(o["contracts"]) for o in s["short_options"] if o["symbol"] == sym
                       and o["type"] == leg.get("type") and abs(float(o["strike"]) - float(leg["strike"])) < 1e-9
                       and str(o["expiry"]) == str(leg["expiry"]))
            if n < 1 or n > have:
                blocks.append(f"You are short {have} of that contract, not {n}.")
            if float(leg.get("limit") or 0) * 100 * n > float(s["account"]["cash"]):
                blocks.append("Not enough cash to buy it back at that price.")
        elif act == "BUY":
            cost = float(leg["dollars"]) if leg.get("dollars") else float(leg["shares"]) * float(leg["limit"])
            if cost <= 0:
                blocks.append("Nothing to buy.")
            if cost > float(s["account"]["cash"]) + 0.005:
                blocks.append(f"That costs ${cost:,.2f}; cash is ${float(s['account']['cash']):,.2f}.")
            value = portfolio(mode, snap, R)["account_value"]
            if value > 0 and cost / value > 0.40:
                warns.append(f"This puts {cost / value:.0%} of the account into one purchase.")
        elif act == "SELL":
            quote = float((s.get("quotes") or {}).get(sym) or 0.0)
            if leg.get("dollars"):
                if quote <= 0:
                    blocks.append(f"No price for {sym}, so a dollar amount cannot be checked.")
                sh = float(leg["dollars"]) / quote if quote > 0 else 0.0
            else:
                sh = float(leg["shares"])
            have = float(pos["shares"]) if pos else 0.0
            short = sum(int(o["contracts"]) for o in s["short_options"] if o["symbol"] == sym and o["type"] == "call")
            if sh <= 0 or sh > have + 1e-9:
                blocks.append(f"You hold {have:g} {sym} shares.")
            elif have - sh < short * 100:
                blocks.append(f"{short} calls are open against {sym}: selling {sh:g} shares would leave them uncovered. Close the calls first.")
            if pos and float(leg.get("limit") or (quote if leg.get("dollars") else 0)) < float(pos["avg_cost"]):
                warns.append(f"Selling below your average cost of ${float(pos['avg_cost']):.2f} realizes a loss.")
        else:
            blocks.append(f"Unknown order type {act!r}.")
    except (KeyError, TypeError, ValueError) as e:
        blocks.append(f"The order is missing a field or has a bad number ({e}).")
    return {"blocks": blocks, "warnings": warns}


# ------------------------------------------------------------------ orders
def legs_of(p: dict) -> list:
    """Broker orders a proposal turns into."""
    if p["kind"] == "roll up and out":
        return [{"instrument": "option", "symbol": p["symbol"], **{k: v for k, v in leg.items()}} for leg in p["legs"]]
    if p["kind"] in ("rotation stock", "idle cash to SPY", "release parked SPY"):
        leg = {"instrument": "stock", "action": p["action"], "symbol": p["symbol"]}
        leg.update({"dollars": p["dollars"]} if p.get("dollars") else {"shares": p["shares"], "limit": p["limit"]})
        if p.get("parked"):
            leg["parked"] = True
        return [leg]
    return [{"instrument": "option", "action": "SELL TO OPEN", "symbol": p["symbol"],
             "type": "call" if "call" in p["kind"] else "put", "strike": p["strike"], "expiry": p["expiry"],
             "contracts": p["contracts"], "limit": p["limit"], "limit_floor": p.get("limit_floor"),
             "option_id": p.get("option_id")}]


def outbox(mode) -> list:
    latest = {}
    for o in _lines(state_dir(mode) / "outbox.jsonl"):
        latest[o["order_id"]] = {**latest.get(o["order_id"], {}), **o}
    return sorted(latest.values(), key=lambda o: o["created"], reverse=True)


def _put_order(mode, rec: dict) -> None:
    with (state_dir(mode) / "outbox.jsonl").open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def queue_order(mode, source, legs, proposal_id=None, overrides=None, note="") -> dict:
    oid = hashlib.sha1(f"{now()}|{json.dumps(legs, sort_keys=True)}".encode()).hexdigest()[:10]
    rec = {"order_id": oid, "created": now(), "source": source, "proposal_id": proposal_id, "legs": legs,
           "status": "queued", "overrides": overrides or [], "note": note,
           "send_note": ("STAGING: use Simulate fill. Nothing is sent." if mode == "staging" else
                         "Waiting: sending is off (WHEEL_SEND=0)." if not SEND_ENABLED else f"Queued for {config.BROKER}.")}
    _put_order(mode, rec)
    log(mode, {"event": "order_queued", "order_id": oid, "source": source, "proposal_id": proposal_id,
               "legs": legs, "overrides": overrides or [], "note": note})
    return rec


def decide(mode, pid, decision, note="", edits=None, override=False) -> dict:
    out = run_engine(mode)
    p = next((x for x in out["proposals"] if x["id"] == pid), None)
    if p is None:
        return {"ok": False, "error": "That proposal is no longer current. Refresh and look again."}
    if decision == "decline":
        log(mode, {"event": "proposed", **p})
        log(mode, {"event": "decision", "id": pid, "decision": "declined", "reason": note, "symbol": p["symbol"], "kind": p["kind"]})
        return {"ok": True}
    legs = legs_of(p)
    overrides = []
    if edits:
        if len(legs) != 1:
            return {"ok": False, "error": "A roll cannot be edited here; decline it and enter the two orders by hand."}
        leg = legs[0]
        for k in ("strike", "expiry", "contracts", "limit", "shares"):
            if k in edits and edits[k] not in (None, ""):
                leg[k] = edits[k] if k == "expiry" else (int(edits[k]) if k in ("contracts", "shares") else float(edits[k]))
        leg.pop("option_id", None)
        leg.pop("limit_floor", None)
        chk = check_order(mode, load_snapshot(mode), rules_for(load_settings(mode)), leg)
        if chk["blocks"]:
            return {"ok": False, "error": " ".join(chk["blocks"]), **chk}
        if chk["warnings"] and not override:
            return {"ok": False, "needs_override": True, **chk}
        overrides = chk["warnings"]
    log(mode, {"event": "proposed", **p})
    log(mode, {"event": "decision", "id": pid, "decision": "placed", "reason": note, "symbol": p["symbol"],
               "kind": p["kind"], "edited": bool(edits), "edits": edits or {}, "overrides": overrides})
    return {"ok": True, "order": queue_order(mode, "engine", legs, pid, overrides, note)}


def manual(mode, leg, note="", override=False) -> dict:
    leg = dict(leg)
    leg["symbol"] = str(leg.get("symbol", "")).upper()
    leg["instrument"] = "stock" if leg.get("action") in ("BUY", "SELL") else "option"
    chk = check_order(mode, load_snapshot(mode), rules_for(load_settings(mode)), leg)
    if chk["blocks"]:
        return {"ok": False, "error": " ".join(chk["blocks"]), **chk}
    if chk["warnings"] and not override:
        return {"ok": False, "needs_override": True, **chk}
    return {"ok": True, "order": queue_order(mode, "you", [leg], None, chk["warnings"], note)}


def update_order(mode, oid, **fields) -> None:
    _put_order(mode, {"order_id": oid, **fields})


def record_fills(mode, order, fills) -> None:
    """Journal fills reported by the broker, so premium and net cost follow real prices."""
    for f in fills:
        leg = order["legs"][f["leg"]]
        if leg["instrument"] == "option":
            n = int(f.get("qty") or leg["contracts"])
            log(mode, {"event": "fill", "id": order.get("proposal_id"), "order_id": order["order_id"], "symbol": leg["symbol"],
                       "type": leg["type"], "side": "sell" if leg["action"] == "SELL TO OPEN" else "buy", "contracts": n,
                       "price": f["price"], "fees": round(spec_v2.FEE_PER_CONTRACT * n, 2),
                       "strike": float(leg["strike"]), "expiry": str(leg["expiry"])})
        else:
            sh = f.get("qty") or leg.get("shares")
            if not sh and leg.get("dollars") and float(f.get("price") or 0) > 0:
                sh = round(float(leg["dollars"]) / float(f["price"]), 6)
            log(mode, {"event": "stock_fill", "order_id": order["order_id"], "symbol": leg["symbol"],
                       "side": leg["action"].lower(), "shares": sh, "price": f["price"],
                       **({"parked": True} if leg.get("parked") else {})})


def cancel(mode, oid) -> dict:
    o = next((x for x in outbox(mode) if x["order_id"] == oid), None)
    if not o or o["status"] != "queued":
        return {"ok": False, "error": "Only an order still waiting can be cancelled."}
    _put_order(mode, {"order_id": oid, "status": "cancelled", "closed": now()})
    log(mode, {"event": "order_cancelled", "order_id": oid})
    return {"ok": True}


def change_settings(mode, changes, note="") -> dict:
    clean, errors = clean_settings(changes)
    if errors:
        return {"ok": False, "error": " ".join(errors)}
    cur = load_settings(mode)
    new = {"rules": {**cur.get("rules", {}), **clean["rules"]},
           "uncovered_lots": {**cur.get("uncovered_lots", {}), **clean["uncovered_lots"]}}
    new["rules"] = {k: v for k, v in new["rules"].items() if v != getattr(spec_v2, k)}
    (state_dir(mode) / "settings.json").write_text(json.dumps(new, indent=1, sort_keys=True))
    log(mode, {"event": "settings", "before": cur, "after": new, "note": note})
    return {"ok": True}


def preview(mode, changes) -> dict:
    """What the proposals would be under changed settings. Saves nothing."""
    clean, errors = clean_settings(changes)
    if errors:
        return {"ok": False, "error": " ".join(errors)}
    cur = load_settings(mode)
    new = {"rules": {**cur.get("rules", {}), **clean["rules"]},
           "uncovered_lots": {**cur.get("uncovered_lots", {}), **clean["uncovered_lots"]}}
    a, b = run_engine(mode, cur), run_engine(mode, new)
    line = lambda p: (f"{p['action']} {p.get('contracts', p.get('shares', ''))} {p['symbol']} "
                      + (f"${p['strike']:g} {p['expiry']}" if "strike" in p else p["kind"]))
    return {"ok": True, "now": [line(p) for p in a["proposals"]], "after": [line(p) for p in b["proposals"]],
            "changed": [line(p) for p in a["proposals"]] != [line(p) for p in b["proposals"]]}


def record_run(mode, why="manual") -> dict:
    out = run_engine(mode)
    snap = load_snapshot(mode)
    if snap is None:
        return out
    R = rules_for(load_settings(mode))
    pf = portfolio(mode, snap, R)
    log(mode, {"event": "run", "why": why, "snapshot_date": snap["today"], "version": out["version"],
               "account_value": pf["account_value"], "cash": pf["cash"], "lots_total": pf["lots_total"],
               "calls_out": pf["calls_out"], "proposals": [p["id"] for p in out["proposals"]],
               "skipped": out["skipped"], "blocked": out["blocked"]})
    seen = {e.get("id") for e in _lines(state_dir(mode) / "journal.jsonl") if e.get("event") == "proposed"}
    for p in out["proposals"]:
        if p["id"] not in seen:
            log(mode, {"event": "proposed", **p})
    return out


# ------------------------------------------------------------------ staging only: simulated fills
def _fee(R, n):
    return R.FEE_PER_CONTRACT * n


def apply_leg(snap: dict, leg: dict, price: float, R) -> list:
    """Edit the snapshot as if the leg filled at `price`. Returns journal events."""
    sym, act, ev = leg["symbol"], leg["action"], []
    a = snap["account"]
    if leg["instrument"] == "option":
        n = int(leg["contracts"])
        key = lambda o: (o["symbol"] == sym and o["type"] == leg["type"] and abs(float(o["strike"]) - float(leg["strike"])) < 1e-9
                         and str(o["expiry"]) == str(leg["expiry"]))
        cur = next((o for o in snap["short_options"] if key(o)), None)
        if act == "SELL TO OPEN":
            a["cash"] = round(float(a["cash"]) + price * 100 * n - _fee(R, n), 2)
            if cur:
                cur["contracts"] = int(cur["contracts"]) + n
            else:
                snap["short_options"].append({"symbol": sym, "type": leg["type"], "strike": float(leg["strike"]),
                                              "expiry": str(leg["expiry"]), "contracts": n})
            side = "sell"
        else:
            a["cash"] = round(float(a["cash"]) - price * 100 * n - _fee(R, n), 2)
            cur["contracts"] = int(cur["contracts"]) - n
            snap["short_options"] = [o for o in snap["short_options"] if int(o["contracts"]) > 0]
            side = "buy"
        ev.append({"event": "fill", "symbol": sym, "type": leg["type"], "side": side, "contracts": n, "price": price,
                   "fees": round(_fee(R, n), 2), "strike": float(leg["strike"]), "expiry": str(leg["expiry"])})
    else:
        pos = next((p for p in snap["positions"] if p["symbol"] == sym), None)
        sh = float(leg["dollars"]) / price if leg.get("dollars") else float(leg["shares"])
        if act == "BUY":
            a["cash"] = round(float(a["cash"]) - sh * price, 2)
            if pos:
                tot = float(pos["shares"]) + sh
                pos["avg_cost"] = round((float(pos["shares"]) * float(pos["avg_cost"]) + sh * price) / tot, 4)
                pos["shares"] = round(tot, 6)
            else:
                snap["positions"].append({"symbol": sym, "shares": round(sh, 6), "avg_cost": round(price, 4)})
        else:
            a["cash"] = round(float(a["cash"]) + sh * price, 2)
            pos["shares"] = round(float(pos["shares"]) - sh, 6)
            snap["positions"] = [p for p in snap["positions"] if float(p["shares"]) > 1e-9]
        snap.setdefault("quotes", {}).setdefault(sym, price)
        ev.append({"event": "stock_fill", "symbol": sym, "side": act.lower(), "shares": round(sh, 6), "price": price,
                   **({"parked": True} if leg.get("parked") else {})})
    return ev


def _fill_price(snap, leg):
    if leg.get("limit") not in (None, ""):
        return float(leg["limit"])
    for c in snap.get("candidates") or []:
        if c["symbol"] == leg["symbol"]:
            return float(c["last"])
    return float((snap.get("quotes") or {}).get(leg["symbol"], 0))


def simulate_fill(mode, oid) -> dict:
    if mode != "staging":
        return {"ok": False, "error": "Simulated fills exist only in staging."}
    o = next((x for x in outbox(mode) if x["order_id"] == oid), None)
    if not o or o["status"] != "queued":
        return {"ok": False, "error": "That order is not waiting."}
    snap, R = load_snapshot(mode), rules_for(load_settings(mode))
    for leg in o["legs"]:
        chk = check_order(mode, snap, R, leg)
        if chk["blocks"]:
            return {"ok": False, "error": "The account changed since this was queued: " + " ".join(chk["blocks"])}
        px = _fill_price(snap, leg)
        if px <= 0:
            return {"ok": False, "error": f"No price to fill {leg['symbol']} at."}
        for e in apply_leg(snap, leg, px, R):
            log(mode, {**e, "id": o.get("proposal_id"), "order_id": oid, "simulated": True})
    (state_dir(mode) / "snapshot.json").write_text(json.dumps(snap, indent=1))
    _put_order(mode, {"order_id": oid, "status": "filled", "closed": now(), "send_note": "STAGING: simulated fill at the limit. No order was sent."})
    record_run(mode, "after simulated fill")
    return {"ok": True}


def simulate_expiry(mode, sym, strike, expiry, outcome) -> dict:
    """Staging: end a short call as 'expired' or 'assigned'."""
    if mode != "staging":
        return {"ok": False, "error": "Only in staging."}
    snap = load_snapshot(mode)
    o = next((x for x in snap["short_options"] if x["symbol"] == sym and abs(float(x["strike"]) - float(strike)) < 1e-9
              and str(x["expiry"]) == str(expiry) and x["type"] == "call"), None)
    if not o:
        return {"ok": False, "error": "No such open call."}
    n = int(o["contracts"])
    snap["short_options"] = [x for x in snap["short_options"] if x is not o]
    if outcome == "assigned":
        pos = next(p for p in snap["positions"] if p["symbol"] == sym)
        pos["shares"] = round(float(pos["shares"]) - 100 * n, 6)
        snap["account"]["cash"] = round(float(snap["account"]["cash"]) + float(strike) * 100 * n, 2)
        after = pos["shares"]
        snap["positions"] = [p for p in snap["positions"] if float(p["shares"]) > 1e-9]
        log(mode, {"event": "assigned", "symbol": sym, "type": "call", "strike": float(strike), "contracts": n,
                   "shares_after": after, "proceeds": round(float(strike) * 100 * n, 2), "simulated": True})
    else:
        log(mode, {"event": "expired", "symbol": sym, "type": "call", "strike": float(strike), "expiry": str(expiry),
                   "contracts": n, "simulated": True})
    (state_dir(mode) / "snapshot.json").write_text(json.dumps(snap, indent=1))
    record_run(mode, f"after simulated {outcome}")
    return {"ok": True}


# ------------------------------------------------------------------ the improvement loop
# Goals (the owner writes them), notes (the assistant's notebook) and suggestions (one proposed
# change at a time, with a status). A suggestion that names a settings change is checked against
# ADJUSTABLE the moment it is filed, so nothing outside those bounds can even be proposed.
AGENT_CAN_TUNE = config.get("WHEEL_AGENT_CAN_TUNE", "0") == "1"


def goals(mode) -> str:
    p = state_dir(mode) / "goals.md"
    return p.read_text() if p.exists() else ""


def set_goals(mode, text, source="you") -> dict:
    text = str(text or "").strip()[:4000]
    (state_dir(mode) / "goals.md").write_text(text + "\n")
    log(mode, {"event": "goals", "source": source, "text": text})
    return {"ok": True}


def add_note(mode, text, source="assistant") -> dict:
    text = str(text or "").strip()[:2000]
    if not text:
        return {"ok": False, "error": "Empty note."}
    log(mode, {"event": "note", "source": source, "text": text})
    return {"ok": True}


def notes(mode, limit=30) -> list:
    return [{"at": e["at"], "source": e.get("source"), "text": e["text"]}
            for e in _lines(state_dir(mode) / "journal.jsonl") if e.get("event") == "note"][::-1][:limit]


def suggestions(mode) -> list:
    latest = {}
    for s in _lines(state_dir(mode) / "suggestions.jsonl"):
        latest[s["id"]] = {**latest.get(s["id"], {}), **s}
    return sorted(latest.values(), key=lambda s: s["created"], reverse=True)


def _put_suggestion(mode, rec) -> None:
    with (state_dir(mode) / "suggestions.jsonl").open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def add_suggestion(mode, title, why, change=None, source="assistant") -> dict:
    title, why = str(title or "").strip()[:200], str(why or "").strip()[:2000]
    if not title or not why:
        return {"ok": False, "error": "A suggestion needs a title and a reason."}
    clean = None
    if change:
        clean, errors = clean_settings(change)
        if errors:
            return {"ok": False, "error": "That change is outside what can be adjusted: " + " ".join(errors)}
        if not clean["rules"] and not clean["uncovered_lots"]:
            clean = None
    if sum(1 for s in suggestions(mode) if s["status"] == "open" and s.get("source") == source) >= 3:
        return {"ok": False, "error": "Three suggestions from this source are already open. Resolve one first."}
    sid = hashlib.sha1(f"{now()}|{title}".encode()).hexdigest()[:8]
    rec = {"id": sid, "created": now(), "source": source, "title": title, "why": why, "change": clean,
           "status": "open", "test_result": None}
    _put_suggestion(mode, rec)
    log(mode, {"event": "suggestion", "id": sid, "source": source, "title": title, "why": why, "change": clean})
    return {"ok": True, "id": sid}


def suggestion_tested(mode, sid, result, source="contractor") -> dict:
    s = next((x for x in suggestions(mode) if x["id"] == sid), None)
    if not s:
        return {"ok": False, "error": "No such suggestion."}
    result = str(result or "").strip()[:2000]
    _put_suggestion(mode, {"id": sid, "test_result": result, "tested_at": now(), "tested_by": source})
    log(mode, {"event": "suggestion_tested", "id": sid, "source": source, "title": s["title"], "result": result})
    return {"ok": True}


def suggestion_decide(mode, sid, decision, note="", source="you") -> dict:
    """adopt (apply its settings change, if it has one) or reject. `source` says who decided."""
    s = next((x for x in suggestions(mode) if x["id"] == sid), None)
    if not s or s["status"] != "open":
        return {"ok": False, "error": "That suggestion is not open."}
    if decision == "adopt":
        if source != "you" and not AGENT_CAN_TUNE:
            return {"ok": False, "error": "Only the owner can adopt a suggestion (WHEEL_AGENT_CAN_TUNE is 0)."}
        if source != "you" and not s.get("change"):
            return {"ok": False, "error": "This suggestion has no settings change the assistant could apply."}
        if s.get("change"):
            r = change_settings(mode, s["change"], f"adopted suggestion {sid}: {s['title']}" + (f" ({note})" if note else ""))
            if not r["ok"]:
                return r
    elif decision != "reject":
        return {"ok": False, "error": "Decision must be adopt or reject."}
    status = "adopted" if decision == "adopt" else "rejected"
    _put_suggestion(mode, {"id": sid, "status": status, "decided_at": now(), "decided_by": source, "decision_note": note})
    log(mode, {"event": "suggestion_decided", "id": sid, "decision": status, "by": source, "title": s["title"], "note": note})
    return {"ok": True}


# ------------------------------------------------------------------ outside context
# Research the assistant found (news, filings, insider or congressional trades, an earnings date
# from another source). It is shown next to proposals for the owner to read. The ENGINE NEVER
# READS IT: a data source only changes a trade after it has been tested and added as a rule.
def add_context(mode, symbol, text, url="", source="assistant") -> dict:
    symbol, text = str(symbol or "").upper().strip()[:10], str(text or "").strip()[:600]
    if not symbol or not text:
        return {"ok": False, "error": "Context needs a symbol and a sentence."}
    log(mode, {"event": "context", "symbol": symbol, "text": text, "url": str(url or "")[:400], "source": source})
    return {"ok": True}


def context(mode, days=14, per_symbol=5) -> dict:
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    out = {}
    for e in _lines(state_dir(mode) / "journal.jsonl")[::-1]:
        if e.get("event") == "context" and e["at"] >= cutoff and len(out.setdefault(e["symbol"], [])) < per_symbol:
            out[e["symbol"]].append({"at": e["at"], "text": e["text"], "url": e.get("url", ""), "source": e.get("source")})
    return out


# ------------------------------------------------------------------ scoreboard, history, log
def scoreboard(mode, pf: dict) -> dict:
    ev = _lines(state_dir(mode) / "journal.jsonl")
    fills = [e for e in ev if e.get("event") == "fill"]
    sold = sum(float(e["price"]) * 100 * int(e["contracts"]) for e in fills if e["side"] == "sell")
    bought = sum(float(e["price"]) * 100 * int(e["contracts"]) for e in fills if e["side"] == "buy")
    fees = sum(float(e.get("fees", 0)) for e in fills)
    base = _read(state_dir(mode) / "base.json", None)
    snap = load_snapshot(mode)
    start = held = None
    if base:
        q0 = base.get("quotes", {})
        start = float(base["account"]["cash"]) + sum(float(p["shares"]) * float(q0.get(p["symbol"], p["avg_cost"])) for p in base["positions"])
        q1 = {**q0, **(snap.get("quotes") or {})}
        held = float(base["account"]["cash"]) + sum(float(p["shares"]) * float(q1.get(p["symbol"], p["avg_cost"])) for p in base["positions"])
    weeks = {}
    for e in fills:
        k = dt.datetime.fromisoformat(e["at"]).strftime("%G-W%V")
        w = weeks.setdefault(k, {"week": k, "premium": 0.0, "net": 0.0})
        g = float(e["price"]) * 100 * int(e["contracts"])
        w["premium"] += g if e["side"] == "sell" else 0.0
        w["net"] += (g if e["side"] == "sell" else -g) - float(e.get("fees", 0))
    return {"premium_collected": round(sold, 2), "bought_back": round(bought, 2), "fees": round(fees, 2),
            "net_option_cash": round(sold - bought - fees, 2),
            "open_option_liability": pf["open_option_liability"],
            "option_profit_if_closed_now": round(sold - bought - fees - pf["open_option_liability"], 2),
            "start_value": round(start, 2) if start is not None else None,
            "account_value": pf["account_value"],
            "profit": round(pf["account_value"] - start, 2) if start is not None else None,
            "just_held_value": round(held, 2) if held is not None else None,
            "vs_just_held": round(pf["account_value"] - held, 2) if held is not None else None,
            "call_aways": sum(1 for e in ev if e.get("event") == "assigned"),
            "weeks": [{**w, "premium": round(w["premium"], 2), "net": round(w["net"], 2)} for w in sorted(weeks.values(), key=lambda w: w["week"])]}


def history(mode) -> list:
    return [{"at": e["at"], "account_value": e["account_value"], "lots_total": e["lots_total"],
             "calls_out": e["calls_out"], "why": e.get("why")} for e in _lines(state_dir(mode) / "journal.jsonl")
            if e.get("event") == "run"]


def _say(e: dict) -> str:
    k = e.get("event")
    if k == "run":
        return f"Engine ran ({e.get('why')}): {len(e.get('proposals', []))} proposals, account ${e['account_value']:,.2f}"
    if k == "proposed":
        tail = f"${e['strike']:g} call {e['expiry']}" if "strike" in e else e["kind"]
        return f"Proposed: {e['action']} {e.get('contracts', e.get('shares', ''))} {e['symbol']} {tail}"
    if k == "decision":
        s = f"You {'approved' if e['decision'] == 'placed' else 'declined'} the {e.get('kind', 'proposal')} on {e.get('symbol', '')}"
        if e.get("edited"):
            s += " after editing it"
        if e.get("overrides"):
            s += f", overriding {len(e['overrides'])} warning(s)"
        return s + (f". Note: {e['reason']}" if e.get("reason") else "")
    if k == "order_queued":
        l = e["legs"][0]
        s = f"Order queued by {'you' if e['source'] == 'you' else 'the engine'}: " + " then ".join(
            f"{x['action']} {x.get('contracts', x.get('shares', x.get('dollars', '')))} {x['symbol']}"
            + (f" ${x['strike']:g} {x['type']} {x['expiry']}" if x.get("strike") else "") for x in e["legs"])
        return s + (f" (overrode: {'; '.join(e['overrides'])})" if e.get("overrides") else "")
    if k == "order_cancelled":
        return f"Order {e['order_id']} cancelled"
    if k == "fill":
        return (f"{'SIMULATED ' if e.get('simulated') else ''}Fill: {'sold' if e['side'] == 'sell' else 'bought'} {e['contracts']} {e['symbol']} "
                f"${e.get('strike', 0):g} {e['type']} at ${float(e['price']):.2f}")
    if k == "stock_fill":
        return f"{'SIMULATED ' if e.get('simulated') else ''}Fill: {e['side']} {e['shares']:g} {e['symbol']} shares at ${float(e['price']):,.2f}"
    if k == "assigned":
        return f"{'SIMULATED ' if e.get('simulated') else ''}Called away: {100 * e['contracts']} {e['symbol']} shares sold at ${e['strike']:g}"
    if k == "expired":
        return f"{'SIMULATED ' if e.get('simulated') else ''}Expired worthless: {e['symbol']} ${e['strike']:g} call, premium kept"
    if k == "settings":
        return f"Settings changed: {json.dumps(e['after'])}" + (f". Note: {e['note']}" if e.get("note") else "")
    if k == "scenario":
        return f"Staging scenario loaded: {e.get('name')}"
    if k == "note":
        return f"Note from {e.get('source')}: {e['text']}"
    if k == "context":
        return f"Context on {e['symbol']} from {e.get('source')}: {e['text']}"
    if k == "goals":
        return "Goals updated"
    if k == "suggestion":
        return f"Suggestion from {e.get('source')}: {e['title']}"
    if k == "suggestion_tested":
        return f"Suggestion tested by {e.get('source')}: {e['title']}. Result: {e['result']}"
    if k == "suggestion_decided":
        return f"Suggestion {e['decision']} by {e.get('by')}: {e['title']}"
    if k == "order_sent":
        return f"Order {e['order_id']} sent to {e.get('broker')}"
    return k or "?"


def decision_log(mode, limit=400) -> list:
    ev = _lines(state_dir(mode) / "journal.jsonl")
    out = []
    for i, e in enumerate(ev):
        sym = e.get("symbol") or (e.get("legs") or [{}])[0].get("symbol") or ""
        out.append({"n": i, "at": e["at"], "event": e.get("event"), "symbol": sym, "text": _say(e), "detail": e})
    return out[::-1][:limit]


def full_state(mode) -> dict:
    snap = load_snapshot(mode)
    settings = load_settings(mode)
    R = rules_for(settings)
    if snap is None:
        return {"mode": mode, "send_enabled": SEND_ENABLED and mode == "live", "broker": config.BROKER, "empty": True,
                "settings": settings_view(settings)}
    out = run_engine(mode, settings)
    pf = portfolio(mode, snap, R)
    queued = {o.get("proposal_id") for o in outbox(mode) if o["status"] in ("queued", "sent")}
    declined = {e.get("id") for e in _lines(state_dir(mode) / "journal.jsonl")
                if e.get("event") == "decision" and e.get("decision") == "declined"}
    ctx = context(mode)
    for p in out["proposals"]:
        p["context"] = ctx.get(p["symbol"], [])
        p["legs_to_send"] = legs_of(p)
        p["status"] = "queued" if p["id"] in queued else ("declined" if p["id"] in declined else "new")
    return {"mode": mode, "send_enabled": SEND_ENABLED and mode == "live", "broker": snap.get("broker") or config.BROKER,
            "generated": now(), "engine": out, "portfolio": pf,
            "outbox": outbox(mode), "settings": settings_view(settings), "scoreboard": scoreboard(mode, pf),
            "history": history(mode), "log": decision_log(mode),
            "context": ctx,
            "loop": {"goals": goals(mode), "notes": notes(mode), "suggestions": suggestions(mode),
                     "agent_can_tune": AGENT_CAN_TUNE}}
