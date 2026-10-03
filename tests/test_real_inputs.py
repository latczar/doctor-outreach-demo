"""Real inputs on the laptop: your own research list, real domain checks, and real addresses only via the inbox."""

import email
import time
from dataclasses import replace
from email.policy import default as email_policy

import dns.resolver
import pytest
from fastapi.testclient import TestClient

from outreach import gates
from outreach.db import connect, get_lead
from outreach.gates import DnsVerifier, DomainCheckError, FixtureVerifier, mail_servers, verify_email
from outreach.ingest import FIELDS, check_research_list, template_csv
from outreach.llm.fake import TemplateLLM
from outreach.models import Status
from outreach.pipeline import run_pipeline
from outreach.review import approve
from outreach.sending import InboxRedirect, OutboxSender, send_approved
from outreach.web.app import create_app

from .conftest import SEED, TODAY

FIXTURE = FixtureVerifier.from_file(SEED / "mail_fixture.json")
SAFE = (".example", ".test")


def dns_says(*servers: str) -> DnsVerifier:
    return DnsVerifier(FIXTURE, lookup=lambda domain: list(servers))


# --- real domain checks ---------------------------------------------------------------

def test_a_real_domain_with_a_mail_server_still_needs_a_person_to_confirm_the_mailbox():
    result = verify_email("sam.okoro@realtrust.org.uk", dns_says("mx1.realtrust.org.uk"))
    assert result.status == Status.NEEDS_REVIEW and "no free check can confirm" in result.reason
    confirmed = verify_email("sam.okoro@realtrust.org.uk", dns_says("mx1.realtrust.org.uk"),
                             confirmed_by="Lat (called the trust)")
    assert confirmed.passed and "confirmed by Lat" in confirmed.reason


def test_a_real_domain_with_no_mail_server_would_bounce():
    result = verify_email("sam.okoro@nomail.org.uk", dns_says())
    assert result.status == Status.EMAIL_INVALID and "no mail server" in result.reason


def test_a_failed_lookup_asks_a_person_rather_than_guessing():
    def offline(domain):
        raise DomainCheckError("LifetimeTimeout")

    result = verify_email("sam.okoro@realtrust.org.uk", DnsVerifier(FIXTURE, lookup=offline))
    assert result.status == Status.NEEDS_REVIEW and "couldn't check" in result.reason


def test_made_up_domains_keep_their_fixture_answers_and_never_touch_the_internet():
    def no_internet(domain):
        raise AssertionError(f"looked up {domain}")

    assert verify_email("priya.raman@northbridge.nhs.example", DnsVerifier(FIXTURE, lookup=no_internet)).passed


def test_mail_servers_reads_the_mx_record_and_spots_a_null_mx(monkeypatch):
    class Record:
        def __init__(self, host):
            self.exchange = host

    answers = {"realtrust.org.uk": [Record("mx1.realtrust.org.uk."), Record("mx2.realtrust.org.uk.")],
               "nullmx.org.uk": [Record(".")]}

    def resolve(domain, kind, lifetime):
        if domain not in answers:
            raise dns.resolver.NXDOMAIN
        return answers[domain]

    monkeypatch.setattr(dns.resolver, "resolve", resolve)
    assert mail_servers("realtrust.org.uk") == ["mx1.realtrust.org.uk", "mx2.realtrust.org.uk"]
    assert mail_servers("nullmx.org.uk") == [] and mail_servers("missing.org.uk") == []


# --- your own research list -----------------------------------------------------------

def test_the_template_has_every_column_and_passes_its_own_check():
    text = template_csv()
    assert text.splitlines()[0] == ",".join(FIELDS)
    assert check_research_list(text.encode()) == ""


ROW = "Dr,Sam Okoro,Consultant,Cardiology,Real Trust,United Kingdom,,sam.okoro@realtrust.org.uk,Trust website,Runs the cardiology teaching programme."


@pytest.mark.parametrize("data, problem", [
    (b"full_name,email\nSam,sam@x.example\n", "missing these columns: grade, specialty, country"),
    ((",".join(FIELDS) + "\n").encode(), "no doctors under them"),
    ("\n".join([",".join(FIELDS)] + [ROW] * 501).encode(), "Upload up to 500"),
    (b"\xff\xfe\x00junk", "save it as CSV (UTF-8)"),
], ids=["missing-columns", "no-rows", "too-many-rows", "not-utf8"])  # short ids: Windows caps a test's name
def test_a_bad_upload_says_what_to_fix(data, problem):
    assert problem in check_research_list(data)


def wait_for_run(client) -> str:
    for _ in range(200):
        page = client.get("/run").text
        if "Run finished" in page or "The run stopped" in page:
            return page
        time.sleep(0.05)
    raise AssertionError("the run never finished")


def test_an_uploaded_list_runs_through_the_same_steps(settings, monkeypatch):
    monkeypatch.setattr(gates, "mail_servers", lambda domain: [f"mx1.{domain}"])  # no real lookups in tests
    client = TestClient(create_app(replace(settings), llm=TemplateLLM()))
    upload = f"{','.join(FIELDS)}\n{ROW}\n".encode()
    client.post("/upload", files={"file": ("my list.csv", upload, "text/csv")}, data={"fresh": "yes"})

    assert "Research list: 1 row read, 1 new doctor, 0 duplicates merged." in wait_for_run(client)
    conn = connect(settings.db_path)
    try:
        sam = dict(conn.execute("SELECT * FROM leads WHERE full_name = 'Sam Okoro'").fetchone())
        others = conn.execute("SELECT COUNT(*) FROM leads WHERE full_name != 'Sam Okoro'").fetchone()[0]
    finally:
        conn.close()
    # Fit the campaign, real domain with a mail server, so a person confirms the mailbox.
    assert (sam["status"], sam["stage"], others) == (Status.NEEDS_REVIEW, "email_verified", 0)
    assert "no free check can confirm" in sam["reason"]
    assert len(list(settings.uploads_dir.glob("*-my-list.csv"))) == 1


def test_the_live_site_refuses_files_and_runs_the_sample_list_instead(settings, tmp_path):
    online = replace(settings, demo_online=True, snapshot_path=tmp_path / "no-snapshot.db")
    client = TestClient(create_app(online, llm=TemplateLLM()))
    page = client.post("/upload", files={"file": ("list.csv", template_csv().encode(), "text/csv")}).text
    assert "works on the laptop copy only" in page and 'action="/upload/sample"' in page

    client.post("/upload/sample")
    conn = connect(online.db_path)
    try:
        rows = {r["full_name"]: dict(r) for r in conn.execute(
            "SELECT full_name, status, reason FROM leads WHERE status != 'DUPLICATE'")}
        duplicates = conn.execute("SELECT COUNT(*) FROM leads WHERE status = 'DUPLICATE'").fetchone()[0]
    finally:
        conn.close()
    # The same steps as an upload: made-up domains need a person to confirm the mailbox, as real ones do.
    assert rows["Sam Example"]["status"] == Status.NEEDS_REVIEW and "made-up domain" in rows["Sam Example"]["reason"]
    assert rows["Jo Sample"]["status"] == Status.NEEDS_REVIEW
    assert rows["Lee Placeholder"]["status"] == Status.DISQUALIFIED  # dermatology isn't targeted
    assert rows["Ana Demo"]["status"] == Status.EMAIL_INVALID  # info@ is a shared inbox
    assert duplicates == 1  # Sam Example appears twice in the list


def test_the_template_downloads(settings):
    response = TestClient(create_app(replace(settings), llm=TemplateLLM())).get("/template.csv")
    assert response.headers["content-disposition"].endswith('filename="research-list-template.csv"')
    assert response.content.decode("utf-8-sig").startswith(",".join(FIELDS))


# --- real addresses only ever go to the demo inbox ------------------------------------

def approve_with_a_real_address(conn, services, campaign) -> int:
    run_pipeline(conn, services, SEED / "leads_raw.csv")
    draft = conn.execute("""SELECT d.* FROM drafts d JOIN leads l ON l.id = d.lead_id
                            WHERE l.full_name = 'Priya Raman' AND d.status = 'PENDING_APPROVAL'""").fetchone()
    approve(conn, draft["id"], "Lat", campaign)
    conn.execute("UPDATE leads SET email = 'priya.raman@realtrust.org.uk' WHERE id = ?", (draft["lead_id"],))
    return draft["lead_id"]


def test_without_the_demo_inbox_a_real_address_is_blocked(conn, services, campaign, settings):
    lead_id = approve_with_a_real_address(conn, services, campaign)
    summary = send_approved(conn, campaign, OutboxSender(settings.outbox_dir), SAFE, today=TODAY)
    assert summary.blocked == 1 and "unless every email goes to the demo inbox" in get_lead(conn, lead_id)["reason"]


def test_with_the_demo_inbox_a_real_address_is_sent_to_you_instead(conn, services, campaign, settings):
    approve_with_a_real_address(conn, services, campaign)
    sender = InboxRedirect(OutboxSender(settings.outbox_dir), "lat@inbox.test")
    assert send_approved(conn, campaign, sender, SAFE, today=TODAY).sent == 1
    saved = email.message_from_bytes(next(settings.outbox_dir.glob("*.eml")).read_bytes(), policy=email_policy)
    assert saved["To"] == "lat@inbox.test" and "priya.raman@realtrust.org.uk" in saved["X-Original-To"]
