"""Reporting: match plan (CSV), manual-review workbook (XLSX), audit log (JSON)."""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

from .models import MatchResult

log = logging.getLogger("report")


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_match_plan(results: list[MatchResult], out_dir: Path) -> Path:
    df = pd.DataFrame([r.as_row() for r in results])
    path = out_dir / f"match_plan_{_ts()}.csv"
    df.to_csv(path, index=False)
    log.info("Wrote match plan: %s", path)
    return path


def write_manual_review(results: list[MatchResult], out_dir: Path) -> Path | None:
    review = [r for r in results if r.decision == "REVIEW"]
    if not review:
        log.info("No manual-review items.")
        return None

    # Cap candidates shown per item so the report stays usable (and within Excel limits).
    MAX_CANDIDATES = 5
    EXCEL_ROW_LIMIT = 1_048_576

    rows = []
    for r in review:
        base = {
            "ukg_id": r.ukg.source_id,
            "ukg_employee_number": r.ukg.employee_number,
            "ukg_first": r.ukg.first_name,
            "ukg_last": r.ukg.last_name,
            "ukg_current_email": r.ukg.email,
            "ukg_department": r.ukg.department,
            "ukg_job_title": r.ukg.job_title,
            "ukg_hire_date": r.ukg.hire_date,
            "tier": r.tier,
            "reason": " | ".join(r.reasons),
        }
        if not r.candidates:
            rows.append({**base, "candidate_rank": "-", "candidate_upn": "(none)"})
        for i, c in enumerate(r.candidates[:MAX_CANDIDATES], 1):
            rows.append({
                **base,
                "candidate_rank": i,
                "candidate_score": c.get("score"),
                "candidate_first": c.get("first"),
                "candidate_last": c.get("last"),
                "candidate_upn": c.get("upn"),
                "candidate_employee_number": c.get("employee_number"),
                "candidate_department": c.get("department"),
                "candidate_job_title": c.get("job_title"),
                "candidate_hire_date": c.get("hire_date"),
                "DECISION (yes/no)": "",          # human fills this in
                "CHOSEN_UPN (override)": "",       # or paste correct UPN
            })

    df = pd.DataFrame(rows)

    # If it won't fit in a single Excel sheet, write CSV instead (no crash, no data loss).
    if len(df) >= EXCEL_ROW_LIMIT:
        path = out_dir / f"manual_review_{_ts()}.csv"
        df.to_csv(path, index=False)
        log.warning("Manual-review set is very large (%d rows from %d items). "
                    "Wrote CSV instead of Excel: %s. This usually means matching is too "
                    "broad - check that UKG is filtered to ACTIVE employees and that "
                    "secondary signals are populated.", len(df), len(review), path)
        return path

    path = out_dir / f"manual_review_{_ts()}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        df.to_excel(xl, index=False, sheet_name="Review")
        ws = xl.sheets["Review"]
        ws.freeze_panes = "A2"
        for col in ws.columns:
            width = max((len(str(c.value)) for c in col if c.value), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(width + 2, 45)
    log.info("Wrote manual review workbook: %s (%d items, %d rows)",
             path, len(review), len(df))
    return path


def write_hr_plan(rows: list[dict], out_dir: Path) -> Path | None:
    """Write the UKG -> Entra HR-data change plan (attributes + manager)."""
    if not rows:
        log.info("HR sync: no attribute/manager changes to write.")
        return None
    df = pd.DataFrame(rows)
    path = out_dir / f"hr_sync_plan_{_ts()}.csv"
    df.to_csv(path, index=False)
    log.info("Wrote HR sync plan: %s (%d users)", path, len(rows))
    return path


def write_on_prem_report(rows: list[dict], out_dir: Path) -> Path | None:
    """List AD-synced (on-prem mastered) users with their intended HR changes.

    These are NOT written to Entra (the write would be rejected/overwritten by
    Entra Connect). Hand this to whoever manages on-prem AD so the changes are
    applied there (title / department / physicalDeliveryOfficeName / manager),
    then they flow up to Entra automatically.
    """
    on_prem = [r for r in rows if r.get("on_prem_synced")]
    if not on_prem:
        log.info("No on-prem (AD-synced) users in this plan.")
        return None
    df = pd.DataFrame(on_prem)
    path = out_dir / f"on_prem_changes_{_ts()}.csv"
    df.to_csv(path, index=False)
    log.warning("%d AD-synced user(s) were NOT written to Entra (mastered on-prem). "
                "See: %s", len(on_prem), path)
    return path


def write_powershell_script(rows: list[dict], out_dir: Path) -> Path | None:
    """Generate a ready-to-run PowerShell script for AD-synced users.

    Produces Set-ADUser commands that apply the intended HR changes in on-prem AD.
    The script uses UPN to find users and resolves managers by UPN (primary) or
    employee number (fallback).
    Run on a domain-joined machine with RSAT/ActiveDirectory module installed.
    """
    on_prem = [r for r in rows if r.get("on_prem_synced") and r.get("entra_upn")]
    if not on_prem:
        return None

    from datetime import datetime
    lines = [
        f"# Auto-generated by ukg-to-entra on {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"# Apply these changes on a domain-joined machine with RSAT/AD module.",
        f"# {len(on_prem)} AD-synced user(s) whose HR data must be set in on-prem AD.",
        "#",
        "# AD attribute mapping:",
        "#   jobTitle         -> title",
        "#   department       -> department",
        "#   officeLocation   -> physicalDeliveryOfficeName (Office)",
        "#   manager          -> manager (DN reference, resolved by UPN)",
        "#",
        "# REVIEW this script before running. Dry-run with -WhatIf is recommended.",
        "",
        "Import-Module ActiveDirectory",
        "",
    ]

    for r in on_prem:
        upn = r["entra_upn"]
        name = r.get("name", "?")
        lines.append(f"# --- User: {name} ({upn}) ---")
        lines.append(f'$user = Get-ADUser -Filter "UserPrincipalName -eq \'{upn}\'"')
        lines.append("if ($user) {")

        # Build Set-ADUser parameters for attributes
        params = []
        if r.get("set_jobTitle"):
            params.append(f'-Title "{r["set_jobTitle"]}"')
        if r.get("set_department"):
            params.append(f'-Department "{r["set_department"]}"')
        if r.get("set_officeLocation"):
            params.append(f'-Office "{r["set_officeLocation"]}"')

        if params:
            param_str = " ".join(params)
            lines.append(f"    Set-ADUser $user {param_str}")

        # Manager resolution — primary: UPN, fallback: employee number
        mgr_upn = r.get("manager_upn")
        mgr_empno = r.get("manager_employee_number")
        mgr_name = r.get("manager_new")
        if mgr_name and mgr_upn:
            lines.append(f"    # Manager: {mgr_name} (UPN: {mgr_upn})")
            lines.append(f'    $mgr = Get-ADUser -Filter "UserPrincipalName -eq \'{mgr_upn}\'"')
            lines.append("    if ($mgr) {")
            lines.append("        Set-ADUser $user -Manager $mgr.DistinguishedName")
            lines.append("    } else {")
            lines.append(f'        Write-Warning "Could not resolve manager (UPN {mgr_upn}) for {name}"')
            lines.append("    }")
        elif mgr_name and mgr_empno:
            # Fallback: resolve by employee number if no UPN available
            lines.append(f"    # Manager: {mgr_name} (no UPN - fallback to employee number {mgr_empno})")
            lines.append(f'    $mgr = Get-ADUser -Filter "EmployeeNumber -eq \'{mgr_empno}\'"')
            lines.append("    if ($mgr) {")
            lines.append("        Set-ADUser $user -Manager $mgr.DistinguishedName")
            lines.append("    } else {")
            lines.append(f'        Write-Warning "Could not resolve manager (empno {mgr_empno}) for {name}"')
            lines.append("    }")
        elif mgr_name:
            # Last resort: resolve by name
            lines.append(f"    # Manager: {mgr_name} (no UPN or empno - resolve by name)")
            lines.append(f'    $mgr = Get-ADUser -Filter "Name -like \'*{mgr_name}*\'"')
            lines.append("    if ($mgr -and $mgr.Count -eq 1) {")
            lines.append("        Set-ADUser $user -Manager $mgr.DistinguishedName")
            lines.append("    } else {")
            lines.append(f'        Write-Warning "Could not uniquely resolve manager \'{mgr_name}\' for {name}"')
            lines.append("    }")

        lines.append("} else {")
        lines.append(f'    Write-Warning "User {upn} not found in AD"')
        lines.append("}")
        lines.append("")

    path = out_dir / f"on_prem_changes_{_ts()}.ps1"
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Wrote PowerShell script for %d AD-synced user(s): %s", len(on_prem), path)
    return path


def write_backup(backup: list[dict], out_dir: Path) -> Path | None:
    """Save the BEFORE state of every user actually written, for rollback.
    Each entry: {entra_id, name, before:{attr: old_value, managerId: old_id}}."""
    if not backup:
        return None
    path = out_dir / f"backup_{_ts()}.json"
    path.write_text(json.dumps(backup, indent=2, default=str))
    log.info("Wrote rollback backup (%d users): %s", len(backup), path)
    return path


def write_audit(results: list[MatchResult], applied: list[dict], out_dir: Path) -> Path:
    payload = {
        "generated_at": datetime.now().isoformat(),
        "summary": summarize(results),
        "applied": applied,
        "decisions": [r.as_row() for r in results],
    }
    path = out_dir / f"audit_{_ts()}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    log.info("Wrote audit log: %s", path)
    return path


def summarize(results: list[MatchResult]) -> dict:
    by_decision = Counter(r.decision for r in results)
    by_tier = Counter(r.tier for r in results)
    return {
        "total_ukg_records": len(results),
        "by_decision": dict(by_decision),
        "by_tier": dict(by_tier),
    }


def print_summary(results: list[MatchResult]):
    s = summarize(results)
    print("\n" + "=" * 60)
    print("MATCH SUMMARY")
    print("=" * 60)
    print(f"Total UKG records: {s['total_ukg_records']}")
    print("\nBy decision:")
    for k, v in sorted(s["by_decision"].items()):
        print(f"  {k:<12} {v}")
    print("\nBy tier:")
    for k, v in sorted(s["by_tier"].items()):
        print(f"  {k:<28} {v}")
    print("=" * 60 + "\n")
