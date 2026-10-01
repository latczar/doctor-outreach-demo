"""A person decides what the rules couldn't, and the rules still run afterwards."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from outreach.db import get_lead
from outreach.llm.fake import TemplateLLM
from outreach.override import OverrideError, recheck, remove
from outreach.pipeline import run_pipeline
from outreach.web.app import create_app
from outreach.web.present import bucket_of, tracker

from .conftest import SEED


@pytest.fixture
def ran(conn, services):
    run_pipeline(conn, services, SEED / "leads_raw.csv")
    return conn


def lead_id(conn, name: str | None) -> int:
    if name is None:
        return conn.execute("SELECT id FROM leads WHERE full_name = '' AND status = 'NEEDS_REVIEW'").fetchone()["id"]
    return conn.execute("SELECT id FROM leads WHERE full_name = ? AND status != 'DUPLICATE'", (name,)).fetchone()["id"]


def gate(conn, lid: int, stage: str) -> dict:
    return json.loads(get_lead(conn, lid)["gate_results"])[stage]


def test_fixing_an_unknown_grade_lets_the_rules_pass_the_doctor(ran, services):
    isla = lead_id(ran, "Isla McKenzie")
    outcome = recheck(ran, isla, services, "Lat", "Checked the trust's staff page", grade="Registrar (ST3+)")
    assert outcome.status == "PENDING_APPROVAL" and "Now on the final list" in outcome.message
    assert gate(ran, isla, "qualify")["evidence"]["grade"] == "Clinical Fellow -> Registrar (ST3+)"
    event = ran.execute("SELECT actor, detail FROM audit_log WHERE event = 'override.recheck'").fetchone()
    assert event["actor"] == "reviewer:Lat" and json.loads(event["detail"])["changes"] == {"grade": "Registrar (ST3+)"}


def test_a_grade_the_campaign_doesnt_target_is_still_disqualified_by_the_rules(ran, services):
    isla = lead_id(ran, "Isla McKenzie")
    outcome = recheck(ran, isla, services, "Lat", "Asked the trust", grade="Specialty Doctor")
    assert outcome.status == "DISQUALIFIED" and "isn't targeted" in outcome.message


def test_a_fix_never_skips_the_other_rules(ran, services):
    nameless = lead_id(ran, None)
    outcome = recheck(ran, nameless, services, "Lat", "Found the name on the rota", full_name="Ama Owusu")
    # The name is fixed, but the email was a secretary's shared inbox all along, and the rules say so.
    assert outcome.status == "EMAIL_INVALID" and "shared inbox" in outcome.message
    assert get_lead(ran, nameless)["full_name"] == "Ama Owusu"


def test_a_person_can_confirm_a_mailbox_the_computer_cannot(ran, services):
    hannah = lead_id(ran, "Hannah Lewis")
    outcome = recheck(ran, hannah, services, "Lat", "Called the trust switchboard", confirm_email=True)
    assert outcome.status == "PENDING_APPROVAL"
    assert "confirmed by Lat (Called the trust switchboard)" in gate(ran, hannah, "email_verified")["reason"]


def test_a_confirmation_cannot_overrule_a_mail_server(ran, services):
    hannah = lead_id(ran, "Hannah Lewis")
    outcome = recheck(ran, hannah, services, "Lat", "Sure it's right", email="nobody@northbridge.nhs.example",
                      confirm_email=True)
    assert outcome.status == "EMAIL_INVALID" and "doesn't exist" in outcome.message
    assert get_lead(ran, hannah)["email_origin"] == "person"


@pytest.mark.parametrize("reviewer, note, kwargs, message", [
    ("", "Checked", {"grade": "Consultant"}, "Enter your name"),
    ("Lat", "", {"grade": "Consultant"}, "short note"),
    ("Lat", "Checked", {}, "Nothing changed"),
    ("Lat", "Checked", {"grade": "Wizard"}, "Pick a grade"),
])
def test_a_decision_needs_a_name_a_note_and_a_real_change(ran, services, reviewer, note, kwargs, message):
    with pytest.raises(OverrideError, match=message):
        recheck(ran, lead_id(ran, "Isla McKenzie"), services, reviewer, note, **kwargs)


def test_taking_a_doctor_off_the_list_records_who_and_why(ran):
    isla, hannah = lead_id(ran, "Isla McKenzie"), lead_id(ran, "Hannah Lewis")
    remove(ran, isla, "Lat", "Not a clinical role")
    remove(ran, hannah, "Lat", "Left the trust last month")
    isla_row, hannah_row = get_lead(ran, isla), get_lead(ran, hannah)
    assert (isla_row["status"], isla_row["reason"]) == ("DISQUALIFIED", "Removed by Lat: Not a clinical role")
    assert hannah_row["status"] == "EMAIL_INVALID" and bucket_of(hannah_row["status"]) == "removed"
    assert [t["state"] for t in tracker(hannah_row)][3] == "stopped"  # stopped at Email verified?
    assert gate(ran, hannah, "email_verified")["reason"] == "Removed by Lat: Left the trust last month"


def test_a_second_decision_on_the_same_doctor_is_refused(ran, services):
    isla = lead_id(ran, "Isla McKenzie")
    recheck(ran, isla, services, "Lat", "Checked", grade="Consultant")
    with pytest.raises(OverrideError, match="isn't waiting for a decision"):
        remove(ran, isla, "Someone else", "Too late")


def test_the_doctor_page_offers_the_decision_and_the_list_links_to_it(settings, ran):
    client = TestClient(create_app(replace(settings), llm=TemplateLLM()))
    isla = lead_id(ran, "Isla McKenzie")
    assert f'href="/leads/{isla}#decide">Decide' in client.get("/doctors?show=needs").text
    assert "This doctor needs your decision" in client.get(f"/leads/{isla}").text

    refused = client.post(f"/leads/{isla}/recheck", data={"reviewer": "Lat", "note": "", "grade": "Consultant"})
    assert "short note" in refused.text and "This doctor needs your decision" in refused.text

    done = client.post(f"/leads/{isla}/recheck", data={"reviewer": "Lat", "note": "Checked the staff page",
                                                        "grade": "Consultant"})
    assert "Now on the final list" in done.text and "This doctor needs your decision" not in done.text
    assert "Lat fixed the record and the rules ran again" in done.text
