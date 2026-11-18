"""
spec.py - WHEEL LIVE v1. The rules the proposal engine applies. Written 2026-11-17.

A change to any number here is a new version (spec_v2.py) with a dated note saying what
evidence prompted it. engine.py reads these constants; nothing else may hard-code them.

WHAT THIS IS
  A proposal engine for an options wheel on a Robinhood account whose owner approves every
  trade. It reads an account snapshot, applies the rules below and prints proposals. It places
  nothing. The owner decides and places (or approves) each order. It is not advice.

WHERE THE RULES COME FROM
  [S] wheel_ladder/study_calls2.py, 2026-11-17 (the corrected run; study_calls.py is the
      first run, kept unchanged): 891 cases (33 names x 27 monthly starts, 26 weeks each, real
      option bars, calls on 2 of 3 lots, dividends credited, open calls valued at no less than
      intrinsic). Mean 26-week log return: hold 0.102; 8-day at/above cost 0.090; 35-day
      at/above cost 0.090; 35-day 0.15-delta 0.080; 35-day 0.30-delta 0.073; 8-day 0.30-delta
      0.061. The two at/above-cost rules are tied and are not statistically distinguishable
      from holding (90% interval on the difference includes zero); every rule that ignores
      cost is clearly below holding. The ranking is the same at twice the bid-ask haircut.
      35-day at/above cost beat holding in 61% of cases: +4 points when the stock ended the
      26 weeks up 10% or less, -15 points when it rose more. Premium about 2% of the position
      per 26 weeks. Calls at 0.30 delta ignoring cost were called away below cost in 84% of
      cases. 35-day is used over 8-day because the two tie on return, 35-day needs about a
      quarter of the orders, and calls over 30 days keep qualified-covered-call treatment [R].
      The window was mostly rising, the worst case for covered calls.
  [A] Engine audit, 2026-11-17 (three independent passes): no look-ahead, ledger reconciles
      to the cent. Findings used here: the spread assumption is the size of the profit, so
      real quotes gate every proposal; far-out-of-the-money penny calls fill about 30% below
      their quote, so a minimum premium applies; rolls were mis-modelled and are two orders
      on this account, so nothing rolls automatically; earnings dates must come from a
      calendar, not from filing times; "premium collected" overstated net option cash by
      about 2x, so the journal reports net.
  [R] Research, 2026-11-17: the option-selling premium is documented for indexes and is
      statistically absent for most single stocks; fat-premium names tend to keep
      underperforming; no put-management rule (50% take-profit, 21-day exit, rolling, stops)
      has evidence behind it; weekly selling collected more and returned less; "never sell a
      call below cost" has no published evidence (it earns its place here only through [S]);
      calls with 30 days or fewer to expiry are not "qualified covered calls" for US tax and
      can stop the holding-period clock on stock held under a year.
  [H] Robinhood, read 2026-11-17: the agent account is options level 2 (single-leg only: no
      one-order rolls, no stock-plus-option orders); with approvals on the owner places or
      approves each order; Robinhood starts closing at-risk expiring positions at 3:30 pm ET;
      $0.04 per contract in fees.

  [C] Codex adversarial review, 2026-11-17. In the engine: malformed snapshots could bypass the
      approvals gate, invent uncovered lots or defeat the cost floor, so every snapshot is now
      validated and any error means no proposal; the sell-price floor now rounds up and the
      minimum premium binds on the price actually worked; unknown, empty or stale earnings
      dates count as unknown. In the study: the five defects listed in study_calls2.py.
      Still open: fills assume the day's opening price for the whole order.

NOT TESTED
  Puts on diversified ETFs (the research default) could not be backtested: the option-data
  keys were rejected on 2026-11-17. The put rules below are therefore OFF by default.
"""

VERSION = "wheel-live-v1"

# ---------------------------------------------------------------- account gates [H]
REQUIRE_APPROVALS_ON = True      # refuse to propose anything if the owner's approval is off
MIN_OPTIONS_LEVEL = 2
MAX_PROPOSALS = 3                # per run; fewer, better-explained proposals [R]

# ---------------------------------------------------------------- covered calls [S]
CALL_DTE_TARGET = 35
CALL_DTE_MIN = 31                # > 30 days keeps the call "qualified" for tax purposes [R]
CALL_DTE_MAX = 45
CALL_DELTA_TARGET = 0.30
CALL_DELTA_BAND = (0.20, 0.40)   # same tolerance the study used
CALL_FLOOR = "cost"              # "cost": strike >= net cost per share; "none": by delta only
UNCOVERED_LOTS = {"HOOD": 1}     # core holdings: lots never written against. Set yours on the dashboard.
DEFAULT_UNCOVERED_LOTS = 0       # shares that arrived through put assignment are fully covered
PRE_EARNINGS_MIN_DTE = 7         # if earnings fall inside the target expiry, use the longest
                                 # expiry that ends before earnings and is at least this far out
ETFS = {"SPY", "QQQ", "IWM", "XLF", "XLP", "EEM", "EWJ", "FEZ", "EWZ", "FXI", "GDX"}  # no earnings

# ---------------------------------------------------------------- quote gates [A][R]
MIN_PREMIUM = 0.10               # dollars per share
MIN_YIELD_ANNUAL = 0.05          # premium / share price, annualized (same as the study)
MAX_SPREAD = 0.40                # (ask - bid) / mid; wider than this is not proposed
MIN_OPEN_INTEREST = 50
LIMIT_FLOOR_FRACTION = 0.25      # never work a sell below bid + 25% of the spread
TICK_BELOW_3 = 0.01
TICK_ABOVE_3 = 0.05
FEE_PER_CONTRACT = 0.04

# ---------------------------------------------------------------- open-position notices [H][R]
EXPIRY_NOTICE_DTE = 1            # flag short options expiring within a day
NEAR_STRIKE = 0.02               # ... or within 2% of the strike at that point
# Nothing closes, rolls or stops out automatically. The engine states the choices
# (let it be assigned / buy it back / roll as two orders) with their cash effect.

# ---------------------------------------------------------------- puts (OFF: not tested)
PUTS_ENABLED = False
PUT_ALLOW_LIST = ["XLF", "XLP", "EEM", "EWJ", "FEZ"]   # diversified ETFs; never ranked by premium
PUT_DTE_TARGET = 35
PUT_DTE_MIN = 31
PUT_DTE_MAX = 45
PUT_DELTA_TARGET = 0.30
PUT_DELTA_BAND = (0.20, 0.40)
PUT_NAME_CAP = 0.20              # strike x 100 x contracts <= 20% of account value, per name
STRESS_DROP = 0.30               # every holding and every assigned put falls 30% together
STRESS_LIMIT = 0.25              # block a put if that scenario would cost > 25% of the account.
                                 # With a 30% drop this caps stock exposure, counting cash pledged
                                 # to puts, at about 83% of the account. (A limit at or above the
                                 # drop can never bind for cash-secured puts; Codex review.)
# Every put is fully cash-secured: strike x 100 x contracts <= cash not already securing a put.
