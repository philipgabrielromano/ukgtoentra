"""
Interactive confirmation of fuzzy matches at write-time.

When enabled (--interactive), the user is shown each fuzzy auto-apply match
(nickname / spelling-variant / typo, i.e. tiers T4* that the engine would write)
and asked which ones NOT to apply. Anything the user excludes is demoted to a
skipped status and never written.

Safety:
  - Only fuzzy auto-apply matches are prompted. Exact matches (T2/T3) are trusted
    and not slowed down; REVIEW items are already held back regardless.
  - If there is no interactive terminal (e.g. a scheduled run), prompting is
    skipped and ALL fuzzy matches are demoted to review (conservative: not written),
    so an automated run never silently writes unconfirmed fuzzy matches.
  - Default for ambiguous/blank input is to KEEP the match? No — see prompt: the
    user lists the ones to EXCLUDE, so blank input = apply all shown (they were
    already vetted by the matcher). Each can also be inspected one-by-one.
"""
from __future__ import annotations

import sys
import logging

from .models import MatchResult

log = logging.getLogger("interactive")

FUZZY_TIERS = ("T4_FUZZY+SIGNAL", "T4b_NICKNAME")  # tiers that auto-apply via fuzzy logic


def _is_fuzzy_autoapply(r: MatchResult) -> bool:
    return r.decision == "AUTO_APPLY" and (
        r.tier in FUZZY_TIERS or r.tier.startswith("T4b_") or r.tier.startswith("T4_FUZZY")
    )


def confirm_fuzzy_matches(results: list[MatchResult], entra_by_id: dict) -> int:
    """Prompt the user about fuzzy auto-apply matches. Returns count excluded.

    Demoted matches get decision='SKIP_USER_EXCLUDED' so they are not written and
    are visible in the reports/audit.
    """
    fuzzy = [r for r in results if _is_fuzzy_autoapply(r)]
    if not fuzzy:
        log.info("No fuzzy matches to confirm.")
        return 0

    # No TTY (scheduled/non-interactive): refuse to silently write fuzzy matches.
    if not sys.stdin or not sys.stdin.isatty():
        for r in fuzzy:
            r.decision = "REVIEW"
            r.tier = "T4_NONINTERACTIVE_HELD"
            r.reasons.append("Interactive mode requested but no terminal; held for review.")
        log.warning("No interactive terminal: %d fuzzy match(es) held for review "
                    "(not written).", len(fuzzy))
        return len(fuzzy)

    print("\n" + "=" * 72)
    print(f"FUZZY MATCH CONFIRMATION  —  {len(fuzzy)} matches the engine would auto-apply")
    print("=" * 72)
    print("These are nickname / spelling-variant / typo matches (last name matches\n"
          "exactly). Review the list, then enter the NUMBERS you do NOT want to apply.\n")

    for i, r in enumerate(fuzzy, 1):
        e = entra_by_id.get(r.entra.source_id) if r.entra else None
        cur_title = (e.raw.get("jobTitle") if e and e.raw else None) or "(blank)"
        cur_dept = (e.raw.get("department") if e and e.raw else None) or "(blank)"
        score = r.candidates[0].get("score") if r.candidates else "?"
        print(f"  [{i:>3}] UKG: {r.ukg.first_name} {r.ukg.last_name:<18} "
              f"-> Entra: {r.entra.first_name} {r.entra.last_name}")
        print(f"        UPN: {r.entra.email}   name-score: {score}")
        print(f"        will set: title={r.ukg.job_title!r} loc={r.ukg.location!r}")
        print(f"        Entra now: title={cur_title!r} dept={cur_dept!r} "
              f"(UKG dept: {r.ukg.department!r}, not synced)")
        print()

    print("-" * 72)
    print("Enter numbers to EXCLUDE (comma/space separated), e.g.  3, 7, 12")
    print("  • blank + Enter  = apply ALL shown")
    print("  • 'none'         = apply ALL shown")
    print("  • 'all'          = exclude ALL (write none of these)")
    raw = input("Exclude which? > ").strip().lower()

    excluded_idx: set[int] = set()
    if raw in ("", "none"):
        excluded_idx = set()
    elif raw == "all":
        excluded_idx = set(range(1, len(fuzzy) + 1))
    else:
        for tok in raw.replace(",", " ").split():
            if tok.isdigit():
                n = int(tok)
                if 1 <= n <= len(fuzzy):
                    excluded_idx.add(n)
                else:
                    print(f"  (ignoring out-of-range: {n})")
            else:
                print(f"  (ignoring non-number: {tok!r})")

    for i, r in enumerate(fuzzy, 1):
        if i in excluded_idx:
            r.decision = "SKIP_USER_EXCLUDED"
            r.tier = "USER_EXCLUDED_" + r.tier
            r.reasons.append("Excluded by user during interactive confirmation.")

    kept = len(fuzzy) - len(excluded_idx)
    print(f"\nConfirmed: applying {kept}, excluding {len(excluded_idx)}.")
    log.info("Interactive fuzzy confirmation: %d applied, %d excluded.",
             kept, len(excluded_idx))
    return len(excluded_idx)
