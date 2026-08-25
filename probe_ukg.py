"""
Probe common UKG Pro endpoints to discover which one your tenant exposes.
Reads credentials from config/.env. Makes READ-ONLY GET requests.

    python3 probe_ukg.py
"""
import base64
from pathlib import Path

import requests
from dotenv import dotenv_values

cfg = dotenv_values(Path(__file__).resolve().parent / "config" / ".env")
base = cfg["UKG_BASE_URL"].rstrip("/")

token = base64.b64encode(f"{cfg['UKG_USERNAME']}:{cfg['UKG_PASSWORD']}".encode()).decode()
headers = {
    "Authorization": f"Basic {token}",
    "US-Customer-Api-Key": cfg["UKG_CUSTOMER_API_KEY"],
    "Accept": "application/json",
}
if cfg.get("UKG_USER_API_KEY"):
    headers["US-User-Api-Key"] = cfg["UKG_USER_API_KEY"]

# Common employee-read endpoints across UKG Pro API versions/packages.
CANDIDATES = [
    "/personnel/v1/employee-details",
    "/personnel/v1/employees",
    "/personnel/v1/employment-details",
    "/services/v2/employee",
    "/services/v1/employee",
    "/configuration/v1/employees",
    "/employee/v1/employee-job-history",
    "/personnel/v1/person-details",
]

print(f"Base URL: {base}\n")
print(f"{'STATUS':<8}{'PATH'}")
print("-" * 60)

winners = []
for path in CANDIDATES:
    url = f"{base}{path}"
    try:
        r = requests.get(url, headers=headers, params={"page": 1, "per_Page": 1}, timeout=30)
        code = r.status_code
    except requests.RequestException as e:
        print(f"{'ERR':<8}{path}  ({e})")
        continue
    flag = ""
    if code == 200:
        flag = "  <-- WORKS"
        winners.append(path)
    elif code in (401, 403):
        flag = "  (auth/permission issue, but path exists)"
    elif code == 404:
        flag = "  (not found)"
    print(f"{code:<8}{path}{flag}")

print("-" * 60)
if winners:
    print(f"\nUse this in config/.env:\n  UKG_EMPLOYEE_READ_PATH={winners[0]}")
    # show the shape of the first row so we can confirm field mapping
    r = requests.get(f"{base}{winners[0]}", headers=headers,
                     params={"page": 1, "per_Page": 1}, timeout=30)
    try:
        data = r.json()
        rows = data if isinstance(data, list) else data.get("value") or data.get("data") or []
        if rows:
            print("\nSample record keys (to confirm field mapping):")
            for k in sorted(rows[0].keys()):
                print(f"  {k}")
    except Exception:
        print("\n(Could not parse sample JSON — paste the response and I'll map it.)")
else:
    print("\nNo 200 found. If you see 401/403 anywhere, it's an auth/scope issue, "
          "not a path issue. If all 404, check the base URL and your API package "
          "in UKG, or paste your UKG API/Postman docs and I'll pin the exact path.")
