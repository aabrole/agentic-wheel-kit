"""
spec_v2.py - WHEEL LIVE v2 (BETA). Written 2026-11-18. Everything in spec.py (v1) is unchanged
and still applies; this file only ADDS two proposal types. engine.py defaults to v1; the
dashboard runs v2.

WHAT IS NEW, AND THE EVIDENCE
  ROLLS  When a short call is in the money with 7 days or fewer left, propose rolling it up
         and out: buy it back, sell a later call at a HIGHER strike, only if the pair is a net
         credit even at the worst prices the orders would be worked at. Otherwise nothing is
         proposed and the shares are called away (a realized gain, since calls are at or above
         cost). Two orders on this account (level 2 is single-leg).
         Evidence: wheel_ladder/study_managed.py, 891 cases. Rolling added about +0.1 to +0.3
         log points per 26 weeks; a qualifying roll existed in only 10% to 27% of cases. Small
         but never negative across the cost scenarios and at twice the spread.
  ROTATION (BETA)  When free cash covers at least 100 shares of an eligible stock, propose
         buying it; calls are proposed on it on the next run. Eligible = price >= $10 and above
         its 200-day average. Pick = highest trailing 252-session return ("strongest trend").
         Never a stock already held. The owner chose this over "calmest quality" on 2026-11-18.
         Evidence: wheel_ladder/study_rotate.py arm ROTM, PARTIAL (47 of about 198 names):
         one year, 965 cases: 37.8% against 39.7% holding and 26.6% with the cash in SPY;
         2.5 years, 141 cases: +123% mean / +102% median against +111% / +49% holding.
         NOT CONFIRMED: it bought the window's biggest winners, the 2.5-year figure rests on
         three overlapping start dates, it is the fourth pick rule tried on the same cases, and
         its drawdowns are close to holding's. It stays BETA until the full-list run agrees,
         holds at twice the spread, and is not carried by a few names.
  Not adopted: the implied-volatility filter (worth about +0.5 log points in the study) needs
  a history of each stock's implied volatility that the live snapshot does not carry yet.
  Not adopted: skipping macro news days (no measurable effect in the study).
"""
from spec import *  # noqa: F401,F403  (v1 rules, unchanged)

VERSION = "wheel-live-v2-beta"
MAX_PROPOSALS = 5

ROLLS_ENABLED = True
ROLL_DTE = 7                     # consider a roll when an in-the-money call has this many days or fewer

ROTATION_ENABLED = True
ROT_RULE = "trend"               # "trend" (beta) | "quality" (calmest, the earlier rule) | "spy" | "off"
ROT_MIN_PRICE = 10.0
ROT_QUALITY_MAX_VOL = 0.40       # "quality" only: one-year realized volatility cap
ROT_CASH_BUFFER = 0.02           # keep 2% of the purchase aside for slippage
ROT_LIMIT_PAD = 0.002            # buy limit this far above the last price
ROT_SHOW_RANKS = 6               # how many ranked candidates a proposal lists
