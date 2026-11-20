# Drills

Status: **run here** means done on the build machine with real PostgreSQL and two running servers; **to run** means it
needs the hospital's own equipment. Write the date, who ran it and the result when each is done on site.

| Drill | Status | Result / what to do |
| --- | --- | --- |
| Internet cut-off | Run here | Cloud stopped, hospital kept working, queue grew with retries (5 s doubling to 1 h). Cloud started: 1016 rows delivered, 51 tables / 929 rows identical by hash; a later patient edit synced. Found and fixed: a child row queued before its parent broke the cloud (now whole transactions, parents first, all-or-nothing). |
| Restore from backup | Run here | Encrypted dump restored into a clean database, counts checked (automated test). **To run** on a clean second machine from the offsite copy. |
| Wrong role | Run here | Every page as every role, plus a matrix of closed pages (automated test). Cloud manager: reports 200, invoices 403, edit 403. |
| Backup failure | Run here (logic) | Health check turns bad when the newest backup is too old; alert sent once and again on recovery (tests). |
| Cloud outage | Run here (as cut-off) | Same as the cut-off drill. Long outage (hours) **to run**. |
| Power cut | To run | Pull the plug during activity; on boot the database must start clean and the worker resume sync. UPS shutdown setup is outside the code. |
| Busy day | To run | Load test with several workstations at once; watch the health page and sync backlog. |
| Docker images | To run | Compose files validated only (`docker compose config`); images were not built here (no Docker daemon). First build on the server is the real test. |

## Phase 5 drills

| Drill | Status | Result / what to do |
| --- | --- | --- |
| Patient import at volume | Run here | 3,000 made-up rows (with deliberate duplicates and shared names): about 1 minute, 2,974 created, 1 same person linked, 25 weaker matches held for a person to decide. Run again: all 2,975 skipped as already imported, the 25 still held, in about 5 seconds. A dry run saved nothing and used no hospital numbers. |
| Sample check against paper | To run | Needs a receptionist, a nurse and the hospital's real paper records. |
| Set-up check on real data | To run | `check_setup` must be all green on the hospital's own files. |
| Training practice case | To run | Needs real staff on a training copy. |
| Pilot, cut-over, rollback | To run | Two weeks of real use; the pages record the results, the hospital makes the decisions. |
