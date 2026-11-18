"""
universe2.py - the EXPANDED wheel universe. PRE-DECLARED 2026-11-18, before any option data
for the new names was downloaded and before any run on them. Do not edit after results exist;
a change is a new file.

Why: every wheel test so far used the 33 usable names of options_farm's FARM_UNIVERSE. the owner
asked for a list closer to what r/Optionswheel posters actually trade (he remembered one of
89 or 189 names). That poster's own list could not be read (Reddit blocks automated access),
so this list is built by rule. If he supplies the poster's list, it goes in a new file.

RULE (names chosen by membership / liquidity / name recognition, never by a backtest)
  FARM     the existing 37 farm names.
  SP100    the S&P 100 as Claude knows it (GOOG and BRK.B left out: a duplicate share class and
           a dotted symbol). Membership was NOT checked against a current official list.
  REDDIT   the 21 tickers in the "Week 46: $537 in premium" screenshot the owner shared 2026-11-18.
  ETF      broad, sector, bond, metal, oil and country funds with active options.
  ACTIVE   widely traded optionable single stocks popular with retail option sellers.
  Every ticker was checked on 2026-11-18 (Yahoo): has daily prices through 2026-10-09 and has
  listed options today.

EXCLUDED, by rules fixed before this list was built
  NO_HISTORY   no price history returned for the window.
  SPLITS       any split or spin-off share adjustment on or after 2024-01-18. Strikes are never
               split-adjusted, so the option cube breaks across the date (the reason NVDA and
               SMCI were excluded in spec.py). This costs NVDA, AVGO, NFLX, NOW, WMT and five
               sector funds; testing them from their split date on is possible later and
               would be a new, separately declared universe.
  NIO, BABA    foreign filers with no 8-K earnings dates (spec.py).
  A name with no earnings dates after the EDGAR pull is not traded by the engine either
  (sim.earnings_clash returns True when a stock has no dates). Expect that for most of the
  foreign filers in ACTIVE; the count is reported with the results.

LATE_START names listed after 2024-01-22. They are kept; a case or a trade only exists from
their first session with option bars. Their results cover a shorter, later period and are
reported separately from the full-window names.

ETFS2 have no earnings; the engine must treat every name in ETFS2 as an ETF (sim.py only
knows SPY, QQQ, IWM).
"""

FARM = [
    "AAL", "AAPL", "AMD", "AMZN", "BABA", "BAC", "CCL", "COIN", "DIS", "DKNG", "F", "GOOGL",
    "HOOD", "INTC", "IWM", "MARA", "META", "MSFT", "MU", "NIO", "NVDA", "PFE", "PLTR", "PYPL",
    "QQQ", "RIOT", "RIVN", "SMCI", "SNAP", "SOFI", "SPY", "T", "TSLA", "UBER", "WBD", "WFC",
    "XOM",
]

SP100 = [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "AMD", "AMGN", "AMT", "AMZN", "AVGO", "AXP",
    "BA", "BAC", "BK", "BKNG", "BLK", "BMY", "C", "CAT", "CHTR", "CL", "CMCSA", "COF", "COP",
    "COST", "CRM", "CSCO", "CVS", "CVX", "DE", "DHR", "DIS", "DUK", "EMR", "FDX", "GD", "GE",
    "GILD", "GM", "GOOGL", "GS", "HD", "HON", "IBM", "INTC", "INTU", "ISRG", "JNJ", "JPM", "KO",
    "LIN", "LLY", "LMT", "LOW", "MA", "MCD", "MDLZ", "MDT", "MET", "META", "MMM", "MO", "MRK",
    "MS", "MSFT", "NEE", "NFLX", "NKE", "NOW", "NVDA", "ORCL", "PEP", "PFE", "PG", "PLTR", "PM",
    "PYPL", "QCOM", "RTX", "SBUX", "SCHW", "SO", "SPG", "T", "TGT", "TMO", "TMUS", "TSLA",
    "TXN", "UBER", "UNH", "UNP", "UPS", "USB", "V", "VZ", "WFC", "WMT", "XOM",
]

REDDIT = [
    "ACHR", "AMZN", "CRSP", "FIG", "FRMI", "HOOD", "IREN", "JOBY", "LUNR", "NNE", "NOW", "NVDA",
    "QUBT", "RDDT", "RGTI", "S", "SPCX", "TOST", "UMAC", "UPST", "VKTX",
]

ETFS2 = [
    "ARKK", "DIA", "EEM", "EFA", "EWZ", "FXI", "GDX", "GLD", "HYG", "IWM", "KRE", "QQQ", "SLV",
    "SMH", "SPY", "TLT", "USO", "XBI", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE",
    "XLU", "XLV", "XLY",
]

ACTIVE = [
    "ABNB", "AFRM", "AI", "AMC", "APP", "ARM", "ASTS", "BB", "BBAI", "BIDU", "CCJ", "CELH",
    "CHWY", "CIFR", "CLF", "CLSK", "CRWD", "CVNA", "DAL", "DASH", "DDOG", "DJT", "DOCU", "DVN",
    "EBAY", "ENPH", "ERIC", "ET", "ETSY", "FCX", "FSLR", "GME", "GRAB", "HAL", "HIMS", "HUT",
    "IONQ", "JD", "KHC", "KMI", "LCID", "LI", "LUV", "LYFT", "MRVL", "MSTR", "NCLH", "NEM",
    "NET", "NOK", "NU", "OKLO", "ON", "OPEN", "OXY", "PANW", "PATH", "PBR", "PDD", "PINS",
    "PTON", "RBLX", "RCL", "RKLB", "ROKU", "SE", "SHOP", "SLB", "SMR", "SNOW", "SOUN", "TLRY",
    "TSM", "TTD", "U", "UAL", "VALE", "WULF", "XPEV", "ZM",
]

NO_HISTORY = ['BK', 'ROKU', 'WBD']
SPLITS = {
 "AVGO": {
  "2024-07-15": 10.0
 },
 "BKNG": {
  "2026-04-06": 25.0
 },
 "CMCSA": {
  "2026-01-05": 1.067
 },
 "CRWD": {
  "2026-07-02": 4.0
 },
 "CVNA": {
  "2026-05-08": 5.0
 },
 "FDX": {
  "2026-06-01": 1.241
 },
 "GE": {
  "2024-04-02": 1.253
 },
 "HON": {
  "2025-10-30": 1.061,
  "2026-06-29": 0.9535
 },
 "LCID": {
  "2025-09-02": 0.1
 },
 "MMM": {
  "2024-04-01": 1.196
 },
 "MSTR": {
  "2024-08-08": 10.0
 },
 "NFLX": {
  "2025-11-17": 10.0
 },
 "NOW": {
  "2025-12-18": 5.0
 },
 "NVDA": {
  "2024-06-10": 10.0
 },
 "PANW": {
  "2024-12-16": 2.0
 },
 "SMCI": {
  "2024-10-01": 10.0
 },
 "TLRY": {
  "2025-12-02": 0.1
 },
 "WMT": {
  "2024-02-26": 3.0
 },
 "XLB": {
  "2025-12-05": 2.0
 },
 "XLE": {
  "2025-12-05": 2.0
 },
 "XLK": {
  "2025-12-05": 2.0
 },
 "XLU": {
  "2025-12-05": 2.0
 },
 "XLY": {
  "2025-12-05": 2.0
 }
}
OLD_EXCLUDED = ['NIO', 'BABA']
LATE_START = {
 "FIG": "2025-07-31",
 "FRMI": "2025-10-01",
 "NNE": "2024-05-08",
 "RDDT": "2024-03-21",
 "SPCX": "2026-06-12",
 "UMAC": "2024-02-14"
}

EXCLUDED = set(NO_HISTORY) | set(SPLITS) | set(OLD_EXCLUDED)
CANDIDATES = sorted(set(FARM) | set(SP100) | set(REDDIT) | set(ETFS2) | set(ACTIVE))     # 242
UNIVERSE2 = [t for t in CANDIDATES if t not in EXCLUDED]                                    # 214
NEW_NAMES = [t for t in UNIVERSE2 if t not in FARM]                                         # 182 to download
