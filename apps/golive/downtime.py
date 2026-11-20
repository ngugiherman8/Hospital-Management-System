"""Paper forms for when the local server is down. Each is printed in bulk and kept in its department.

Every form has the same header (date, time of the event, who) because the paper is typed in afterwards and the real
time of each event must be kept. `rows` are columns of a log sheet; `fields` are boxes on a one-patient form.
"""

HEADER = ["Date", "Time of event (24 hour)", "Staff name and signature"]

FORMS = {
    "registration": {
        "title": "Patient registration",
        "department": "Reception",
        "fields": [
            "First name",
            "Last name",
            "Date of birth",
            "Sex (M / F)",
            "ID type and number",
            "Phone",
            "Residence",
            "Next of kin: name, relationship, phone",
            "Allergies",
            "Payer (cash / SHA / insurer / company)",
            "Insurer or company and member number",
            "Clinic (general / mother and child / dental / eye)",
        ],
        "note": "Give the patient a paper number starting with P- so it can be matched when the system is back.",
    },
    "triage": {
        "title": "Triage",
        "department": "Triage",
        "fields": [
            "Patient name and paper number",
            "Temperature (C)",
            "Pulse (/min)",
            "Blood pressure (mmHg)",
            "Respiratory rate (/min)",
            "Oxygen saturation (%)",
            "Weight (kg)",
            "Height (cm)",
            "Blood sugar (mmol/L)",
            "Priority (routine / urgent / emergency)",
            "Reason for visit",
        ],
    },
    "consultation": {
        "title": "Doctor's consultation note",
        "department": "Outpatient clinic",
        "fields": [
            "Patient name and paper number",
            "Allergies checked (Y / N)",
            "History and examination",
            "Diagnosis",
            "Tests ordered",
            "Prescription (drug, dose, how often, days)",
            "Plan, admit or follow-up date",
        ],
    },
    "lab": {
        "title": "Laboratory request and result",
        "department": "Laboratory",
        "fields": [
            "Patient name and paper number",
            "Test requested",
            "Sample taken (time)",
            "Result and unit",
            "Flag (normal / high / low / critical)",
            "Critical result phoned to (name, time)",
        ],
    },
    "pharmacy": {
        "title": "Dispensing and stock issue log",
        "department": "Pharmacy",
        "rows": [
            "Patient name and paper number",
            "Drug, strength, form",
            "Batch number",
            "Quantity given",
            "Price (KES)",
        ],
        "note": "Count the shelf at the end of the downtime and write the counts on the back.",
    },
    "cashier": {
        "title": "Cash receipts log",
        "department": "Cashier",
        "rows": [
            "Receipt number (from the paper book)",
            "Patient name and paper number",
            "For (service)",
            "Method (cash / M-Pesa code)",
            "Amount (KES)",
        ],
        "note": "Use the numbered receipt book. Never reuse or tear out a receipt number.",
    },
    "ward": {
        "title": "Ward admissions and transfers",
        "department": "Wards",
        "rows": [
            "Patient name and paper number",
            "Admitted / transferred / discharged",
            "Ward and bed",
            "Reason or diagnosis",
            "Doctor",
        ],
    },
    "maternity": {
        "title": "Antenatal, postnatal and delivery record",
        "department": "Maternity",
        "fields": [
            "Mother's name and paper number",
            "Visit type (antenatal / postnatal / delivery)",
            "Gestation (weeks)",
            "Blood pressure",
            "Weight",
            "Findings",
            "Baby: sex, weight, time of birth",
            "Plan and next visit",
        ],
    },
    "dental": {
        "title": "Dental treatment record",
        "department": "Dental",
        "fields": [
            "Patient name and paper number",
            "Tooth numbers",
            "Findings",
            "Procedure done",
            "Price (KES)",
            "Next visit",
        ],
    },
    "imaging": {
        "title": "Imaging request and report",
        "department": "Imaging",
        "fields": [
            "Patient name and paper number",
            "Study requested",
            "Pregnancy or contrast safety questions answered (Y / N)",
            "Date and time taken",
            "Report",
            "Reported by",
            "Critical finding phoned to (name, time)",
        ],
    },
}
