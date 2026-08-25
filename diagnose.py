"""
Diagnostic: inspect the UKG/Entra data and explain the match breakdown.
Read-only. Helps explain WHY matches land where they do.

    python3 diagnose.py
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import dotenv_values
from src.entra_client import EntraClient
from src.ukg_client import UkgClient
from src.matcher import Matcher
from src.normalize import name_key

cfg = dict(dotenv_values(Path(__file__).resolve().parent / "config" / ".env"))

entra = EntraClient(cfg).fetch_users()
ukg_client = UkgClient(cfg)
ukg = ukg_client.fetch_employees()           # respects UKG_ACTIVE_ONLY

print("\n" + "=" * 64)
print("DATA OVERVIEW")
print("=" * 64)
print(f"Entra users:        {len(entra)}")
print(f"UKG employees:      {len(ukg)}  (active filter: {ukg_client.active_only})")

# field population
def pct(items, attr):
    n = sum(1 for p in items if getattr(p, attr))
    return f"{n}/{len(items)} ({100*n//max(len(items),1)}%)"

print("\nUKG field population:")
for a in ("first_name", "last_name", "employee_number", "department",
          "job_title", "hire_date", "location", "manager"):
    print(f"  {a:<18}{pct(ukg, a)}")
print("\nEntra field population:")
for a in ("first_name", "last_name", "employee_number", "department",
          "job_title", "location"):
    print(f"  {a:<18}{pct(entra, a)}")

# duplicate-name analysis (the cause of T5_AMBIGUOUS)
ukg_names = Counter(name_key(p.first_name, p.last_name) for p in ukg)
entra_names = Counter(name_key(p.first_name, p.last_name) for p in entra)
dup_ukg = {k: v for k, v in ukg_names.items() if v > 1}
dup_entra = {k: v for k, v in entra_names.items() if v > 1}
print("\nDuplicate names:")
print(f"  UKG names appearing >1x:   {len(dup_ukg)}  (top: "
      f"{sorted(dup_ukg.items(), key=lambda x:-x[1])[:3]})")
print(f"  Entra names appearing >1x: {len(dup_entra)}")

# run matcher and show breakdown
results = Matcher(cfg).match_all(entra, ukg)
print("\n" + "=" * 64)
print("MATCH BREAKDOWN")
print("=" * 64)
for k, v in sorted(Counter(r.decision for r in results).items()):
    print(f"  {k:<12}{v}")
print()
for k, v in sorted(Counter(r.tier for r in results).items()):
    print(f"  {k:<30}{v}")

# show a few sample ambiguous cases
amb = [r for r in results if r.decision == "REVIEW"][:5]
if amb:
    print("\nSample REVIEW cases:")
    for r in amb:
        print(f"  {r.ukg.first_name} {r.ukg.last_name} | dept={r.ukg.department} "
              f"| {len(r.candidates)} candidates | {r.tier}")
