"""Urgent alerts: one hook for the team's channel, and a failed alert never stops the work."""

import json
import smtplib

from outreach.notify import Alert, FileNotifier, WebhookNotifier, raise_alert
from outreach.pipeline import run_pipeline
from outreach.review import approve
from outreach.sending import send_approved

from .conftest import SEED, TODAY


def alerts(conn) -> list[dict]:
    return [json.loads(r["detail"]) for r in conn.execute("SELECT detail FROM audit_log WHERE event = 'alert.raised'")]


def test_file_notifier_writes_one_line_per_alert(conn, tmp_path):
    notifier = FileNotifier(tmp_path / "alerts.log")
    assert raise_alert(conn, notifier, Alert("send_failed", "An email failed to send."))
    assert "[send_failed]  An email failed to send." in (tmp_path / "alerts.log").read_text(encoding="utf-8")
    assert alerts(conn)[0]["delivered"] is True


def test_an_unreachable_webhook_is_logged_and_never_raises(conn):
    notifier = WebhookNotifier("http://127.0.0.1:9/hook", timeout=1.0)  # nothing listens on port 9
    assert raise_alert(conn, notifier, Alert("send_failed", "An email failed to send.")) is False
    recorded = alerts(conn)[0]
    assert recorded["delivered"] is False and recorded["error"]


def test_a_profile_that_gives_the_ai_orders_raises_one_alert(conn, services, tmp_path):
    services.notifier = FileNotifier(tmp_path / "alerts.log")
    run_pipeline(conn, services, SEED / "leads_raw.csv")
    run_pipeline(conn, services, SEED / "leads_raw.csv")  # a second run doesn't repeat it
    raised = alerts(conn)
    assert len(raised) == 1 and raised[0]["kind"] == "suspicious_profile"
    assert "Mohammed Iqbal" in raised[0]["text"]


class BrokenSender:
    name = "broken"

    def send(self, message):
        raise smtplib.SMTPServerDisconnected("connection lost")


def test_a_failed_send_raises_an_alert(conn, services, campaign, tmp_path):
    run_pipeline(conn, services, SEED / "leads_raw.csv")
    draft = conn.execute(
        "SELECT d.id FROM drafts d JOIN leads l ON l.id = d.lead_id WHERE l.full_name = 'Priya Raman'").fetchone()
    approve(conn, draft["id"], "Lat", campaign)
    send_approved(conn, campaign, BrokenSender(), (".example",), today=TODAY,
                  notifier=FileNotifier(tmp_path / "alerts.log"))
    assert [a["kind"] for a in alerts(conn)] == ["send_failed"]
    assert "Priya Raman" in alerts(conn)[0]["text"]
