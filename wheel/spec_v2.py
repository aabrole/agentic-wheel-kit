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
         Evidence: wheel_ladder/study_rotate.py arm ROTM, full list (196 names, 4,050 one-year
         cases): 34.9% against 43.8% holding and 28.3% with the cash in SPY; log gap vs holding
         +0.025 [-0.066, +0.124], vs the SPY arm +0.017 [-0.101, +0.135]; beat holding 56%.
         NOT CONFIRMED. It failed the three conditions set beforehand: the gaps span zero, they
         shrink at twice the spread, and its purchases concentrate in a few names (RGTI, QUBT,
         UMAC, RKLB, WULF, ASTS, CIFR, IREN). The 118-name partial run had shown 40.1% vs 35.3%.
         A different arm (ROTT: uptrend filter, then highest implied volatility) did clear zero
         against both, but it is the sixth rule tried on the same cases and is not adopted.
         The rule stays available and BETA; the evidence does not favour it over "spy".
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
ROT_RULE = "trend"               # "trend" (beta, not confirmed) | "quality" (calmest) | "spy" (all free cash to SPY) | "off"
SWEEP_TO_SPY = True              # with "trend" or "quality": cash too small for 100 shares is parked in SPY, and
                                 # parked SPY counts as buying power (it is sold when it can fund a 100-share lot).
                                 # Owner's choice 2026-11-18: "most committed to the wheel, leftover cash held in SPY".
SWEEP_MIN = 25.0                 # do not bother parking less than this
ROT_MIN_PRICE = 10.0
ROT_QUALITY_MAX_VOL = 0.40       # "quality" only: one-year realized volatility cap
ROT_CASH_BUFFER = 0.02           # keep 2% of the purchase aside for slippage
ROT_LIMIT_PAD = 0.002            # buy limit this far above the last price
PARK_SYMBOL = "SPY"
ROT_SHOW_RANKS = 6               # how many ranked candidates a proposal lists
