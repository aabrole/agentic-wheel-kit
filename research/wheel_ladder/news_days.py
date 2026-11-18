"""
news_days.py - "red folder" US macro release days, 2024-01 .. 2026-10. Declared 2026-11-18
before any run that uses them.

CPI, PPI and the jobs report: every date FRED lists for releases 10 (Consumer Price Index),
46 (Producer Price Index) and 50 (Employment Situation), pulled 2026-11-18, used as returned.
A few are revision or catch-up dates (for example 2024-02-09), and the late-2025 gaps are the
government shutdown; they are left in rather than hand-edited.
FOMC: the scheduled rate-decision days (second day of each meeting), from the Federal
Reserve's published calendars as Claude knows them; not re-checked against the Fed's site.

THE GUARDRAIL TESTED (rule "+NEWS" in study_managed.py)
  No option order fills on a news day: nothing is sent at the close before one, and an order
  still waiting does not fill on one (it tries again the next session). Expiries, call-aways
  and dividends are unaffected. Everything else is the rule it is added to.
  Daily bars only: this tests "do not open or roll on those days". It cannot see what happens
  inside the day (the 8:30 release is already in the opening price; the 2pm Fed decision is not).
"""
CPI = """2024-01-11 2024-02-09 2024-02-13 2024-03-12 2024-04-10 2024-05-15 2024-06-12 2024-07-11
2024-08-14 2024-09-11 2024-10-10 2024-11-13 2024-12-11 2025-01-15 2025-02-12 2025-03-12 2025-04-10
2025-05-13 2025-06-11 2025-07-15 2025-08-12 2025-09-11 2025-10-24 2025-12-18 2026-01-13 2026-02-13
2026-03-11 2026-04-10 2026-05-12 2026-06-10 2026-07-14 2026-08-12 2026-09-11 2026-10-14""".split()
PPI = """2024-01-12 2024-02-14 2024-02-16 2024-03-14 2024-04-11 2024-05-14 2024-06-13 2024-07-12
2024-08-13 2024-09-12 2024-10-11 2024-11-14 2024-12-12 2025-01-14 2025-02-13 2025-03-13 2025-04-11
2025-05-15 2025-06-12 2025-07-16 2025-08-14 2025-09-10 2025-11-25 2026-01-14 2026-01-30 2026-02-27
2026-03-18 2026-04-14 2026-05-13 2026-06-11 2026-07-15 2026-08-13 2026-09-10 2026-10-15""".split()
JOBS = """2024-01-05 2024-01-10 2024-02-02 2024-03-08 2024-04-05 2024-05-03 2024-06-07 2024-07-05
2024-08-02 2024-09-06 2024-10-04 2024-11-01 2024-12-06 2025-01-10 2025-02-07 2025-03-07 2025-04-04
2025-05-02 2025-06-06 2025-07-03 2025-08-01 2025-09-05 2025-11-20 2025-12-16 2026-01-09 2026-02-11
2026-03-06 2026-04-03 2026-05-08 2026-06-05 2026-07-02 2026-08-07 2026-09-04 2026-10-02""".split()
FOMC = """2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18
2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10
2026-01-28 2026-03-18 2026-04-29 2026-06-17 2026-07-29 2026-09-16""".split()
ALL = sorted(set(CPI) | set(PPI) | set(JOBS) | set(FOMC))


def session_set(CAL):
    import pandas as pd
    return {CAL.pos[pd.Timestamp(d)] for d in ALL if pd.Timestamp(d) in CAL.pos}
