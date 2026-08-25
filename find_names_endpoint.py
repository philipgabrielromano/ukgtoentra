"""
Find which UKG endpoint contains the person's NAME and EMAIL.
The employment-details endpoint has job data but no names; names live elsewhere.
Read-only. Prints field names for each endpoint that responds 200.

    python3 find_names_endpoint.py
"""
import base64
import json
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

CANDIDATES = [
    "/personnel/v1/person-details",
    "/personnel/v1/employee-details",
    "/personnel/v1/employees",
    "/personnel/v1/person",
    "/services/v2/employee",
    "/employee/v1/employee-job-history",
    "/personnel/v1/employment-information",
]

NAME_HINTS = ("first", "last", "name", "email", "preferred")

for path in CANDIDATES:
    try:
        r = requests.get(f"{base}{path}", headers=headers,
                         params={"page": 1, "per_Page": 3}, timeout=30)
    except requests.RequestException as e:
        print(f"{path}: ERROR {e}"); continue
    if r.status_code != 200:
        print(f"{path}: HTTP {r.status_code}"); continue
    data = r.json()
    rows = data if isinstance(data, list) else data.get("value") or data.get("data") or []
    if not rows:
        print(f"{path}: 200 but empty"); continue
    keys = list(rows[0].keys())
    name_fields = [k for k in keys if any(h in k.lower() for h in NAME_HINTS)]
    print(f"\n{'='*64}\n{path}  (HTTP 200, {len(keys)} fields)\n{'='*64}")
    print(f"  NAME/EMAIL fields: {name_fields or '(none found)'}")
    # show sample values for those fields
    for k in name_fields:
        v = rows[0].get(k)
        print(f"    {k:<28} = {v}")
    # show id fields for joining to employment-details
    id_fields = [k for k in keys if k.lower() in
                 ("employeeid", "employeenumber", "personid", "id")]
    print(f"  ID fields (for joining): {id_fields}")
