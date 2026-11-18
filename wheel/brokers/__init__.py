"""Broker adapters. Each one offers:
    snapshot(extra_symbols=()) -> dict   the account in the shape engine.py reads
    send(order) -> dict                  pass one approved outbox order to the broker
    status(order) -> dict                has it filled?
No adapter decides anything: the engine proposes, the owner approves, the adapter carries.
"""


def get(name):
    if name == "alpaca":
        from . import alpaca
        return alpaca
    if name == "robinhood":
        from . import robinhood
        return robinhood
    raise ValueError(f"Unknown broker {name!r}. Use alpaca or robinhood.")
