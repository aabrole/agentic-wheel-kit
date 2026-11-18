"""
journal.py - append-only record of what was proposed, what the owner decided and what filled.

One JSON object per line. The engine never reads prices from here; it reads only the net call
premium kept per share for each holding, which sets the cost floor for the next call.

Events (field "event"):
  proposed   the proposal dict as shown
  decision   {"id", "decision": "placed" | "declined", "reason"}
  fill       {"id", "symbol", "type", "side": "sell" | "buy", "contracts", "price", "fees"}
  expired    {"symbol", "type", "strike", "expiry"}
  assigned   {"symbol", "type", "strike", "contracts", "shares_after"}
             calls: shares sold; puts: shares bought. shares_after = shares of that symbol held
             once the assignment has settled (0 if the whole holding was called away).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


def append(path, event: dict) -> None:
    rec = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **event}
    with Path(path).open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def read(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def net_option_cash(events, symbol=None) -> float:
    """Dollars in minus dollars out on option fills, after fees. This is the income figure:
    gross premium overstated it about twofold in testing."""
    total = 0.0
    for e in events:
        if e.get("event") != "fill" or (symbol and e.get("symbol") != symbol):
            continue
        gross = float(e["price"]) * 100 * int(e["contracts"])
        total += (gross if e["side"] == "sell" else -gross) - float(e.get("fees", 0.0))
    return round(total, 2)


def net_call_premium_per_share(events, symbol, shares) -> float:
    """Call premium kept on this holding so far, per share currently held. When some shares are
    called away, the premium that belonged to them leaves with them; the shares that remain
    keep their part. A full call-away resets it (a new wheel starts with a new cost)."""
    total = 0.0
    for e in events:
        if e.get("symbol") != symbol:
            continue
        if e.get("event") == "assigned" and e.get("type") == "call":
            if "shares_after" not in e:
                raise ValueError("a call assignment in the journal needs 'shares_after'")
            after, sold = float(e["shares_after"]), 100 * int(e["contracts"])
            total = total * after / (after + sold) if after > 0 else 0.0
        elif e.get("event") == "fill" and e.get("type") == "call":
            gross = float(e["price"]) * 100 * int(e["contracts"])
            total += (gross if e["side"] == "sell" else -gross) - float(e.get("fees", 0.0))
    return round(total / shares, 4) if shares else 0.0
