"""Shared data models for both sides of the sync."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Person:
    """A normalized employee record from either Entra ID or UKG Pro."""
    source: str                      # "entra" | "ukg"
    source_id: str                   # graph object id  OR  ukg internal id
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    preferred_name: Optional[str] = None   # UKG preferredName / Entra goes-by name
    email: Optional[str] = None      # UPN (entra) or current work email (ukg)
    employee_number: Optional[str] = None
    hire_date: Optional[str] = None
    department: Optional[str] = None
    job_title: Optional[str] = None
    manager: Optional[str] = None
    manager_employee_number: Optional[str] = None
    location: Optional[str] = None
    enabled: bool = True
    # UKG employment status: True if the employee is active (not terminated).
    is_active: bool = True
    termination_date: Optional[str] = None
    # True if this Entra user is mastered by on-prem AD (Entra Connect syncs AD->Entra).
    # Writing HR attributes to Entra for these users will be rejected or overwritten,
    # so the tool skips them and reports them for handling in on-prem AD instead.
    on_prem_synced: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def display(self) -> str:
        return f"{self.first_name or '?'} {self.last_name or '?'} <{self.email or 'no-email'}>"


@dataclass
class MatchResult:
    """The outcome of attempting to match one UKG person to Entra."""
    ukg: Person
    entra: Optional[Person]
    decision: str                    # AUTO_APPLY | REVIEW | SKIP | NO_MATCH
    confidence: float                # 0-100
    tier: str                        # human-readable rule that fired
    reasons: list[str] = field(default_factory=list)
    secondary_agreements: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)  # for review export
    target_email: Optional[str] = None

    def as_row(self) -> dict:
        return {
            "decision": self.decision,
            "confidence": round(self.confidence, 1),
            "tier": self.tier,
            "ukg_id": self.ukg.source_id,
            "ukg_employee_number": self.ukg.employee_number,
            "ukg_first": self.ukg.first_name,
            "ukg_last": self.ukg.last_name,
            "ukg_current_email": self.ukg.email,
            "entra_id": self.entra.source_id if self.entra else None,
            "entra_first": self.entra.first_name if self.entra else None,
            "entra_last": self.entra.last_name if self.entra else None,
            "target_email": self.target_email,
            "secondary_agreements": ", ".join(self.secondary_agreements),
            "reasons": " | ".join(self.reasons),
            "candidate_count": len(self.candidates),
        }
