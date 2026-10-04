"""The replay page: the last run, rebuilt from the audit log and played back without running anything."""

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from outreach.llm.fake import TemplateLLM
from outreach.pipeline import run_pipeline
from outreach.review import regenerate
from outreach.web.app import create_app
from outreach.web.replay import build_replay, by_hand, duration

from .conftest import SEED


def test_the_replay_rebuilds_the_run_from_the_log(conn, services, campaign, prompts):
    run_pipeline(conn, services, SEED / "leads_raw.csv")
    replay = build_replay(conn, campaign, prompts)

    assert [step["left"] for step in replay["steps"]] == [27, 24, 19, 18, 14, 11]
    trays = {step["key"]: [tray["text"] for tray in step["trays"]] for step in replay["steps"]}
    assert trays["dedupe"] == ["3 duplicates merged"]
    assert "3 don't fit the campaign" in trays["qualify"] and "2 need you to check" in trays["qualify"]
    assert len(replay["cards"]) == 11

    mohammed = next(card for card in replay["cards"] if card["name"] == "Mohammed Iqbal")
    assert mohammed["injection"] and mohammed["obeyed"] and not mohammed["passed"]
    assert {"made-up money", "a partnership claim"} <= set(mohammed["attempts"][0]["failed"])
    assert replay["by_hand"]["text"] == "2 to 4 hours"
    assert "No made-up money" in replay["example"]["checks"]


def test_the_log_records_why_each_doctor_stopped(conn, services):
    run_pipeline(conn, services, SEED / "leads_raw.csv")
    statuses = {json.loads(r["detail"])["status"] for r in conn.execute(
        "SELECT detail FROM audit_log WHERE event = 'qualify.stopped'")}
    assert statuses == {"DISQUALIFIED", "NEEDS_REVIEW"}


def test_what_people_do_after_the_run_is_not_part_of_the_replay(conn, services, campaign, prompts):
    run_pipeline(conn, services, SEED / "leads_raw.csv")
    before = build_replay(conn, campaign, prompts)
    draft = conn.execute("""SELECT d.id FROM drafts d JOIN leads l ON l.id = d.lead_id
                            WHERE l.full_name = 'Tom Hargreaves'""").fetchone()
    regenerate(conn, draft["id"], "Lat", "Make it shorter", campaign, services.llm, prompts)

    after = build_replay(conn, campaign, prompts)
    assert (after["tries"], len(after["cards"])) == (before["tries"], len(before["cards"]))


def test_times_read_as_plain_words():
    assert (duration(0.4), duration(7), duration(124), duration(180)) == ("under a second", "7 s", "2 min 4 s", "3 min")
    assert by_hand(24)["text"] == "2 to 4 hours" and by_hand(9)["text"] == "0.8 to 1.5 hours"


def test_the_replay_page_plays_back_and_never_runs_anything(settings, conn, services):
    client = TestClient(create_app(replace(settings), llm=TemplateLLM()))
    assert "No run to replay yet" in client.get("/replay").text

    run_pipeline(conn, services, SEED / "leads_raw.csv")
    page = client.get("/replay").text
    assert "Replay: one run of the workflow" in page and "Final list: 11 doctors passed every rule." in page
    assert "2 to 4 hours" in page and "Its profile hides an instruction" in page
    assert "&lt;doctor_profile&gt;" in page  # Show prompt holds the real prompt, fenced data and all
    # It only reads: no form on the page can run the pipeline or send anything.
    assert 'action="/run"' not in page and 'action="/send"' not in page
    assert " AI " not in page
    assert 'href="/replay"' in client.get("/").text
