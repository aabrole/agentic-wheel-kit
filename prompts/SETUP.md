# Setup prompt

Paste everything below into Claude Code (or a similar coding assistant) in an empty folder.

---

Set up the Agentic Wheel Kit for me.

1. Clone `https://github.com/aabrole/agentic-wheel-kit.git` and run `./setup.sh --no-run`. If a test fails, stop and show me the failure.
2. Read `DISCLAIMER.md` and `RESULTS.md` and give me a five-line summary of what the tests showed and what is still beta. Do not soften it.
3. Start practice mode (`.venv/bin/python wheel/dashboard/server.py`) and tell me the address to open.
4. Ask me which broker I want: Alpaca paper or Robinhood.
   - Alpaca: tell me where to create a paper API key. I will paste the key and secret into `.env` myself. Do not ask me to paste them in chat.
   - Robinhood: set `BROKER=robinhood` in `.env` and tell me to run `.venv/bin/python -m wheel.agent connect` myself, since it needs me to sign in.
5. When I say the broker is connected, run `.venv/bin/python -m wheel.agent read` and `.venv/bin/python -m wheel.agent status`, and show me the result.

Rules for you:

- Leave `WHEEL_SEND=0`. Do not change it. I will turn sending on myself if I decide to.
- Do not run `wheel.agent send`.
- Do not approve, edit or decline any proposal for me.
- Never print my keys.
