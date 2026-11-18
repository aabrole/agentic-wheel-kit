"""
agent.py - the part that talks to the outside world. Run from the repo root:

    python3 -m wheel.agent connect     # Robinhood only: sign in and bind the agent account
    python3 -m wheel.agent read        # read the account from the broker into state/live/
    python3 -m wheel.agent notify      # send new proposals to Telegram (if configured)
    python3 -m wheel.agent send        # pass approved orders to the broker (needs WHEEL_SEND=1)
    python3 -m wheel.agent sync        # ask the broker whether sent orders filled
    python3 -m wheel.agent cycle       # read, sync, notify, send: what the server timer runs
    python3 -m wheel.agent telegram    # long-running: Approve / Decline buttons in Telegram
    python3 -m wheel.agent status

It never decides a trade. The engine proposes from the rules, the owner approves on the dashboard
(or in Telegram), and only then does `send` carry the order to the broker.
"""
import asyncio
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))

from . import brokers, candidates, config, earnings  # noqa: E402
from .universe import ETFS  # noqa: E402
import core  # noqa: E402

MODE = "live"


def _state(name):
    return core.state_dir(MODE) / name


def read():
    b = brokers.get(config.BROKER)
    snap = b.snapshot()
    if config.BROKER == "alpaca":
        need = [p["symbol"] for p in snap["positions"] if p["shares"] >= 100]
        snap["earnings"] = {**snap.get("earnings", {}), **earnings.lookup(need, ETFS)}
    cache = _state(f"candidates_{snap['today']}.json")
    if cache.exists():
        c = json.loads(cache.read_text())
    else:
        c = candidates.build()
        cache.write_text(json.dumps(c))
    snap["candidates"] = [{k: r.get(k) for k in ("symbol", "last", "sma200", "ret_252", "vol_252")} for r in c["candidates"]]
    snap["candidates_as_of"] = c["candidates_as_of"]
    snap["_note"] = f"LIVE: read from {snap.get('broker', config.BROKER)} at {core.now()}."
    _state("snapshot.json").write_text(json.dumps(snap, indent=1))
    if not _state("base.json").exists():
        _state("base.json").write_text(json.dumps(snap, indent=1))
    out = core.record_run(MODE, "account read")
    print(f"read {config.BROKER}: {len(snap['positions'])} positions, cash ${snap['account']['cash']:,.2f}, "
          f"{len(out['proposals'])} proposals, blocked: {out['blocked'] or 'no'}")
    return out


def send():
    b = brokers.get(config.BROKER)
    for o in core.outbox(MODE):
        more = o["status"] == "sent" and len(o.get("broker_orders") or []) < len(o["legs"])
        if o["status"] != "queued" and not more:
            continue
        if not config.SEND:
            print(f"order {o['order_id']}: NOT sent, WHEEL_SEND is 0")
            continue
        try:
            r = b.send(o)
        except Exception as e:
            r = {"status": o["status"], "send_note": f"NOT SENT: {type(e).__name__}: {str(e)[:300]}"}
        core.update_order(MODE, o["order_id"], **r)
        if r.get("status") == "sent" and o["status"] == "queued":
            core.log(MODE, {"event": "order_sent", "order_id": o["order_id"], "broker": config.BROKER, "note": r.get("send_note", "")})
        print(f"order {o['order_id']}: {r.get('status')} {r.get('send_note', '')}")


def sync():
    b = brokers.get(config.BROKER)
    for o in core.outbox(MODE):
        if o["status"] != "sent":
            continue
        r = b.status(o)
        if r.get("fills"):
            core.record_fills(MODE, o, r["fills"])
        core.update_order(MODE, o["order_id"], status=r["status"], broker_orders=r["broker_orders"],
                          **({"closed": core.now()} if r["status"] in ("filled", "cancelled") else {}))
        print(f"order {o['order_id']}: {r['status']}")


# ------------------------------------------------------------------ Telegram (optional)
def _tg(method, **params):
    token = config.get("TELEGRAM_BOT_TOKEN")
    data = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in params.items()}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", data=data, timeout=70) as r:
        return json.loads(r.read())


def _card(p):
    if p["kind"] == "covered call":
        head = f"Sell {p['contracts']} {p['symbol']} ${p['strike']:g} call, expires {p['expiry']}, limit ${p['limit']:.2f} (about ${p['credit_if_filled_at_limit']:,.0f})"
    elif p["kind"] == "roll up and out":
        head = f"Roll {p['contracts']} {p['symbol']} calls up to ${p['strike']:g} ({p['expiry']}) for about ${p['credit_if_filled_at_limit']:,.0f}"
    elif p["kind"] == "rotation stock":
        head = f"BETA rule: buy {p['shares']} {p['symbol']} at up to ${p['limit']:,.2f} (${p['cost']:,.0f})"
    else:
        head = f"{p['action']} {p['symbol']} ({p['kind']})"
    lines = [head, f"You are agreeing to: {p['agreeing_to']}."] + [f"Note: {f}" for f in p.get("flags", [])[:3]]
    return "\n".join(lines)


def notify():
    if not (config.get("TELEGRAM_BOT_TOKEN") and config.get("TELEGRAM_CHAT_ID")):
        print("Telegram is not configured; skipping.")
        return
    seen_file = _state("notified.json")
    seen = set(json.loads(seen_file.read_text())) if seen_file.exists() else set()
    st = core.full_state(MODE)
    for p in st["engine"]["proposals"]:
        if p["id"] in seen or p["status"] != "new":
            continue
        _tg("sendMessage", chat_id=config.get("TELEGRAM_CHAT_ID"), text=_card(p),
            reply_markup={"inline_keyboard": [[{"text": "Approve", "callback_data": f"a:{p['id']}"},
                                               {"text": "Decline", "callback_data": f"d:{p['id']}"}]]})
        seen.add(p["id"])
    seen_file.write_text(json.dumps(sorted(seen)))


def telegram():
    """Approve / Decline from Telegram. Only the configured chat is obeyed. An approval here queues
    the order exactly as the dashboard button does; on Robinhood the app still asks you again."""
    chat, offset = str(config.get("TELEGRAM_CHAT_ID")), 0
    print("listening for Telegram button presses (Ctrl-C to stop)")
    while True:
        for u in _tg("getUpdates", offset=offset, timeout=60).get("result", []):
            offset = u["update_id"] + 1
            cb = u.get("callback_query")
            if not cb or str(cb["message"]["chat"]["id"]) != chat:
                continue
            verb, pid = cb["data"].split(":", 1)
            r = core.decide(MODE, pid, "approve" if verb == "a" else "decline", note="from Telegram")
            msg = ("Approved and queued." if verb == "a" else "Declined.") if r.get("ok") else r.get("error", "Could not do that.")
            _tg("answerCallbackQuery", callback_query_id=cb["id"], text=msg[:190])
            _tg("sendMessage", chat_id=chat, text=msg)


def connect():
    from .brokers import rh_client as rh

    async def go():
        hs = rh.Handshake()
        task = asyncio.create_task(hs.run())
        await hs.url_ready.wait()
        if not hs.url:
            print("Could not start the connection:", rh.read_json("connection.json"))
            return
        print("\n1. Open this address, sign in to Robinhood and choose the agent account:\n\n" + hs.url)
        print("\n2. Robinhood then sends your browser to a localhost page that will not load. That is expected.")
        pasted = await asyncio.get_running_loop().run_in_executor(None, input, "3. Paste that page's full address here: ")
        err = hs.submit(pasted)
        if err:
            print("That address did not work:", err)
            task.cancel()
            return
        await task
        print(json.dumps(rh.read_json("connection.json"), indent=1))
    asyncio.run(go())


def status():
    st = core.full_state(MODE)
    if st.get("empty"):
        print("No account read yet. Run: python3 -m wheel.agent read")
        return
    pf = st["portfolio"]
    print(f"broker {st['broker']}  sending {'ON' if st['send_enabled'] else 'OFF'}  account ${pf['account_value']:,.2f}  "
          f"cash ${pf['cash']:,.2f}  calls out {pf['calls_out']}/{pf['lots_total']}  proposals {len(st['engine']['proposals'])}  "
          f"orders waiting {sum(o['status'] == 'queued' for o in st['outbox'])}")


def cycle():
    read()
    sync()
    notify()
    send()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"read": read, "send": send, "sync": sync, "notify": notify, "telegram": telegram, "connect": connect,
          "status": status, "cycle": cycle}.get(cmd)
    if not fn:
        sys.exit(__doc__)
    fn()
