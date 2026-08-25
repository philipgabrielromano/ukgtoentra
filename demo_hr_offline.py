"""
Offline demo of the UKG -> Entra HR-data sync (no live APIs).
Shows attribute changes + manager resolution, in dry-run.

    python3 demo_hr_offline.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.models import Person
from src.matcher import Matcher
from src.field_map import load_field_map
from src.hr_sync import HrSync
from src import reporting

REPORTS = Path(__file__).resolve().parent / "reports"
REPORTS.mkdir(exist_ok=True)


def E(i, f, l, upn, **kw):
    p = Person("entra", f"e{i}", f, l, upn, **kw)
    p.raw = {"jobTitle": kw.get("job_title"), "department": kw.get("department"),
             "officeLocation": kw.get("location")}
    return p


def U(i, f, l, **kw):
    return Person("ukg", f"u{i}", f, l, **kw)


# Entra users (identity side; HR fields possibly stale/empty)
entra = [
    E(1, "Ann", "Lee", "ann.lee@corp.com", job_title="Analyst", department="Finance"),
    E(2, "Jane", "Boss", "jane.boss@corp.com", job_title="Director", department="Finance"),
    E(3, "Carlos", "Mendez", "carlos.mendez@corp.com"),  # no HR data yet
]
entra[2].on_prem_synced = True   # Carlos is AD-synced -> must NOT be written to Entra

# UKG employees (HR source of truth)
ukg = [
    U(1, "Ann", "Lee", job_title="Senior Analyst", department="Finance",
      location="Boston", manager="Jane Boss", employee_number="100"),
    U(2, "Jane", "Boss", job_title="Director", department="Finance",
      location="NYC", employee_number="101"),
    U(3, "Carlos", "Mendez", job_title="Engineer II", department="IT",
      location="Austin", manager="Boss, Jane", employee_number="102"),
]

cfg = {"AUTO_APPLY_UNIQUE_EXACT_NAME": True, "FUZZY_MIN_SCORE": 85,
       "FUZZY_REQUIRE_SECONDARY": True,
       "SECONDARY_SIGNALS": "employee_number,department,job_title,manager"}

results = Matcher(cfg).match_all(entra, ukg)


class FakeEntra:
    def update_user_attributes(self, eid, changes): return True, "204"
    def get_current_manager_id(self, eid): return None
    def set_manager(self, eid, mid): return True, "204"


fields = load_field_map(cfg)
hr = HrSync(FakeEntra(), fields, entra, results, sync_manager=True)
plan = hr.build_plan(results)
hr.apply_plan(plan, do_write=True)   # simulate --apply to show on-prem skip behavior
rows = [c.as_row() for c in plan]
reporting.write_hr_plan(rows, REPORTS)
reporting.write_on_prem_report(rows, REPORTS)

print("\n=== UKG -> Entra HR sync plan (simulated --apply) ===")
for c in plan:
    tag = "  [AD-SYNCED -> on-prem]" if c.on_prem_synced else ""
    print(f"\n{c.name}  (entra {c.entra_id})  status={c.status}{tag}")
    for k, v in c.attribute_changes.items():
        print(f"    set {k:<16} = {v}")
    if c.manager_change:
        print(f"    set manager          -> {c.manager_change['name']}  [{c.manager_change['reason']}]")
    if c.manager_unresolved:
        print(f"    MANAGER UNRESOLVED   : {c.manager_unresolved}")
