# What was tested, and what it showed

Every rule in this kit was written down before it was run against real option prices. This page is the short version. The code that produced it is in `research/`, and `prompts/RUN_FULL_TEST.md` shows how to rerun it yourself.

**Status on 2026-11-18:** the tests on the original 33 stocks are complete. The larger list (about 198 stocks) is partly run: results marked PARTIAL cover 118 of them and will be replaced when the run finishes.

**Read this first:** the data is January 2024 to October 2026. The market mostly rose. That is the worst case for covered calls and the best case for buying what has been going up. Nothing here was tested in a falling market.

## 1. Premium is not profit

A $50,000 account running the wheel on the highest-premium stocks, one contract per stock, for 2.7 years:

| | Premium collected | Profit | Per year | Worst drop |
|---|---|---|---|---|
| Highest-premium stocks | $42,509 | $7,293 | 5.2% | -10% |
| Calm quality stocks | $2,599 to $14,383 | $426 to $2,691 | 0.3% to 2.0% | -1% to -8% |
| S&P 500 (SPY), buy and hold | none | $33,157 | 20.6% | -19% |

About a fifth of the premium was left as profit. The rest went to stocks that fell after assignment. (47 stocks, PARTIAL.)

## 2. Covered calls against simply holding

300 shares, calls on two of the three lots, 26 weeks at a time, 891 cases (33 stocks, 27 start dates).

| Rule | Average 26-week return | Average worst drop |
|---|---|---|
| Hold, no calls | 17.1% | -26% |
| 35-day call at or above your cost (the kit's base rule) | 12.2% | -22% |
| Same, with daily management (roll up, sell only when premium is rich) | 12.8% | -23% |
| Calls that ignore your cost | 7.7% to 11.0% | -14% to -20% |

- Calls gave smaller drops and less upside. They beat holding by about 4 points when the stock ended up 10% or less, and trailed by about 15 points when it rose more.
- Every rule that sold calls below cost was clearly worse than holding. That is why "never below cost" is locked in the kit.
- Daily management added about half a point. Selling only when option prices were high did most of that. Rolling was possible in 10% to 27% of cases.
- Skipping CPI, jobs and Fed days made no measurable difference.

## 3. Where called-away cash goes (the rotation rule)

Same start, but when shares are called away the cash is put to work and every dollar is reinvested. One year, 2,433 cases, 118 stocks. PARTIAL.

| Called-away cash goes to | Average return | Average worst drop | Beat holding | Beat the SPY version |
|---|---|---|---|---|
| Nothing (idle) | 16.9% | -20% | 41% | 3% |
| SPY | 24.5% | -23% | 55% | n/a |
| Calmest quality stock, with calls | 21.2% | -21% | 49% | 21% |
| Highest-premium stock, with calls | 25.6% | -40% | 52% | 42% |
| **Strongest one-year trend, with calls (the kit's beta rule)** | **40.1%** | **-33%** | **68%** | **60%** |
| Hold the original shares | 35.3% | -33% | n/a | n/a |

Over 2.5 years (325 cases, three start dates): the trend rule averaged +103% (median +87%) against +91% (median +47%) for holding and +67% for the SPY version.

Why it is still **beta**:

- It bought the biggest winners of this period (SOFI, ASTS, crypto miners). That works in a rising market and is the first thing to fail when the market turns.
- It was the fourth pick rule tried on the same cases, so part of the win is selection.
- The 2.5-year figure rests on three overlapping start dates.
- Its drops were about as deep as holding. It is the higher-return version, not the safer one.
- The full list and the double-spread rerun are not finished.

## 4. What the kit does with this

- Base rule: 35-day calls at or above your net cost, one lot always kept free. Tested, modest, locked.
- Rolls: on, small benefit.
- Rotation: on, labelled beta on every proposal, switchable to quality, SPY or off from the dashboard.
- Puts: off. Not tested enough.
- The scoreboard shows premium next to profit and next to "if you had just held", because that gap is the main finding.

## Known weaknesses in the tests

- One period of history, mostly rising.
- Fills are assumed at the day's opening price, less a spread estimated from one day of live quotes. That spread estimate is about the size of the profit on the call rules, so every test is also run at double the spread.
- Earnings dates come from SEC filing times and can be a day off.
- Early assignment before dividends is not modelled. Taxes are not modelled.
- The stock list was chosen by the author, not drawn from an official index list.
