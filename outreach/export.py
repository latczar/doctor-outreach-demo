"""The final list for the team: CSV to download, or tab-separated text to paste into Google Sheets or Excel."""

from __future__ import annotations

import csv
import io
import sqlite3

from .db import loads
from .models import STATUS_LABELS, Status, on_final_list

COLUMNS = [
    "Name", "Grade", "Specialty", "Employer", "Country", "Email", "Email found by",
    "Why they qualified", "Email check", "History check", "Status", "Reason", "Approved by", "Sent at",
]


def _rows(conn: sqlite3.Connection, leads: list[dict]) -> list[list[str]]:
    rows = []
    for lead in leads:
        gates = loads(lead["gate_results"], {})
        approval = conn.execute(
            "SELECT reviewer FROM drafts WHERE lead_id = ? AND status IN ('APPROVED', 'SENT') ORDER BY id DESC LIMIT 1",
            (lead["id"],),
        ).fetchone()
        sent = conn.execute(
            "SELECT sent_at FROM outreach_log WHERE lead_id = ? AND status = 'sent'", (lead["id"],)
        ).fetchone()
        rows.append([
            lead["full_name"], lead["grade"], lead["specialty"], lead["employer"], lead["country"], lead["email"],
            {"provided": "research list", "pattern_guess": "pattern guess", "person": "added by a person"}.get(
                lead["email_origin"] or "", ""),
            gates.get("qualify", {}).get("reason", ""),
            gates.get("email_verified", {}).get("reason", ""),
            gates.get("not_contacted", {}).get("reason", ""),
            STATUS_LABELS[Status(lead["status"])],
            lead["reason"],
            approval["reviewer"] if approval else "",
            sent["sent_at"] if sent else "",
        ])
    return rows


def select_leads(conn: sqlite3.Connection, campaign_id: str, which: str = "final") -> list[dict]:
    """'final' = cleared to contact (on the final list). 'all' = every unique doctor."""
    leads = [dict(r) for r in conn.execute(
        "SELECT * FROM leads WHERE campaign_id = ? AND status != 'DUPLICATE' ORDER BY id", (campaign_id,))]
    return [l for l in leads if which == "all" or on_final_list(l["status"])]


def export_csv(conn: sqlite3.Connection, campaign_id: str, which: str = "final",
               leads: list[dict] | None = None) -> str:
    """CSV of the given doctors (for example, the filtered list on screen), or of `which` when none are given."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    writer.writerows(_rows(conn, leads if leads is not None else select_leads(conn, campaign_id, which)))
    return out.getvalue()


def export_tsv(conn: sqlite3.Connection, leads: list[dict]) -> str:
    """Tab-separated text. Pasted into cell A1 of a Google Sheet or Excel, each value lands in its own column."""
    clean = lambda value: " ".join(str(value or "").split())  # no tabs or line breaks inside a cell
    lines = ["\t".join(COLUMNS)]
    lines += ["\t".join(clean(v) for v in row) for row in _rows(conn, leads)]
    return "\n".join(lines) + "\n"
