# Weekly review prompt

Give this to your always-on agent (or paste it into Claude Code on the machine running the kit) each Friday after the close.

---

Write my weekly wheel review from `state/live/journal.jsonl` and the output of `.venv/bin/python -m wheel.agent status`. Read only. Change nothing.

Cover, in this order:

1. **The two numbers together:** premium collected this week and net option cash after buy-backs and fees. Then the same since the start.
2. **Against doing nothing:** account value now, and what it would be if I had just held what I started with.
3. **What happened:** each fill, each call that expired, each call-away, each roll, one line apiece.
4. **My decisions:** what I approved, edited or declined, and every guardrail I overrode, with the reason I gave.
5. **What is open:** each call still out, its strike, days left, and whether it is in the money.
6. **Coming up:** earnings dates inside the next 45 days for anything I hold.
7. **One suggestion at most,** only if the journal supports it, phrased as something to test. Do not suggest changing a rule because of one week's result.

Keep it under 250 words. Never report premium without the profit figure next to it.
