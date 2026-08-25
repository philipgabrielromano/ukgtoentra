"""
The matching engine.

Given the full set of Entra people and UKG people, produce a MatchResult per UKG person.
The engine is deliberately conservative: it auto-applies only high-confidence matches and
routes everything else to a human-review queue.

NOTE: Entra has NO employee number in this deployment, so matching is name-based
with secondary HR signals (department, job title, hire date, location, manager) used
to disambiguate and to gate auto-apply. A strong-key tier is still attempted in case
employee_number ever becomes available on both sides (it simply won't fire today).

Tiers (in priority order):
  T1  STRONG_KEY        - same employee_number on BOTH sides (inactive unless Entra gets IDs)
  T2  EXACT_NAME+SIG    - exact normalized first+last AND >=1 secondary signal agrees
  T3  UNIQUE_EXACT_NAME - exact normalized first+last, unique on BOTH sides (config gated)
  T4  FUZZY+SIG         - fuzzy/nickname name AND >=1 secondary signal agrees
  T5  AMBIGUOUS         - multiple plausible candidates -> REVIEW
  T6  NO_MATCH          - nothing plausible -> NO_MATCH
  T0  COLLISION         - 2+ UKG records map to the same Entra user -> REVIEW (safety)
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from rapidfuzz import fuzz

from .models import Person, MatchResult
from .normalize import (name_key, normalize_name, first_names_compatible,
                        first_name_relationship, variant_set, parse_date)


class Matcher:
    def __init__(self, config: dict):
        self.cfg = config
        self.auto_unique_exact = config.get("AUTO_APPLY_UNIQUE_EXACT_NAME", True)
        self.fuzzy_min = int(config.get("FUZZY_MIN_SCORE", 88))
        self.fuzzy_require_secondary = config.get("FUZZY_REQUIRE_SECONDARY", True)
        # Auto-apply fuzzy matches when last name matches exactly AND first name is a
        # known nickname/spelling-variant (no secondary signal required). Safe because
        # it never fires when first names differ entirely (e.g. relatives sharing a
        # surname). Set FUZZY_NICKNAME_AUTOAPPLY=false to send all fuzzy to review.
        self.fuzzy_nickname_autoapply = str(
            config.get("FUZZY_NICKNAME_AUTOAPPLY", "true")).strip().lower() == "true"
        # Entra has no employee number here, so signals default to HR attributes
        # that exist on both sides. (employee_number stays supported in case it
        # is ever populated in Entra, but is not relied on.)
        self.secondary_fields = [
            s.strip() for s in config.get(
                "SECONDARY_SIGNALS",
                "upn_name,department,job_title,hire_date,location,manager",
            ).split(",") if s.strip()
        ]

    # ---------- index building ----------
    def _build_entra_indexes(self, entra: list[Person]):
        by_empno: dict[str, list[Person]] = defaultdict(list)
        by_email: dict[str, Person] = {}
        by_namekey: dict[str, list[Person]] = defaultdict(list)
        for p in entra:
            if p.employee_number:
                by_empno[str(p.employee_number).strip().lower()].append(p)
            if p.email:
                by_email[p.email.strip().lower()] = p
            by_namekey[name_key(p.first_name, p.last_name)].append(p)
        return by_empno, by_email, by_namekey

    # ---------- secondary signal comparison ----------
    def _compare_secondary(self, ukg: Person, entra: Person) -> list[str]:
        agree: list[str] = []
        for f in self.secondary_fields:
            if f == "employee_number":
                if ukg.employee_number and entra.employee_number and \
                        str(ukg.employee_number).strip().lower() == str(entra.employee_number).strip().lower():
                    agree.append("employee_number")
            elif f == "existing_email":
                if ukg.email and entra.email and ukg.email.strip().lower() == entra.email.strip().lower():
                    agree.append("existing_email")
            elif f == "hire_date":
                du, de = parse_date(ukg.hire_date), parse_date(entra.hire_date)
                if du and de and du == de:
                    agree.append("hire_date")
            elif f == "upn_name":
                if self._upn_matches_name(ukg, entra):
                    agree.append("upn_name")
            elif f in ("department", "job_title", "manager", "location"):
                uv = normalize_name(getattr(ukg, f, None))
                ev = normalize_name(getattr(entra, f, None))
                if uv and ev and (uv == ev or fuzz.token_sort_ratio(uv, ev) >= 90):
                    agree.append(f)
        return agree

    @staticmethod
    def _upn_matches_name(ukg: Person, entra: Person) -> bool:
        """Confirm identity using the Entra UPN/email local-part, which usually
        encodes the person's real name even when their displayName is a nickname.

        Handles the common conventions:
          - firstinitial + lastname     (promano  -> P. Romano)
          - firstname + lastname         (philiprromano / philip.romano)
          - firstname.lastname / first_last with separators

        The UKG LAST NAME must appear in the UPN (strong anchor), AND the first
        name must be consistent (full first name present, OR the UPN's leading
        initial equals the UKG first initial). This correctly ACCEPTS
        'dtravis' for Dre'ane Travis and REJECTS 'jshaffer' for Ryan Shaffer.
        """
        local = (entra.email or "").split("@")[0].lower()
        if not local:
            return False
        compact = "".join(ch for ch in local if ch.isalnum())
        fn = normalize_name(ukg.first_name).replace(" ", "")
        ln = normalize_name(ukg.last_name).replace(" ", "")
        if not fn or not ln or len(ln) < 2:
            return False
        # The UPN must END with the last name (anchored) - this prevents 'mcwilliams'
        # being treated as 'm' + 'williams'. Find lastname as a suffix of compact.
        if not compact.endswith(ln):
            # full first name + lastname not at the very end? still allow if both
            # full names present (e.g. philip.romano -> philiprromano contains both).
            if fn in compact and ln in compact:
                return True
            return False
        prefix = compact[:-len(ln)]      # everything before the trailing lastname
        if not prefix:
            return False                  # UPN is just the last name - not enough
        # Case 1: full first name as the prefix (philiprromano, philip.romano).
        if prefix == fn or prefix.startswith(fn):
            return True
        # Case 2: prefix is a known nickname of the first name (timmoore -> tim).
        from .normalize import alias_set
        if prefix in alias_set(fn) or alias_set(prefix) & alias_set(fn):
            return True
        # Case 3: first-initial + lastname (promano, dtravis, agauze).
        if len(prefix) == 1 and prefix == fn[0]:
            return True
        return False

    @staticmethod
    def _name_score(ukg: Person, entra: Person) -> float:
        u = f"{normalize_name(ukg.first_name)} {normalize_name(ukg.last_name)}".strip()
        e = f"{normalize_name(entra.first_name)} {normalize_name(entra.last_name)}".strip()
        return float(fuzz.token_sort_ratio(u, e))

    # ---------- main entry ----------
    def match_all(self, entra: list[Person], ukg: list[Person]) -> list[MatchResult]:
        by_empno, by_email, by_namekey = self._build_entra_indexes(entra)
        results: list[MatchResult] = []

        # Pre-compute name-key uniqueness on the UKG side for tier T3.
        ukg_namekey_counts: dict[str, int] = defaultdict(int)
        for p in ukg:
            ukg_namekey_counts[name_key(p.first_name, p.last_name)] += 1

        for u in ukg:
            results.append(self._match_one(
                u, by_empno, by_email, by_namekey, ukg_namekey_counts, entra
            ))

        self._flag_collisions(results)
        return results

    @staticmethod
    def _flag_collisions(results: list[MatchResult]) -> None:
        """If two+ UKG records would be written the SAME Entra email, that is a
        red flag (one Entra identity can only belong to one person). Demote all
        colliding auto-applies to REVIEW so a human resolves it.
        Strong-key (employee_number) matches are trusted and excluded."""
        from collections import defaultdict
        buckets: dict[str, list[MatchResult]] = defaultdict(list)
        for r in results:
            if r.decision == "AUTO_APPLY" and r.target_email:
                buckets[r.target_email.strip().lower()].append(r)
        for email, group in buckets.items():
            if len(group) <= 1:
                continue
            # keep a strong-key winner if exactly one exists
            strong = [g for g in group if g.tier.startswith("T1_STRONG_KEY")]
            if len(strong) == 1:
                losers = [g for g in group if g is not strong[0]]
            else:
                losers = group
            for g in losers:
                g.decision = "REVIEW"
                g.tier = "T0_COLLISION_" + g.tier
                g.reasons.append(
                    f"COLLISION: {len(group)} UKG records mapped to same email "
                    f"{email}; demoted to manual review.")
                if g.entra and not g.candidates:
                    g.candidates = [Matcher._cand(g.entra, g.confidence)]

    def _match_one(self, u: Person, by_empno, by_email, by_namekey,
                   ukg_namekey_counts, all_entra) -> MatchResult:

        # ---- T1: strong key (employee number) ----
        if u.employee_number:
            cands = by_empno.get(str(u.employee_number).strip().lower(), [])
            if len(cands) == 1:
                e = cands[0]
                sec = self._compare_secondary(u, e)
                return self._apply_or_skip(
                    u, e, 99.0, "T1_STRONG_KEY_EMPNO",
                    [f"Matched on employee number {u.employee_number}"], sec)

        # NOTE: matching on existing email is intentionally NOT used here. Entra is
        # the email source of truth and UKG emails are known to be wrong/missing, so
        # email is not a reliable signal in this deployment.

        # ---- name-based candidates ----
        nk = name_key(u.first_name, u.last_name)
        exact = list(by_namekey.get(nk, []))

        # gather fuzzy/nickname candidates (compatible first name + close last name)
        fuzzy: list[tuple[Person, float]] = []
        u_last = normalize_name(u.last_name)
        for e in all_entra:
            if e in exact:
                continue
            # First name compatible via legal name, the UKG preferred name, or the
            # Entra preferred/goes-by name (handles "Mike" vs legal "Michael", etc.).
            fn_ok = (first_names_compatible(u.first_name, e.first_name)
                     or (u.preferred_name and first_names_compatible(u.preferred_name, e.first_name))
                     or (e.preferred_name and first_names_compatible(u.first_name, e.preferred_name)))
            # OR the Entra UPN encodes this person's name (firstinitial+lastname etc.).
            # This catches nickname displayNames the nickname map doesn't know.
            upn_ok = self._upn_matches_name(u, e)
            if not fn_ok and not upn_ok:
                continue
            last_score = fuzz.ratio(u_last, normalize_name(e.last_name))
            if last_score >= self.fuzzy_min or upn_ok:
                fuzzy.append((e, self._name_score(u, e)))
        fuzzy.sort(key=lambda x: x[1], reverse=True)

        # ---- T2 / T3: exact name ----
        if exact:
            # rank exact candidates by secondary agreement
            scored = []
            for e in exact:
                sec = self._compare_secondary(u, e)
                scored.append((e, sec))
            scored.sort(key=lambda x: len(x[1]), reverse=True)
            best_e, best_sec = scored[0]

            # unique on both sides?
            unique_both = len(exact) == 1 and ukg_namekey_counts[nk] == 1

            if best_sec:
                # Guard: the secondary signal must UNIQUELY pick a winner. If a
                # runner-up among same-name candidates has just as many agreeing
                # signals, the signal did not disambiguate -> send to review.
                tied = [e for e, s in scored if len(s) == len(best_sec)]
                if len(tied) > 1:
                    return self._make_review(
                        u, exact, "T5_AMBIGUOUS_SIGNAL_TIE",
                        [f"{len(exact)} Entra users share this name and "
                         f"{len(tied)} tie on secondary signals {best_sec}; cannot disambiguate"])
                conf = 95.0 + min(len(best_sec), 3)
                return self._apply_or_skip(
                    u, best_e, conf, "T2_EXACT_NAME+SIGNAL",
                    [f"Exact name match; secondary signals agree: {best_sec}"], best_sec)

            if len(exact) == 1 and unique_both:
                if self.auto_unique_exact:
                    return self._apply_or_skip(
                        u, best_e, 90.0, "T3_UNIQUE_EXACT_NAME",
                        ["Exact name, unique on both sides, no secondary signal available"],
                        best_sec)
                else:
                    return self._make_review(
                        u, exact, "T3_UNIQUE_EXACT_NAME_REVIEW",
                        ["Exact unique name but auto-apply disabled; needs review"])

            # multiple exact name matches -> ambiguous
            return self._make_review(
                u, exact, "T5_AMBIGUOUS_EXACT",
                [f"{len(exact)} Entra users share this exact name; cannot disambiguate"])

        # ---- T4: fuzzy / nickname ----
        if fuzzy:
            best_e, best_score = fuzzy[0]
            sec = self._compare_secondary(u, best_e)
            second_score = fuzzy[1][1] if len(fuzzy) > 1 else 0
            close_runner_up = second_score >= best_score - 3

            if sec and not close_runner_up:
                conf = min(94.0, best_score) + min(len(sec), 3)
                return self._apply_or_skip(
                    u, best_e, conf, "T4_FUZZY+SIGNAL",
                    [f"Fuzzy/nickname match (score {best_score:.0f}); signals agree: {sec}"],
                    sec)

            # ---- T4b: SAFE nickname/variant auto-apply (no secondary needed) ----
            # Only fires when the LAST name matches exactly (or is a known variant)
            # AND the first names are a known nickname/spelling-variant/typo of each
            # other AND there is a single unambiguous candidate. This captures
            # Phil/Philip, Jeff/Jeffrey, Nicole/Nichole, etc. while LEAVING in review
            # cases where only the last name matches but first names differ entirely
            # (e.g. relatives: Amanda vs Katie Gauze, William vs Mike McWilliams).
            if self.fuzzy_nickname_autoapply and not close_runner_up:
                u_last_n = normalize_name(u.last_name)
                e_last_n = normalize_name(best_e.last_name)
                last_exact = (u_last_n == e_last_n) or \
                    bool(variant_set(u_last_n) & variant_set(e_last_n)) or \
                    (u_last_n and e_last_n and fuzz.ratio(u_last_n, e_last_n) >= 95)
                rel = first_name_relationship(
                    u.preferred_name or u.first_name, best_e.first_name)
                if u.preferred_name and rel == "none":
                    rel = first_name_relationship(u.first_name, best_e.first_name)
                if last_exact and rel in ("nickname", "variant", "high_similarity",
                                          "initial", "exact"):
                    conf = 88.0 if rel in ("nickname", "variant") else 86.0
                    return self._apply_or_skip(
                        u, best_e, conf, f"T4b_NICKNAME_{rel.upper()}",
                        [f"Last name matches; first name is a {rel} "
                         f"('{u.first_name}'~'{best_e.first_name}'); single candidate"],
                        sec)

            # fuzzy without secondary, or with ambiguity -> review
            return self._make_review(
                u, [c for c, _ in fuzzy[:5]], "T4_FUZZY_REVIEW",
                [f"Fuzzy candidate(s) found (best {best_score:.0f}); "
                 + ("no secondary signal" if not sec else "ambiguous runner-up")])

        # ---- T6: nothing ----
        return MatchResult(
            ukg=u, entra=None, decision="NO_MATCH", confidence=0.0,
            tier="T6_NO_MATCH",
            reasons=["No Entra user matched on name (exact or fuzzy)"])

    # ---------- result builders ----------
    def _apply_or_skip(self, u, e, conf, tier, reasons, sec) -> MatchResult:
        """A confident identity match. The HR sync uses (ukg, entra) + decision;
        target_email is retained only for reporting/traceability."""
        return MatchResult(
            ukg=u, entra=e, decision="AUTO_APPLY", confidence=conf, tier=tier,
            reasons=reasons, secondary_agreements=sec, target_email=e.email,
            candidates=[self._cand(e, conf)])

    def _make_review(self, u, entra_cands, tier, reasons) -> MatchResult:
        cands = [self._cand(e, self._name_score(u, e)) for e in entra_cands]
        return MatchResult(
            ukg=u, entra=entra_cands[0] if entra_cands else None,
            decision="REVIEW", confidence=0.0, tier=tier,
            reasons=reasons, candidates=cands,
            target_email=entra_cands[0].email if entra_cands else None)

    def _make_skip(self, u, e, tier, reasons, sec=None) -> MatchResult:
        return MatchResult(
            ukg=u, entra=e, decision="SKIP", confidence=100.0, tier=tier,
            reasons=reasons, secondary_agreements=sec or [],
            target_email=e.email if e else None)

    @staticmethod
    def _cand(e: Person, score) -> dict:
        return {
            "entra_id": e.source_id,
            "first": e.first_name, "last": e.last_name, "upn": e.email,
            "employee_number": e.employee_number, "department": e.department,
            "job_title": e.job_title, "manager": e.manager,
            "hire_date": e.hire_date, "score": round(float(score), 1),
        }
