"""
Tests for the matching engine using realistic, tricky synthetic data.

Covers:
  - exact unique name
  - nickname (Bob/Robert) with secondary signal
  - duplicate names (two John Smiths) -> ambiguous review
  - employee-number strong key wins
  - email already correct -> skip
  - accents / hyphens normalization
  - typo fuzzy match needs secondary
  - no match
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import Person
from src.matcher import Matcher

CFG = {
    "AUTO_APPLY_UNIQUE_EXACT_NAME": True,
    "FUZZY_MIN_SCORE": 85,
    "FUZZY_REQUIRE_SECONDARY": True,
    "SECONDARY_SIGNALS": "upn_name,department,job_title,hire_date,location,manager",
}


def E(i, f, l, upn, **kw):
    return Person(source="entra", source_id=f"e{i}", first_name=f, last_name=l, email=upn, **kw)


def U(i, f, l, email=None, **kw):
    return Person(source="ukg", source_id=f"u{i}", first_name=f, last_name=l, email=email, **kw)


def decide(entra, ukg):
    m = Matcher(CFG)
    res = m.match_all(entra, ukg)
    return {r.ukg.source_id: r for r in res}


def test_exact_unique_name_auto_applies():
    # UPN does not encode the name -> isolates the T3 unique-exact path.
    entra = [E(1, "Alice", "Walker", "user12345@corp.com")]
    ukg = [U(1, "Alice", "Walker", email="old@corp.com")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "AUTO_APPLY"
    assert r.target_email == "user12345@corp.com"
    assert r.tier == "T3_UNIQUE_EXACT_NAME"


def test_nickname_with_secondary_signal():
    entra = [E(1, "Robert", "Jones", "robert.jones@corp.com", department="Sales")]
    ukg = [U(1, "Bob", "Jones", email="wrong@corp.com", department="Sales")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "AUTO_APPLY"
    assert r.tier == "T4_FUZZY+SIGNAL"
    assert "department" in r.secondary_agreements


def test_nickname_without_secondary_now_auto_applies_safely():
    # NEW (T4b): trusted nickname (Bob<->Robert) + exact last name + single
    # candidate auto-applies even with no secondary signal and a non-encoding UPN.
    entra = [E(1, "Robert", "Jones", "user99@corp.com")]
    ukg = [U(1, "Bob", "Jones", email="wrong@corp.com")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "AUTO_APPLY"
    assert r.tier.startswith("T4b_NICKNAME")


def test_relative_sharing_surname_stays_in_review():
    # Different first names, same surname -> must NOT auto-apply (likely relatives).
    entra = [E(1, "Lisa", "Heintz", "user50@corp.com")]
    ukg = [U(1, "Elizabeth", "Heintz", email="x@corp.com")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "REVIEW"


def test_duplicate_names_are_ambiguous():
    entra = [
        E(1, "John", "Smith", "user1@corp.com"),
        E(2, "John", "Smith", "user2@corp.com"),
    ]
    ukg = [U(1, "John", "Smith", email="x@corp.com")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "REVIEW"
    assert r.tier == "T5_AMBIGUOUS_EXACT"
    assert len(r.candidates) == 2


def test_duplicate_names_resolved_by_secondary_signal():
    # Entra has NO employee number; disambiguate by department instead.
    entra = [
        E(1, "John", "Smith", "john.smith1@corp.com", department="Sales"),
        E(2, "John", "Smith", "john.smith2@corp.com", department="Engineering"),
    ]
    ukg = [U(1, "John", "Smith", email="x@corp.com", department="Engineering")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "AUTO_APPLY"
    assert r.tier == "T2_EXACT_NAME+SIGNAL"
    assert r.entra.source_id == "e2"


def test_duplicate_names_signal_tie_goes_to_review():
    # Two John Smiths BOTH in Engineering -> dept does not disambiguate -> review
    entra = [
        E(1, "John", "Smith", "john.smith1@corp.com", department="Engineering"),
        E(2, "John", "Smith", "john.smith2@corp.com", department="Engineering"),
    ]
    ukg = [U(1, "John", "Smith", email="x@corp.com", department="Engineering")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "REVIEW"
    assert r.tier == "T5_AMBIGUOUS_SIGNAL_TIE"


def test_accents_and_hyphens_normalize():
    entra = [E(1, "José", "García-López", "jose.garcia@corp.com")]
    ukg = [U(1, "Jose", "Garcia Lopez", email="bad@corp.com")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "AUTO_APPLY"
    assert r.target_email == "jose.garcia@corp.com"


def test_typo_fuzzy_needs_secondary():
    # last name typo: Andersen vs Anderson
    entra = [E(1, "Mark", "Anderson", "mark.anderson@corp.com", hire_date="2020-01-15")]
    ukg = [U(1, "Mark", "Andersen", email="bad@corp.com", hire_date="2020-01-15")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "AUTO_APPLY"
    assert "hire_date" in r.secondary_agreements


def test_upn_initial_lastname_confirms_nickname():
    # Entra displayName is a nickname; UPN is firstinitial+lastname.
    entra = [E(1, "Phil", "Romano", "promano@org.com")]
    ukg = [U(1, "Philip", "Romano", email="x@x.com")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "AUTO_APPLY"
    assert "upn_name" in r.secondary_agreements


def test_upn_full_nickname_in_local_part():
    entra = [E(1, "Timothy", "Moore", "timmoore@org.com")]
    ukg = [U(1, "Timothy", "Moore", email="x@x.com")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "AUTO_APPLY"


def test_upn_wrong_initial_rejected():
    # Ryan Shaffer should NOT match jshaffer@ (different person, J initial).
    entra = [E(1, "Jack", "Shaffer", "jshaffer@org.com")]
    ukg = [U(1, "Ryan", "Shaffer", email="x@x.com")]
    r = decide(entra, ukg)["u1"]
    assert r.decision != "AUTO_APPLY"   # must not auto-apply a wrong-initial UPN


def test_upn_lastname_substring_not_fooled():
    # 'mmcwilliams' must not match William (lastname mcwilliams != m+williams).
    entra = [E(1, "Mike", "McWilliams", "mmcwilliams@org.com")]
    ukg = [U(1, "William", "McWilliams", email="x@x.com")]
    r = decide(entra, ukg)["u1"]
    assert r.decision != "AUTO_APPLY"


def test_no_match():
    entra = [E(1, "Alice", "Walker", "alice.walker@corp.com")]
    ukg = [U(1, "Zach", "Nonexistent", email="x@corp.com")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "NO_MATCH"


def test_unique_exact_disabled_goes_to_review():
    cfg = dict(CFG, AUTO_APPLY_UNIQUE_EXACT_NAME=False)
    m = Matcher(cfg)
    entra = [E(1, "Alice", "Walker", "user12345@corp.com")]
    ukg = [U(1, "Alice", "Walker", email="old@corp.com")]
    r = {x.ukg.source_id: x for x in m.match_all(entra, ukg)}["u1"]
    assert r.decision == "REVIEW"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
