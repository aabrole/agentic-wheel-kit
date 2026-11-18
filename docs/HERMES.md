# Telegram, and an always-on agent as the bookkeeper

The kit decides nothing with AI. The engine applies written rules. An always-on agent such as Hermes is useful around it: it runs the cycle on time, tells you what happened, and writes the weekly review.

This page has not been run end to end yet. Treat it as a starting point.

## Telegram: proposals with buttons

1. In Telegram, message **@BotFather**, create a bot, and copy its token.
2. Message your new bot once, then find your chat id (for example by messaging **@userinfobot**).
3. Put both in `.env`:

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

4. Send any new proposals to your phone:

```
.venv/bin/python -m wheel.agent notify
```

5. To make the Approve and Decline buttons work, keep the listener running:

```
.venv/bin/python -m wheel.agent telegram
```

On a server, `sudo systemctl enable --now wheel-telegram.service` does the same.

Only the chat id in `.env` is obeyed. Approving in Telegram does exactly what the Approve button on the control panel does: it queues the order. With Robinhood, the Robinhood app still asks you to approve.

## Using Hermes (or any agent that can run commands on the server)

Install the agent by following its own guide. Then give it these three jobs. Each is a command it runs and a short instruction.

**Morning run, on trading days**

```
cd ~/wheel-kit && .venv/bin/python -m wheel.agent cycle && .venv/bin/python -m wheel.agent status
```

Instruction: "Run this at 9:45 Eastern on trading days. Send me the status line and one sentence per proposal. Do not approve anything."

**Heads-up check, mid-afternoon**

Instruction: "Run `python -m wheel.agent read`. If the output or `state/live/journal.jsonl` shows a call in the money within two days of expiry, or anything blocked, message me. Otherwise stay quiet."

**Weekly review, Friday after the close**

Instruction: use the prompt in [prompts/WEEKLY_REVIEW.md](../prompts/WEEKLY_REVIEW.md).

## Rules for the agent

Give your agent these as standing instructions:

- It may run `read`, `sync`, `notify`, `status` and `cycle`.
- It may **not** approve or decline a proposal, edit `.env`, edit anything in `wheel/`, or change settings.
- It may suggest a rule change in the weekly review. A suggestion is tested first (see `prompts/RUN_FULL_TEST.md`) and you make the change yourself from the control panel.
- It reports premium and profit together, never premium alone.

The reason for the last two: a week or a month of your own trades is far too little to tell a better rule from a lucky stretch. An agent that tunes rules on recent results will chase whatever just worked.
