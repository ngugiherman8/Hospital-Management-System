"""A starter list of common ICD-10 codes for the search box.

Doctors may also type any code and name by hand. Importing the full WHO list is a later step
and needs the hospital's licence terms confirmed.
"""

COMMON_ICD10 = [
    ("A09", "Diarrhoea and gastroenteritis, presumed infectious"),
    ("A15.0", "Tuberculosis of lung"),
    ("A37.9", "Whooping cough, unspecified"),
    ("A90", "Dengue fever"),
    ("B20", "HIV disease"),
    ("B50.9", "Plasmodium falciparum malaria, unspecified"),
    ("B54", "Unspecified malaria"),
    ("B82.9", "Intestinal parasitism, unspecified"),
    ("D50.9", "Iron deficiency anaemia, unspecified"),
    ("E11.9", "Type 2 diabetes mellitus without complications"),
    ("E43", "Severe protein-energy malnutrition"),
    ("G40.9", "Epilepsy, unspecified"),
    ("H10.9", "Conjunctivitis, unspecified"),
    ("H66.9", "Otitis media, unspecified"),
    ("I10", "Essential (primary) hypertension"),
    ("I50.9", "Heart failure, unspecified"),
    ("J00", "Acute nasopharyngitis (common cold)"),
    ("J02.9", "Acute pharyngitis, unspecified"),
    ("J03.9", "Acute tonsillitis, unspecified"),
    ("J06.9", "Acute upper respiratory infection, unspecified"),
    ("J18.9", "Pneumonia, unspecified"),
    ("J20.9", "Acute bronchitis, unspecified"),
    ("J45.9", "Asthma, unspecified"),
    ("K29.7", "Gastritis, unspecified"),
    ("K35.8", "Acute appendicitis, other and unspecified"),
    ("K59.0", "Constipation"),
    ("L08.9", "Local infection of skin, unspecified"),
    ("L20.9", "Atopic dermatitis, unspecified"),
    ("M54.5", "Low back pain"),
    ("N39.0", "Urinary tract infection, site not specified"),
    ("N76.0", "Acute vaginitis"),
    ("O20.9", "Haemorrhage in early pregnancy, unspecified"),
    ("O80", "Single spontaneous delivery"),
    ("R05", "Cough"),
    ("R10.4", "Other and unspecified abdominal pain"),
    ("R50.9", "Fever, unspecified"),
    ("R51", "Headache"),
    ("S61.9", "Open wound of wrist and hand, part unspecified"),
    ("T14.9", "Injury, unspecified"),
    ("Z23", "Need for immunization against a single bacterial disease"),
]


def search_icd10(text, limit=10):
    text = (text or "").strip().lower()
    if not text:
        return []
    hits = [(c, n) for c, n in COMMON_ICD10 if text in c.lower() or text in n.lower()]
    return hits[:limit]
