# Telegram, and an always-on agent as the bookkeeper

The kit decides nothing with AI. The engine applies written rules. An always-on agent such as Hermes is useful around it: it runs the cycle on time, tells you what happened, and writes the weekly review.

The Hermes setup script was written against the Hermes Agent v0.23 documentation and checked with a dry run. It has not yet been run against a live Hermes install.

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

## Hermes: the always-on assistant

Hermes Agent is a free, open-source agent from Nous Research. It stays running, keeps memory, has a built-in scheduler and can message you on Telegram. The kit ships a Hermes skill and a script that sets everything up.

**1. Install Hermes** (its own installer), then choose your model:

```
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
source ~/.bashrc          # or ~/.zshrc
hermes setup
```

**2. Add the wheel layer** from the kit folder:

```
bash deploy/hermes.sh
```

That does two things:

- Copies the `wheel-desk` skill into `~/.hermes/skills/`. The skill tells Hermes what the kit is, which commands it may run, and what it must never do.
- Creates three scheduled jobs, in Eastern time:

| Job | When | What it does |
|---|---|---|
| `wheel-morning` | 9:45, trading days | Runs the cycle and sends you the proposals with reasons |
| `wheel-afternoon` | 3:15, trading days | Checks open calls; messages you only if one is in the money near expiry or a run was blocked |
| `wheel-weekly` | Friday 4:30 | Writes the weekly review: premium next to profit, against just holding |

Run `bash deploy/hermes.sh --dry-run` first to see every command without running it.

**3. Connect your phone and start it:**

```
hermes gateway setup      # Telegram, or another app
hermes gateway start
hermes cron list          # wheel-morning, wheel-afternoon, wheel-weekly
```

If you use Hermes for the schedule on a server, turn the kit's own timer off so the cycle does not run twice: `sudo systemctl disable --now wheel-cycle.timer`.

Talk to it any time: "what is the wheel doing?", "what is open?", "how did last week go?".

Hermes's Telegram and the kit's own Telegram buttons are separate. Hermes sends you reports. The kit's buttons (above) are what approve or decline a proposal.

## Rules for the agent

The `wheel-desk` skill already carries these. If you use a different agent, give it the same standing instructions:

- It may run `read`, `sync`, `notify`, `status` and `cycle`.
- It may **not** approve or decline a proposal, edit `.env`, edit anything in `wheel/`, or change settings.
- It may suggest a rule change in the weekly review. A suggestion is tested first (see `prompts/RUN_FULL_TEST.md`) and you make the change yourself from the control panel.
- It reports premium and profit together, never premium alone.

The reason for the last two: a week or a month of your own trades is far too little to tell a better rule from a lucky stretch. An agent that tunes rules on recent results will chase whatever just worked.
