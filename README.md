# Agentic Wheel Kit

**Start here:** [What it does](#what-it-does) · [What you need](#what-you-need) · [Setup](#setup) · [What was tested](RESULTS.md) · [Disclaimer](DISCLAIMER.md) · [Free 24/7 server](docs/ORACLE_SETUP.md)

An options wheel that an AI agent prepares and you approve. The engine reads your account, proposes each trade with the reason and the numbers, and waits. Nothing is sent until you say yes.

It comes with a control panel, two broker connections (Alpaca and Robinhood agent accounts), a server installer, and the tests the rules came from.

> **Real money is possible. Read [DISCLAIMER.md](DISCLAIMER.md) first.** This is not financial advice. The kit starts in practice mode with sending turned off. In testing, selling covered calls did **not** beat holding the shares; it gave smaller drops and less upside. One rule that did better is labelled beta. The numbers are in [RESULTS.md](RESULTS.md).

---

## What it does

```
 1. READ             2. PROPOSE              3. YOU DECIDE            4. SEND                5. RECORD
 Your account  -->   Engine applies    -->   Control panel or   -->   Approved orders  -->   Journal and scoreboard:
 (Alpaca or          written rules:          Telegram: approve,       go to your broker.     premium next to profit,
  Robinhood)         calls, rolls,           edit or decline.         Robinhood asks you     next to "just held".
                     rotation (beta)         Guardrails check         again in its app.
                                             everything you enter.
```

The rules, in plain words:

- **Covered calls** on shares you hold: about 35 days out, strike at or above your net cost, one lot always kept free.
- **Rolls**: when a call is in the money with a week left, roll it up and out, only if that pays a net credit.
- **Rotation (beta)**: when cash is free, buy the stock with the strongest one-year gain that is above its 200-day average, then sell calls on it.
- **Locked guardrails**: never a call below your cost, never through an earnings date, never anything if trade approvals are off, no puts.

You can change the adjustable rules from the control panel. Every change is logged with what it did to the proposals.

## What you need

| You need | Why | Cost |
|---|---|---|
| A computer with Python 3.9 or newer (3.10 or newer for Robinhood) | Runs the kit | Free |
| **Either** an Alpaca account | Paper trading with fake money, through an API key | Free |
| **Or** a Robinhood agent account, options level 2, trade approvals ON | Real account; every order is approved in the Robinhood app | Free to open |
| An Oracle Cloud Always Free server (optional) | Runs it while your computer is off | Free |
| A Telegram bot (optional) | Proposals on your phone with Approve and Decline buttons | Free |
| Hermes or another always-on agent (optional) | Morning summary and a weekly review | Free |

## Setup

```
git clone https://github.com/aabrole/agentic-wheel-kit.git
cd agentic-wheel-kit
./setup.sh
```

That installs everything into a local folder, runs the tests, and opens the control panel in **practice mode** at http://127.0.0.1:8090 with a made-up account. Click everything. Nothing can be sent.

Prefer to have an AI assistant do it? Paste [prompts/SETUP.md](prompts/SETUP.md) into Claude Code or a similar tool.

### Practice mode

Use the scenario menu at the top:

- **Sample account**: 300 HOOD shares and a little cash. You get a covered-call proposal.
- **Made up: 200 HOOD called away**: free cash, so you get a rotation proposal.
- **Made up: roll due**: a call in the money near expiry, so you get a roll proposal.

Approve an order, press **Simulate fill**, and watch the portfolio, scoreboard and log update. Try editing a strike below your cost to see a guardrail stop you.

### Connect Alpaca (paper)

1. Create an Alpaca account, switch to **Paper Trading**, and generate an API key.
2. Put the key and secret in `.env` as `ALPACA_KEY` and `ALPACA_SECRET`. Leave `ALPACA_LIVE=0`.
3. Read the account and open the live view:

```
.venv/bin/python -m wheel.agent read
WHEEL_MODE=live .venv/bin/python wheel/dashboard/server.py
```

Alpaca has no approval step of its own. The Approve button on the control panel is the approval.

### Connect Robinhood

1. Set `BROKER=robinhood` in `.env`.
2. Run `.venv/bin/python -m wheel.agent connect`. Sign in on Robinhood's own page and choose the account the agent may use.
3. In the Robinhood app, make sure **trade approvals are on** for that account. The engine proposes nothing if they are off.
4. `.venv/bin/python -m wheel.agent read`, then open the live view as above.

The Robinhood order path has not been run end to end from this kit yet. Start with one small order.

### Turning sending on

Sending is off until you set `WHEEL_SEND=1` in `.env`. With it off, approved orders wait in the "Orders waiting to be sent" list and never leave your machine.

```
.venv/bin/python -m wheel.agent send     # pass approved orders to the broker
.venv/bin/python -m wheel.agent sync     # check whether they filled
.venv/bin/python -m wheel.agent cycle    # read, sync, notify, send in one go
```

### Run it on a free server

See [docs/ORACLE_SETUP.md](docs/ORACLE_SETUP.md). One script installs the kit behind HTTPS and a password.

### Telegram and Hermes

The kit includes a Hermes layer: a skill that teaches Hermes the routine and its limits, and three scheduled jobs (morning run, afternoon check, Friday review).

```
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash     # Hermes's own installer
hermes setup                                                           # choose your model
bash deploy/hermes.sh                                                  # add the wheel skill and jobs
hermes gateway setup && hermes gateway start                           # connect your phone
```

Hermes reports and reminds. It is told never to approve a trade or change a rule. See [docs/HERMES.md](docs/HERMES.md) for details and for the kit's own Telegram Approve and Decline buttons.

## Run the tests on your own data

[prompts/RUN_FULL_TEST.md](prompts/RUN_FULL_TEST.md) is a prompt you paste into Claude Code. With your own Alpaca data key it downloads about three years of option prices and reruns every test in [RESULTS.md](RESULTS.md).

It takes several hours and a few gigabytes of disk. The results describe one stretch of history. They are not a forecast.

## What is in the repo

| Folder | What it is |
|---|---|
| `wheel/engine.py`, `wheel/spec.py`, `wheel/spec_v2.py` | The proposal engine and its rules. Pure functions: an account goes in, proposals come out. It places nothing. |
| `wheel/dashboard/` | The control panel: one small server, one page. |
| `wheel/brokers/` | Alpaca and Robinhood connections. |
| `wheel/agent.py` | Reads the account, sends approved orders, Telegram. |
| `deploy/` | Server install script and services, and `hermes.sh` for the Hermes layer. |
| `hermes/` | The `wheel-desk` skill Hermes loads. |
| `research/` | The test code and the small data files it needs. Downloaded option prices are not included. |
| `tests/` | 59 automated tests. Run them with `.venv/bin/python -m pytest -q tests`. |

## What is and is not verified

| Part | Status |
|---|---|
| Engine, guardrails, control panel | Tested (59 automated tests, clicked through in a browser) |
| Alpaca: reading an account | Run against a real paper account |
| Alpaca: sending orders | Tested against a fake connection only. Not yet run on a paper account. |
| Robinhood: connecting | Connection code is from a kit that ran a live stock bot |
| Robinhood: reading options, sending option orders | Written from Robinhood's responses. Not yet run end to end. |
| Telegram buttons | Written, not yet run |
| Hermes layer (skill and scheduled jobs) | Written from Hermes's documentation, dry-run only. Not yet run on a live Hermes install. |
| Server install | Adapted from a working installer. Not yet run for this kit. |
| Research rerun on a fresh clone | Runs on the author's machine. Fresh-clone run not yet verified. |

## License

MIT. See [LICENSE](LICENSE).
