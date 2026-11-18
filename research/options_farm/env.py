"""Load keys from ../.env into os.environ without ever printing them."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    p = next((q for q in (ROOT / ".env", ROOT.parent / ".env") if q.exists()), None)
    if p is None:
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().replace("export ", "")
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)
    for new, old in (("ALPACA_KEY", "ALPACA_PAPER_KEY"), ("ALPACA_SECRET", "ALPACA_PAPER_SECRET")):
        if os.environ.get(new) and not os.environ.get(old):      # the kit's .env names; data works with paper keys
            os.environ[old] = os.environ[new]


load_env()
