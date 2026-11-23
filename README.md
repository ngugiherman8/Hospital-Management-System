# Hospital & Clinic Management System

Django + PostgreSQL system for a small hospital. This repository holds **Phase 1 (foundation)**, **Phase 2 (clinical)** and **Phase 2B (imaging, antenatal and postnatal care, dental)**, **Phase 3 (billing and stock)** and **Phase 4 (hybrid deployment and hardening)** of the project plan:
staff sign-in with roles, an audit trail, the patient registry, appointments with a daily queue, and the clinical record
(triage, notes and diagnoses, lab orders and results, prescriptions, wards and admissions).

## Run it

### With Docker (recommended for the hospital server)

```bash
cp .env.example .env          # then edit DJANGO_SECRET_KEY and passwords
docker compose up --build
```

The app is at http://localhost:8000. Create the first administrator:

```bash
docker compose exec web python manage.py createsuperuser
```

### For development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env && set -a && . ./.env && set +a    # needs PostgreSQL running (see POSTGRES_* in .env)
python manage.py migrate
python manage.py seed_demo      # one demo user per role, password: demo-password-123 (DJANGO_DEBUG only)
python manage.py seed_clinical  # placeholder lab tests, drugs and wards; replace with the hospital's own lists
python manage.py runserver
```

Demo sign-ins: `admin`, `reception`, `nurse`, `doctor`, `lab`, `pharmacist`, `cashier`.

### Checks (the same ones CI runs)

```bash
ruff check . && ruff format --check .
python manage.py makemigrations --check --dry-run
pytest
```

## What is in Phase 1

| Area | What it does |
| --- | --- |
| `apps.accounts` | Custom user with a role: administrator, receptionist, nurse, doctor, lab technician, pharmacist, cashier. Admins can do everything; other roles only their own actions. |
| `apps.core` | Append-only audit log (who created, changed, deleted or viewed what, with old and new values and IP address). Gap-free number sequences. Role decorators. |
| `apps.patients` | Patient registry: hospital number (`HN-000001`), Kenyan phone normalisation, duplicate detection (same ID or phone, or same name and date of birth), search, edit. |
| `apps.appointments` | Walk-ins and booked visits, daily queue numbers (`A-001`), doctor double-booking check, and a status workflow (booked, waiting for triage, waiting for doctor, with doctor, completed, did not come, cancelled) where each step is limited to the right role. |

Models that inherit `AuditedModel` are logged automatically. Record views are logged with `log_access(obj)`.
Audit entries cannot be edited or deleted, in code or in the admin.

## What is in Phase 2

| Area | What it does |
| --- | --- |
| `apps.clinical` | Triage vitals with out-of-range flags (proposed ranges, to be signed off by the clinical lead), consultation with versioned notes (a save never overwrites), ICD-10 diagnoses (starter list, free entry allowed) and an outcome (go home, admit, refer). |
| `apps.orders` | Test catalogue, orders with gap-free numbers (`LAB-2026-000001`), lab work list, result entry with auto flag from the normal range. A correction adds a new result and keeps the old one. |
| `apps.pharmacy_rx` | Drug list and prescriptions with an allergy check that must be acknowledged. Dispensing stays "pending" until Phase 3. |
| `apps.wards` | Wards, beds, admit, transfer and discharge with a summary. A database constraint allows one active admission per bed and per patient. |

Visit status now also has "Awaiting results" and "Admitted". Each move has a role and a condition (for example triage must
be saved before "Waiting for doctor"); both live in `apps/appointments/models.py` (`TRANSITIONS`, `CONDITIONS`). After
all tests are resulted, the lab or the doctor can return the visit to "With doctor". The queue puts emergencies first,
then urgent, then routine. Reception can send an emergency straight to a doctor with a reason, which is kept on the visit
and in the audit log. The patient page shows a timeline of visits, diagnoses and results to nurses and doctors only, and
each clinical record opened is logged as a view.

## What is in Phase 2B

| Area | What it does |
| --- | --- |
| `apps.imaging` | Modalities and imaging catalogue as data (DICOM code, room), imaging orders next to lab tests with a clinical reason and safety questions (pregnancy for radiation studies, contrast allergy and kidney problems for contrast studies), accession numbers (`IMG-2026-000001`), radiographer work list (ordered, arrived, images done), versioned reports (preliminary, final locked, addendum with a reason), critical-finding alerts until the ordering clinician acknowledges, and JPEG or PDF attachments. |
| `apps.maternity` | Pregnancy episode with due date and gestation from the last menstrual period, antenatal contacts (never edited) with danger-sign and blood-pressure flags, delivery summary, newborn record with its own hospital number linked to the mother, postnatal contacts for mother and baby, and a due and missed list. The contact schedule is data (Administration). |
| `apps.dental` | FDI tooth chart (append only), visit record with medical checks, treatment plan with estimate and recorded consent, procedures with anaesthetic note, and completed items marked ready for billing. Dental X-rays are ordered through imaging. |

New roles: dentist, midwife, radiographer, radiologist. Dental visits skip triage; a midwife runs maternal and child
health visits; dentists and midwives can only open visits of their own clinic. Pregnancy records (which can hold HIV
status) are limited to midwives, doctors and administrators, and every view is logged. Nurses do not see them, which is
stricter than the "nurse can view" line in the Phase 2B table; change it if the hospital decides otherwise.

Run `python manage.py seed_clinical` for placeholder modalities, scans, dental procedures and the WHO-style contact
schedule (8 antenatal and 4 postnatal contacts). Confirm the schedule the hospital follows before go-live.

**Not built (needs equipment or later phases):** the image server and web viewer (set `IMAGING_VIEWER_URL` to show an
"Open in viewer" link once one exists), sending a worklist to scanners, billing charges for imaging, dental and contacts
(Phase 3 reads `ready_for_billing` on completed dental items and will add the others), labour-ward chart, immunisation,
family planning, multiple births (one baby per delivery), and Ministry of Health antenatal returns. Uploaded scan
printouts are stored under `MEDIA_ROOT` (default `media/`); back that folder up with the database.

## What is in Phase 3

Billing, insurer and SHA claims, pharmacy and stock (`apps/billing`, `apps/stock`).

- **Prices.** `ServiceItem` (registration, consultations, every test and scan, drugs, dental procedures, bed days,
  antenatal and postnatal contacts) with a price per payer type. A payer with no price of its own pays the self-pay
  price. Prices change in Administration (administrators only). `python manage.py seed_billing` adds a placeholder price
  list, **every price in it is a placeholder**.
- **Automatic charges** go onto the visit's draft invoice, once each: registration (first visit only), consultation
  done, lab test ordered, scan when the images are done, dental procedure done, antenatal and postnatal contact saved,
  each completed bed night (`python manage.py post_bed_nights` daily, and at discharge), and drugs when dispensed.
  A cancelled test comes off a draft invoice; after finalising it raises an alert for the cashier. A service with no
  price never blocks care: it raises an alert on the invoice.
- **Invoices.** Gap-free number given at finalising, drafts have none. The cashier adds or removes lines before
  finalising and prints. After that nothing changes: a mistake is a void (administrator, with a reason, only when
  nothing is paid) or a refund. Totals are always worked out from the lines and payments.
- **Payments.** Cash, M-Pesa (the cashier types the code), card, insurer, deposit. Receipt numbers, part payments,
  refunds as negative payments approved by an administrator. A repeated M-Pesa code is refused by the database.
  `POST /billing/mpesa/confirm/` accepts a confirmation `{invoice_number, code, amount}` with header
  `X-Mpesa-Secret`; it is off until `MPESA_CALLBACK_SECRET` is set, and a repeat creates no second payment.
  It is **not connected to Safaricom**: that needs the hospital's Daraja access.
- **Claims.** Made from a finalised SHA, insurer or corporate invoice. Statuses draft, submitted, approved, rejected,
  paid. Export a month as one CSV (drafts become submitted). A rejected claim is corrected and goes out again in the
  next export. Marking a claim paid records an insurer payment on the invoice.
- **Stock.** Suppliers, purchase orders, goods receipts, batches (batch, expiry, unit cost). Quantity on hand is
  always calculated from append-only movements (receive, dispense, adjust, expired, return). Dispensing takes from the
  earliest-expiring usable batch, never from an expired one, never more than is on hand, and locks the batches so two
  pharmacists cannot take the same stock. It adds the charge and marks the prescription part dispensed or dispensed.
  Warnings for stock at or under the reorder level and batches expiring within 90 days. Prescribers see stock in the
  drug list. Adjustments and write-offs need a reason. The stock-take screen compares counted with the system and
  saves differences as adjustments.
- **End of day.** Each cashier counts per method against what the system expects and signs; a difference needs an
  explanation; payments cannot be added after the cashier closes. Administrators see all cash-ups and a day report that
  checks the money ledger and the stock movements.

Placeholders to confirm with the hospital: the payer list (taken from the patient record), the placeholder prices, the
default reorder level (20 units for every drug), registration charged once per patient, claim file layout (a generic
CSV, not the current SHA format), and the receipt and invoice print layouts.

Known limits: a deposit is an ordinary part payment on one invoice (nothing carries across invoices); patient co-pay
for insured visits is not modelled; credit notes for a finalised invoice are a void plus a new invoice; supplier
payments are not tracked.

## Patient journey

One thread from the front door to the cashier, built on the visit (`Appointment`).

- **Arrival** (`/appointments/arrival/`, reception): search by name, number, ID or phone. Found: start the visit (or check in a booked one). Not found: register, with the searched name already typed in. A patient already in today's queue cannot be started twice.
- **Where is everyone?** (`/appointments/journey/`): one column per place (triage, waiting for doctor, with doctor, lab, imaging, pharmacy, ward, elsewhere, checkout) with minutes spent there.
- **Sending on**: the clinician sends the patient to lab, imaging, pharmacy, back on the doctor's queue, checkout, or somewhere else (with a note). Lab and imaging need an order first. The location also follows every status change by itself.
- **Journey timeline** (`/appointments/<id>/journey/`): every step, who made it and how long the patient was there (`VisitEvent`, append only).
- **Checkout** (`/billing/checkout/`, cashier): the visit is now "Seen, to checkout". It can only become "Checked out" when every invoice is finalised and either paid, or claimed from the insurer/SHA with any co-pay taken. Claims, follow-up and monthly reconciliation continue as in Phase 3.

Visits that were "Completed" before this change are moved to "Checked out" by migration `0003` so they do not flood the checkout queue.

## Who can do what

| Action | Reception | Nurse | Doctor | Other staff |
| --- | --- | --- | --- | --- |
| Register and edit patients, book visits | Yes | No | No | No |
| View patients, view queue | Yes | Yes | Yes | Yes |
| Check in a booked patient, cancel | Yes | No | No | No |
| Send patient from triage to doctor | No | Yes | No | No |
| Call patient in, finish consultation | No | No | Yes | No |
| Save triage | No | Yes | No | No |
| Notes, diagnoses, orders, prescriptions, admit, discharge | No | No | Yes | No |
| Transfer between beds | No | Yes | No | No |
| Enter lab results | No | No | No | Lab technician |
| See notes, results, timeline | No | Yes | Yes | No |
| Order imaging | No | No | Yes | Dentist, midwife in their own clinic |
| Take images, confirm safety questions | No | No | No | Radiographer |
| Write imaging reports | No | No | No | Radiologist |
| Pregnancy and postnatal records | No | No | Yes | Midwife |
| Dental chart and treatment plan | No | No | View only | Dentist |
| View invoices, take payments, edit lines before finalising, claims, own cash-up | No | No | No | Cashier (administrator too) |
| Void an invoice, approve a refund, change prices, see all cash-ups and the day report | No | No | No | Administrator only |
| Dispense, receive stock, purchase orders, adjustments (with a reason), stock-take | No | No | No | Pharmacist (administrator too) |
| See stock levels | No | Yes | Yes | Pharmacist, dentist, midwife |

## Phase 4: running it for real

- `HMS_MODE=local` (hospital server, default) or `cloud` (read-only copy for reports). See `docs/runbook.md`.
- Sync: every save is queued in the same transaction (`sync_outbox`), a worker sends it to the cloud, the cloud applies
  each change once. Page: **Sync status** (`/sync/status/`). Health: `/ops/health/`. Manager report: `/ops/reports/`.
- Commands: `run_worker`, `run_checks`, `backup_db`, `restore_db`, `apply_db_grants`, `prepare_cloud`, `create_cloud_user`.
- New role **Manager**: cloud and local day report, no clinical or billing edits.
- Security: lockout, 2FA for administrators and managers, limited database account. See `docs/security.md`.
- Drills and what is still to be tried on real equipment: `docs/drills.md`.

## Phase 5: pilot and go-live

- Read `docs/golive.md`. Commands: `import_patients`, `patient_sample`, `load_setup`, `check_setup`, `seed_training`.
- `HMS_MODE=training` is a separate practice copy (red banner, no sync, no backups).
- Pages: Problems, Paper forms (all staff); Paper vs system, Go or no-go, Training, Set-up check, Access review, Weekly review
  (manager and administrator; only an administrator records agreements and attendance).
- New accounts must choose their own password at first sign-in.
- One-page guides per role: `docs/guides/`.

## Settings

All settings come from environment variables (see `.env.example`). With `DJANGO_DEBUG` off, the app refuses to start
without `DJANGO_SECRET_KEY`, forces HTTPS and secure cookies. Staff are signed out after 30 minutes of inactivity.
`HOSPITAL_NAME` and `HOSPITAL_NUMBER_PREFIX` customise the screens and patient numbers.

## Not built yet

Live M-Pesa prompts and online SHA submission (they need the hospital's access);
a screen to send non-clinical cloud requests (such as appointment requests) down to the hospital, sync of user group/permission links, pruning of old sync rows, a QR code for 2FA setup. Not yet done for Phase 2: printouts (prescription, discharge summary, lab
result), a referral letter, the full ICD-10 list, and a screen to maintain the test catalogue and drug list (use
Administration for now).

Before go-live: confirm data protection registration and health information rules with a lawyer or the hospital's
compliance officer, set a strong `DJANGO_SECRET_KEY`, and put the server behind HTTPS.
