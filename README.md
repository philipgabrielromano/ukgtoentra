# UKG Pro → Entra ID  HR-Data Sync

A safe, auditable, **one-direction** sync that reads HR data from **UKG Pro** and
writes it into **Microsoft Entra ID** for the matching user:

- **Job Title / Description** → Entra `jobTitle`
- **Department** → Entra `department`
- **Location** → Entra `officeLocation`
- **Manager** → Entra manager *relationship* (`/manager/$ref`)

UKG Pro is the source of truth and is **never written to** (read-only). Identity/email
is **not** synced by this tool.

---

## Why no email sync?

The email-into-UKG step was originally intended only as a way to create a shared key
for matching. But the matching engine doesn't need it — and since **Entra does not store
the UKG employee number**, writing emails wouldn't have produced a reliable shared key
anyway. So matching is done directly on **First + Last name plus secondary HR signals**,
and the tool only moves HR data in the direction you actually need: UKG → Entra.

---

## Matching strategy (name-based, since Entra has no employee number)

The engine is conservative: it **auto-applies only high-confidence matches** and routes
everything else to a human-review queue. Nothing ambiguous is ever written.

| Tier | Rule | Action |
|------|------|--------|
| **T2** | Exact name **+ a secondary signal uniquely agrees** | Auto-apply |
| **T3** | Exact name, unique on both sides | Auto-apply (configurable) |
| **T4** | Nickname/typo match **+ secondary signal** | Auto-apply, else review |
| **T5** | Multiple same-name people / signal can't disambiguate | → Manual review |
| **T6** | No match | → Manual review / skip |
| **T0** | 2+ UKG records map to the same Entra user (collision) | → Manual review (safety) |

**Nickname & spelling-variant handling (T4b):** When the **last name matches exactly**
and the first name is a known nickname (Phil/Philip, Jeff/Jeffrey, Tom/Thomas, Kim/Kimberly)
or spelling variant/typo (Nicole/Nichole, Micheal/Michael, Abigial/Abigail), the match
auto-applies even without a secondary signal. **Critically, this never fires when the first
names differ entirely** — relatives who share a surname (e.g. *Amanda* vs *Katie* Gauze,
*William* vs *Mike* McWilliams) always stay in manual review. Toggle with
`FUZZY_NICKNAME_AUTOAPPLY`.

**Secondary signals** (configurable): department, job title, hire date, location, manager.
The more of these you have populated on **both** sides, the more matches auto-apply and the
smaller your review queue.

### AD-synced (on-prem mastered) users — important
Some users are mastered by **on-prem Active Directory** (Entra Connect syncs AD → Entra).
For those, writing HR attributes to Entra is **rejected or overwritten** at the next sync
cycle (~30 min). This is independent of Intune device management — a user can sign in via
an Intune/Entra-joined device and still be an AD-synced *account*.

This tool reads each user's `onPremisesSyncEnabled` flag and:
- **Cloud-only users** → written to Entra normally.
- **AD-synced users** → **never written to Entra** (even with `--apply`). Their intended
  changes are exported to `reports/on_prem_changes_*.csv` so they can be applied in
  on-prem AD instead (`title`, `department`, `physicalDeliveryOfficeName`, `manager`),
  from where Entra Connect carries them up automatically. A warning is logged so this is
  never silent.

The run summary always shows the split, e.g.
`HR sync: 412 users with changes (405 cloud-only -> Entra, 7 AD-synced -> on-prem report only)`.

### Safeguards
- **Dry-run by default.** Nothing writes to Entra unless you pass `--apply`.
- **Only changed fields are written**; missing UKG data never blanks an existing Entra value.
- **Manager is resolved to a real Entra user** (by manager employee number → match table →
  exact/fuzzy name, incl. "Last, First"); unresolved managers are reported, never guessed.
- **Collision + signal-tie detection** prevents writing to the wrong same-named person.
- **Full audit trail** (CSV + JSON) and a **manual-review workbook** (Excel) with ranked
  candidates and a yes/no approval column.
- Read-only on UKG; idempotent (re-runs only write real differences).

---

## Setup

1. `cp config/.env.example config/.env` and fill in credentials + field mapping.
2. `pip install -r requirements.txt`
3. **Plan (no writes):**
   ```
   python -m src.main --mode plan
   ```
   Review `reports/hr_sync_plan_*.csv` and `reports/manual_review_*.xlsx`.
4. **Apply HR data to Entra:**
   ```
   python -m src.main --mode apply --apply
   ```
5. **Apply approved ambiguous matches too** (set `DECISION (yes/no)` = `yes` in the workbook):
   ```
   python -m src.main --mode apply --apply --review-file reports/manual_review_x.xlsx
   ```

## Interactive fuzzy confirmation (`--interactive`)
When writing for real, add `--interactive` to be prompted about the fuzzy
(nickname/spelling-variant) matches before they're written. The tool lists each one
(UKG name → Entra name, score, the values it will set, and the current Entra values)
and asks **which numbers NOT to apply**:

```
[  1] UKG: Philip Romano   -> Entra: Phil Romano   ...
[  2] UKG: Jeffrey Lounds  -> Entra: Jeff Lounds   ...
Exclude which? > 2
```
- blank/`none` = apply all shown · `all` = exclude all · `3, 7 12` = exclude those
- Exact matches (T2/T3) are trusted and **not** prompted (no needless clicking).
- Excluded matches become `SKIP_USER_EXCLUDED` and are never written (visible in audit).
- **Non-interactive safety:** if run without a terminal (e.g. a scheduled job),
  fuzzy matches are automatically **held for review, not written**, so automation
  never silently applies an unconfirmed fuzzy match.

Example:
```
python -m src.main --mode apply --apply --interactive
```

## Permissions
- **Entra / Microsoft Graph:** app registration with **`User.ReadWrite.All`**
  (application permission, admin consent) — needed to write attributes and link managers.
- **UKG Pro:** read-only service account with Web Service access. Confirm
  `UKG_EMPLOYEE_READ_PATH` against your tenant's API docs.

## Tests / demo
```
python tests/test_matcher.py     # matching engine (10 tests)
python tests/test_hr_sync.py     # field diff + manager resolution (9 tests)
python demo_hr_offline.py        # end-to-end dry-run on synthetic data
```
