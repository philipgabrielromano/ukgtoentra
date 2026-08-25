"""Tests for interactive fuzzy confirmation (input mocked)."""
import builtins
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import Person, MatchResult
from src import interactive


def fuzzy_result(uid, first, last, entra_first):
    e = Person("entra", f"e{uid}", entra_first, last, f"{entra_first}.{last}@x.com")
    e.raw = {"jobTitle": None, "department": None}
    u = Person("ukg", f"u{uid}", first, last, job_title="Eng", department="IT")
    return MatchResult(ukg=u, entra=e, decision="AUTO_APPLY", confidence=90,
                       tier="T4b_NICKNAME", target_email=e.email,
                       candidates=[{"score": 90, "first": entra_first, "last": last,
                                    "upn": e.email, "entra_id": e.source_id}])


def run_with_input(results, answer, monkeypatch_tty=True):
    entra_by_id = {r.entra.source_id: r.entra for r in results}
    # force a "tty" and feed the answer
    orig_isatty = sys.stdin.isatty
    sys.stdin.isatty = lambda: True
    orig_input = builtins.input
    builtins.input = lambda *a, **k: answer
    try:
        excluded = interactive.confirm_fuzzy_matches(results, entra_by_id)
    finally:
        builtins.input = orig_input
        sys.stdin.isatty = orig_isatty
    return excluded


def test_blank_applies_all():
    r1 = fuzzy_result(1, "Philip", "Romano", "Phil")
    r2 = fuzzy_result(2, "Jeffrey", "Lounds", "Jeff")
    excluded = run_with_input([r1, r2], "")
    assert excluded == 0
    assert r1.decision == "AUTO_APPLY" and r2.decision == "AUTO_APPLY"


def test_exclude_specific():
    r1 = fuzzy_result(1, "Philip", "Romano", "Phil")
    r2 = fuzzy_result(2, "Jeffrey", "Lounds", "Jeff")
    r3 = fuzzy_result(3, "Thomas", "Trachsel", "Tom")
    excluded = run_with_input([r1, r2, r3], "2")
    assert excluded == 1
    assert r1.decision == "AUTO_APPLY"
    assert r2.decision == "SKIP_USER_EXCLUDED"
    assert r3.decision == "AUTO_APPLY"


def test_exclude_all():
    r1 = fuzzy_result(1, "Philip", "Romano", "Phil")
    r2 = fuzzy_result(2, "Jeffrey", "Lounds", "Jeff")
    excluded = run_with_input([r1, r2], "all")
    assert excluded == 2
    assert r1.decision == "SKIP_USER_EXCLUDED" and r2.decision == "SKIP_USER_EXCLUDED"


def test_multiple_separators():
    rs = [fuzzy_result(i, "Philip", f"Last{i}", "Phil") for i in range(1, 6)]
    excluded = run_with_input(rs, "1, 3 5")
    assert excluded == 3
    assert rs[0].decision == "SKIP_USER_EXCLUDED"
    assert rs[1].decision == "AUTO_APPLY"
    assert rs[2].decision == "SKIP_USER_EXCLUDED"
    assert rs[4].decision == "SKIP_USER_EXCLUDED"


def test_non_interactive_holds_all():
    # No TTY -> all fuzzy held for review, none written
    r1 = fuzzy_result(1, "Philip", "Romano", "Phil")
    entra_by_id = {r1.entra.source_id: r1.entra}
    orig = sys.stdin.isatty
    sys.stdin.isatty = lambda: False
    try:
        held = interactive.confirm_fuzzy_matches([r1], entra_by_id)
    finally:
        sys.stdin.isatty = orig
    assert held == 1
    assert r1.decision == "REVIEW"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}"); passed += 1
        except Exception:
            print(f"FAIL  {t.__name__}"); traceback.print_exc(); failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
