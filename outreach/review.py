"""Step 6: a named person approves, edits, rejects or asks for another draft."""

from __future__ import annotations

import json
import sqlite3

from .db import audit, get_lead, now_iso, transaction
from .drafting import DraftOutcome, Prompts, draft_context, draft_for_lead
from .guardrails import run_checks
from .llm import LLM
from .models import Campaign, Status
from .normalise import clean


class ReviewError(Exception):
    """Shown to the reviewer as-is."""


# Only the newest draft for a doctor can be acted on, and only while it's still waiting.
OPEN_AND_LATEST = """
    id = ? AND status IN ('PENDING_APPROVAL', 'FAILED_CHECKS')
    AND NOT EXISTS (SELECT 1 FROM drafts AS newer WHERE newer.lead_id = drafts.lead_id AND newer.id > drafts.id)
"""


def _reviewer(name: str | None) -> str:
    name = clean(name)
    if not name:
        raise ReviewError("Enter your name, so the decision is recorded against it.")
    return name[:60]


def get_draft(conn: sqlite3.Connection, draft_id: int) -> dict:
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise ReviewError(f"Draft #{draft_id} doesn't exist.")
    return dict(row)


def approve(
    conn: sqlite3.Connection,
    draft_id: int,
    reviewer: str,
    campaign: Campaign,
    subject: str | None = None,
    body: str | None = None,
) -> None:
    reviewer = _reviewer(reviewer)
    draft = get_draft(conn, draft_id)
    lead = get_lead(conn, draft["lead_id"])
    final_subject = subject.strip() if subject is not None else draft["subject"] or ""
    final_body = body.replace("\r\n", "\n").strip() if body is not None else draft["body"] or ""
    edited = final_subject != (draft["subject"] or "") or final_body != (draft["body"] or "")

    # The same checks run on a person's edits, so nobody can send a placeholder by accident either.
    checks = run_checks(
        final_subject, final_body, draft["personal_detail_used"] or "(written by reviewer)",
        draft_context(lead, campaign),
    )
    failures = [c.detail for c in checks if not c.ok]
    if failures:
        raise ReviewError("This version still fails our checks: " + " ".join(failures))

    with transaction(conn):
        cursor = conn.execute(
            f"""UPDATE drafts SET status = 'APPROVED', subject = ?, body = ?, checks = ?,
                   edited_by_reviewer = ?, reviewer = ?, reviewed_at = ?
                WHERE {OPEN_AND_LATEST}""",
            (final_subject, final_body, json.dumps([c.as_dict() for c in checks]), int(edited),
             reviewer, now_iso(), draft_id),
        )
        if cursor.rowcount != 1:
            raise ReviewError("This draft has already been reviewed or replaced. Refresh the page.")
        cursor = conn.execute(
            """UPDATE leads SET status = 'APPROVED', stage = 'approval', reason = ?, updated_at = ?
               WHERE id = ? AND status IN ('PENDING_APPROVAL', 'DRAFT_FAILED')""",
            (f"Approved by {reviewer}{' after editing' if edited else ''}. Ready to send.", now_iso(),
             lead["id"]),
        )
        if cursor.rowcount != 1:
            raise ReviewError("This doctor isn't waiting for approval any more. Refresh the page.")
        audit(conn, "approval.approved", lead_id=lead["id"], actor=f"reviewer:{reviewer}",
              detail={"draft_id": draft_id, "edited": edited})


def reject(conn: sqlite3.Connection, draft_id: int, reviewer: str, note: str) -> None:
    reviewer = _reviewer(reviewer)
    note = clean(note)
    if not note:
        raise ReviewError("Add a short reason for rejecting, so the team can learn from it.")
    draft = get_draft(conn, draft_id)
    with transaction(conn):
        cursor = conn.execute(
            f"""UPDATE drafts SET status = 'REJECTED', reviewer = ?, review_note = ?, reviewed_at = ?
                WHERE {OPEN_AND_LATEST}""",
            (reviewer, note, now_iso(), draft_id),
        )
        if cursor.rowcount != 1:
            raise ReviewError("This draft has already been reviewed or replaced. Refresh the page.")
        conn.execute(
            "UPDATE leads SET status = 'REJECTED', stage = 'approval', reason = ?, updated_at = ? WHERE id = ?",
            (f"Rejected by {reviewer}: {note}", now_iso(), draft["lead_id"]),
        )
        audit(conn, "approval.rejected", lead_id=draft["lead_id"], actor=f"reviewer:{reviewer}",
              detail={"draft_id": draft_id, "note": note})


def regenerate(
    conn: sqlite3.Connection,
    draft_id: int,
    reviewer: str,
    instruction: str | None,
    campaign: Campaign,
    llm: LLM,
    prompts: Prompts,
) -> DraftOutcome:
    reviewer = _reviewer(reviewer)
    instruction = clean(instruction) or None
    draft = get_draft(conn, draft_id)
    with transaction(conn):
        cursor = conn.execute(
            f"""UPDATE drafts SET status = 'SUPERSEDED', reviewer = ?, review_note = ?, reviewed_at = ?
                WHERE {OPEN_AND_LATEST}""",
            (reviewer, instruction, now_iso(), draft_id),
        )
        if cursor.rowcount != 1:
            raise ReviewError("This draft has already been reviewed or replaced. Refresh the page.")
        conn.execute(
            "UPDATE leads SET status = ?, stage = 'draft', reason = ?, updated_at = ? WHERE id = ?",
            (Status.READY_TO_DRAFT, f"{reviewer} asked for a new draft.", now_iso(), draft["lead_id"]),
        )
        audit(conn, "approval.regenerate", lead_id=draft["lead_id"], actor=f"reviewer:{reviewer}",
              detail={"draft_id": draft_id, "instruction": instruction})
    lead = get_lead(conn, draft["lead_id"])
    return draft_for_lead(
        conn, lead, campaign, llm, prompts,
        reviewer_instruction=instruction, actor=f"reviewer:{reviewer}",
    )
