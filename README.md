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

Phase 1 exit gate: a receptionist can register a patient and book a visit.

| Area | Where | Notes |
| --- | --- | --- |
| Users, roles, departments | `apps/core` | Roles: admin, receptionist, doctor, nurse, lab, pharmacist, cashier. 20-minute idle timeout, 10-character minimum password |
| Audit log | `apps/core/audit.py` | Any model extending `AuditedModel` is logged automatically (who, what changed, when, IP). Record views are logged. Entries are append-only |
| Login history | `apps/core` | Logins, logouts and failed attempts |
| Patient registry | `apps/patients` | Sequential hospital number (HN-000123), phone normalisation, duplicate detection (same ID, same phone, similar name and date of birth), payer / cover |
| Appointments | `apps/appointments` | Booking per doctor and clinic with double-booking prevention, arrived / cancelled, SMS reminder task, no-show sweep |
| Today's queue | `apps/appointments` | Walk-ins and arrived appointments get a daily ticket (A-001), stage tracking, 30-second auto refresh |

## Not built yet

- Two-factor login for admins: the flag exists on the user, but no second step is enforced.
- Break-glass override screen: the audit entry type exists; the screen that asks for a reason comes with the clinical module.
- Real SMS provider: `SMS_BACKEND` defaults to a console logger. Add a class with `send(to, message)` and point the setting at it.
- SHA / insurer cover verification (Phase 3): the registration screen has no "verified" status yet.
- Triage and consultation screens (Phase 2): the queue buttons only move a patient between stages.
- Cloud replication (Phase 4).
