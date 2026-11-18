"""
farm_universe.py - PRE-DECLARED before any simulation run (2026-10-11).

FARM_UNIVERSE  liquid optionable names with small premiums available, across price
               levels. Used by flow-follow, The Strat 2-1-2, breakout, pre-earnings
               (single stocks only) and the monkeys. Chosen by liquidity / name
               recognition / price level, not by any backtest.
PEAD_UNIVERSE  the locked PEAD v2 universe (40 mega caps) from
               news_catalyst_options/universe.py, used only by the PEAD family.

Window: Alpaca options daily bars begin 2024-01-18 (verified). Last full session
before this build: 2026-10-09.
"""

ETFS = ["SPY", "QQQ", "IWM"]

STOCKS = [
    # mega / large caps (expensive premiums: forces OTM / short-dated)
    "AAPL", "AMD", "NVDA", "TSLA", "META", "AMZN", "MSFT", "GOOGL", "UBER", "PYPL",
    "DIS", "MU", "XOM", "WFC", "BABA", "SMCI", "COIN",
    # cheap-premium names (low share price)
    "PLTR", "SOFI", "HOOD", "F", "INTC", "BAC", "NIO", "RIVN", "MARA", "SNAP",
    "AAL", "CCL", "PFE", "T", "RIOT", "WBD", "DKNG",
]

FARM_UNIVERSE = ETFS + STOCKS            # 37 names

PEAD_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "AVGO",
    "JPM", "BAC", "WFC", "GS", "V", "MA", "XOM", "CVX", "UNH", "JNJ",
    "PFE", "MRK", "LLY", "ABBV", "WMT", "COST", "HD", "NKE", "MCD", "SBUX",
    "DIS", "CRM", "ORCL", "INTC", "QCOM", "CSCO", "ADBE", "PYPL", "UBER", "BA",
]

OPTIONS_START = "2024-01-18"     # first Alpaca option daily bar
DATA_END = "2026-10-09"          # last full session
SIM_START = "2024-01-22"         # first decision date with a prior option-bar day
SELECTION_FRACTION = 0.60        # first 60% of sessions = SELECTION, rest = CONFIRMATION
