"""
propose.py - print the engine's proposals for a snapshot file. Places nothing.

    python3 propose.py fixtures/sample_account.json            # readable cards
    python3 propose.py snapshot.json --json                     # machine-readable
    python3 propose.py snapshot.json --journal journal.jsonl    # log what was shown, use kept premium
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402
import journal  # noqa: E402


def money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def card(p: dict) -> str:
    L = [f"[{p['id']}] {p['action']} {p['contracts']} x {p['symbol']} ${p['strike']:g} "
         f"{p['kind']}, expires {p['expiry']} ({p['dte']} days)",
         f"  You are agreeing to: {p['agreeing_to']}."]
    a = p["if_assigned"]
    if p["kind"] == "covered call":
        L += [f"  If assigned: {a['shares_sold']} shares sold for ${a['proceeds']:,.2f} "
              f"({money(a['per_share_vs_net_cost'])} a share against your net cost of ${p['net_cost_per_share']:.2f}); "
              f"with the premium the result is {money(a['result_with_premium'])}.",
              f"  If it expires: you keep the shares and ${p['if_expires']['premium_kept']:,.2f}.",
              f"  Holding does better if {p['symbol']} is above ${p['holding_does_better_above']:.2f} at expiry.",
              f"  Downside: {p['downside']}."]
    else:
        L += [f"  Cash set aside: ${p['cash_set_aside']:,.2f}. If assigned you own {a['shares_bought']} shares "
              f"at a net ${a['net_cost_per_share']:.2f}.",
              f"  Most you can lose: ${p['max_loss']:,.2f}. Whole-account stress loss: ${p['stress_loss_whole_account']:,.2f}."]
    L += [f"  Order: limit ${p['limit']:.2f}, do not work it below ${p['limit_floor']:.2f}. "
          f"Credit at the limit: ${p['credit_if_filled_at_limit']:,.2f} after fees.",
          f"  Quote: stock ${p['spot']:g}, delta {p['delta']:.2f}"
          + (f", spread {p['spread_pct']:.0%} of mid, open interest {p['open_interest']}" if "spread_pct" in p else "") + "."]
    if p.get("evidence"):
        L.append(f"  Test record: {p['evidence']}")
    for f in p.get("flags", []):
        L.append(f"  Note: {f}")
    if p.get("rejected_strikes"):
        L.append("  Passed over: " + "; ".join(f"${r['strike']:g} ({r['reason']})" for r in p["rejected_strikes"]))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--journal")
    a = ap.parse_args()
    snap = json.loads(Path(a.snapshot).read_text())
    if a.journal:
        ev = journal.read(a.journal)
        snap.setdefault("net_premium", {})
        for p in snap["positions"]:
            snap["net_premium"].setdefault(p["symbol"], journal.net_call_premium_per_share(ev, p["symbol"], float(p["shares"])))
    out = engine.propose(snap)
    if a.journal:
        for p in out["proposals"]:
            journal.append(a.journal, {"event": "proposed", **p})
    if a.json:
        print(json.dumps(out, indent=1))
        return
    print(f"{out['version']}  snapshot {snap['today']}  account value ${out.get('account_value', 0):,.2f}")
    for b in out["blocked"]:
        print("BLOCKED:", b)
    if not out["proposals"] and not out["blocked"]:
        print("No proposal today.")
    for p in out["proposals"]:
        print("\n" + card(p))
    for n in out["notices"]:
        print("\nNOTICE:", n["text"])
    if out["skipped"]:
        print("\nNot proposed:")
        for s in out["skipped"]:
            print(f"  {s['symbol']}: {s['reason']}")
    print("\nNothing has been placed. Each order is yours to place or decline.")


if __name__ == "__main__":
    main()
