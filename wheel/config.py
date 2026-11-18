"""config.py - settings from the environment, with a .env file at the repo root as the source."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def get(name, default=""):
    return os.environ.get(name, default)


BROKER = get("BROKER", "alpaca").lower()            # alpaca | robinhood
SEND = get("WHEEL_SEND", "0") == "1"                # the master switch for passing approved orders to the broker
