"""
Show every REVIEW case with the actual UKG->Entra name pair and score, grouped
by tier, so you can quickly see which are obvious approvals vs real ambiguities.
Read-only (no writes).

    python3 show_reviews.py
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import dotenv_values
from src.entra_client import EntraClient
from src.ukg_client import UkgClient
from src.matcher import Matcher

cfg = dict(dotenv_values(Path(__file__).resolve().parent / "config" / ".env"))

entra = EntraClient(cfg).fetch_users()
ukg = UkgClient(cfg).fetch_employees()
results = Matcher(cfg).match_all(entra, ukg)

review = [r for r in results if r.decision == "REVIEW"]
by_tier = defaultdict(list)
for r in review:
    by_tier[r.tier].append(r)

print(f"\n{len(review)} REVIEW cases\n")

for tier in sorted(by_tier):
    items = by_tier[tier]
    print("=" * 78)
    print(f"{tier}  ({len(items)} cases)")
    print("=" * 78)
    for r in items:
        u = r.ukg
        print(f"\nUKG: {u.first_name} {u.last_name}  "
              f"(dept={u.department}, title={u.job_title}, hired={u.hire_date}, mgr={u.manager})")
        if not r.candidates:
            print("     -> no Entra candidate")
        for i, c in enumerate(r.candidates, 1):
            print(f"  #{i} score {c['score']:>5}: {c['first']} {c['last']} "
                  f"<{c['upn']}>  dept={c.get('department')} title={c.get('job_title')}")
    print()
