"""Tests for UKG -> Entra HR data sync: field diffing + manager resolution."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import Person, MatchResult
from src.field_map import load_field_map, diff_attributes
from src.manager_sync import ManagerResolver
from src.hr_sync import HrSync


def E(i, f, l, upn, **kw):
    p = Person(source="entra", source_id=f"e{i}", first_name=f, last_name=l, email=upn, **kw)
    # mimic graph raw payload used for current-value comparison
    p.raw = {"jobTitle": kw.get("job_title"), "department": kw.get("department"),
             "officeLocation": kw.get("location")}
    return p


def U(i, f, l, **kw):
    return Person(source="ukg", source_id=f"u{i}", first_name=f, last_name=l, **kw)


def match(ukg, entra, decision="AUTO_APPLY"):
    return MatchResult(ukg=ukg, entra=entra, decision=decision, confidence=99,
                       tier="T2", target_email=entra.email)


# ---- field mapping / diff ----
def test_default_field_map():
    fields = load_field_map({})
    attrs = {f.graph_attr for f in fields}
    # department is intentionally NOT synced by default
    assert attrs == {"jobTitle", "officeLocation"}


def test_diff_only_changed_attributes():
    fields = load_field_map({})
    entra = E(1, "Ann", "Lee", "ann@corp.com", job_title="Analyst",
              department="Finance", location="NYC")
    # UKG has new title + new location, same department
    ukg = U(1, "Ann", "Lee", job_title="Senior Analyst", department="Finance", location="Boston")
    changes = diff_attributes(ukg, entra, fields)
    assert changes == {"jobTitle": "Senior Analyst", "officeLocation": "Boston"}


def test_department_not_synced_by_default():
    fields = load_field_map({})
    entra = E(1, "Ann", "Lee", "ann@corp.com", job_title="Analyst", department="Finance")
    # UKG has a DIFFERENT department; it must NOT appear in the change set
    ukg = U(1, "Ann", "Lee", job_title="Analyst", department="Engineering")
    changes = diff_attributes(ukg, entra, fields)
    assert changes == {}


def test_department_can_be_reenabled_via_config():
    fields = load_field_map({"UKG_TO_ENTRA_MAP":
                             '{"jobTitle":"job_title","department":"department"}'})
    entra = E(1, "Ann", "Lee", "ann@corp.com", job_title="Analyst", department="Finance")
    ukg = U(1, "Ann", "Lee", job_title="Analyst", department="Engineering")
    changes = diff_attributes(ukg, entra, fields)
    assert changes == {"department": "Engineering"}


def test_diff_never_blanks_existing():
    fields = load_field_map({})
    entra = E(1, "Ann", "Lee", "ann@corp.com", job_title="Analyst", department="Finance")
    ukg = U(1, "Ann", "Lee", job_title=None, department=None)  # UKG missing data
    changes = diff_attributes(ukg, entra, fields)
    assert changes == {}  # do not erase Entra values


def test_custom_field_map():
    fields = load_field_map({"UKG_TO_ENTRA_MAP":
                             '{"jobTitle":"job_title","employeeId":"employee_number"}'})
    attrs = {f.graph_attr for f in fields}
    assert attrs == {"jobTitle", "employeeId"}


# ---- manager resolution ----
def test_manager_by_employee_number():
    entra = [E(2, "Boss", "Person", "boss@corp.com", employee_number="9001")]
    res = ManagerResolver(entra, [])
    ukg = U(1, "Sub", "Ordinate", manager="Whoever", manager_employee_number="9001")
    mgr, reason = res.resolve(ukg)
    assert mgr.source_id == "e2"
    assert "employee number" in reason


def test_manager_by_match_table():
    boss_entra = E(2, "Jane", "Boss", "jane.boss@corp.com")
    boss_ukg = U(2, "Jane", "Boss")
    results = [match(boss_ukg, boss_entra)]
    res = ManagerResolver([boss_entra], results)
    ukg = U(1, "Sub", "Ordinate", manager="Jane Boss")
    mgr, reason = res.resolve(ukg)
    assert mgr.source_id == "e2"


def test_manager_last_first_order():
    boss = E(2, "Jane", "Boss", "jane.boss@corp.com")
    res = ManagerResolver([boss], [])
    ukg = U(1, "Sub", "Ordinate", manager="Boss, Jane")  # UKG "Last, First"
    mgr, reason = res.resolve(ukg)
    assert mgr.source_id == "e2"


def test_manager_unresolved():
    res = ManagerResolver([E(2, "Jane", "Boss", "jane@corp.com")], [])
    ukg = U(1, "Sub", "Ordinate", manager="Nonexistent Manager")
    mgr, reason = res.resolve(ukg)
    assert mgr is None


# ---- end-to-end plan build (with fake client, dry run) ----
class FakeEntra:
    def update_user_attributes(self, eid, changes): return True, "204"
    def get_current_manager_id(self, eid): return None
    def set_manager(self, eid, mid): return True, "204"


def test_hr_plan_build_and_dryrun():
    boss_e = E(2, "Jane", "Boss", "jane.boss@corp.com")
    emp_e = E(1, "Ann", "Lee", "ann@corp.com", job_title="Analyst", department="Finance")
    boss_u = U(2, "Jane", "Boss")
    emp_u = U(1, "Ann", "Lee", job_title="Senior Analyst", department="Finance",
              location="Boston", manager="Jane Boss")
    results = [match(emp_u, emp_e), match(boss_u, boss_e)]
    hr = HrSync(FakeEntra(), load_field_map({}), [boss_e, emp_e], results, sync_manager=True)
    plan = hr.build_plan(results)
    ann = [c for c in plan if c.name == "Ann Lee"][0]
    assert ann.attribute_changes == {"jobTitle": "Senior Analyst", "officeLocation": "Boston"}
    assert ann.manager_change["new_id"] == "e2"
    hr.apply_plan(plan, do_write=False)
    assert ann.status == "DRY_RUN"


def test_on_prem_user_is_not_written():
    emp_e = E(1, "Ann", "Lee", "ann@corp.com", job_title="Analyst")
    emp_e.on_prem_synced = True                       # AD-synced user
    emp_u = U(1, "Ann", "Lee", job_title="Senior Analyst")
    results = [match(emp_u, emp_e)]
    hr = HrSync(FakeEntra(), load_field_map({}), [emp_e], results, sync_manager=False)
    plan = hr.build_plan(results)
    c = plan[0]
    assert c.on_prem_synced is True
    assert c.attribute_changes == {"jobTitle": "Senior Analyst"}  # change still computed
    hr.apply_plan(plan, do_write=True)                # even with --apply...
    assert c.status == "SKIPPED_ON_PREM"              # ...it is NOT written to Entra


def test_cloud_user_still_writes_alongside_on_prem():
    cloud = E(1, "Ann", "Lee", "ann@corp.com", job_title="Analyst")
    synced = E(2, "Bo", "Kim", "bo@corp.com", job_title="Clerk")
    synced.on_prem_synced = True
    results = [match(U(1, "Ann", "Lee", job_title="Senior Analyst"), cloud),
               match(U(2, "Bo", "Kim", job_title="Manager"), synced)]
    hr = HrSync(FakeEntra(), load_field_map({}), [cloud, synced], results, sync_manager=False)
    plan = hr.build_plan(results)
    hr.apply_plan(plan, do_write=True)
    by_name = {c.name: c for c in plan}
    assert by_name["Ann Lee"].status == "WRITTEN"
    assert by_name["Bo Kim"].status == "SKIPPED_ON_PREM"


def test_limit_caps_writes():
    e1 = E(1, "A", "One", "a@corp.com", job_title="Old1")
    e2 = E(2, "B", "Two", "b@corp.com", job_title="Old2")
    e3 = E(3, "C", "Three", "c@corp.com", job_title="Old3")
    results = [match(U(1, "A", "One", job_title="New1"), e1),
               match(U(2, "B", "Two", job_title="New2"), e2),
               match(U(3, "C", "Three", job_title="New3"), e3)]
    hr = HrSync(FakeEntra(), load_field_map({}), [e1, e2, e3], results, sync_manager=False)
    plan = hr.build_plan(results)
    hr.apply_plan(plan, do_write=True, limit=2)
    statuses = [c.status for c in plan]
    assert statuses.count("WRITTEN") == 2
    assert statuses.count("SKIPPED_LIMIT") == 1


def test_backup_captures_before_state():
    e1 = E(1, "A", "One", "a@corp.com", job_title="OldTitle", department="OldDept")
    results = [match(U(1, "A", "One", job_title="NewTitle", department="OldDept"), e1)]
    hr = HrSync(FakeEntra(), load_field_map({}), [e1], results, sync_manager=False)
    plan = hr.build_plan(results)
    backup = []
    hr.apply_plan(plan, do_write=True, backup=backup)
    assert len(backup) == 1
    # only the changed attr (jobTitle) is in the plan; backup records its old value
    assert backup[0]["before"]["jobTitle"] == "OldTitle"


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
