"""
Closer look at REVIEW cases: are they real matches held back because Entra lacks
secondary signals (blank dept/title)? Read-only.

    python3 diagnose_reviews.py
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

review = [r for r in results if r.decision == "REVIEW"]
print(f"\n{len(review)} REVIEW cases\n" + "=" * 64)

# For each review case with exactly one candidate, check whether the candidate's
# name is actually an EXACT normalized match (i.e., likely the right person) and
# whether Entra simply lacked secondary data.
exact_name_single = 0
entra_blank_secondary = 0
fuzzy_real = 0
for r in review:
    if len(r.candidates) != 1:
        continue
    c = r.candidates[0]
    u_full = f"{normalize_name(r.ukg.first_name)} {normalize_name(r.ukg.last_name)}"
    c_full = f"{normalize_name(c.get('first'))} {normalize_name(c.get('last'))}"
    # find the entra person object
    ep = next((p for p in entra if p.source_id == c.get("entra_id")), None)
    entra_has_secondary = bool(ep and (ep.department or ep.job_title or ep.location))
    if u_full == c_full:
        exact_name_single += 1
        if not entra_has_secondary:
            entra_blank_secondary += 1
    else:
        fuzzy_real += 1

print(f"Single-candidate reviews where name is an EXACT match: {exact_name_single}")
print(f"  ...of those, Entra had NO secondary data to confirm:  {entra_blank_secondary}")
print(f"Single-candidate reviews that are genuinely fuzzy:      {fuzzy_real}")
print(f"Multi-candidate (truly ambiguous) reviews:              "
      f"{sum(1 for r in review if len(r.candidates) > 1)}")

print("\nInterpretation:")
print("  'Exact-match-but-Entra-blank' cases are almost certainly correct and are")
print("  only in review because Entra lacked dept/title to auto-confirm. These are")
print("  safe to auto-apply if you trust exact name + unique candidate.")

# tier breakdown
print("\nReview tier breakdown:")
for k, v in sorted(Counter(r.tier for r in review).items()):
    print(f"  {k:<28}{v}")
