"""Urgent alerts for the team, through one hook.

The app doesn't know about WhatsApp, Slack or Teams. It posts each alert to one webhook, and a tool such as
n8n or Zapier forwards it to whichever channel the team uses. With no webhook set, alerts go to a file.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx

from .config import Settings
from .db import audit


@dataclass
class Alert:
    kind: str  # suspicious_profile | send_failed
    text: str
    lead_id: int | None = None


class Notifier(Protocol):
    name: str

    def send(self, alert: Alert) -> None: ...


class FileNotifier:
    """The local stand-in: one line per alert in outbox/alerts.log."""

    name = "alerts file"

    def __init__(self, path: Path):
        self.path = path

    def send(self, alert: Alert) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}  [{alert.kind}]  {alert.text}\n")


class WebhookNotifier:
    """Posts the alert as JSON. n8n or Zapier picks it up and forwards it to WhatsApp, Slack or Teams."""

    name = "webhook"

    def __init__(self, url: str, timeout: float = 5.0):
        self.url, self.timeout = url, timeout

    def send(self, alert: Alert) -> None:
        response = httpx.post(self.url, json=asdict(alert), timeout=self.timeout)
        response.raise_for_status()


def make_notifier(settings: Settings) -> Notifier:
    if settings.alert_webhook_url:
        return WebhookNotifier(settings.alert_webhook_url)
    return FileNotifier(settings.outbox_dir / "alerts.log")


def already_alerted(conn: sqlite3.Connection, kind: str, lead_id: int) -> bool:
    rows = conn.execute(
        "SELECT detail FROM audit_log WHERE event = 'alert.raised' AND lead_id = ?", (lead_id,)
    ).fetchall()
    return any(json.loads(r["detail"]).get("kind") == kind for r in rows)


def raise_alert(conn: sqlite3.Connection, notifier: Notifier, alert: Alert) -> bool:
    """Send the alert and record it. A failed alert is logged, never allowed to stop the pipeline."""
    try:
        notifier.send(alert)
        delivered, error = True, None
    except (httpx.HTTPError, OSError) as exc:
        delivered, error = False, str(exc)
    audit(conn, "alert.raised", lead_id=alert.lead_id,
          detail={"kind": alert.kind, "text": alert.text, "channel": notifier.name,
                  "delivered": delivered, "error": error})
    return delivered
