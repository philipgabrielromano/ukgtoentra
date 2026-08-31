"""
UKG Pro -> Entra ID HR-data sync - CLI entry point.

Reads employees from UKG Pro and Microsoft Entra ID, matches them on First+Last
name with secondary HR signals, then writes UKG's HR data
(Job Title / Location / Manager) into the matched Entra users.
Department sync is disabled by default (re-enable via UKG_TO_ENTRA_MAP).

UKG is the source of truth for HR data; Entra is never the source here and UKG is
never written to (read-only). Identity/email is NOT synced by this tool.

Usage:
  # Match + report only, NO writes (always start here):
  python -m src.main --mode plan

  # Actually write HR data into Entra (after reviewing the plan):
  python -m src.main --mode apply --apply

  # Apply human-approved ambiguous matches from the review workbook too:
  python -m src.main --mode apply --apply --review-file reports/manual_review_x.xlsx

  # Skip manager relationship sync:
  python -m src.main --mode apply --apply --no-manager

Without --apply, NOTHING is written to Entra (dry-run preview).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values

from .entra_client import EntraClient
from .ukg_client import UkgClient
from .matcher import Matcher
from . import reporting
from .field_map import load_field_map
from .hr_sync import HrSync
from .interactive import confirm_fuzzy_matches

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
LOGS = ROOT / "logs"


def load_config() -> dict:
    cfg = dict(dotenv_values(ROOT / "config" / ".env"))
    cfg.update({k: v for k, v in os.environ.items()
                if k.startswith(("ENTRA_", "UKG_", "GRAPH_"))})
    return cfg


def setup_logging(level: str):
    LOGS.mkdir(exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-8s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(LOGS / "sync.log")],
    )


def approved_review_ids(review_path: Path) -> set[str]:
    """Return UKG ids whose review row is approved (DECISION (yes/no) == 'yes')."""
    df = pd.read_excel(review_path)
    ok = set()
    for _, row in df.iterrows():
        if str(row.get("DECISION (yes/no)", "")).strip().lower() == "yes":
            ok.add(str(row.get("ukg_id")))
    return ok


def main():
    ap = argparse.ArgumentParser(description="UKG Pro -> Entra ID HR-data sync")
    ap.add_argument("--mode", choices=["plan", "apply"], default="plan")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write to Entra. Without this flag, runs dry-run.")
    ap.add_argument("--review-file",
                    help="Approved manual_review_*.xlsx; approved rows are promoted "
                         "to AUTO_APPLY for this run.")
    ap.add_argument("--no-manager", action="store_true",
                    help="Skip manager relationship sync.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Write at most N users this run (pilot). 0 = no limit.")
    ap.add_argument("--interactive", action="store_true",
                    help="Before writing, prompt to confirm fuzzy (nickname/variant) "
                         "matches and choose which NOT to apply.")
    args = ap.parse_args()

    cfg = load_config()
    global log
    setup_logging(cfg.get("LOG_LEVEL", "INFO"))
    log = logging.getLogger("main")
    REPORTS.mkdir(exist_ok=True)
    do_write = bool(args.apply) and args.mode == "apply"
    if args.mode == "apply" and not args.apply:
        log.warning("apply mode WITHOUT --apply: dry-run preview only, no writes.")

    # ---- fetch ----
    entra_client = EntraClient(cfg)
    ukg_client = UkgClient(cfg)
    entra_people = entra_client.fetch_users()
    ukg_people = ukg_client.fetch_employees()

    # ---- match ----
    results = Matcher(cfg).match_all(entra_people, ukg_people)

    # ---- promote human-approved review rows to AUTO_APPLY ----
    if args.review_file:
        approved = approved_review_ids(Path(args.review_file))
        promoted = 0
        for r in results:
            if r.ukg.source_id in approved and r.decision == "REVIEW" and r.entra:
                r.decision = "AUTO_APPLY"
                r.tier = "REVIEW_APPROVED_" + r.tier
                promoted += 1
        log.info("Promoted %d human-approved review matches to AUTO_APPLY", promoted)

    # ---- interactive fuzzy confirmation (only when actually writing) ----
    if args.interactive and do_write:
        entra_by_id = {p.source_id: p for p in entra_people}
        confirm_fuzzy_matches(results, entra_by_id)
    elif args.interactive and not do_write:
        log.info("--interactive ignored in dry-run (nothing is written anyway).")

    reporting.print_summary(results)
    reporting.write_match_plan(results, REPORTS)
    reporting.write_manual_review(results, REPORTS)

    # ---- UKG HR data -> Entra ----
    fields = load_field_map(cfg)
    log.info("Mapped fields: %s",
             ", ".join(f"{f.person_attr}->{f.graph_attr}" for f in fields))
    hr = HrSync(entra_client, fields, entra_people, results,
                sync_manager=not args.no_manager)
    plan = hr.build_plan(results)
    on_prem_count = sum(1 for c in plan if c.on_prem_synced)
    cloud_count = len(plan) - on_prem_count
    log.info("HR sync: %d users with changes (%d cloud-only -> Entra, "
             "%d AD-synced -> on-prem report only)", len(plan), cloud_count, on_prem_count)
    backup: list = [] if do_write else None
    if do_write and args.limit:
        log.warning("PILOT MODE: writing at most %d user(s) this run.", args.limit)
    hr.apply_plan(plan, do_write, limit=args.limit, backup=backup)
    rows = [c.as_row() for c in plan]
    reporting.write_hr_plan(rows, REPORTS)
    reporting.write_on_prem_report(rows, REPORTS)
    reporting.write_powershell_script(rows, REPORTS)
    reporting.write_audit(results, rows, REPORTS)
    if backup:
        reporting.write_backup(backup, REPORTS)
    if on_prem_count:
        log.warning("ACTION NEEDED: %d AD-synced user(s) must be updated in on-prem AD "
                    "(not Entra). See reports/on_prem_changes_*.csv", on_prem_count)
    log.info("Done.")


if __name__ == "__main__":
    main()
