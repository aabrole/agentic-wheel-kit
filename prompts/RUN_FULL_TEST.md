# Rerun the tests on your own data

Paste everything below into Claude Code, from inside the cloned repo.

**Before you start:** this downloads about three years of option prices for about 200 stocks. Expect **several hours** and **3 to 4 GB** of disk. You need a free Alpaca account (paper keys work for data). The results cover January 2024 to October 2026 only, and they are a description of that period, not a forecast.

This rerun has been done on the author's machine. It has not yet been verified from a fresh clone, so expect to fix a path or a missing package along the way.

---

Rerun the wheel tests in `research/` and compare the results with `RESULTS.md`.

Setup:

1. Create a virtual environment and install `research/requirements.txt`.
2. Check that `.env` at the repo root has `ALPACA_KEY` and `ALPACA_SECRET`. If not, stop and ask me to add them myself. Never print them.
3. The earnings step reads SEC filings and needs `SEC_CONTACT` in `.env` (my name and a contact address). Ask me to add it. If I would rather not, skip that step and use the shipped earnings files.

Download (run from `research/wheel_ladder/`, in the background, and tell me how long each step is taking):

4. `python3 fetch_universe2.py check`
5. `python3 fetch_universe2.py stock`
6. `python3 fetch_universe2.py estimate` and tell me the estimated hours before continuing.
7. `python3 fetch_universe2.py cube` (the long one; it can be stopped and resumed).
8. `python3 fetch_universe2.py status` and confirm every name has option bars.

Run:

9. `./run_all_u2.sh`

Report, in plain language with tables:

10. Covered calls against holding (from `results/study_u2.json`): average return, worst drop, how often calls won, for all names and for the double-spread run.
11. The rotation test (from `results/study_rotate.json` and the 2.5-year file): every arm against holding and against the SPY arm, with the 90% intervals. Say plainly which differences are statistically clear and which are not.
12. Which stocks the trend rule bought most. If a handful of names carry the result, say so.
13. Every place my numbers differ from `RESULTS.md` by more than a point.

Rules for you:

- Do not change any rule file (`spec*.py`, the `study_*.py` docstrings, `universe2.py`, `news_days.py`). They were fixed before the tests ran. If something will not run, fix the plumbing, not the rule, and tell me what you changed.
- Do not describe any result as a reason to trade. Report what the data shows and what it cannot show.
