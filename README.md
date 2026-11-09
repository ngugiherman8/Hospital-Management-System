# Hospital & Clinic Management System

Django + PostgreSQL. This repository holds **Phase 1 (Foundation)** of the project plan: project setup, CI,
users and roles, audit log, patient registry, appointments and today's queue.

## Run locally (development)

```bash
python -m venv venv && . venv/bin/activate
pip install -r requirements-dev.txt
# PostgreSQL must be running; defaults: db/user/password = hms/hms/hms on localhost
python manage.py migrate
python manage.py seed_demo          # demo clinics and one user per role (password ChangeMe-12345)
python manage.py runserver
```

Sign in as `reception1`, `nurse1`, `doctor1`, `admin1` and so on. **Never run `seed_demo` in production.**

## Run on the hospital server (Docker)

```bash
cp .env.example .env     # set DJANGO_SECRET_KEY and POSTGRES_PASSWORD first
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Services: `db` (PostgreSQL 16), `redis`, `web` (gunicorn), `worker` and `beat` (Celery: SMS reminders, no-show sweep).
Health check: `GET /health/`. Nightly backup: `scripts/backup.sh` (encrypted dump; copy it to the cloud afterwards).
`DJANGO_SSL_REDIRECT=true` expects HTTPS in front of the app (reverse proxy); set it to `false` only for LAN testing.

## Tests and CI

```bash
ruff check . && pytest
```

GitHub Actions (`.github/workflows/ci.yml`) runs lint, a migrations-up-to-date check and the tests against a
PostgreSQL service, then builds the Docker image.

## What is built

### Phase 1: foundation
Users and roles, audit log, login history, patient registry with duplicate detection (search by name, hospital number,
ID or phone), appointments, SMS reminder task, and today's queue. Security: automatic sign-out after 30 minutes idle,
lockout after 5 failed sign-ins in 15 minutes, and two-factor sign-in (authenticator app) for administrators. Two-factor is
on by default in production; set `ADMIN_TWO_FACTOR=false` only while testing. Details are in `apps/core`, `apps/patients`
and `apps/appointments`.

### Phase 2: clinical module

| Area | App | What it does |
| --- | --- | --- |
| Triage | `clinical` | Vitals with out-of-range flags, BMI, pain score, complaint and priority. Nurses edit; doctors and reception view |
| Consultation | `clinical` | History, examination and plan (versioned, never overwritten), ICD-10 search, outcome (home, admit, refer), review date. Doctors edit; nurses view |
| Orders and results | `orders` | Test catalogue (price, normal range, sample type), orders numbered `LAB-2026-000123`, lab work list, results, corrections that add a new result and keep the old one |
| Prescriptions | `pharmacy_rx` | Drug list, dose, frequency, days, calculated quantity, allergy check with a logged acknowledgement. Pharmacists get a read-only list. Stock arrives in Phase 3 |
| Wards and admissions | `wards` | Wards, beds (free, occupied, being cleaned), admit, transfer, discharge summary (printable), bed history. One active admission per bed and per patient, enforced by the database |
| Patient timeline | `clinical` | All visits, diagnoses, results and medicines on one page. Doctors and nurses open it directly; others need a logged break-glass override |

**Visit status** (`apps/appointments/models.py`, `TRANSITIONS`) is the single place that says who may take which step and
what must be true first: save triage, emergency fast-track from reception (reason required, audited), call in, send to
lab (needs an order), return from lab (needs every test resulted or cancelled), complete (needs a diagnosis and an outcome
of home or refer), admit (needs an admission), discharge (needs a summary). The queue is ordered emergency, urgent,
routine, then arrival.

**Who can do what**

| Screen | Can change | Can view |
| --- | --- | --- |
| Triage | Nurse | Doctor, reception |
| Consultation, diagnosis, tests, prescription | Doctor | Nurse |
| Lab work list and results | Lab (procedures: doctor or nurse) | Doctor, nurse |
| Prescription list | Doctor (prescribing) | Nurse, pharmacist |
| Ward board | Nurse (release cleaned beds) | Doctor, reception |
| Admit and discharge | Doctor | Nurse |
| Transfer | Nurse | Doctor |
| Patient timeline | Nobody edits | Doctor, nurse |

Administrators can do everything. Reception, cashier and pharmacist never see notes or result text.

Printouts: prescription, lab result, referral letter and discharge summary (each print is written to the audit log).

### Phase 3: billing and pharmacy (in progress)

| Step | Status | What it does |
| --- | --- | --- |
| 1. Price lists and charges | Built | `ServiceItem` and a price per payer type (cash, SHA, private, corporate; cash price is the fallback). Draft charges appear on their own from clinical events: registration (first visit), consultation finished, each test ordered, each bed day, each dispensed medicine. Each event is charged once, ever |
| 2. Invoices | Built | Gap-free `INV-2026-000123` numbers, total worked out from the lines, draft, open, part-paid, paid, void. Cashier adds or removes lines on a draft, finalises, prints. Administrator issues credit notes and voids |
| 3. Payments and receipts | Built | Cash, M-Pesa (typed code), card, insurer; part payments; inpatient deposits; receipts `RCT-...`; refunds are negative payments approved by an administrator (`RFD-...`). M-Pesa prompt and callback are built: each provider transaction becomes one payment however often it is sent |
| 4. Insurer and SHA claims | Not built | |
| 5. Pharmacy and stock | Not built | Dispensing, batches, expiry and reorder alerts. The billing side is ready: a prescription marked dispensed is charged automatically |
| 6. End of day | Not built | Cash-up and stock-take |

Prices must be set by an administrator in `/admin/` under Service items (each has a price per payer type). Run
`python manage.py sync_service_items` after adding tests or drugs so they can be priced. A charge with no price shows
"No price set" and blocks finalising the invoice. `seed_sample_data` (testing only) loads sample prices.

M-Pesa: the cashier can type the transaction code now. For automatic prompts set `MPESA_CONSUMER_KEY`,
`MPESA_CONSUMER_SECRET`, `MPESA_SHORTCODE`, `MPESA_PASSKEY`, `MPESA_CALLBACK_TOKEN` and `MPESA_CALLBACK_BASE`
(see `.env.example`). The callback address is `/billing/mpesa/callback/<token>/`. The prompt code has been tested only against a
stand-in for Safaricom, not the real service.

### Status against the build instructions

| Phase | Status |
| --- | --- |
| 1 Foundation | Complete, apart from items the instructions leave to later: SMS provider, FHIR-style API |
| 2A Core clinical | Complete: triage, consultation, orders and results, prescriptions, wards, printouts. Still open: the full ICD-10 list (needs the hospital's licensed file; load with `import_icd10`), and catalogue maintenance is through the admin site rather than dedicated screens |
| 2B Imaging, antenatal and postnatal, dental | **Not built.** Needs the radiographer, radiologist, midwife and dentist roles |
| 3 Billing and pharmacy | Steps 1 to 3 built; steps 4 to 6 not built |
| 4, 5 | Not started |

### How this differs from the Phase 2 instructions

The instructions assume a Phase 1 with some helpers; here is how each maps to what exists.

- `accounts` app: users, roles and departments live in `core`.
- `AuditedModel` is in `apps.core.audit`; `log_access(obj)` and `role_required(...)` were added; `Sequence.next(name)` and `Sequence.next_number(prefix)` were added to `core` and are used for order and admission numbers.
- The visit is `clinical.Visit` (linked to the queue entry). It carries the triage fields and the consultation outcome, so there is no separate `TriageRecord` or `Consultation` table. `VitalSigns` holds readings (triage and ward rounds). `ClinicalNote` is the versioned note (`NoteVersion`), `VisitDiagnosis` is `Diagnosis`.
- Each ordered test is one `Order` row (no separate order header and item); `Result` rows are never edited.
- Result flags are normal, high, low, and abnormal (for yes/no style results such as a malaria test).
- Printing: the discharge summary has a print view. Prescription, lab result and referral letters are not built yet.
- Reception, cashier and pharmacist see the ward board and (pharmacist) the prescription list, not admission records.

### Assumptions to confirm (from the instructions' open decisions)
Hand-typed lab results, free-text notes with Plan, adult vital-sign ranges only (paediatric ranges need clinical sign-off),
a starter formulary and test catalogue (edit in the admin), four starter wards.
Load a full ICD-10 list with `python manage.py import_icd10 file.csv` (columns `code,title`).

## Not built yet

- Real SMS provider: `SMS_BACKEND` defaults to a console logger.
- SHA / insurer cover verification, claims, dispensing and stock, end-of-day cash-up (rest of Phase 3).
- Imaging, antenatal and postnatal care, dental (Phase 2B).
- Radiology image storage, HR and payroll: still open scope items in the plan.
- Cloud replication (Phase 4).
