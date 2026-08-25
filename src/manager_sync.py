"""
Manager synchronization (UKG Pro -> Entra ID).

In Entra, a user's manager is a *relationship* to another user object, set via:
    PUT /users/{id}/manager/$ref   body: {"@odata.id": ".../users/{managerId}"}

UKG typically gives us a manager *name* (and sometimes a manager employee number).
So we must resolve that to an Entra user object before we can link it.

Resolution order (most -> least reliable):
  1. UKG manager employee number  -> Entra employeeId
  2. UKG manager name             -> the matched Entra person for that UKG employee
                                     (i.e. find the manager in our own match table)
  3. UKG manager name             -> exact/fuzzy name lookup in Entra directory

Anything that can't be confidently resolved is reported and skipped (never guessed).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from rapidfuzz import fuzz

from .models import Person, MatchResult
from .normalize import normalize_name, name_key

log = logging.getLogger("manager")


class ManagerResolver:
    def __init__(self, entra_people: list[Person], match_results: list[MatchResult]):
        self.entra = entra_people
        self.by_empno: dict[str, Person] = {}
        self.by_namekey: dict[str, list[Person]] = defaultdict(list)
        for p in entra_people:
            if p.employee_number:
                self.by_empno[str(p.employee_number).strip().lower()] = p
            self.by_namekey[name_key(p.first_name, p.last_name)].append(p)

        # Map a UKG manager *name* to the Entra user it was matched to, using our
        # own confident match table. Keyed by normalized "first last".
        self.ukg_name_to_entra: dict[str, Person] = {}
        for r in match_results:
            if r.entra and r.decision in ("AUTO_APPLY", "SKIP"):
                full = f"{normalize_name(r.ukg.first_name)} {normalize_name(r.ukg.last_name)}".strip()
                self.ukg_name_to_entra[full] = r.entra

    def resolve(self, ukg_person: Person) -> tuple[Optional[Person], str]:
        """Return (entra_manager, reason). entra_manager is None if unresolved."""
        mgr_name = ukg_person.manager
        mgr_empno = getattr(ukg_person, "manager_employee_number", None) or \
            (ukg_person.raw or {}).get("supervisorEmployeeNumber")

        # 1) manager employee number -> Entra employeeId
        if mgr_empno:
            hit = self.by_empno.get(str(mgr_empno).strip().lower())
            if hit:
                return hit, f"manager resolved by employee number {mgr_empno}"

        if not mgr_name:
            return None, "no manager value in UKG"

        norm = normalize_name(mgr_name)

        # 2) via our own match table (UKG manager name -> matched Entra user)
        if norm in self.ukg_name_to_entra:
            return self.ukg_name_to_entra[norm], "manager resolved via match table"

        # UKG manager names are often "Last, First" - try swapped order too
        parts = [p for p in norm.replace(",", " ").split() if p]
        candidates_keys = []
        if len(parts) >= 2:
            candidates_keys.append(f"{parts[0]}|{parts[-1]}")
            candidates_keys.append(f"{parts[-1]}|{parts[0]}")

        # 3) direct name lookup in Entra
        for key in candidates_keys:
            hits = self.by_namekey.get(key, [])
            if len(hits) == 1:
                return hits[0], f"manager resolved by exact name ({key})"
            if len(hits) > 1:
                return None, f"manager name ambiguous ({len(hits)} Entra users named like '{mgr_name}')"

        # 4) fuzzy last-resort across Entra display names
        best, best_score = None, 0
        for p in self.entra:
            full = f"{normalize_name(p.first_name)} {normalize_name(p.last_name)}"
            score = max(fuzz.token_sort_ratio(norm, full),
                        fuzz.token_sort_ratio(norm, full.split()[-1] + " " + full.split()[0]
                                              if len(full.split()) >= 2 else full))
            if score > best_score:
                best, best_score = p, score
        if best and best_score >= 92:
            return best, f"manager resolved by fuzzy name (score {best_score})"

        return None, f"manager '{mgr_name}' could not be resolved in Entra"
