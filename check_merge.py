"""
Verify the person-details <-> employment-details merge.
Checks whether employeeId values actually overlap between the two endpoints.
Read-only.

    python3 check_merge.py
"""
import base64
from pathlib import Path

import requests
from dotenv import dotenv_values

cfg = dotenv_values(Path(__file__).resolve().parent / "config" / ".env")
base = cfg["UKG_BASE_URL"].rstrip("/")
token = base64.b64encode(f"{cfg['UKG_USERNAME']}:{cfg['UKG_PASSWORD']}".encode()).decode()
headers = {"Authorization": f"Basic {token}",
           "US-Customer-Api-Key": cfg["UKG_CUSTOMER_API_KEY"],
           "Accept": "application/json"}
if cfg.get("UKG_USER_API_KEY"):
    headers["US-User-Api-Key"] = cfg["UKG_USER_API_KEY"]


def pull(path, n=200):
    r = requests.get(f"{base}{path}", headers=headers,
                     params={"page": 1, "per_Page": n}, timeout=60)
    r.raise_for_status()
    d = r.json()
    return d if isinstance(d, list) else d.get("value") or d.get("data") or []


emp = pull(cfg["UKG_EMPLOYEE_READ_PATH"])
per = pull(cfg.get("UKG_PERSON_READ_PATH", "/personnel/v1/person-details"))
print(f"employment rows: {len(emp)} | person rows: {len(per)}\n")

# What id fields exist on each?
print("employment-details id fields:",
      [k for k in emp[0] if "id" in k.lower() or "number" in k.lower()])
print("person-details   id fields:",
      [k for k in per[0] if "id" in k.lower() or "number" in k.lower()])

emp_ids = {str(r.get("employeeId")) for r in emp}
per_ids = {str(r.get("employeeId")) for r in per}
overlap = emp_ids & per_ids
print(f"\nemployeeId overlap on this page: {len(overlap)} of {len(emp_ids)} "
      f"employment ids found in person set")

if overlap:
    sample = next(iter(overlap))
    print(f"\nSample matched employeeId: {sample}")
    pr = next(r for r in per if str(r.get("employeeId")) == sample)
    print(f"  person firstName={pr.get('firstName')!r} lastName={pr.get('lastName')!r} "
          f"email={pr.get('emailAddress')!r}")
else:
    print("\n*** NO OVERLAP - the two endpoints use different employeeId formats! ***")
    print("employment sample employeeId:", emp[0].get("employeeId"))
    print("person     sample employeeId:", per[0].get("employeeId"))
    print("employment sample employeeNumber:", emp[0].get("employeeNumber"))
    print("person     sample employeeNumber:", per[0].get("employeeNumber"))

# Now run the actual client to see what it produces
print("\n--- running the actual UkgClient.fetch_employees() ---")
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.ukg_client import UkgClient
people = UkgClient(dict(cfg)).fetch_employees()
named = sum(1 for p in people if p.first_name)
print(f"Client returned {len(people)} people; {named} have a first name.")
if people[:1]:
    p = people[0]
    print(f"  sample: first={p.first_name!r} last={p.last_name!r} "
          f"empno={p.employee_number!r} active={p.is_active}")
