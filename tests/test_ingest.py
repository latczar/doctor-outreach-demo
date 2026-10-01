import pytest

from outreach.ingest import ingest_csv
from outreach.normalise import name_key, normalise_grade, normalise_specialty, salutation

from .conftest import SEED


@pytest.mark.parametrize("raw, expected", [
    ("Consultant", "Consultant"),
    ("Locum Consultant", "Consultant"),
    ("Consultant Paediatrician", "Consultant"),
    ("ST5", "Registrar (ST3+)"),
    ("Specialty Registrar ST3", "Registrar (ST3+)"),
    ("ST2", "Junior trainee"),
    ("FY2", "Foundation doctor"),
    ("GP partner", "GP Partner"),
    ("Salaried GP", "Salaried GP"),
    ("Clinical Fellow", None),
    ("", None),
])
def test_grades_are_normalised(raw, expected):
    assert normalise_grade(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("Cardiologist", "Cardiology"),
    ("Pediatrics", "Paediatrics"),
    ("A&E", "Emergency Medicine"),
    ("Anaesthetist", "Anaesthetics"),
    ("GP", "General Practice"),
    ("Underwater basket weaving", None),
])
def test_specialties_are_normalised(raw, expected):
    assert normalise_specialty(raw) == expected


def test_name_key_ignores_titles_and_punctuation():
    assert name_key("Dr Sarah O'Neill") == name_key("Sarah ONeill") == "sarah oneill"


def test_salutation_uses_the_title_from_the_data():
    assert salutation("Mr", "Price") == "Mr Price"
    assert salutation("", "Raman") == "Dr Raman"


def test_seed_file_merges_three_duplicates(conn):
    summary = ingest_csv(conn, "c1", SEED / "leads_raw.csv")
    assert (summary.rows_read, summary.new_doctors, summary.duplicates) == (27, 24, 3)

    dups = conn.execute("SELECT full_name, reason FROM leads WHERE status = 'DUPLICATE' ORDER BY id").fetchall()
    reasons = {row["full_name"]: row["reason"] for row in dups}
    assert "registration number" in reasons["Sarah ONeill"]
    assert "email address" in reasons["J Okafor"]
    assert "name and employer" in reasons["Chloe Barker"]


def test_merge_keeps_the_best_details_from_every_copy(conn):
    ingest_csv(conn, "c1", SEED / "leads_raw.csv")
    chloe = conn.execute(
        "SELECT * FROM leads WHERE full_name = 'Chloe Barker' AND status != 'DUPLICATE'"
    ).fetchone()
    # One copy had the email, the other had the registration number and the notes.
    assert chloe["email"] == "chloe.barker@northbridge.nhs.example"
    assert chloe["reg_number"] == "TST100122"
    assert "ECG" in chloe["profile_notes"]


def test_importing_the_same_file_twice_adds_nothing(conn):
    ingest_csv(conn, "c1", SEED / "leads_raw.csv")
    again = ingest_csv(conn, "c1", SEED / "leads_raw.csv")
    assert again.new_doctors == 0 and again.already_imported == 27
    assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 27


def test_a_later_file_with_a_known_doctor_is_marked_duplicate(conn, tmp_path):
    ingest_csv(conn, "c1", SEED / "leads_raw.csv")
    later = tmp_path / "batch2.csv"
    later.write_text(
        "title,full_name,grade,specialty,employer,country,reg_number,email,source,profile_notes\n"
        "Dr,Priya R,Consultant,Cardiology,Somewhere Else,UK,,PRIYA.RAMAN@northbridge.nhs.example,LinkedIn,\n",
        encoding="utf-8",
    )
    summary = ingest_csv(conn, "c1", later)
    assert summary.duplicates == 1 and summary.new_doctors == 0
    row = conn.execute("SELECT reason FROM leads WHERE source_file = 'batch2.csv'").fetchone()
    assert "earlier import" in row["reason"] and "email" in row["reason"]
