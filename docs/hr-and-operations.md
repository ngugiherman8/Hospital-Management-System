# Staff, rosters, payroll, theatre, general store and assets

This chapter covers the modules added after go-live planning: `hr`, `roster`, `payroll`, `theatre` and `supplies`.

## First-time set-up (once, on the hospital server)

```
python manage.py migrate
python manage.py seed_hr        # departments and leave types (check the leave days against your HR policy)
python manage.py seed_roster    # Morning, Afternoon, Night, Long day (change on the Roster set-up page)
python manage.py seed_payroll   # starting PAYE/NSSF/SHIF/housing-levy figures, NOT confirmed
```

All three commands are safe to run twice. Do not run them on the cloud copy: the data reaches it through sync.

## New roles

| Role | Does |
| --- | --- |
| HR and payroll officer (`hr`) | Staff records, contracts, licences, leave balances, shift set-up, payroll preparation. Needs two-factor sign-in. |
| Storekeeper (`storekeeper`) | General store, issuing supplies, asset register. |
| Manager (existing) | Approves leave, shift swaps, supply requests and pay runs. Sees the staff directory without pay or ID numbers. |

Create the accounts in the administration site like any other user. Link each person who signs in to their staff record (Staff, then Edit, then "user"), otherwise My shifts, My leave and My payslips show a "not linked" page.

## Rules the system enforces

**Roster.** A shift is refused when the person is not on the staff list that day, is on approved leave, has an expired licence from a professional body, already has an overlapping shift, or would get less than 11 hours of rest (setting `ROSTER_MIN_REST_HOURS`). Going over contract hours only warns. Approving leave takes planned shifts in those dates off the roster. A shift swap needs the colleague to agree, then a planner to approve, and the rules are checked again for the new person.

**Attendance.** Clock-in and clock-out records are never edited. A missed one is added by a planner with a reason and is marked as such. Set `ATTENDANCE_ALLOWED_NETS` (a list such as `["192.168.1.0/24"]`) to allow self-clocking only from the hospital network. This is a convenience, not strong security.

**Leave.** The balance is the sum of a ledger. Each month HR presses "Add leave for the month" once; running a month twice does nothing. Nobody can decide their own leave. Days count Monday to Saturday, less public holidays that HR has entered (Easter and Eid move, so enter each year's list).

**Payroll.** Pay is calculated from the contract, allowances, deductions and one-off items for the month, with part-months for joiners and leavers, and unpaid leave taken off. The person who calculates a run cannot approve it, and approved payslips cannot be changed: corrections go in the next month as a one-off item. Loans stop when paid back.

**Theatre.** A booking is refused if the theatre (with 15 minutes of clean-up, setting `THEATRE_CLEANING_MINUTES`), the patient, or anyone in the team is already busy, on leave or has an expired licence. Emergencies are booked anyway and the clash is written on the case. An operation cannot start until consent and the identity check are ticked; only an emergency can start without them, with a written reason. Finishing it adds the procedure's price to the patient's bill, once.

**General store.** Separate from the pharmacy stock. A request is approved by a manager (not the person who asked), then issued by the storekeeper, who cannot give more than was asked for or is in the store. Movements are never edited.

**Assets.** Every asset gets a tag. Anyone can report a fault by tag. A fault puts the asset "under repair" until its tickets are closed. A planned service resets the next service date. Value is straight-line over the useful life, for information only.

## What you must check yourself

These are not guesses to trust blindly.

1. **Statutory payroll figures.** The starting values (PAYE bands and personal relief, NSSF 6% up to KES 108,000, SHIF 2.75% with a KES 300 minimum, housing levy 1.5% each side, and SHIF and housing levy taken off before PAYE) came from public sources found in November 2026 and are marked unconfirmed. Payroll refuses to run until a manager has checked them against the current KRA, NSSF and SHA notices and pressed Confirm. When a rate changes, add a new figure with a new start date; old runs keep what they used.
2. **Leave days and rules** in `seed_hr` are starting points. Compare with your HR policy and the Employment Act.
3. **Overtime is not calculated.** HR enters it as a one-off item.
4. **A contract change in the middle of a month** is not split: the contract in force at month end is used. End the old contract and start the new one on the first of a month.
5. **Export files** (bank list, statutory summary) are plain CSV. They are not in the exact upload format of your bank, iTax, NSSF or SHA portals, so a person must map the columns.
6. **Not included:** staff document uploads, appraisals, recruitment, training records, an operative-notes record for theatre (the clinical notes stay in the patient record), stock expiry dates in the general store, and asset depreciation postings to an accounts system.

## Backups and sync

All new tables sync to the cloud copy like the rest. The bank account number is kept out of the audit log and the sync queue. The limited database account cannot edit or delete leave entries, clock punches or store movements.
