"""The online copy: a fresh server starts from a snapshot, and runs finish before the page loads."""

from dataclasses import replace

from fastapi.testclient import TestClient

from outreach.db import connect
from outreach.llm.fake import TemplateLLM
from outreach.pipeline import reset_data, run_pipeline
from outreach.web.app import create_app

from .conftest import SEED


def test_a_fresh_online_server_starts_from_the_snapshot(settings, services, tmp_path):
    snapshot = tmp_path / "snapshot.db"
    snap = connect(snapshot)
    reset_data(snap, SEED)
    run_pipeline(snap, services, SEED / "leads_raw.csv")
    snap.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    snap.close()

    online = replace(settings, db_path=tmp_path / "online" / "outreach.db", demo_online=True, snapshot_path=snapshot)
    client = TestClient(create_app(online, llm=TemplateLLM()))
    page = client.get("/").text
    assert "Online prototype with made-up data" in page
    assert "19 of 24 doctors fit the campaign" in page

    client.post("/reset")
    finished = client.post("/run")  # follows the redirect to the Run page
    assert "Run finished" in finished.text and "Qualified doctor: 19 of 24" in finished.text


def test_the_local_app_shows_no_online_banner(settings, conn):
    client = TestClient(create_app(replace(settings), llm=TemplateLLM()))
    assert "Online prototype" not in client.get("/").text
