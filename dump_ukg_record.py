"""
Dump a few RAW UKG records so we can see the exact field names your tenant returns.
Also shows the description lookup tables (org-levels, locations) so you can confirm
codes are being translated correctly.
Read-only. Helps us fix status detection and de-duplication.

    python3 dump_ukg_record.py
"""
import base64
import json
from collections import Counter
from pathlib import Path

import requests
from dotenv import dotenv_values

cfg = dotenv_values(Path(__file__).resolve().parent / "config" / ".env")
base = cfg["UKG_BASE_URL"].rstrip("/")
path = cfg["UKG_EMPLOYEE_READ_PATH"]

token = base64.b64encode(f"{cfg['UKG_USERNAME']}:{cfg['UKG_PASSWORD']}".encode()).decode()
headers = {"Authorization": f"Basic {token}",
           "US-Customer-Api-Key": cfg["UKG_CUSTOMER_API_KEY"],
           "Accept": "application/json"}
if cfg.get("UKG_USER_API_KEY"):
    headers["US-User-Api-Key"] = cfg["UKG_USER_API_KEY"]

r = requests.get(f"{base}{path}", headers=headers,
                 params={"page": 1, "per_Page": 50}, timeout=60)
r.raise_for_status()
data = r.json()
rows = data if isinstance(data, list) else data.get("value") or data.get("data") or []
print(f"Pulled {len(rows)} sample rows from {path}\n")

if not rows:
    print("No rows returned."); raise SystemExit

# 1) Full field list with example values from the first record
print("=" * 64)
print("FIELD NAMES + SAMPLE VALUES (first record)")
print("=" * 64)
for k in sorted(rows[0].keys()):
    v = rows[0][k]
    if isinstance(v, (dict, list)):
        v = json.dumps(v)[:60] + "..."
    print(f"  {k:<32} = {v}")

# 2) Any field that looks like a status, so we can fix active-detection
print("\n" + "=" * 64)
print("CANDIDATE STATUS FIELDS (distinct values across sample)")
print("=" * 64)
for k in rows[0].keys():
    kl = k.lower()
    if any(t in kl for t in ("status", "active", "term", "separat", "employ")):
        vals = Counter(str(row.get(k)) for row in rows)
        print(f"  {k}: {dict(list(vals.items())[:8])}")

# 3) Detect one-row-per-job duplication: same employee number repeated?
print("\n" + "=" * 64)
print("DUPLICATION CHECK (rows per employee number)")
print("=" * 64)
empno_field = None
for k in rows[0].keys():
    if k.lower() in ("employeenumber", "employeeid"):
        empno_field = k; break
if empno_field:
    counts = Counter(str(row.get(empno_field)) for row in rows)
    dupes = {k: v for k, v in counts.items() if v > 1}
    print(f"  Employee-number field: {empno_field}")
    print(f"  Distinct employees in sample: {len(counts)} of {len(rows)} rows")
    print(f"  Employees with multiple rows: {len(dupes)} (e.g. "
          f"{dict(list(dupes.items())[:3])})")
else:
    print("  No obvious employee-number field found - see field list above.")

# 4) Description lookup tables (org-levels + locations)
print("\n" + "=" * 64)
print("DESCRIPTION LOOKUP TABLES")
print("=" * 64)
config_endpoints = [
    ("/configuration/v1/org-levels/1", "Org Level 1 (orgLevel1)", "code", "description"),
    ("/configuration/v1/org-levels/2", "Org Level 2 (orgLevel2)", "code", "description"),
    ("/configuration/v1/org-levels/3", "Org Level 3 (orgLevel3)", "code", "description"),
    ("/configuration/v1/org-levels/4", "Org Level 4 (orgLevel4)", "code", "description"),
    ("/configuration/v1/locations", "Location", "locationCode", "description"),
]
for ep, label, code_field, desc_field in config_endpoints:
    try:
        resp = requests.get(f"{base}{ep}", headers=headers,
                            params={"page": 1, "per_Page": 100}, timeout=30)
        if resp.status_code != 200:
            print(f"\n  {label} ({ep}): HTTP {resp.status_code}")
            continue
        cdata = resp.json()
        crows = cdata if isinstance(cdata, list) else cdata.get("value") or cdata.get("data") or []
        print(f"\n  {label} ({ep}): {len(crows)} entries (showing first 10)")
        for cr in crows[:100]:
            c = cr.get(code_field) or cr.get("code") or "?"
            d = cr.get(desc_field) or "?"
            print(f"    {c:<12} → {d}")
    except Exception as e:
        print(f"\n  {label} ({ep}): ERROR {e}")
