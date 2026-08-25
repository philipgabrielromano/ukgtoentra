"""
Roll back a sync run using a backup_*.json file produced during --apply.

Restores each written user's attributes (and manager) to their pre-sync values.
Dry-run by default; pass --apply to actually restore.

    python3 rollback.py reports/backup_20260602_140000.json            # preview
    python3 rollback.py reports/backup_20260602_140000.json --apply     # restore

Note: attributes that were blank before are restored to blank (cleared), returning
the user to exactly the state captured before the sync.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import dotenv_values
from src.entra_client import EntraClient

ap = argparse.ArgumentParser(description="Roll back an HR sync run")
ap.add_argument("backup_file", help="Path to backup_*.json")
ap.add_argument("--apply", action="store_true", help="Actually restore (else dry-run)")
args = ap.parse_args()

cfg = dict(dotenv_values(Path(__file__).resolve().parent / "config" / ".env"))
client = EntraClient(cfg)

backup = json.loads(Path(args.backup_file).read_text())
print(f"Loaded {len(backup)} users from {args.backup_file}")
print("MODE:", "RESTORE (writing)" if args.apply else "DRY RUN (preview only)\n")

restored = failed = 0
for entry in backup:
    eid = entry["entra_id"]
    before = entry.get("before", {})
    attrs = {k: v for k, v in before.items() if k != "managerId"}
    mgr = before.get("managerId")

    print(f"{entry.get('name','?')} ({eid})")
    for k, v in attrs.items():
        print(f"    restore {k} -> {v!r}")
    if "managerId" in before:
        print(f"    restore manager -> {mgr!r}")

    if not args.apply:
        continue
    ok = True
    if attrs:
        # send blanks as None to clear fields that were empty before
        success, detail = client.update_user_attributes(eid, attrs)
        ok &= success
        print(f"    attrs[{detail}]")
    if "managerId" in before and mgr:
        success, detail = client.set_manager(eid, mgr)
        ok &= success
        print(f"    manager[{detail}]")
    restored += 1 if ok else 0
    failed += 0 if ok else 1

if args.apply:
    print(f"\nRestored {restored} users, {failed} failed.")
else:
    print("\nDry run complete. Re-run with --apply to actually restore.")
