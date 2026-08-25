"""
Microsoft Entra ID client using Microsoft Graph (client-credentials flow).

Reads enabled users and maps Graph fields -> Person.
  givenName        -> first_name
  surname          -> last_name
  userPrincipalName-> email (this is what we write to UKG)
  employeeId       -> employee_number   (strong key if populated in Entra)
  employeeHireDate -> hire_date
  department / jobTitle / officeLocation -> secondary signals
"""
from __future__ import annotations

import logging
from typing import Optional

import requests
import msal

from .http_util import RateLimiter, request_with_retry
from .models import Person

log = logging.getLogger("entra")

SELECT = ",".join([
    "id", "givenName", "surname", "displayName", "userPrincipalName", "mail",
    "accountEnabled", "employeeId", "employeeHireDate", "department",
    "jobTitle", "officeLocation", "city", "state", "country", "companyName",
    "onPremisesSyncEnabled",
])


class EntraClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.base = cfg["GRAPH_BASE_URL"].rstrip("/")
        self.session = requests.Session()
        self.limiter = RateLimiter(float(cfg.get("RATE_LIMIT_PER_SEC", 5)))
        self.max_retries = int(cfg.get("HTTP_MAX_RETRIES", 4))
        self._token = None
        authority = f"https://login.microsoftonline.com/{cfg['ENTRA_TENANT_ID']}"
        self._app = msal.ConfidentialClientApplication(
            client_id=cfg["ENTRA_CLIENT_ID"],
            client_credential=cfg["ENTRA_CLIENT_SECRET"],
            authority=authority,
        )

    def _auth_header(self) -> dict:
        result = self._app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            raise RuntimeError(f"Entra auth failed: {result.get('error_description', result)}")
        return {"Authorization": f"Bearer {result['access_token']}"}

    def fetch_users(self) -> list[Person]:
        headers = self._auth_header()
        only_enabled = str(self.cfg.get("ENTRA_ONLY_ENABLED", "true")).lower() == "true"
        group_id = self.cfg.get("ENTRA_FILTER_GROUP_ID") or ""

        if group_id:
            url = f"{self.base}/groups/{group_id}/members/microsoft.graph.user?$select={SELECT}&$top=999"
        else:
            url = f"{self.base}/users?$select={SELECT}&$top=999"
            if only_enabled:
                url += "&$filter=accountEnabled eq true"

        people: list[Person] = []
        while url:
            resp = request_with_retry(
                self.session, "GET", url, headers=headers,
                limiter=self.limiter, max_retries=self.max_retries)
            resp.raise_for_status()
            data = resp.json()
            for u in data.get("value", []):
                if only_enabled and u.get("accountEnabled") is False:
                    continue
                people.append(self._to_person(u))
            url = data.get("@odata.nextLink")
        log.info("Fetched %d users from Entra ID", len(people))
        return people

    # ---------- write (UKG -> Entra HR data) ----------
    def update_user_attributes(self, entra_id: str, changes: dict) -> tuple[bool, str]:
        """PATCH /users/{id} with the changed attributes. Requires User.ReadWrite.All."""
        if not changes:
            return True, "no changes"
        url = f"{self.base}/users/{entra_id}"
        resp = request_with_retry(
            self.session, "PATCH", url, headers={**self._auth_header(),
                                                 "Content-Type": "application/json"},
            json=changes, limiter=self.limiter, max_retries=self.max_retries)
        if 200 <= resp.status_code < 300:
            return True, str(resp.status_code)
        return False, f"{resp.status_code}: {resp.text[:300]}"

    def get_current_manager_id(self, entra_id: str) -> Optional[str]:
        url = f"{self.base}/users/{entra_id}/manager?$select=id"
        resp = request_with_retry(
            self.session, "GET", url, headers=self._auth_header(),
            limiter=self.limiter, max_retries=self.max_retries)
        if resp.status_code == 404:
            return None
        if 200 <= resp.status_code < 300:
            return resp.json().get("id")
        return None

    def set_manager(self, entra_id: str, manager_id: str) -> tuple[bool, str]:
        """PUT /users/{id}/manager/$ref to establish the manager relationship."""
        url = f"{self.base}/users/{entra_id}/manager/$ref"
        body = {"@odata.id": f"{self.base}/users/{manager_id}"}
        resp = request_with_retry(
            self.session, "PUT", url, headers={**self._auth_header(),
                                               "Content-Type": "application/json"},
            json=body, limiter=self.limiter, max_retries=self.max_retries)
        if 200 <= resp.status_code < 300:
            return True, str(resp.status_code)
        return False, f"{resp.status_code}: {resp.text[:300]}"

    @staticmethod
    def _to_person(u: dict) -> Person:
        return Person(
            source="entra",
            source_id=u.get("id", ""),
            first_name=u.get("givenName"),
            last_name=u.get("surname"),
            email=u.get("userPrincipalName") or u.get("mail"),
            employee_number=u.get("employeeId"),
            hire_date=u.get("employeeHireDate"),
            department=u.get("department"),
            job_title=u.get("jobTitle"),
            location=u.get("officeLocation"),
            enabled=bool(u.get("accountEnabled", True)),
            on_prem_synced=bool(u.get("onPremisesSyncEnabled") or False),
            raw=u,
        )
