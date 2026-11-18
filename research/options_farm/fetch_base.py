"""Step 1: underlying daily bars (raw + split) for both universes, contract lists, splits."""
import json
import odata as D
from farm_universe import FARM_UNIVERSE, PEAD_UNIVERSE

names = sorted(set(FARM_UNIVERSE) | set(PEAD_UNIVERSE))
splits = {}
for s in names:
    D.stock_daily(s, adjustment="raw"); D.stock_daily(s, adjustment="split")
    sd = D.split_dates(s)
    if sd:
        splits[s] = [str(x.date()) for x in sd]
print("splits in data:", splits)
(D.CACHE / "splits.json").write_text(json.dumps(splits, indent=1))
for s in names:
    c = D.list_contracts(s, "2024-01-18", "2026-12-31")
    print(s, len(c), c.expiry.nunique(), c.expiry.min().date(), c.expiry.max().date(), c.root.unique()[:5])
print(D.flush_calls())
