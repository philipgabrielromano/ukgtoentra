"""
Tests for T4b safe nickname/variant auto-apply, using the REAL cases observed in
the user's data. The critical safety property: relatives who share a surname but
have entirely different first names must NOT auto-match.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import Person
from src.matcher import Matcher

CFG = {
    "AUTO_APPLY_UNIQUE_EXACT_NAME": True,
    "FUZZY_MIN_SCORE": 60,            # lower so candidates are gathered
    "FUZZY_REQUIRE_SECONDARY": True,
    "FUZZY_NICKNAME_AUTOAPPLY": "true",
    "SECONDARY_SIGNALS": "department,job_title,hire_date,location,manager",
}


def E(i, f, l): return Person("entra", f"e{i}", f, l, f"{f}.{l}@corp.com".lower())
def U(i, f, l): return Person("ukg", f"u{i}", f, l)


def decide(entra, ukg):
    return {r.ukg.source_id: r for r in Matcher(CFG).match_all(entra, ukg)}


# ---- SHOULD auto-apply (nickname / variant / typo, same last name) ----
SAFE_PAIRS = [
    ("Philip", "Phil", "Romano"), ("Jeffrey", "Jeff", "Lounds"),
    ("Lawrence", "Larry", "Price"), ("Nicholas", "Nick", "Garrett"),
    ("Vincent", "Vince", "Getz"), ("Gregory", "Greg", "Edelman"),
    ("Benjamin", "Ben", "Haring"), ("Joshua", "Josh", "Frazier"),
    ("Kimberly", "Kim", "Labriola"), ("Thomas", "Tom", "Trachsel"),
    ("Sandra", "Sandy", "Marek"), ("Cynthia", "Cindy", "Fekete"),
    # spelling variants / typos
    ("April", "Aprile", "Steele"), ("Nichole", "Nicole", "Samsa"),
    ("Teresa", "Theresa", "Barger"), ("Micheal", "Michael", "Clayton"),
    ("Abigial", "Abigail", "Toporowsky"), ("Kurdtis", "Kurdis", "Lilley"),
]

# ---- Must STAY in review (different people, shared surname = likely relatives) ----
RELATIVE_PAIRS = [
    ("Amanda", "Katie", "Gauze"), ("Elizabeth", "Lisa", "Heintz"),
    ("Catherine", "Colleen", "Porter"), ("Grace", "Joey", "Worley"),
    ("Jessica", "Jay", "Nemeth"), ("Noah", "Noel", "Lee"),
    ("Diana", "Karina", "Diaz"), ("Louis", "Mario", "LaGuardia"),
    ("Chanze", "Colton", "Boyd"), ("Allison", "Leo", "Swogger"),
    ("William", "Mike", "McWilliams"), ("Alayna", "Avril", "Marks"),
]


def test_safe_nickname_and_variant_pairs_auto_apply():
    failed = []
    for ukg_first, entra_first, last in SAFE_PAIRS:
        entra = [E(1, entra_first, last)]
        ukg = [U(1, ukg_first, last)]
        r = decide(entra, ukg)["u1"]
        if r.decision != "AUTO_APPLY":
            failed.append((ukg_first, entra_first, last, r.decision, r.tier))
    assert not failed, f"These SAFE pairs did NOT auto-apply: {failed}"


def test_relatives_stay_in_review():
    leaked = []
    for ukg_first, entra_first, last in RELATIVE_PAIRS:
        entra = [E(1, entra_first, last)]
        ukg = [U(1, ukg_first, last)]
        r = decide(entra, ukg)["u1"]
        if r.decision == "AUTO_APPLY":
            leaked.append((ukg_first, entra_first, last, r.tier))
    assert not leaked, f"DANGER: these different-person pairs auto-matched: {leaked}"


def test_two_relatives_present_does_not_grab_wrong_one():
    # Both a real nickname match AND a relative share the surname.
    entra = [E(1, "Phil", "Romano"), E(2, "Katie", "Romano")]
    ukg = [U(1, "Philip", "Romano")]
    r = decide(entra, ukg)["u1"]
    assert r.decision == "AUTO_APPLY"
    assert r.entra.first_name == "Phil"   # picked the nickname, not the relative


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    p = f = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}"); p += 1
        except Exception:
            print(f"FAIL  {t.__name__}"); traceback.print_exc(); f += 1
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)
