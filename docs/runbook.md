# Runbook: running the system day to day

## 1. Install on a fresh hospital server
1. Install Ubuntu LTS, Docker and Docker Compose. Put the second disk at `/mnt/second-disk` and a UPS on the server
   (set the UPS software to shut the machine down cleanly at about 20% battery).
2. Copy the project to `/opt/hms`. `cp .env.example .env` and set: `DJANGO_DEBUG=false`, a long random
   `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `HMS_HOST` (the name staff type), two different database passwords
   (`POSTGRES_OWNER_*` changes structure, `POSTGRES_USER` is the limited website account), `ALERT_EMAILS`, mail settings.
3. Make the backup key once: `openssl rand -base64 48 > /etc/hms/backup.key; chmod 600 /etc/hms/backup.key`.
   **Keep a copy of this key somewhere other than the server (printed, in the safe). Backups cannot be opened without it.**
   The containers run as a non-root user, so that user must be able to read the key and write the backup folder:
   find its number with `docker compose -f docker-compose.prod.yml run --rm worker id -u`, then `chown <number> /etc/hms/backup.key /mnt/second-disk/hms-backups`.
4. `docker compose -f docker-compose.prod.yml up -d --build`. Migrations and database grants run on start.
5. Create the first administrator: `docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser`.
6. Sign in, open **Sync status** and `/ops/health/` (should say ok).

## 2. HTTPS certificate on workstations
Caddy makes its own certificate authority. Copy `/data/caddy/pki/authorities/local/root.crt` out of the caddy container
(`docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt .`) and install it as a trusted root on each
workstation once. Point the name in `HMS_HOST` at the server (router DNS or each machine's hosts file).

## 3. Two-factor sign-in
Administrators and managers must use an authenticator app. At first sign-in the system shows a setup key; type it into
the app (no QR code yet) and enter the 6-digit code. Lost phone: another administrator removes the device under
Administration, TOTP devices; the user enrols again.

## 4. Cloud copy
1. On the cloud server copy `.env.cloud.example` to `.env.cloud`, fill it in, and run
   `docker compose -f docker-compose.cloud.yml --env-file .env.cloud up -d --build`.
2. Put the same `SYNC_TOKEN` and the cloud address (`SYNC_CLOUD_URL`) in the hospital `.env`, then restart the worker.
3. The cloud is read-only. Cloud users are made on the cloud with
   `manage.py create_cloud_user` (passwords never travel in sync). Managers see `/ops/reports/`.
4. Watch **Sync status**: waiting rows should fall to 0 within minutes of the internet being back.

## 5. Backups
Nightly at 02:00 the worker makes an encrypted dump in `BACKUP_DIR_HOST` and keeps 30 daily and 12 monthly files.
The off-site copy is best done from the host: a cron job at 03:30 running `rclone copy /mnt/second-disk/hms-backups remote:hms-backups`
(the files are encrypted). `BACKUP_COPY_COMMAND` runs inside the worker container, which has no rclone, so leave it empty
unless you add your copy tool to the image. The health page does not watch the off-site copy: check the cloud folder weekly. The health page warns if the newest is over 30 hours old.

## 6. Restore (also the quarterly drill)
Restores always go into a **new** database, never over the live one.
1. `docker compose -f docker-compose.prod.yml exec -e HMS_USE_OWNER_DB=true worker python manage.py restore_db --file /backups/hms-YYYYmmdd-HHMMSS.dump.enc --into hms_restore`
   (run it in the worker, which holds the backup folder and key; the owner account is needed to create the new database)
   (it checks row counts against the live database; add `--strict` to demand equal counts).
2. Point a test copy of the site at `hms_restore` and sign in; open a patient, an invoice, the stock page.
3. For a real disaster: stop the site, point `POSTGRES_DB` at the restored database, start it, then write down what time
   the data goes back to.
Run this on a different machine each quarter and record it in `docs/drills.md`.

## 7. Alerts
Contacts are `ALERT_EMAILS` and optionally an SMS webhook. An alert is sent once, repeated every 6 hours while the
problem lasts, and a message says when it clears. Alerts cover: database, low disk, old backup, sync backlog.

## 8. Updating
`git pull`, then `docker compose -f docker-compose.prod.yml up -d --build`. Take a manual backup first:
`docker compose -f docker-compose.prod.yml exec worker python manage.py backup_db`.
