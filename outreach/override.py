"""A person decides what the rules couldn't.

The person never skips the rules. They either supply the missing fact (a name, a grade, a better email) or
confirm what a computer can't check (a mailbox on a catch-all domain), and the rules then run again from the
top. Or they take the doctor off the list. Either way their name and note go in the history.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .db import audit, get_lead, loads, now_iso, transaction, update_lead
from .drafting import draft_for_lead
from .models import Status
from .normalise import CANONICAL_GRADES, clean, normalise_email, split_name
from .pipeline import Services, run_gates


class OverrideError(Exception):
    """Shown to the person as-is."""


@dataclass
class OverrideOutcome:
    status: Status
    message: str  # what happened, in plain English


def _waiting_lead(conn: sqlite3.Connection, lead_id: int, reviewer: str, note: str) -> tuple[dict, str, str]:
    reviewer, note = clean(reviewer)[:60], clean(note)
    if not reviewer:
        raise OverrideError("Enter your name, so the decision is recorded against it.")
    if not note:
        raise OverrideError("Add a short note on what you checked, so the team can see why.")
    lead = get_lead(conn, lead_id)
    if lead is None:
        raise OverrideError("That doctor doesn't exist.")
    if lead["status"] != Status.NEEDS_REVIEW:
        raise OverrideError("This doctor isn't waiting for a decision any more. Refresh the page.")
    return lead, reviewer, note


def recheck(
    conn: sqlite3.Connection,
    lead_id: int,
    services: Services,
    reviewer: str,
    note: str,
    *,
    title: str | None = None,
    full_name: str | None = None,
    grade: str | None = None,
    email: str | None = None,
    confirm_email: bool = False,
) -> OverrideOutcome:
    """Apply the person's corrections, then run the rules again from the top."""
    lead, reviewer, note = _waiting_lead(conn, lead_id, reviewer, note)

    changes = {}
    if clean(title) and clean(title) != (lead["title"] or ""):
        changes["title"] = clean(title)
    if clean(full_name) and clean(full_name) != (lead["full_name"] or ""):
        first, last = split_name(full_name)
        changes.update(full_name=clean(full_name), first_name=first, last_name=last)
    if grade and grade != lead["grade"]:
        if grade not in CANONICAL_GRADES:
            raise OverrideError("Pick a grade from the list.")
        changes["grade"] = grade
    if normalise_email(email) and normalise_email(email) != (lead["email"] or ""):
        changes.update(email=normalise_email(email), email_origin="person")
    confirmed_by = f"{reviewer} ({note})" if confirm_email else None
    if not changes and not confirmed_by:
        raise OverrideError("Nothing changed. Correct a detail or confirm the email, or remove the doctor instead.")

    with transaction(conn):
        # Only while it's still waiting, so a double click or a second person can't run it twice.
        claimed = conn.execute(
            "UPDATE leads SET status = 'NEW', stage = 'dedupe', updated_at = ? WHERE id = ? AND status = 'NEEDS_REVIEW'",
            (now_iso(), lead_id),
        ).rowcount
        if claimed != 1:
            raise OverrideError("This doctor isn't waiting for a decision any more. Refresh the page.")
        if changes:
            update_lead(conn, lead_id, **changes)
        shown = {k: v for k, v in changes.items() if k not in ("first_name", "last_name", "email_origin")}
        audit(conn, "override.recheck", lead_id=lead_id, actor=f"reviewer:{reviewer}",
              detail={"note": note, "changes": shown, "confirmed_email": bool(confirmed_by)})

    # The rules decide again, with the person's corrections and confirmation.
    ready = run_gates(conn, get_lead(conn, lead_id), services, email_confirmed_by=confirmed_by)
    lead = get_lead(conn, lead_id)
    if not ready:
        return OverrideOutcome(Status(lead["status"]), f"The rules ran again with your changes. {lead['reason']}")

    outcome = draft_for_lead(conn, lead, services.campaign, services.llm, services.prompts,
                             actor=f"reviewer:{reviewer}")
    if outcome.status == Status.PENDING_APPROVAL:
        return OverrideOutcome(outcome.status,
                               "Now on the final list. The AI wrote an email, and it's waiting for review.")
    return OverrideOutcome(outcome.status,
                           "Now on the final list, but the AI's email failed our checks, so it needs fixing in Review.")


def remove(conn: sqlite3.Connection, lead_id: int, reviewer: str, note: str) -> None:
    """Take the doctor off the list, at the step where they were waiting, with the person's reason."""
    lead, reviewer, note = _waiting_lead(conn, lead_id, reviewer, note)
    status = Status.DISQUALIFIED if lead["stage"] == "qualify" else Status.EMAIL_INVALID
    reason = f"Removed by {reviewer}: {note}"
    gates = loads(lead["gate_results"], {})
    gates[lead["stage"]] = {"passed": False, "reason": reason, "evidence": {"removed_by": reviewer}}
    with transaction(conn):
        claimed = conn.execute(
            "UPDATE leads SET status = ?, reason = ?, gate_results = ?, updated_at = ? "
            "WHERE id = ? AND status = 'NEEDS_REVIEW'",
            (status, reason, json.dumps(gates, ensure_ascii=False), now_iso(), lead_id),
        ).rowcount
        if claimed != 1:
            raise OverrideError("This doctor isn't waiting for a decision any more. Refresh the page.")
        audit(conn, "override.removed", lead_id=lead_id, actor=f"reviewer:{reviewer}", detail={"note": note})
