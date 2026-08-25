"""
Show the actual UKG-name vs Entra-candidate-name pairs for fuzzy REVIEW cases,
so we can see WHY they don't match exactly (nickname? maiden name? middle name?).
Read-only.

    python3 diagnose_fuzzy.py
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import dotenv_values
from src.entra_client import EntraClient
from src.ukg_client import UkgClient
from src.matcher import Matcher
from src.normalize import normalize_name

cfg = dict(dotenv_values(Path(__file__).resolve().parent / "config" / ".env"))
entra = EntraClient(cfg).fetch_users()
ukg = UkgClient(cfg).fetch_employees()
results = Matcher(cfg).match_all(entra, ukg)

fuzzy = [r for r in results if r.tier == "T4_FUZZY_REVIEW" and r.candidates]

print(f"\n{len(fuzzy)} fuzzy review cases. UKG name  ->  best Entra candidate\n" + "=" * 70)

# classify the difference
first_diff = last_diff = both_diff = 0
patterns = Counter()
for r in fuzzy:
    c = r.candidates[0]
    uf, ul = normalize_name(r.ukg.first_name), normalize_name(r.ukg.last_name)
    cf, cl = normalize_name(c.get("first")), normalize_name(c.get("last"))
    fd, ld = uf != cf, ul != cl
    if fd and ld: both_diff += 1; tag = "BOTH differ"
    elif fd: first_diff += 1; tag = "FIRST differs"
    else: last_diff += 1; tag = "LAST differs"

    # heuristics for the cause
    cause = ""
    if fd and (cf.startswith(uf[:3]) or uf.startswith(cf[:3])):
        cause = "likely nickname"
    elif ld and (ul in cl or cl in ul):
        cause = "last-name substring (compound/maiden?)"
    elif fd and len(cf) and len(uf) and cf[0] == uf[0]:
        cause = "same initial (nickname?)"
    patterns[tag + (f" / {cause}" if cause else "")] += 1

    cf_disp = (c.get('first') or '(none)')
    cl_disp = (c.get('last') or '(none)')
    print(f"  {str(r.ukg.first_name):>12} {str(r.ukg.last_name):<15} -> "
          f"{cf_disp:>12} {cl_disp:<15}  score={c.get('score')}  [{tag}]")

print("\n" + "=" * 70)
print("PATTERN SUMMARY")
print("=" * 70)
print(f"  First name differs only: {first_diff}")
print(f"  Last name differs only:  {last_diff}")
print(f"  Both differ:             {both_diff}")
print("\nDetailed patterns:")
for k, v in patterns.most_common():
    print(f"  {v:>3}  {k}")
