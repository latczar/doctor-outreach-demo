from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest

from outreach.config import ROOT, get_settings
from outreach.db import connect
from outreach.drafting import Prompts
from outreach.gates import DirectoryFinder, FixtureVerifier
from outreach.llm.fake import TemplateLLM
from outreach.models import load_campaign
from outreach.pipeline import Services, reset_data

SEED = ROOT / "data" / "seed"
TODAY = date(2026, 10, 1)


@pytest.fixture
def settings(tmp_path):
    return replace(
        get_settings(),
        db_path=tmp_path / "test.db",
        outbox_dir=tmp_path / "outbox",
        exports_dir=tmp_path / "exports",
        llm_provider="fake",
        email_sender="outbox",
        demo_inbox="",  # tests never use the inbox or key from your .env
        uploads_dir=tmp_path / "uploads",
        resend_api_key="",
    )


@pytest.fixture
def conn(settings):
    connection = connect(settings.db_path)
    reset_data(connection, SEED)
    yield connection
    connection.close()


@pytest.fixture
def campaign():
    return load_campaign(ROOT / "config" / "campaign.json")


@pytest.fixture
def prompts():
    return Prompts.load(ROOT / "prompts" / "v1")


@pytest.fixture
def services(campaign, prompts):
    return Services(
        campaign=campaign,
        llm=TemplateLLM(),
        finder=DirectoryFinder.from_file(SEED / "email_directory.json"),
        verifier=FixtureVerifier.from_file(SEED / "mail_fixture.json"),
        prompts=prompts,
        today=TODAY,
    )


def make_lead(**overrides) -> dict:
    """A lead dict shaped like a database row, for testing one gate at a time."""
    lead = {
        "id": 999,
        "title": "Dr",
        "full_name": "Priya Raman",
        "first_name": "Priya",
        "last_name": "Raman",
        "grade_raw": "Consultant",
        "grade": "Consultant",
        "specialty_raw": "Cardiologist",
        "specialty": "Cardiology",
        "employer": "Northbridge University Hospitals NHS Foundation Trust",
        "country": "United Kingdom",
        "reg_number": "TST100101",
        "email": "priya.raman@northbridge.nhs.example",
        "email_origin": "provided",
        "source": "Trust consultant directory",
        "profile_notes": "Leads the trust's echocardiography teaching programme for third-year medical students.",
        "gate_results": "{}",
    }
    lead.update(overrides)
    return lead


def reply(subject: str, body: str, detail: str = "the echocardiography teaching programme") -> str:
    return json.dumps({"subject": subject, "body": body, "personal_detail_used": detail})


GOOD_BODY = (
    "Dear Dr Raman,\n\n"
    "I noticed that you lead the echocardiography teaching programme for third-year students at "
    "Northbridge, and I wondered whether you might enjoy mentoring visiting medical students too.\n\n"
    "We arrange observership placements that last two to four weeks. Mentors choose the dates and "
    "host one or two students at a time, and they receive an honorarium of £300 per placement. "
    "The students are in their clinical years and keen to learn from experienced cardiologists.\n\n"
    "Would you be open to a 15-minute call, with no commitment, to hear more?"
)
