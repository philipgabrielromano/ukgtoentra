"""
UKG Pro (UltiPro) REST client - READ ONLY.

This tool only reads HR data from UKG and writes it into Entra, so the UKG client
needs no write/update capability. UKG remains the untouched source of truth for HR data.

Because UKG endpoint paths and payloads differ across tenants/API versions, the
read path is configurable in .env. The mapping below covers the common
employee-details shape; adjust _to_person if your tenant returns different keys.
"""
from __future__ import annotations

import base64
import logging
import requests

from .http_util import RateLimiter, request_with_retry
from .models import Person

log = logging.getLogger("ukg")


class UkgClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.base = cfg["UKG_BASE_URL"].rstrip("/")
        self.session = requests.Session()
        self.limiter = RateLimiter(float(cfg.get("RATE_LIMIT_PER_SEC", 5)))
        self.max_retries = int(cfg.get("HTTP_MAX_RETRIES", 4))
        self.read_path = cfg["UKG_EMPLOYEE_READ_PATH"]            # employment/job data
        # Optional second endpoint holding names/email (person-details). When set, the two
        # are merged by employee key so we get name + job in one Person.
        self.person_path = cfg.get("UKG_PERSON_READ_PATH", "").strip()
        self.active_only = str(cfg.get("UKG_ACTIVE_ONLY", "true")).strip().lower() == "true"
        # Whether to look up human-readable descriptions for org-level codes and locations
        self._lookup_descriptions = str(cfg.get("UKG_LOOKUP_DESCRIPTIONS", "true")).strip().lower() == "true"

        user = cfg["UKG_USERNAME"]
        pw = cfg["UKG_PASSWORD"]
        token = base64.b64encode(f"{user}:{pw}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "US-Customer-Api-Key": cfg["UKG_CUSTOMER_API_KEY"],
            "Accept": "application/json",
        }
        if cfg.get("UKG_USER_API_KEY"):
            self._headers["US-User-Api-Key"] = cfg["UKG_USER_API_KEY"]

        # --- Description lookup tables (code → human-readable name) ---
        # Populated at startup so _to_person() can translate codes like "SOTREG" → "South Region"
        # and "CORPOH" → "Corporate Office" before they reach Entra.
        self._dept_lookup: dict[str, str] = {}       # orgLevel3Code → description
        self._location_lookup: dict[str, str] = {}   # locationCode → description
        if self._lookup_descriptions:
            self._load_lookup_tables()

    def _load_lookup_tables(self):
        """Fetch org-level and location configuration tables from UKG so we can
        translate raw codes (like SOTREG, CORPOH) into human-readable descriptions
        (like "South Region", "Corporate Office") before writing to Entra.

        Endpoints used:
          /configuration/v1/org-levels/3  → department / functional area descriptions
          /configuration/v1/locations     → physical work-location descriptions
        """
        # --- Department (orgLevel3) ---
        try:
            rows = self._fetch_config("/configuration/v1/org-levels/3")
            for r in rows:
                code = (r.get("code") or "").strip()
                desc = (r.get("description") or "").strip()
                if code and desc:
                    self._dept_lookup[code.upper()] = desc
            if self._dept_lookup:
                log.info("Loaded %d department descriptions from /configuration/v1/org-levels/3",
                         len(self._dept_lookup))
        except Exception as e:
            log.warning("Could not load org-level/3 descriptions (department codes will be "
                        "written as-is): %s", e)

        # --- Location ---
        try:
            rows = self._fetch_config("/configuration/v1/locations")
            for r in rows:
                code = (r.get("locationCode") or r.get("code") or "").strip()
                desc = (r.get("description") or "").strip()
                if code and desc:
                    self._location_lookup[code.upper()] = desc
            if self._location_lookup:
                log.info("Loaded %d location descriptions from /configuration/v1/locations",
                         len(self._location_lookup))
        except Exception as e:
            log.warning("Could not load location descriptions (location codes will be "
                        "written as-is): %s", e)

    def _fetch_config(self, path: str) -> list[dict]:
        """Fetch all rows from a UKG configuration endpoint (paged)."""
        rows_all: list[dict] = []
        page = 1
        per_page = 200
        while True:
            url = f"{self.base}{path}"
            resp = request_with_retry(
                self.session, "GET", url, headers=self._headers,
                params={"page": page, "per_Page": per_page},
                limiter=self.limiter, max_retries=self.max_retries)
            if resp.status_code == 404:
                log.debug("Config endpoint %s returned 404 — skipping.", path)
                break
            if resp.status_code != 200:
                break
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("value") or data.get("data") or []
            if not rows:
                break
            rows_all.extend(rows)
            if len(rows) < per_page:
                break
            page += 1
        return rows_all

    def _lookup_dept(self, code: str | None) -> str | None:
        """Translate an orgLevel3 code to its description. Falls back to the raw code."""
        if not code:
            return None
        desc = self._dept_lookup.get(code.strip().upper())
        return desc if desc else code

    def _lookup_location(self, code: str | None) -> str | None:
        """Translate a location code to its description. Falls back to the raw code."""
        if not code:
            return None
        desc = self._location_lookup.get(code.strip().upper())
        return desc if desc else code

    def _page_all(self, path: str) -> list[dict]:
        """Fetch all rows from a paged UKG endpoint as raw dicts."""
        rows_all, page, per_page = [], 1, 200
        while True:
            url = f"{self.base}{path}"
            resp = request_with_retry(
                self.session, "GET", url, headers=self._headers,
                params={"page": page, "per_Page": per_page},
                limiter=self.limiter, max_retries=self.max_retries)
            if resp.status_code == 404:
                log.error("UKG path returned 404 (%s). Check the configured path.", url)
            resp.raise_for_status()
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("value") or data.get("data") or []
            if not rows:
                break
            rows_all.extend(rows)
            if len(rows) < per_page:
                break
            page += 1
        return rows_all

    @staticmethod
    def _key(r: dict):
        """Join key shared by person-details & employment-details endpoints.

        IMPORTANT: the two endpoints spell the SAME field with different casing:
          employment-details -> 'employeeID'  (capital D)
          person-details     -> 'employeeId'  (lowercase d)
        The VALUES are identical (e.g. AQ6FRL0000K0). So we match the alphanumeric
        employee id case-insensitively across both spellings. (employeeNumber is NOT
        a usable join key - person-details returns it as null.)"""
        for k, v in r.items():
            if k.lower() == "employeeid" and v not in (None, ""):
                return str(v).strip().upper()
        # fallback (shouldn't be needed): personId
        pid = r.get("personId") or r.get("PersonId")
        return str(pid).strip().upper() if pid else ""

    def fetch_employees(self) -> list[Person]:
        emp_rows = self._page_all(self.read_path)

        # Merge in personal details (names/email) if a second endpoint is configured.
        if self.person_path:
            person_rows = self._page_all(self.person_path)
            person_by_key = {self._key(r): r for r in person_rows}
            log.info("Fetched %d employment rows and %d person rows from UKG",
                     len(emp_rows), len(person_rows))
            merged, unmatched = [], 0
            for er in emp_rows:
                pr = person_by_key.get(self._key(er))
                if pr is None:
                    unmatched += 1
                    merged.append(er)
                    continue
                # Start from employment row, then overlay person fields so names/email
                # from person-details always win (employment-details has no name fields).
                merged.append({**er, **pr})
            if unmatched:
                log.warning("%d employment rows had no matching person record (by employeeId)",
                            unmatched)
            emp_rows = merged
        else:
            log.info("Fetched %d rows from UKG (%s). NOTE: no UKG_PERSON_READ_PATH set - "
                     "if this endpoint lacks names, set it to the person-details endpoint.",
                     len(emp_rows), self.read_path)

        people = [self._to_person(r) for r in emp_rows]

        # De-duplicate: collapse multiple rows per employee into one (keep active/most recent).
        people = self._dedupe(people)

        total = len(people)
        if self.active_only:
            active = [p for p in people if p.is_active]
            log.info("After merge/dedupe: %d employees; %d active, %d inactive "
                     "(filtered out). Set UKG_ACTIVE_ONLY=false to include all.",
                     total, len(active), total - len(active))
            return active
        log.info("After merge/dedupe: %d employees (active filter OFF)", total)
        return people

    @staticmethod
    def _dedupe(people: list[Person]) -> list[Person]:
        """UKG often returns one row per job/position. Collapse to one row per
        employee key, preferring an active row, then the most recent job."""
        from collections import defaultdict
        groups: dict[str, list[Person]] = defaultdict(list)
        for p in people:
            key = (p.employee_number or p.source_id or "").strip()
            groups[key].append(p)

        def recency(p: Person):
            return (p.raw or {}).get("dateInJob") or (p.raw or {}).get("statusStartDate") or ""

        result = []
        for key, grp in groups.items():
            if len(grp) == 1:
                result.append(grp[0]); continue
            actives = [p for p in grp if p.is_active]
            pool = actives if actives else grp
            pool.sort(key=recency, reverse=True)
            result.append(pool[0])
        if len(result) != len(people):
            log.info("Dedupe collapsed %d rows -> %d unique employees",
                     len(people), len(result))
        return result

    @staticmethod
    def _first(d: dict, *keys):
        # exact match first (fast path)
        for k in keys:
            if k in d and d[k] not in (None, ""):
                return d[k]
        # case-insensitive fallback - UKG mixes casing across endpoints
        # (e.g. employeeID vs employeeId), so don't let casing cause misses.
        lower_map = {k.lower(): k for k in d}
        for k in keys:
            actual = lower_map.get(k.lower())
            if actual is not None and d[actual] not in (None, ""):
                return d[actual]
        return None

    def _to_person(self, r: dict) -> Person:
        # Supervisor/manager name may come as separate first/last fields.
        sup_first = self._first(r, "supervisorFirstName", "SupervisorFirstName")
        sup_last = self._first(r, "supervisorLastName", "SupervisorLastName")
        manager = self._first(r, "supervisorName", "managerName", "SupervisorName")
        if not manager and (sup_first or sup_last):
            manager = f"{sup_first or ''} {sup_last or ''}".strip()

        return Person(
            source="ukg",
            source_id=str(self._first(r, "employeeId", "EmployeeId", "employeeID",
                                      "employeeNumber", "EmployeeNumber") or ""),
            first_name=self._first(r, "firstName", "FirstName", "givenName",
                                   "preferredFirstName", "legalFirstName"),
            last_name=self._first(r, "lastName", "LastName", "surname", "legalLastName"),
            preferred_name=self._first(r, "preferredName", "PreferredName",
                                       "nickName", "knownAs"),
            email=self._first(r, "emailAddress", "EmailAddress", "workEmail",
                              "emailAddressWork", "email", "internetAddress"),
            employee_number=self._first(r, "employeeNumber", "EmployeeNumber"),
            hire_date=self._first(r, "originalHireDate", "lastHireDate", "hireDate",
                                 "HireDate", "startDate"),
            department=self._lookup_dept(
                self._first(r, "orgLevel3Code", "department", "Department",
                            "orgLevel3Description")),
            job_title=self._first(r, "jobDescription", "jobTitle", "JobTitle",
                                  "jobCodeDescription", "primaryJobCode"),
            manager=manager,
            manager_employee_number=self._first(r, "supervisorEmployeeNumber",
                                               "managerEmployeeNumber", "SupervisorEmployeeNumber"),
            location=self._lookup_location(
                self._first(r, "primaryWorkLocationCode", "location", "Location",
                            "workLocation", "locationGLSegment")),
            is_active=self._is_active(r),
            termination_date=self._first(r, "dateOfTermination", "terminationDate",
                                        "TerminationDate", "dateLastWorked"),
            raw=r,
        )

    @staticmethod
    def _is_active(r: dict) -> bool:
        """Best-effort active detection across UKG field variations."""
        # Explicit boolean flags
        for k in ("isActive", "IsActive", "active", "Active"):
            if k in r and r[k] is not None:
                return bool(r[k]) if isinstance(r[k], bool) else \
                    str(r[k]).strip().lower() in ("true", "1", "yes", "a", "active")
        # Status text/code fields (UKG uses single-letter codes: A=Active, T=Terminated,
        # L=Leave, D=Deceased, P/S=Suspended, etc.)
        status = None
        for k in ("employeeStatusCode", "EmployeeStatusCode", "employmentStatus",
                  "EmploymentStatus", "employeeStatus", "EmployeeStatus",
                  "statusCode", "StatusCode", "status", "Status"):
            if k in r and r[k] not in (None, ""):
                status = str(r[k]).strip().lower()
                break
        if status is not None:
            # exact single-letter UKG status codes
            active_codes = {"a", "active", "l", "p", "leaveofabsence-paid"}  # treat paid leave as active
            terminated_codes = {"t", "terminated", "term", "i", "inactive", "d",
                                "deceased", "r", "retired", "s", "separated"}
            if status in terminated_codes or status.startswith(("term", "inactiv", "separat")):
                return False
            if status in active_codes or status.startswith("activ"):
                return True
        # Fallback: a termination date in the past implies terminated
        term = UkgClient._first(r, "dateOfTermination", "terminationDate",
                                "TerminationDate", "dateLastWorked", "lastDayWorked",
                                "separationDate")
        if term:
            return False
        return True  # default to active if nothing indicates otherwise
