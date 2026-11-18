# Contractor prompt: test a suggestion

Paste this into Codex or Claude Code on the machine that has the kit and the research data. The coding agent is the contractor: it builds and tests. The assistant (Hermes) reviews results and files suggestions. You decide.

**Before you start:** testing needs the research data from `prompts/RUN_FULL_TEST.md` (hours to download the first time).

---

Test one open suggestion from the wheel kit and record the result.

1. Run `.venv/bin/python -m wheel.agent suggestions` and `.venv/bin/python -m wheel.agent goals`. Show me the open suggestions and ask which one to test if there is more than one.
2. Read the suggestion's `change`. Map it to the test code in `research/wheel_ladder/`:
   - days to expiry or delta: `DTE` and `DELTA` in `study_managed.py`
   - lots kept free: `COVER_LOTS` (environment variable) in `study_managed.py`
   - rolls on or off: the `ROLL` and `BASE` rules already compared in `study_managed.py`
   - pick rule: the arms in `study_rotate.py`
3. Write down, before running anything, what result would count as "better" for my goals (for example: at least the same average return with a smaller worst drop). Show me and wait for my OK.
4. Run the test on the full list, and again with `HAIRCUT_MULT=2`. If you must add a variant, add it as a new named rule or a new environment variable. Do not edit an existing rule or its docstring.
5. Report in plain language: average return, worst drop and how often it won, for the current setting and the suggested one, with the 90% interval on the difference. Say whether the difference is statistically clear.
6. Record it: `.venv/bin/python -m wheel.agent tested <id> "<two-sentence result with the numbers>"`.

Rules for you:

- Do not adopt or reject the suggestion. Do not run `wheel.agent tune`. Do not edit `.env` or anything in `wheel/`.
- A result from 2024 to 2026 describes that period. Say so in the recorded result.
- If the test cannot answer the question (for example the setting is not in the research code), record that instead of a number.
