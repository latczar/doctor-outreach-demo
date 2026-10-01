"""Turns stored data into plain English for the screens. Nothing here makes a decision."""

from __future__ import annotations

import re

from markupsafe import Markup, escape

from ..db import loads
from ..guardrails import INJECTION_HINT, word_stem
from ..models import INTAKE, WAITING, WORKFLOW, Status, gates_passed, on_final_list

# --- three plain phrases instead of sixteen statuses ---------------------------

BUCKETS = {
    "final": "On the final list",
    "needs": "Needs you",
    "removed": "Not on the list",
    "unchecked": "Not checked yet",
}
BUCKET_TONE = {"final": "good", "needs": "warn", "removed": "muted", "unchecked": "muted"}


def bucket_of(status: str) -> str:
    """Every doctor is in exactly one: cleared to contact, waiting for a person's decision, or taken off."""
    if status == Status.NEW:
        return "unchecked"
    if status == Status.NEEDS_REVIEW:
        return "needs"
    return "final" if on_final_list(status) else "removed"


def tone(status: str) -> str:
    return BUCKET_TONE[bucket_of(status)]


# How a count of doctors in each status reads in a sentence: "3 don't fit the campaign".
PHRASES = {
    Status.NEW: ("not checked yet", "not checked yet"),
    Status.DUPLICATE: ("duplicate merged", "duplicates merged"),
    Status.DISQUALIFIED: ("doesn't fit the campaign", "don't fit the campaign"),
    Status.NEEDS_REVIEW: ("needs you to check", "need you to check"),
    Status.NO_EMAIL: ("has no email", "have no email"),
    Status.EMAIL_INVALID: ("has a bad email", "have bad emails"),
    Status.ALREADY_CONTACTED: ("was contacted recently", "were contacted recently"),
    Status.SUPPRESSED: ("asked not to be contacted", "asked not to be contacted"),
    Status.READY_TO_DRAFT: ("is waiting for a draft", "are waiting for a draft"),
    Status.DRAFT_FAILED: ("email needs fixing", "emails need fixing"),
    Status.PENDING_APPROVAL: ("is waiting for your review", "are waiting for your review"),
    Status.REJECTED: ("was rejected by a reviewer", "were rejected by a reviewer"),
    Status.APPROVED: ("is approved and ready to send", "are approved and ready to send"),
    Status.SEND_BLOCKED: ("was blocked at send", "were blocked at send"),
    Status.SEND_FAILED: ("failed to send", "failed to send"),
    Status.SENT: ("sent", "sent"),
}


def phrase(status: str, n: int) -> str:
    singular, plural = PHRASES[Status(status)]
    return f"{n} {singular if n == 1 else plural}"


# --- a parcel-style tracker per doctor ---------------------------------------------

TRACKER_STEPS = [INTAKE["label"]] + [step["label"] for step in WORKFLOW]
NEEDS_PERSON = {Status.NEEDS_REVIEW, Status.DRAFT_FAILED, Status.SEND_FAILED}


def tracker(lead: dict) -> list[dict]:
    """One dot per step: done, waiting, person (needs you), stopped or todo."""
    status = Status(lead["status"])
    if status == Status.DUPLICATE:
        states = ["stopped"] + ["todo"] * len(WORKFLOW)
    elif status == Status.SENT:
        states = ["done"] * len(TRACKER_STEPS)
    else:
        passed = gates_passed(status, lead["stage"])
        here = "waiting" if status in WAITING else ("person" if status in NEEDS_PERSON else "stopped")
        states = ["done"] * (1 + passed) + [here] + ["todo"] * (len(WORKFLOW) - passed - 1)
    return [{"label": label, "state": state} for label, state in zip(TRACKER_STEPS, states)]


def step_status(step: dict) -> tuple[str, str]:
    """A task-list status for one workflow step, as in the GOV.UK task list pattern: words and a colour."""
    stopped, waiting = step["stopped"], step["waiting"]
    if any(code in NEEDS_PERSON for code in stopped):
        return "Needs you", "warn"
    if Status.PENDING_APPROVAL in waiting:
        return "Waiting for you", "waiting"
    if Status.READY_TO_DRAFT in waiting:
        return "In progress", "waiting"
    if Status.APPROVED in waiting:
        return "Ready to send", "waiting"
    if step["of"] == 0 and step["count"] == 0:
        return "Cannot start yet", "muted"
    return "Done", "good"


FINAL_LIST_LEAD_IN = {
    Status.READY_TO_DRAFT: "On the final list: waiting for a draft",
    Status.DRAFT_FAILED: "On the final list: the email needs fixing",
    Status.PENDING_APPROVAL: "On the final list: email waiting for review",
    Status.APPROVED: "On the final list: approved, ready to send",
    Status.SEND_FAILED: "On the final list: sending failed",
    Status.SENT: "Email sent and logged",
}


def where_now(lead: dict) -> tuple[str, str]:
    """A bold lead-in saying where the doctor is, and the reason in plain words."""
    status = Status(lead["status"])
    reason = headline(lead["reason"])
    current = next((t["label"] for t in tracker(lead) if t["state"] in ("waiting", "person", "stopped")), "")
    if status == Status.NEW:
        return "Not checked yet", "Run the pipeline to check this doctor."
    if status == Status.DUPLICATE:
        return "Merged with another row", reason
    if status in FINAL_LIST_LEAD_IN:
        return FINAL_LIST_LEAD_IN[status], reason
    if status == Status.NEEDS_REVIEW:
        return f"Needs you at {current}", reason
    return f"Not on the list: stopped at {current}", reason


def pager(total: int, page: str | int, size: int) -> dict:
    """Which slice of a long table to show, for example events 26 to 50 of 140, page 2 of 6.

    A page that isn't a number, or doesn't exist, becomes the nearest real page.
    """
    try:
        wanted = int(page)
    except (TypeError, ValueError):
        wanted = 1
    pages = max(1, -(-total // size))
    page = min(max(wanted, 1), pages)
    offset = (page - 1) * size
    return {"page": page, "pages": pages, "total": total, "size": size, "offset": offset,
            "first": offset + 1 if total else 0, "last": min(offset + size, total)}


def headline(reason: str | None) -> str:
    """The first sentence of a reason, for tables. The full reason is on the doctor's page."""
    text = (reason or "").strip()
    match = re.search(r"(?<=[.!?])\s", text)
    return text[: match.start()] if match else text


# --- explanations for terms: a small "i" that opens one line --------------------------

GLOSSARY = {
    "catch-all": "A domain that accepts mail for any address, so we can't tell whether this mailbox really exists.",
    "MX record": "The internet setting that says which server receives a domain's email. "
                 "No MX record means no email can be delivered.",
    "shared inbox": "An address like info@ or reception@ that a team reads, not a named doctor.",
    "do-not-contact list": "People who opted out or whose email bounced. We never email them again.",
    "registration number": "The doctor's professional registration number. "
                           "It stays the same when they change jobs or email address.",
    "cooldown": "The minimum gap between two contacts with the same doctor, set in the campaign file.",
}
_TERMS = re.compile("|".join(re.escape(t) for t in GLOSSARY), re.IGNORECASE)


def term_id(term: str) -> str:
    """The id of a term's explanation box, for example term-catch-all."""
    return "term-" + re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")


def explain_terms(text: str | None) -> Markup:
    """Escape the text, then put a small "i" after each known term.

    Clicking or tapping the "i" opens the term's explanation, which base.html prints once per page.
    It uses the HTML popover attribute, so it needs no JavaScript and works on phones, where hovering
    doesn't.
    """
    def wrap(match: re.Match) -> str:
        term = next(t for t in GLOSSARY if t.lower() == match.group(0).lower())
        return (f'{match.group(0)}<button type="button" class="info" popovertarget="{term_id(term)}" '
                f'aria-label="What does {escape(term)} mean?">i</button>')

    return Markup(_TERMS.sub(wrap, str(escape(text or ""))))


# --- highlighting the personal detail and any injected instructions ---------------


def highlight(text: str | None, stems: set[str]) -> Markup:
    """Escape the text and mark the words whose stems the personalised check matched."""
    parts = re.split(r"([A-Za-z]+)", text or "")
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1 and word_stem(part) in stems:
            out.append(f"<mark>{escape(part)}</mark>")
        else:
            out.append(str(escape(part)))
    return Markup("".join(out))


def highlight_notes(notes: str | None, stems: set[str]) -> Markup:
    """Like highlight(), but a sentence that gives orders to the AI is marked in red."""
    notes = notes or ""
    match = INJECTION_HINT.search(notes)
    if not match:
        return highlight(notes, stems)
    start = notes.rfind(".", 0, match.start()) + 1
    end = notes.find(".", match.end())
    end = len(notes) if end == -1 else end + 1
    return Markup(
        highlight(notes[:start], stems)
        + Markup('<mark class="danger">') + escape(notes[start:end]) + Markup("</mark>")
        + highlight(notes[end:], stems)
    )


# --- the audit log in plain English -------------------------------------------------

STEP_EVENTS = {
    "qualify": ("Qualified for the campaign", "Did not qualify"),
    "email_available": ("Email found", "No email found"),
    "email_verified": ("Email verified", "Email failed verification"),
    "not_contacted": ("History check passed", "Skipped: contacted before, or opted out"),
}


def who(actor: str) -> str:
    return actor.removeprefix("reviewer:") if actor.startswith("reviewer:") else "System"


def describe_event(event: str, actor: str, detail: dict) -> str:
    stage, _, outcome = event.partition(".")
    if stage in STEP_EVENTS and outcome in ("passed", "stopped"):
        return STEP_EVENTS[stage][0 if outcome == "passed" else 1]
    if event == "draft.attempt":
        failures = len(detail.get("failures", []))
        result = "passed every check" if detail.get("passed") else (
            f"failed {failures} check{'s' if failures != 1 else ''}")
        return f"Draft {detail.get('attempt')} written by {detail.get('provider')}: {result}"
    person = who(actor)
    return {
        "demo.reset": "Demo reset: earlier outreach and the do-not-contact list loaded",
        "ingest.imported": "Imported from the research list",
        "ingest.duplicate": "Recognised as a duplicate and merged",
        "draft.model_error": "The drafting step couldn't run",
        "draft.gave_up": "Draft failed every attempt, so it was handed to a person",
        "approval.approved": f"{person} approved the email" + (" after editing it" if detail.get("edited") else ""),
        "approval.rejected": f"{person} rejected the email",
        "approval.regenerate": f"{person} asked for a new draft",
        "send.sent": "Email sent and logged",
        "send.blocked": "Sending was blocked",
        "send.failed": "Sending failed. It will be retried next time",
        "send.skipped": "Not sent again: it had already been sent",
        "override.recheck": f"{person} fixed the record and the rules ran again"
                            + (", with the mailbox confirmed by hand" if detail.get("confirmed_email") else ""),
        "override.removed": f"{person} took the doctor off the list",
        "alert.raised": ("Urgent alert sent to the team" if detail.get("delivered")
                         else "Urgent alert could not be delivered") + f" ({detail.get('channel', 'unknown')})",
    }.get(event, event)


def event_detail(detail: dict) -> str:
    for key in ("reason", "note", "instruction", "text", "error"):
        if detail.get(key):
            return str(detail[key])
    return " ".join(detail.get("failures", []))


# --- one doctor's journey through the workflow --------------------------------------


def _step(step: dict, state: str, text: str) -> dict:
    """state: done, waiting, person, stopped or todo."""
    return {"label": step["label"], "how": step["how"], "state": state, "text": text}


def timeline(lead: dict, drafts: list[dict], sends: list[dict]) -> list[dict]:
    status = Status(lead["status"])
    if status == Status.DUPLICATE:
        return [_step(INTAKE, "stopped", lead["reason"] or "")]

    merged = " Details were merged in from other copies." if lead["merge_notes"] else ""
    rows = [_step(INTAKE, "done", f"Row {lead['source_row']} of {lead['source_file']}.{merged}")]
    gates = loads(lead["gate_results"], {})
    passed_draft = next(
        (d for d in reversed(drafts)
         if d["status"] in ("PENDING_APPROVAL", "APPROVED", "SENT", "REJECTED")
         and all(c["ok"] for c in loads(d["checks"], [{"ok": False}]))),
        None,
    )
    approved = next((d for d in reversed(drafts) if d["status"] in ("APPROVED", "SENT")), None)
    sent = next((s for s in sends if s["status"] == "sent"), None)

    for step in WORKFLOW:
        key, state, text = step["key"], "todo", ""
        if key in gates:
            gate = gates[key]
            state = "done" if gate["passed"] else ("person" if status == Status.NEEDS_REVIEW else "stopped")
            text = gate["reason"]
        elif key == "draft":
            if passed_draft:
                state, text = "done", (f"Passed every check on attempt {passed_draft['attempt']} "
                                       f"({passed_draft['provider']}).")
            elif status == Status.READY_TO_DRAFT:
                state, text = "waiting", "Waiting for the system to write a draft."
            elif status == Status.DRAFT_FAILED:
                state, text = "person", lead["reason"]
            elif drafts:
                state, text = "person", "The drafts failed our checks."
        elif key == "approval":
            if approved:
                state = "done"
                text = f"Approved by {approved['reviewer']}" + (", after editing." if approved["edited_by_reviewer"] else ".")
            elif status == Status.REJECTED:
                state, text = "stopped", lead["reason"]
            elif status == Status.PENDING_APPROVAL:
                state, text = "waiting", "Waiting for a person to review the draft."
        elif key == "send":
            if status == Status.SENT:
                state, text = "done", lead["reason"]
            elif status == Status.SEND_BLOCKED:
                state, text = "stopped", lead["reason"]
            elif status == Status.SEND_FAILED:
                state, text = "person", lead["reason"]
            elif status == Status.APPROVED:
                state, text = "waiting", "Approved. It goes out when someone clicks Send approved."
        elif key == "log" and sent:
            state, text = "done", f"Logged at {sent['sent_at'][:16].replace('T', ' ')} UTC, message {sent['message_id']}."
        rows.append(_step(step, state, text))
    return rows
