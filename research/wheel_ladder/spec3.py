"""
spec3.py - FROZEN rules for wheel v3 ("the guide's wheel"). Written 2026-11-17 before any v3 run.
Do not edit after results exist; a change is a new spec file with a new name.

Source: the r/Optionswheel sticky "The Wheel (aka Triple Income) Strategy Explained"
(u/ScottishTrader), pasted by the owner on 2026-11-17. spec.py and spec2.py broke its two main
rules (they ranked names by premium and put up to 40% of the account in one name). This spec
follows the guide as written, at the owner's account size.

Same data, fills, half-spreads, fees and window as spec.py:
  options_farm cube (Alpaca daily option bars, 2024-01-18 .. 2026-10-09), fills at the next
  session's open with the calibrated half-spread, $0.03 per contract per side.
This is the THIRD look at the same 2.7 years, mostly a bull market. A pass here is weak
evidence; a fail is stronger. The random-pick control is part of the reading for that reason.

Where the guide leaves a choice open, the choice below is MINE (Claude's), made before any run
and marked [op] (operationalization). Nothing here was tuned on results.
"""

START_CASH = 50_000.0
SIM_START = "2024-01-22"
SIM_END = "2026-10-09"
EXCLUDE = {"NVDA", "SMCI", "NIO", "BABA"}      # split / missing-earnings names, as in spec.py

# ---------------------------------------------------------------- stock selection
# Guide: profitable, stable chart "without wild gyrations", bullish or neutral trend, no
# sub-$10 stocks. [op] Mechanical version, using only prices known at the decision:
MIN_PRICE = 10.0
MAX_REALIZED_VOL = 0.40      # annualized stdev of the last 252 daily log returns (split-adjusted)
SMA_N = 200                  # close must be above its 200-day simple average
# [op] When more names qualify than there is room for, take the calmest first (lowest
# realized vol). The guide ranks by quality, never by premium.

# ---------------------------------------------------------------- sizing
# Guide: 5% (at most 10%) of the account at risk in any one stock; keep ~50% in cash.
NAME_CAPS = [0.05, 0.10]     # collateral per name <= cap x equity; both are reported
DEPLOY_MAX = 0.50            # put collateral + assigned stock (at strike) <= 50% of equity

# ---------------------------------------------------------------- puts
DTE_TARGET = 35              # guide: 30-45 DTE
DELTA_TARGET = 0.30          # guide: ~0.30 delta
TAKE_PROFIT = 0.50           # guide: close at 50% profit
MIN_PREMIUM = 0.05
MIN_BAR_VOLUME = 1
# Earnings: never open or roll into an option whose life contains an earnings session.
# Rolling (guide: "roll out in time, and down in strike, for a net credit ... for as long as
# a net credit is possible"; otherwise take assignment):
ROLL_DTE = 21                # [op] check a put for rolling once it is ITM with <= 21 DTE
# Roll target: the expiry nearest DTE_TARGET that is later than the current one; among puts
# with strike <= current strike, the LOWEST strike whose sale (after half-spread and fees)
# still exceeds the cost to buy back the current put. No cap on the number of rolls.

# ---------------------------------------------------------------- covered calls
CC_DTE = 8                   # guide: 7-10 DTE
CC_DELTA = 0.30              # [op] guide gives no delta for calls
# Strike must be at or above NET stock cost (assignment cost minus every premium collected
# in this wheel, per share). If no such call has a tradable price, hold the shares and wait.
# Calls are left to expire (guide). No stop on the stock (guide: hold, be patient).

# ---------------------------------------------------------------- cash
CASH_RATES = [0.0, 0.04]     # [op] idle cash earns nothing, or a flat 4% a year; both reported

# ---------------------------------------------------------------- controls
#   SPY buy and hold, $50,000, total return.
#   MONKEYS: same engine, sizing and rolling, but NO quality filter and a random eligible
#   name and random candidate contract. 100 seeds at the 10% cap, 0% cash rate.
N_MONKEYS = 100

# ---------------------------------------------------------------- pre-registered questions
# Reported for each cap x cash-rate run:
#   Q1  Did it return at least 10% a year (the low end of the guide's "10% to 20%+")?
#   Q2  Did it beat SPY buy and hold on time-weighted CAGR?
#   Q3  Was its maximum drawdown smaller than SPY's?
#   Q4  (10% cap, 0% cash) Is its CAGR at or above the monkeys' 90th percentile, i.e. did
#       the quality filter add anything over random names?
# Also reported regardless: total premium collected vs actual profit, assignments, rolls,
# and what is still held at the end.

# ---------------------------------------------------------------- arm B: 300 HOOD shares
# Arm B is a holder of 300 HOOD shares. It starts with 300 HOOD shares bought at the first close
# and no other cash, and runs the guide's covered-call rule on them (CC_DTE, CC_DELTA,
# strike >= net cost). If the shares are called away, it sells up to 3 cash-secured puts on
# HOOD (DTE_TARGET, DELTA_TARGET, same take-profit, roll and earnings rules) to get back in.
# No quality filter (the holding is given). Compared with simply holding the 300 shares.
# HOOD rose roughly tenfold in this window, so this arm mostly measures the cost of capping
# a winner; it says little about a flat or falling stock.
HOOD_SHARES = 300
