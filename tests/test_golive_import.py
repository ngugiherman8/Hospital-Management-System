import csv

import pytest
from django.core.management import call_command

from apps.golive import importer
from apps.golive.models import LegacyRecord
from apps.patients.models import Patient

HEADER = ["id", "file_number", "name", "dob", "sex", "id_number", "phone", "decision"]


def write(tmp_path, rows, name="old.csv"):
    path = tmp_path / name
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    return str(path)


def by_id(outcomes):
    return {o.legacy_id: o for o in outcomes}


@pytest.mark.django_db
def test_import_cleans_creates_and_can_run_again(tmp_path):
    path = write(
        tmp_path,
        [
            ["L1", "F-100", "WANJIRU KAMAU", "03/04/1990", "female", "12345678", "0712 345 678", ""],
            ["L2", "F-101", "otieno  Odhiambo", "1985-11-30", "M", "", "n/a", ""],
        ],
    )
    first = by_id(importer.run_import(path))
    assert first["L1"].outcome == "created" and first["L2"].outcome == "created"
    p = Patient.objects.get(id_number="12345678")
    assert (p.first_name, p.last_name, p.phone) == ("Wanjiru", "Kamau", "+254712345678")
    assert Patient.objects.get(last_name="Odhiambo").phone == ""
    again = by_id(importer.run_import(path))
    assert again["L1"].outcome == again["L2"].outcome == "already_imported"
    assert Patient.objects.count() == 2


@pytest.mark.django_db
def test_dry_run_saves_nothing_and_uses_no_numbers(tmp_path):
    path = write(tmp_path, [["L1", "", "Amina Yusuf", "2001-01-02", "F", "", "", ""]])
    out = importer.run_import(path, dry_run=True)
    assert out[0].outcome == "created"
    assert Patient.objects.count() == 0 and LegacyRecord.objects.count() == 0
    importer.run_import(path)
    assert Patient.objects.get().hospital_number.endswith("000001")  # the dry run did not use up number 1


@pytest.mark.django_db
def test_bad_rows_are_rejected_with_reasons(tmp_path):
    path = write(
        tmp_path,
        [
            ["", "", "No Id", "2001-01-02", "F", "", "", ""],
            ["L2", "", "Single", "2001-01-02", "F", "", "", ""],
            ["L3", "", "No Dob", "garbage", "F", "", "", ""],
            ["L4", "", "Future Baby", "2999-01-01", "M", "", "", ""],
            ["L5", "", "No Sex", "2001-01-02", "", "", "", ""],
            ["L6", "", "Good One", "2001-01-02", "M", "", "", ""],
            ["L6", "", "Good One", "2001-01-02", "M", "", "", ""],
        ],
    )
    out = importer.run_import(path)
    assert [o.outcome for o in out] == ["rejected"] * 5 + ["created", "rejected"]
    assert "legacy_id" in out[0].note and "name" in out[1].note and "date of birth" in out[2].note
    assert "not possible" in out[3].note and "sex" in out[4].note and "twice" in out[6].note
    assert Patient.objects.count() == 1


@pytest.mark.django_db
def test_same_person_twice_is_linked_not_duplicated(tmp_path):
    path = write(
        tmp_path,
        [
            ["L1", "A1", "Mary Achieng", "1980-05-05", "F", "11112222", "", ""],
            ["L2", "A2", "MARY ACHIENG", "05/05/1980", "F", "", "0700111222", ""],  # same name and birth date
            ["L3", "A3", "Mary A Otieno", "1979-01-01", "F", "11112222", "", ""],  # same ID number
        ],
    )
    out = by_id(importer.run_import(path))
    assert out["L1"].outcome == "created"
    assert out["L2"].outcome == "linked_in_file" and out["L3"].outcome == "linked_in_file"
    assert Patient.objects.count() == 1
    assert LegacyRecord.objects.filter(patient=Patient.objects.get()).count() == 3


@pytest.mark.django_db
def test_patient_already_in_system_is_linked_and_never_overwritten(tmp_path):
    mine = Patient.objects.create(
        first_name="Grace", last_name="Wambui", date_of_birth="1992-02-02", sex="F", phone="+254799000111"
    )
    path = write(tmp_path, [["L1", "G1", "Grace Wambui", "1992-02-02", "F", "55556666", "0711000000", ""]])
    out = importer.run_import(path, update_blank=True)
    assert out[0].outcome == "linked_existing" and out[0].hospital_number == mine.hospital_number
    mine.refresh_from_db()
    assert mine.phone == "+254799000111"  # typed in the system, kept
    assert mine.id_number == "55556666"  # was empty, filled
    assert Patient.objects.count() == 1


@pytest.mark.django_db
def test_weaker_match_is_held_until_someone_decides(tmp_path):
    Patient.objects.create(first_name="John", last_name="Mwangi", date_of_birth="1970-07-07", sex="M")
    held = write(tmp_path, [["L1", "", "Joseph Mwangi", "1970-07-07", "M", "", "", ""]])
    assert importer.run_import(held)[0].outcome == "held_for_review"
    assert Patient.objects.count() == 1
    new = write(tmp_path, [["L1", "", "Joseph Mwangi", "1970-07-07", "M", "", "", "new"]], "new.csv")
    assert importer.run_import(new)[0].outcome == "created"
    assert Patient.objects.count() == 2


@pytest.mark.django_db
def test_decision_same_as_links_to_a_hospital_number_or_earlier_row(tmp_path):
    base = Patient.objects.create(first_name="John", last_name="Mwangi", date_of_birth="1970-07-07", sex="M")
    path = write(
        tmp_path,
        [
            ["L1", "", "Joseph Mwangi", "1970-07-07", "M", "", "", f"same_as:{base.hospital_number}"],
            ["L2", "", "Jo Mwangi", "1970-07-07", "M", "", "", "same_as:L1"],
            ["L3", "", "Ghost", "1970-07-07", "M", "", "", "same_as:NOPE"],
        ],
    )
    out = by_id(importer.run_import(path))
    assert out["L1"].outcome == out["L2"].outcome == "linked_by_decision"
    assert out["L3"].outcome == "rejected"
    assert Patient.objects.count() == 1


@pytest.mark.django_db
def test_age_only_needs_the_flag_and_is_marked_estimated(tmp_path):
    path = tmp_path / "age.csv"
    path.write_text("id,name,age,sex\nL1,Baby Kip,4,M\n")
    assert importer.run_import(str(path))[0].outcome == "rejected"
    out = importer.run_import(str(path), estimate_from_age=True)[0]
    assert out.outcome == "created" and "estimated" in out.note
    assert LegacyRecord.objects.get().dob_estimated


@pytest.mark.django_db
def test_old_card_number_finds_the_patient_in_search(tmp_path):
    importer.run_import(write(tmp_path, [["L1", "CARD-77", "Amina Yusuf", "2001-01-02", "F", "", "", ""]]))
    assert Patient.search("CARD-77").count() == 1


@pytest.mark.django_db
def test_command_report_and_sample_check(tmp_path, capsys):
    path = write(
        tmp_path,
        [[f"L{i}", f"C{i}", f"Person{i} Test", f"19{70 + i}-01-01", "F", str(10000000 + i), "", ""] for i in range(8)],
    )
    report = str(tmp_path / "report.csv")
    call_command("import_patients", path, "--report", report)
    assert "created: 8" in capsys.readouterr().out
    assert len(list(csv.DictReader(open(report)))) == 8
    sheet = str(tmp_path / "sample.csv")
    call_command("patient_sample", "--count", "5", "--seed", "1", "--out", sheet)
    rows = list(csv.DictReader(open(sheet)))
    assert len(rows) == 5
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):  # nothing checked yet
        call_command("patient_sample", "--result", sheet)
    for r in rows:
        r["matches_paper"] = "Y"
    with open(sheet, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    call_command("patient_sample", "--result", sheet)
    rows[0]["matches_paper"], rows[0]["what_differs"] = "N", "wrong birth year"
    with open(sheet, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(CommandError):
        call_command("patient_sample", "--result", sheet)
