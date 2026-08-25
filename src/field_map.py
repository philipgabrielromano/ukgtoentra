"""
Field mapping for the UKG Pro -> Entra ID (HR data) sync direction.

Each MappedField describes how to take a value from the matched UKG Person and
write it into a Microsoft Graph user attribute. Manager is handled separately
(see manager_sync.py) because in Entra it is a *relationship*, not a string.

The default mappings below are the common case; override any of them via
config (UKG_TO_ENTRA_MAP in .env, JSON) without touching code.

Graph user attributes commonly targeted:
    jobTitle               <- UKG job title / job description
    department             <- UKG department / org level
    officeLocation         <- UKG work location
    city / state / country <- UKG location components (optional)
    employeeId             <- UKG employee number (optional, useful as strong key)
    companyName            <- UKG legal entity / company (optional)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional

from .models import Person


@dataclass
class MappedField:
    graph_attr: str                 # Microsoft Graph attribute name to write
    person_attr: str                # attribute on the UKG Person object
    label: str                      # human label for reports
    transform: Optional[Callable[[object], object]] = None

    def value_from(self, ukg: Person):
        raw = getattr(ukg, self.person_attr, None)
        if raw in (None, ""):
            return None
        return self.transform(raw) if self.transform else raw


# Default UKG-attribute -> Graph-attribute mappings the user asked for:
#   Job Description -> jobTitle
#   Department      -> department
#   Location        -> officeLocation
#   (Manager handled separately as a relationship)
DEFAULT_MAP = [
    MappedField("jobTitle", "job_title", "Job Title / Description"),
    MappedField("department", "department", "Department"),
    MappedField("officeLocation", "location", "Location / Office"),
]


def load_field_map(config: dict) -> list[MappedField]:
    """Load mappings from config JSON if provided, else defaults.

    config["UKG_TO_ENTRA_MAP"] example (JSON string):
      {"jobTitle":"job_title","department":"department",
       "officeLocation":"location","employeeId":"employee_number"}
    """
    raw = config.get("UKG_TO_ENTRA_MAP")
    if not raw:
        return list(DEFAULT_MAP)
    mapping = json.loads(raw) if isinstance(raw, str) else raw
    labels = {
        "jobTitle": "Job Title / Description",
        "department": "Department",
        "officeLocation": "Location / Office",
        "city": "City", "state": "State", "country": "Country",
        "employeeId": "Employee Number", "companyName": "Company",
    }
    return [MappedField(g, p, labels.get(g, g)) for g, p in mapping.items()]


def diff_attributes(ukg: Person, entra: Person, fields: list[MappedField]) -> dict:
    """Return {graph_attr: new_value} only for attributes whose value differs
    from what Entra currently has. Skips empty source values so we never blank
    out an existing Entra value with missing UKG data."""
    changes = {}
    current = entra.raw or {}
    for f in fields:
        new_val = f.value_from(ukg)
        if new_val is None:
            continue
        cur_val = current.get(f.graph_attr)
        if (cur_val or "") != (new_val or ""):
            changes[f.graph_attr] = new_val
    return changes
