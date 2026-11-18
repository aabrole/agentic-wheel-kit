#!/usr/bin/env bash
# One-shot setup. Safe to run again.
#   ./setup.sh            install, run the tests, open the control panel in practice mode
#   ./setup.sh --no-run   install and test only
#   ./setup.sh --hermes   also add the Hermes layer (skill + three scheduled jobs)
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "Python 3.9 or newer is required."; exit 1; }

echo "== Python environment (.venv)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q numpy yfinance pytest
if ! .venv/bin/pip install -q "mcp==2.3.0" 2>/dev/null; then
  echo "   (the Robinhood connection package needs Python 3.10 or newer and did not install; Alpaca and practice mode still work)"
fi

echo "== Settings (.env)"
if [ ! -f .env ]; then
  cp .env.example .env && chmod 600 .env
  echo "   Created .env from the example. Nothing is connected yet and sending is OFF."
fi

echo "== Tests"
.venv/bin/python -m pytest -q tests

cat <<'MSG'

Setup is done. Nothing is connected to a broker and no order can be sent.

Next:
  1. Practice mode (made-up account, every button works):
       .venv/bin/python wheel/dashboard/server.py        then open http://127.0.0.1:8090
  2. Connect a broker when you are ready:
       Alpaca paper:  put ALPACA_KEY and ALPACA_SECRET in .env, then  .venv/bin/python -m wheel.agent read
       Robinhood:     set BROKER=robinhood in .env, then              .venv/bin/python -m wheel.agent connect
  3. See your own account:  WHEEL_MODE=live .venv/bin/python wheel/dashboard/server.py
  4. Add the always-on assistant (Hermes):  ./setup.sh --hermes     (or: bash deploy/hermes.sh)
  5. Read DISCLAIMER.md before you set WHEEL_SEND=1.
MSG

if [ "${1:-}" = "--hermes" ]; then
  bash deploy/hermes.sh
  exit 0
fi

if [ "${1:-}" != "--no-run" ]; then
  echo
  echo "Starting practice mode at http://127.0.0.1:8090 (Ctrl-C to stop)"
  exec .venv/bin/python wheel/dashboard/server.py
fi
