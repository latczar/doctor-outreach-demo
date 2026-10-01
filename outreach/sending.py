"""Steps 7 and 8: send approved emails at most once each, then log them."""

from __future__ import annotations

import smtplib
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Protocol

from .config import Settings
from .db import audit, get_lead, now_iso, transaction, update_lead
from .gates import check_history
from .models import Campaign, Status
from .notify import Alert, Notifier, raise_alert


class EmailSender(Protocol):
    name: str

    def send(self, message: EmailMessage) -> None: ...


class OutboxSender:
    """Writes each email to a folder as an .eml file. Open one in Outlook or Thunderbird to see it."""

    name = "outbox"

    def __init__(self, folder: Path):
        self.folder = folder

    def send(self, message: EmailMessage) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_id = "".join(ch for ch in message["Message-ID"] if ch.isalnum())[:24]
        (self.folder / f"{stamp}-{safe_id}.eml").write_bytes(bytes(message))


class SmtpSender:
    """Real SMTP, for example Mailpit on localhost:1025 (web inbox on :8025)."""

    name = "smtp"

    def __init__(self, host: str, port: int):
        self.host, self.port = host, port

    def send(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
            smtp.send_message(message)


def make_sender(settings: Settings) -> EmailSender:
    if settings.email_sender == "smtp":
        return SmtpSender(settings.smtp_host, settings.smtp_port)
    return OutboxSender(settings.outbox_dir)


def build_message(campaign: Campaign, lead: dict, draft: dict) -> EmailMessage:
    sender = campaign.sender
    message = EmailMessage()
    message["From"] = formataddr((sender.name, sender.email))
    message["To"] = formataddr((lead["full_name"], lead["email"]))
    message["Subject"] = draft["subject"]
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=sender.email.split("@")[1])
    message["List-Unsubscribe"] = f"<mailto:{sender.email}?subject=unsubscribe>"
    message["X-Campaign"] = campaign.id
    message.set_content(f"{draft['body']}\n\n{draft['footer']}\n")
    return message


@dataclass
class SendSummary:
    sent: int = 0
    blocked: int = 0
    failed: int = 0
    deferred: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [f"Sent {self.sent}"]
        for label, value in (("blocked", self.blocked), ("failed", self.failed),
                             ("held for tomorrow (daily cap)", self.deferred),
                             ("skipped (already sent)", self.skipped)):
            if value:
                parts.append(f"{value} {label}")
        return ", ".join(parts) + "."


def _block(conn, lead_id: int, reason: str, summary: SendSummary, actor: str) -> None:
    with transaction(conn):
        update_lead(conn, lead_id, status=Status.SEND_BLOCKED, stage="send", reason=reason)
        audit(conn, "send.blocked", lead_id=lead_id, actor=actor, detail={"reason": reason})
    summary.blocked += 1


def send_approved(
    conn: sqlite3.Connection,
    campaign: Campaign,
    sender: EmailSender,
    allowed_suffixes: tuple[str, ...],
    today: date | None = None,
    actor: str = "system",
    notifier: Notifier | None = None,
) -> SendSummary:
    today = today or datetime.now(timezone.utc).date()
    summary = SendSummary()
    sent_today = conn.execute(
        "SELECT COUNT(*) FROM outreach_log WHERE campaign_id = ? AND status = 'sent' AND substr(sent_at, 1, 10) = ?",
        (campaign.id, today.isoformat()),
    ).fetchone()[0]

    queue = conn.execute(
        """SELECT d.* FROM drafts d JOIN leads l ON l.id = d.lead_id
           WHERE d.status = 'APPROVED' AND l.campaign_id = ? AND l.status IN ('APPROVED', 'SEND_FAILED')
           ORDER BY d.reviewed_at, d.id""",
        (campaign.id,),
    ).fetchall()

    for row in queue:
        draft = dict(row)
        lead = get_lead(conn, draft["lead_id"])

        if sent_today >= campaign.daily_send_cap:
            summary.deferred += 1
            continue

        # 1. Demo safety: never email a real domain from this prototype.
        if not lead["email"].endswith(allowed_suffixes):
            _block(conn, lead["id"], f"Safety guard: this demo only sends to {', '.join(allowed_suffixes)} addresses.",
                   summary, actor)
            continue

        # 2. Check again right before sending. Someone may have opted out since approval.
        fresh = check_history(conn, lead, campaign, today)
        if not fresh.passed:
            _block(conn, lead["id"], f"Stopped at send time: {fresh.reason}", summary, actor)
            continue

        # 3. Claim the send before doing it. The unique key means a second attempt finds the claim and stops.
        key = f"{campaign.id}:{lead['id']}"
        claimed = conn.execute(
            """INSERT INTO outreach_log (campaign_id, lead_id, full_name, email, reg_number, status, idempotency_key, sent_at)
               VALUES (?, ?, ?, ?, ?, 'sending', ?, ?)
               ON CONFLICT (idempotency_key) DO UPDATE SET status = 'sending', error = NULL, sent_at = excluded.sent_at
               WHERE outreach_log.status = 'failed'""",
            (campaign.id, lead["id"], lead["full_name"], lead["email"], lead["reg_number"] or None, key, now_iso()),
        ).rowcount
        if claimed == 0:
            summary.skipped += 1
            audit(conn, "send.skipped", lead_id=lead["id"], actor=actor,
                  detail={"reason": "already sent, or a send is in progress", "key": key})
            continue

        # 4. Send.
        message = build_message(campaign, lead, draft)
        try:
            sender.send(message)
        except (OSError, smtplib.SMTPException) as exc:
            with transaction(conn):
                conn.execute("UPDATE outreach_log SET status = 'failed', error = ? WHERE idempotency_key = ?",
                             (str(exc), key))
                update_lead(conn, lead["id"], status=Status.SEND_FAILED, stage="send",
                            reason=f"Sending failed ({exc}). It will be retried next time you click Send.")
                audit(conn, "send.failed", lead_id=lead["id"], actor=actor, detail={"error": str(exc)})
            if notifier:
                raise_alert(conn, notifier, Alert(
                    "send_failed",
                    f"An approved email to {lead['full_name']} failed to send ({exc}). "
                    "It will be retried next time someone clicks Send approved.",
                    lead["id"]))
            summary.failed += 1
            continue

        # 5. Log it. If the program died just before this, the row stays 'sending' and is never resent.
        with transaction(conn):
            conn.execute(
                "UPDATE outreach_log SET status = 'sent', message_id = ?, sent_at = ? WHERE idempotency_key = ?",
                (message["Message-ID"], now_iso(), key),
            )
            conn.execute("UPDATE drafts SET status = 'SENT' WHERE id = ?", (draft["id"],))
            update_lead(conn, lead["id"], status=Status.SENT, stage="send",
                        reason=f"Sent on {today} via {sender.name}. Logged for future campaigns.")
            audit(conn, "send.sent", lead_id=lead["id"], actor=actor,
                  detail={"message_id": message["Message-ID"], "via": sender.name})
        sent_today += 1
        summary.sent += 1

    return summary
