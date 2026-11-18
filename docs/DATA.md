# Where outside data goes

You can connect data sources to Claude or to your assistant: congressional and insider trades (QuiverQuant), filings and web research (Firecrawl), news, fundamentals. This page says where each kind belongs, because the place matters more than the source.

There are three places, and data reaches the trade itself only through the first.

| Place | Who uses it | What it is for | Can it change a trade? |
|---|---|---|---|
| **1. Research** | Claude in Claude Code or Codex (the contractor) | Testing an idea on history: "does following insider buys improve the stock pick?", "does a profit filter beat the trend rule?" | Only after the test, and only when you adopt it as a rule |
| **2. Context** | Your assistant (Hermes) | What a person wants to know before approving: an earnings date check, a filing, a news item, insider or congressional trades in a stock you hold | No. It is shown next to the proposal. The engine never reads it. |
| **3. Engine inputs** | The engine | Plain facts in fixed fields: prices, option quotes, your positions, earnings and ex-dividend dates, the candidate list | Yes, and that is why these come from the broker or from files you control |

## How to use each

**Research.** Ask the contractor to test a data-driven idea the same way the other rules were tested. Write the rule down first, run it on history, run it again at double the spread cost, and look at whether a handful of names carry the result. Then file it as a suggestion with `wheel.agent suggest` and record the result with `wheel.agent tested`. A good first question for a fundamentals source: the kit ranks stocks by one-year trend because no history of profits and margins was available when it was tested. A source that gives fundamentals as they were known at each date would let you test a real quality filter.

**Context.** The Hermes skill already does this when connectors are present. It confirms earnings and ex-dividend dates from a second source, and records one-sentence findings with a link:

```
.venv/bin/python -m wheel.agent context HOOD "Two directors sold shares on Nov 12." https://example.com/filing
```

They appear on the proposal card under "Context from your assistant".

**Engine inputs.** The one input worth checking against outside data is the earnings date, because the engine will not sell a call that spans earnings. If your assistant reports a different date from the one the engine has, correct it yourself in `state/earnings.json`:

```
{"HOOD": "2027-02-09"}
```

## Why not feed signals straight to the engine

Because an untested signal is an opinion, and the point of the engine is that it has none. "Congress bought it" may or may not help a covered-call book. Until it has been tested on the same footing as the other rules, it belongs in context, where you can read it and decide.
