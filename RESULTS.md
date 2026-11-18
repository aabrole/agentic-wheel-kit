# What was tested, and what it showed

Every rule in this kit was written down before it was run against real option prices. This page is the short version. The code that produced it is in `research/`, and `prompts/RUN_FULL_TEST.md` shows how to rerun it yourself.

**Status on 2026-11-18:** complete. The full list is 198 stocks and funds (214 chosen beforehand, 16 dropped for missing earnings dates). An earlier partial run on 118 of them showed the trend rule beating holding. The full run did not confirm that, and section 3 says so.

**Read this first:** the data is January 2024 to October 2026. The market mostly rose. That is the worst case for covered calls and the best case for buying what has been going up. Nothing here was tested in a falling market.

## 1. Premium is not profit

A $50,000 account running the wheel, one contract per stock, for 2.7 years, on all 198 names:

| | Premium collected | Profit | Per year | Worst drop |
|---|---|---|---|---|
| Highest-premium stocks, 5% of the account per stock | $81,418 | $20,811 | 13.7% | -16% |
| Highest-premium stocks, 10% per stock | $105,793 | $24,959 | 16.1% | -17% |
| Calm quality stocks | $4,875 to $21,391 | $976 to $5,847 | 0.7% to 4.2% | -2% to -5% |
| S&P 500 (SPY), buy and hold | none | $33,157 | 20.6% | -19% |

About a quarter of the premium was left as profit: $575 a week collected became $147 a week kept. The rest went to stocks that fell after assignment. The highest-premium account ended the test holding 19 stocks below what it paid for them. Its profit was real, with a slightly smaller worst drop than SPY, and it still trailed SPY.

## 2. Covered calls against simply holding

300 shares, calls on two of the three lots, 26 weeks at a time, 5,232 cases (197 names, 27 start dates). Shares start below their cost, as most people's do when they begin selling calls.

| Rule | Average 26-week return | Average worst drop | Beat holding |
|---|---|---|---|
| Hold, no calls | 18.6% | -25% | n/a |
| 35-day call at or above your cost (the kit's base rule) | 12.9% | -21% | 58% |
| Same, with daily management (roll up, sell only when premium is rich) | 14.0% | -22% | 58% |

- Calls gave smaller drops and less upside. The managed rule beat holding by about 3 points when the stock ended up 10% or less, and trailed by about 14 points when it rose more.
- In the typical case the managed rule was not distinguishable from holding. On average it earned less, because the average is pulled up by big winners and calls cap those.
- With shares starting at cost instead of below it, the same rules returned 10.6% and 12.1%.
- At double the spread cost the managed rule returned 13.8%.
- On the first 33 stocks, every rule that sold calls below cost was clearly worse than holding. That is why "never below cost" is locked in the kit.
- Skipping CPI, jobs and Fed days changed the result by less than a tenth of a point. Not worth a rule.

## 3. Where called-away cash goes (the rotation rule)

Same start, but when shares are called away the cash is put to work and every dollar is reinvested. One year, 4,050 cases, 196 names.

| Called-away cash goes to | Average return | Average worst drop | Beat holding | Beat the SPY version |
|---|---|---|---|---|
| Nothing (idle) | 20.7% | -20% | 42% | 3% |
| SPY | 28.3% | -24% | 56% | n/a |
| Calmest quality stock, with calls | 25.0% | -21% | 50% | 22% |
| Highest-premium stock, with calls | 40.9% | -39% | 61% | 55% |
| **Strongest one-year trend, with calls (the kit's beta rule)** | **34.9%** | **-37%** | **56%** | **45%** |
| Uptrend filter, then highest premium, with calls | 45.5% | -37% | 65% | 59% |
| Hold the original shares | 43.8% | -34% | n/a | n/a |

**The kit's beta rule was not confirmed.** Three conditions were set before the full run, and it failed all three:

1. Beat holding and the SPY version on the full list. It did not: the gap against each was too small to tell from zero, and its average return was 9 points below holding.
2. Hold at double the spread cost. The gap shrank further.
3. Not be carried by a few names. Most of its purchases were a handful of quantum, space and crypto-mining stocks (RGTI, QUBT, UMAC, RKLB, WULF, ASTS, CIFR, IREN).

On 118 stocks the same rule had looked like a winner (40.1% against 35.3%). Adding the remaining 80 names removed the edge. That is what a result that depended on the stock list looks like.

One other row did beat holding and the SPY version, at normal and at double spread cost: filter for stocks in an uptrend, then pick the highest premium. Treat it as a lead, not a finding. It was the sixth pick rule tried on the same cases, it was not the rule chosen beforehand, its drops were deeper than holding, and its average return was within 2 points of simply holding. It needs a test on data it has not seen.

Over 2.5 years there are only three overlapping start dates (549 cases), so these are descriptions, not evidence: holding +95%, SPY version +69%, the beta rule +91%, uptrend then premium +133%.

## 4. What the kit does with this

- Base rule: 35-day calls at or above your net cost, one lot always kept free. Tested, modest, locked.
- Rolls: on, small benefit.
- Rotation: labelled beta and "not confirmed" on every proposal. The full run does not support it over putting called-away cash in SPY. Switchable to quality, SPY or off from the dashboard.
- Puts: off. Not tested enough.
- The scoreboard shows premium next to profit and next to "if you had just held", because that gap is the main finding.

## Known weaknesses in the tests

- One period of history, mostly rising.
- Fills are assumed at the day's opening price, less a spread estimated from one day of live quotes. That spread estimate is about the size of the profit on the call rules, so every test is also run at double the spread.
- Earnings dates come from SEC filing times and can be a day off.
- Early assignment before dividends is not modelled. Taxes are not modelled.
- The stock list was chosen by the author, not drawn from an official index list.
