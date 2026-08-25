"""
UKG Pro -> Entra ID HR-data sync orchestrator.

For every confidently matched (UKG, Entra) pair, compute the attribute changes
(jobTitle, department, officeLocation, ...) and the manager relationship change,
then optionally write them to Entra via Graph.

Only AUTO_APPLY / SKIP matches are used as a basis for HR writes (i.e. we trust
the identity match). REVIEW / NO_MATCH records are reported but not written.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .models import MatchResult, Person
from .field_map import MappedField, diff_attributes
from .manager_sync import ManagerResolver

log = logging.getLogger("hrsync")


@dataclass
class HrChange:
    entra_id: str
    name: str
    attribute_changes: dict = field(default_factory=dict)      # graph_attr -> new value
    manager_change: Optional[dict] = None                       # {new_id, name, reason}
    manager_unresolved: Optional[str] = None                    # reason if couldn't resolve
    on_prem_synced: bool = False                                # mastered by on-prem AD
    entra_upn: Optional[str] = None                            # UPN for finding user in AD
    manager_employee_number: Optional[str] = None              # manager empno for AD DN resolution
    manager_upn: Optional[str] = None                          # manager UPN for AD DN resolution
    status: str = "PLANNED"
    detail: str = ""

    def has_work(self) -> bool:
        return bool(self.attribute_changes) or bool(self.manager_change)

    def as_row(self) -> dict:
        row = {
            "entra_id": self.entra_id,
            "name": self.name,
            "entra_upn": self.entra_upn,
            "on_prem_synced": self.on_prem_synced,
            "status": self.status,
            "detail": self.detail,
            "manager_new": (self.manager_change or {}).get("name"),
            "manager_upn": self.manager_upn,
            "manager_employee_number": self.manager_employee_number,
            "manager_reason": (self.manager_change or {}).get("reason")
                              or self.manager_unresolved or "",
        }
        for k, v in self.attribute_changes.items():
            row[f"set_{k}"] = v
        return row


class HrSync:
    def __init__(self, entra_client, fields: list[MappedField],
                 entra_people: list[Person], match_results: list[MatchResult],
                 sync_manager: bool = True):
        self.entra = entra_client
        self.fields = fields
        self.sync_manager = sync_manager
        self.resolver = ManagerResolver(entra_people, match_results)
        self.entra_by_id = {p.source_id: p for p in entra_people}

    def build_plan(self, match_results: list[MatchResult]) -> list[HrChange]:
        plan: list[HrChange] = []
        for r in match_results:
            if r.decision not in ("AUTO_APPLY", "SKIP") or not r.entra:
                continue
            entra = self.entra_by_id.get(r.entra.source_id, r.entra)
            change = HrChange(
                entra_id=entra.source_id,
                name=f"{r.ukg.first_name} {r.ukg.last_name}",
                on_prem_synced=entra.on_prem_synced,
                entra_upn=entra.email,
                manager_employee_number=r.ukg.manager_employee_number,
            )
            change.attribute_changes = diff_attributes(r.ukg, entra, self.fields)

            if self.sync_manager and r.ukg.manager:
                mgr, reason = self.resolver.resolve(r.ukg)
                if mgr and mgr.source_id != entra.source_id:
                    change.manager_change = {
                        "new_id": mgr.source_id, "name": mgr.display, "reason": reason}
                    change.manager_upn = mgr.email  # UPN for AD PowerShell resolution
                elif not mgr:
                    change.manager_unresolved = reason

            if change.has_work() or change.manager_unresolved:
                plan.append(change)
        return plan

    def apply_plan(self, plan: list[HrChange], do_write: bool,
                   limit: int = 0, backup: list | None = None) -> list[HrChange]:
        """Apply the plan to Entra.

        do_write : if False, dry-run (status=DRY_RUN), nothing is sent.
        limit    : if >0, only WRITE at most this many users (rest -> SKIPPED_LIMIT).
                   Use for a small pilot before the full run.
        backup   : if a list is passed, the pre-change ("before") state of every
                   user actually written is appended for rollback.
        """
        written = 0
        for c in plan:
            # NEVER write to Entra for on-prem (AD) mastered users: the write would be
            # rejected or overwritten by Entra Connect at the next sync (~30 min).
            # These must be changed in on-prem AD instead. Report them separately.
            if c.on_prem_synced:
                c.status = "SKIPPED_ON_PREM"
                c.detail = ("User is mastered by on-prem AD (onPremisesSyncEnabled=true). "
                            "Apply these changes in Active Directory; they will flow up "
                            "via Entra Connect. See on_prem_changes report.")
                continue
            if not do_write:
                c.status = "DRY_RUN"
                continue
            if limit and written >= limit:
                c.status = "SKIPPED_LIMIT"
                c.detail = f"--limit {limit} reached; not written this run"
                continue

            # --- capture BEFORE state for rollback ---
            entra = self.entra_by_id.get(c.entra_id)
            if backup is not None:
                before = {"entra_id": c.entra_id, "name": c.name, "before": {}}
                cur_raw = (entra.raw if entra else {}) or {}
                for attr in c.attribute_changes:
                    before["before"][attr] = cur_raw.get(attr)  # may be None (was blank)
                if c.manager_change:
                    before["before"]["managerId"] = self.entra.get_current_manager_id(c.entra_id)
                backup.append(before)

            ok_all, details = True, []
            if c.attribute_changes:
                ok, d = self.entra.update_user_attributes(c.entra_id, c.attribute_changes)
                ok_all &= ok
                details.append(f"attrs[{d}]")

            if c.manager_change:
                cur = self.entra.get_current_manager_id(c.entra_id)
                if cur == c.manager_change["new_id"]:
                    details.append("manager[unchanged]")
                else:
                    ok, d = self.entra.set_manager(c.entra_id, c.manager_change["new_id"])
                    ok_all &= ok
                    details.append(f"manager[{d}]")

            c.status = "WRITTEN" if ok_all else "FAILED"
            c.detail = " ".join(details)
            if ok_all:
                written += 1
            log.info("[%s] %s %s", c.status, c.name, c.detail)
        return plan
