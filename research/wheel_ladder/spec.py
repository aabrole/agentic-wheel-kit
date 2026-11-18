"""
spec.py - FROZEN rules for the tier-ladder wheel backtest. Written 2026-10-12 before any run.
Do not edit after results exist; a change is a new spec file with a new name.

Source: the tier-ladder plan the owner approved in chat on 2026-10-12 07:2x ET (wheel that
reinvests everything and moves up a tier as the account grows).

DATA (real traded option prices, not Black-Scholes):
  options_farm cube: Alpaca daily option bars, every weekly expiry 0-80 DTE, strikes in a
  band around spot, 2024-01-18 .. 2026-10-09. Stock bars raw (strikes are unadjusted).
  Fills at the option's daily OPEN on the session after the decision, minus/plus the
  half-spread from options_farm/calibrate.py (live Alpaca quotes 2026-10-12, bucketed by
  premium x moneyness x DTE). $0.03 fee per contract per side. No historical quotes exist,
  so the half-spread is an estimate from today's market.

WINDOW: decisions from 2024-01-22, last session 2026-10-09 (~2.7 years, mostly a bull
  market with the April 2025 crash inside). Short puts do well in bull markets, so a
  positive result here is weaker evidence than it looks; the controls below are the test.
"""

START_EQUITY = 600.0
DEPOSITS = [0.0, 100.0, 250.0]        # $ added at the close of the first session of each month
SIM_START = "2024-01-22"
SIM_END = "2026-10-09"

# ---------------------------------------------------------------- universe
# options_farm FARM_UNIVERSE, minus names that split inside the window (strike/share
# bookkeeping) and single stocks with no earnings dates on file (the earnings rule
# could not be enforced for them).
EXCLUDE = {"NVDA", "SMCI", "NIO", "BABA"}
ETFS = {"SPY", "QQQ", "IWM"}

# ---------------------------------------------------------------- tiers
# (tier, equity_from, equity_to, underlying price band, max wheels at once, max contracts per wheel)
TIERS = [
    (0, 0.0, 1_500.0, (2.50, 15.0), 1, 1),
    (1, 1_500.0, 5_000.0, (10.0, 45.0), 2, 2),
    (2, 5_000.0, 25_000.0, (45.0, 250.0), 3, 3),
    (3, 25_000.0, float("inf"), (45.0, 10_000.0), 5, 10),   # ETFs allowed from tier 3 only
]
PROMOTE_DAYS = 10          # equity >= next tier's floor for 10 consecutive sessions
DEMOTE_CUSHION = 0.85      # drop a tier only when equity < 0.85 x current tier floor
# A tier change never closes a position; it applies to the next new trade.

# ---------------------------------------------------------------- the wheel
DTE_TARGET = 35            # days to expiry for new puts and calls
DELTA_TARGET = 0.30        # |delta| of the short option (market.py smile, +-0.10 tolerance)
TAKE_PROFIT = 0.50         # buy back when the option's close <= 50% of the entry credit
MANAGE_DTE = 21            # at <= 21 DTE: buy back if OTM; if ITM hold to expiry (assignment)
MIN_PREMIUM = 0.05         # skip contracts quoted under $0.05
MIN_BAR_VOLUME = 1         # contract must have traded on the decision day
CC_NEVER_BELOW_BASIS = True   # covered call strike >= assignment strike
# Earnings: never open a short option whose life [entry, expiry] contains an earnings
# reaction session for that name. For covered calls, try shorter expiries (21/14/7 DTE)
# that end before earnings; if none, hold the shares uncovered until after earnings.

# Ranking among eligible names: highest annualized net yield
#   (entry price x (1 - half-spread) - fee/100) / strike x 365 / DTE.
# Sizing: contracts = min(tier cap, floor(free cash / open-wheel slots left / (strike x 100))).
# Free cash = cash - collateral of open puts. Cash earns nothing (Robinhood cash account).

# ---------------------------------------------------------------- controls (same deposits)
#   SPY buy and hold (total return, deposits invested at the close).
#   V1 (UPRO/SGOV vol target, 10-pt band), close-to-close, no settlement lag.
#   MONKEYS: identical engine, but each new wheel picks a random eligible name and a random
#   candidate contract (|delta| 0.20-0.40). 200 seeds.
N_MONKEYS = 200

# ---------------------------------------------------------------- pre-registered verdict
# PASS only if, at $100/month deposits, the ladder's time-weighted CAGR
#   (a) beats SPY buy-and-hold over the window, AND
#   (b) is at or above the monkeys' 90th percentile.
# Otherwise the ladder is reported as FAIL. Max drawdown and worst single-wheel loss are
# reported regardless.
