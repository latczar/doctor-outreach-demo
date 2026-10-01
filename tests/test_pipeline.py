import time
from dataclasses import replace

from fastapi.testclient import TestClient

from outreach.export import export_csv
from outreach.llm.fake import TemplateLLM
from outreach.pipeline import funnel, run_pipeline
from outreach.web.app import create_app

from .conftest import SEED


def statuses(conn) -> dict:
    rows = conn.execute("SELECT full_name, status FROM leads WHERE status != 'DUPLICATE'").fetchall()
    return {r["full_name"] or "(no name)": r["status"] for r in rows}


def test_full_run_on_the_seed_data(conn, services):
    run_pipeline(conn, services, SEED / "leads_raw.csv")

    view = funnel(conn, services.campaign.id)
    assert (view["rows"], view["unique"], view["duplicates"]) == (27, 24, 3)
    assert {step["label"]: step["count"] for step in view["steps"]} == {
        "Qualified doctor": 19,
        "Email available?": 18,
        "Email verified?": 14,
        "Not previously contacted?": 11,
        "Personalised email": 10,
        "Human approval": 0,
        "Send": 0,
        "Log outreach": 0,
    }
    approval = next(s for s in view["steps"] if s["key"] == "approval")
    assert approval["waiting"] == {"PENDING_APPROVAL": 10} and approval["stopped"] == {}
    assert approval["summary"] == "0 of 10 emails approved by a person"
    assert view["steps"][0]["summary"] == "19 of 24 doctors fit the campaign"
    assert view["final_list"] == 11

    s = statuses(conn)
    assert s["Emily Chen"] == "DISQUALIFIED"
    assert s["Isla McKenzie"] == "NEEDS_REVIEW"
    assert s["Rob Fielding"] == "NO_EMAIL"
    assert s["Daniel Price"] == "PENDING_APPROVAL"  # email guessed, then verified
    assert s["Hannah Lewis"] == "NEEDS_REVIEW"  # catch-all domain
    assert s["Grace Adeyemi"] == "EMAIL_INVALID"  # info@ inbox
    assert s["Ben Carter"] == "ALREADY_CONTACTED"
    assert s["Rachel Moore"] == "ALREADY_CONTACTED"  # matched on registration number
    assert s["Laura Simmons"] == "SUPPRESSED"
    assert s["Kwame Asante"] == "PENDING_APPROVAL"  # contacted, but outside the cooldown
    assert s["Mohammed Iqbal"] == "DRAFT_FAILED"  # prompt injection blocked


def test_running_twice_changes_nothing(conn, services):
    run_pipeline(conn, services, SEED / "leads_raw.csv")
    before = conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
    run_pipeline(conn, services, SEED / "leads_raw.csv")
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == before


def test_greeting_uses_mr_for_a_uk_surgeon(conn, services):
    run_pipeline(conn, services, SEED / "leads_raw.csv")
    body = conn.execute(
        "SELECT body FROM drafts d JOIN leads l ON l.id = d.lead_id WHERE l.full_name = 'Daniel Price'"
    ).fetchone()["body"]
    assert body.startswith("Dear Mr Price,")


def test_export_contains_only_doctors_who_passed_every_check(conn, services):
    run_pipeline(conn, services, SEED / "leads_raw.csv")
    lines = export_csv(conn, services.campaign.id, "final").strip().splitlines()
    assert len(lines) == 1 + 11
    assert "Emily Chen" not in "\n".join(lines)


def test_web_review_flow_and_double_approval_message(settings, conn, services):
    run_pipeline(conn, services, SEED / "leads_raw.csv")
    client = TestClient(create_app(replace(settings), llm=TemplateLLM()))
    client.post("/reviewer", data={"reviewer": "Lat"})
    assert "Reviewing as" in client.get("/review").text

    draft = conn.execute(
        """SELECT d.id, d.subject, d.body FROM drafts d JOIN leads l ON l.id = d.lead_id
           WHERE l.full_name = 'Priya Raman'"""
    ).fetchone()
    form = {"reviewer": "Lat", "subject": draft["subject"], "body": draft["body"]}
    assert client.post(f"/drafts/{draft['id']}/approve", data=form, follow_redirects=False).status_code == 303
    again = client.post(f"/drafts/{draft['id']}/approve", data=form)
    assert "already been reviewed" in again.text

    sent = client.post("/send")  # follows the redirect to /log, which shows the result once
    assert "Sent 1." in sent.text
    assert len(list(settings.outbox_dir.glob("*.eml"))) == 1


def test_web_pages_render(settings, conn):
    client = TestClient(create_app(replace(settings), llm=TemplateLLM()))
    assert client.get("/").status_code == 200
    client.post("/run")
    # The run happens in a background thread; wait until the Run page says it finished.
    for _ in range(200):
        if "Run finished" in client.get("/run").text:
            break
        time.sleep(0.05)
    assert "Waiting for you" in client.get("/review").text
    lead_id = conn.execute("SELECT id FROM leads WHERE full_name = 'Priya Raman'").fetchone()[0]
    assert "Journey through the workflow" in client.get(f"/leads/{lead_id}").text
    assert client.get("/log").status_code == 200
    assert client.get("/export/final.csv").headers["content-type"].startswith("text/csv")
