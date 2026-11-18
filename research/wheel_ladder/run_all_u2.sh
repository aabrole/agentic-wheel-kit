#!/bin/zsh
# Runs every expanded-universe test once, in order. Needs the option download to be complete.
cd "$(dirname "$0")"
python3 fetch_universe2.py status
python3 study_u2.py                    > logs/study_u2.log 2>&1
HAIRCUT_MULT=2 python3 study_u2.py     > logs/study_u2_h2.log 2>&1
python3 study_rotate.py                > logs/study_rotate.log 2>&1
HAIRCUT_MULT=2 python3 study_rotate.py > logs/study_rotate_h2.log 2>&1
ROT_H=630 python3 study_rotate.py      > logs/study_rotate_630.log 2>&1
python3 sim7.py                        > logs/sim7.log 2>&1
echo done
