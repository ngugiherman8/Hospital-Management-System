# Go-live guide (Phase 5)

The software side of the pilot and go-live. People, dates and sign-offs belong to the hospital; the system records them
(**Go or no-go**, **Training**, **Problems**, **Paper vs system**) but cannot make them happen.

## Decisions to have before starting
Pilot department and its head; who signs off the pilot and go-live; who decides on a rollback; where the old patient data
lives and who can clean it; data protection registration and compliance review; who supports the system after go-live and
the budget. These are in the Phases 3 to 5 document.

## 1. Patient data migration
1. Export the old records to CSV. Required columns: a stable id from the old system (`legacy_id`), name (or first and last
   name), date of birth, sex. Useful: old file or card number, ID number, phone, residence, next of kin, payer.
2. Practise on a copy: `python manage.py import_patients old.csv --dry-run --report report.csv`.
3. Read the report. `rejected` rows are fixed in the CSV (the note says why). `held_for_review` rows are people who may
   already exist: add a `decision` column with `new` (a different person) or `same_as:<legacy id or hospital number>`.
   `linked_in_file` and `linked_existing` are the same person found twice and are linked, not duplicated.
4. Run it for real (without `--dry-run`). Run it again as often as needed: rows already imported are skipped, and nothing
   staff typed in the system is overwritten (`--update-blank` only fills empty fields).
5. Where only an age is known, `--estimate-from-age` uses 1 July of the birth year and marks the record as estimated.
6. Check a sample with a receptionist and a nurse: `patient_sample --count 50 --seed 1 --out sample.csv`. They fill in
   `matches_paper` (Y or N) and `what_differs`; `patient_sample --result sample.csv` lists the mismatches and fails until
   every sampled row matches. Fix the data or the CSV and import again.
7. Cut-over day: export the final changes from the old system and run the same import. Old card numbers are searchable.

## 2. Set-up data
First, once, run `seed_clinical` and `seed_billing`. They create the starter imaging studies, dental procedures,
antenatal schedule and the fixed charge codes the system relies on (REG, CONS-*, BED-*, ANC-CONTACT, PNC-CONTACT),
plus placeholder drugs, tests, wards and prices. These are replaced by the next step; the Set-up check lists any active
drug without counted stock so unused starter drugs can be switched off in Administration. Never run `seed_demo` here.
Then put CSV files in one folder and run `load_setup <folder>`: `users.csv`, `drugs.csv`, `tests.csv`, `prices.csv`,
`wards.csv`, `opening_stock.csv` (columns are at the top of `apps/golive/setup_data.py`). All or nothing: one bad row
loads nothing and the message names the file and row. Safe to run again; opening stock is never added twice.
New accounts get a temporary password in `new_accounts.csv` (hand them out, then delete the file) and must choose their
own at first sign-in. Any price you did not supply stays at the starter value: go through the **Prices** page line by
line with the cashier lead and the pharmacist before go-live.
`check_setup` (or the **Set-up check** page) must be all green: every role has an account, no demo accounts, every
service priced, every drug has counted unexpired stock, beds entered, administrators enrolled in two-factor, health checks
passing, no critical problem open. Opening **cash** balances are not held by the system: count them, and record who
agreed on the go/no-go page.

## 3. Training
Run a training copy as a separate server with `HMS_MODE=training` and its own database (never the real one). It shows a
red banner on every page, never syncs or backs up. `seed_training --reset` (run with `HMS_USE_OWNER_DB=true`: wiping needs the owner database account) fills it with practice accounts
(`train-<role>`), made-up patients, a catalogue and stock; run it again before each session. Use the one-page guides in
`docs/guides/`. Print the sign-in sheet from **Training** and record who finished a practice case without help.

## 4. Pilot (two weeks, paper kept alongside)
Daily 15-minute check-in. Log every problem on **Problems** with a severity (critical: same day with a workaround;
high: 3 working days; medium: next release; low: backlog; times to be confirmed with the hospital). Each evening enter the
paper cash, visit and dispensed totals on **Paper vs system**; any difference needs a written reason. The pilot ends when
no critical problem is open and the department head and clinical lead sign off.

## 5. Cut-over
Pick a quiet day that is not month-end. The day before, agree every item on **Go or no-go** for each department and write
down who agreed (the page refuses "no critical problem open" while one is open). Print the **Paper forms** for every
department. Run a final import, switch departments one at a time, keep the project team on site.

**Rollback.** If a critical failure cannot be fixed the same day, the named decision-maker (hospital manager with the
project lead) sends the affected department back to paper. Nothing entered is deleted. Paper from the gap is entered
before the department switches back.

**Server down.** Switch to paper, call IT, type the paper in afterwards. Known limit: the system stamps records with the
time they are typed in; the real time of each paper event is written in the note or free-text field, because records
cannot be back-dated yet.

## 6. Stabilise
Weekly: **Weekly review** with the hospital manager. Monthly: **Access review** (refused requests, wrong passwords,
night-time access, heavy use), user accounts review, dependency updates. Quarterly: restore drill (`docs/runbook.md`).
