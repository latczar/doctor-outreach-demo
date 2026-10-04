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
        return "Not yet", "muted"
    return "Done", "good"


# --- the Overview's board: the workflow left to right, a box a step ----------------

# The board's boxes are small, so they use the short status words: "3 bad emails", "1 opted out".
BOARD_WORDS = {
    Status.NEW: ("not checked yet", "not checked yet"),
    Status.DUPLICATE: ("duplicate merged", "duplicates merged"),
    Status.DISQUALIFIED: ("doesn't fit", "don't fit"),
    Status.NEEDS_REVIEW: ("needs you", "need you"),
    Status.NO_EMAIL: ("has no email", "have no email"),
    Status.EMAIL_INVALID: ("bad email", "bad emails"),
    Status.ALREADY_CONTACTED: ("contacted recently", "contacted recently"),
    Status.SUPPRESSED: ("opted out", "opted out"),
    Status.READY_TO_DRAFT: ("waiting for a draft", "waiting for a draft"),
    Status.DRAFT_FAILED: ("needs fixing", "need fixing"),
    Status.PENDING_APPROVAL: ("waiting for you", "waiting for you"),
    Status.REJECTED: ("rejected", "rejected"),
    Status.APPROVED: ("ready to send", "ready to send"),
    Status.SEND_BLOCKED: ("blocked at send", "blocked at send"),
    Status.SEND_FAILED: ("failed to send", "failed to send"),
    Status.SENT: ("sent", "sent"),
}

# What follows a box's big number: "19 of 24"; the second form is for when none have arrived.
BOARD_COUNT = {"send": ("of {of}", "sent"), "log": ("logged", "logged")}
# What a step that hasn't started is waiting for, so it doesn't just look switched off.
BOARD_AFTER = {"send": "After approval", "log": "After sending"}


def board_words(status: str, n: int) -> str:
    singular, plural = BOARD_WORDS[Status(status)]
    return f"{n} {singular if n == 1 else plural}"


def your_move(view: dict) -> dict | None:
    """The step where a person is needed next, in the same order as the Overview's to-do list.

    `codes` are the statuses its button deals with, so the box doesn't repeat them as reasons.
    """
    steps = {s["key"]: s for s in view["steps"]}
    review = steps["approval"]["waiting"].get(Status.PENDING_APPROVAL, 0)
    fix = steps["draft"]["stopped"].get(Status.DRAFT_FAILED, 0)
    send = steps["send"]["waiting"].get(Status.APPROVED, 0) + steps["send"]["stopped"].get(Status.SEND_FAILED, 0)
    if review:
        return {"key": "approval", "label": f"Review {review} email{'' if review == 1 else 's'}", "href": "/review",
                "codes": {Status.PENDING_APPROVAL}}
    if fix:
        return {"key": "draft", "label": f"Fix {fix} email{'' if fix == 1 else 's'}", "href": "/review",
                "codes": {Status.DRAFT_FAILED}}
    for step in view["steps"]:
        if decide := step["stopped"].get(Status.NEEDS_REVIEW, 0):
            return {"key": step["key"], "label": f"Decide on {decide} doctor{'' if decide == 1 else 's'}",
                    "codes": {Status.NEEDS_REVIEW}, "href": f"/doctors?show=needs&step={step['key']}"}
    if send:  # a failed send stays listed as a reason: it's a problem, not only a job
        return {"key": "send", "label": f"Send {send} email{'' if send == 1 else 's'}", "send": send,
                "codes": {Status.APPROVED}}
    return None


def _box(number: int, key: str, href: str, label: str, how: str, count: int, of: int, detail: str,
         status: tuple[str, str], parts: list[tuple[str, int]], outcomes: list[tuple[str, str]]) -> dict:
    return {
        "number": number, "key": key, "href": href, "label": label, "how": how, "count": count, "of": of,
        "detail": detail, "status": status[0], "tone": status[1], "later": status[0] == "Not yet",
        # (state, how many, share of the doctors who reached this step): a slice of the step's bar
        "parts": [(state, n, round(100 * n / of, 1) if of else 0) for state, n in parts if n],
        "outcomes": outcomes,
    }


def board(view: dict) -> dict:
    """The Overview's board, from funnel(): who got through each step, who is waiting or stopped there, and why.

    The research list is the first box. Each box has a bar of the doctors who reached it, and the step where a
    person is needed next is marked, in the to-do list's order.
    """
    rows, unique, merged = view["rows"], view["unique"], view["duplicates"]
    move = your_move(view) if rows else None
    boxes = [_box(
        1, "intake", f"/doctors?step={INTAKE['key']}", INTAKE["label"], INTAKE["how"], unique, rows,
        f"of {rows}", ("Done", "good") if rows else ("Not yet", "muted"),
        [("done", unique), ("stopped", merged)], [],
    )]
    for number, step in enumerate(view["steps"], start=2):
        person = {code: n for code, n in step["stopped"].items() if code in NEEDS_PERSON}
        stopped = {code: n for code, n in step["stopped"].items() if code not in NEEDS_PERSON}
        here = move if move and move["key"] == step["key"] else None
        # Only jobs and work in progress show on the box; who stopped, and why, is one click away.
        shown = [(tone, code, n) for tone, group in (("waiting", step["waiting"]), ("warn", person))
                 for code, n in group.items() if not (here and code in here["codes"])]
        with_of, without = BOARD_COUNT.get(step["key"], ("of {of}", ""))
        box = _box(
            number, step["key"], f"/doctors?step={step['key']}", step["label"], step["how"], step["count"],
            step["of"], (with_of if step["of"] else without).format(of=step["of"]), step_status(step),
            [("done", step["count"]), ("waiting", sum(step["waiting"].values())),
             ("person", sum(person.values())), ("stopped", sum(stopped.values()))],
            [(tone, board_words(code, n)) for tone, code, n in shown],
        )
        box["move"] = here
        if box["later"]:
            box["after"] = BOARD_AFTER.get(step["key"])
        if step.get("first_time") or step.get("retried"):
            box["tries"] = f"So far, {step['first_time']} passed first time and {step['retried']} on a retry."
        boxes.append(box)
    boxes[0]["move"] = None

    # The whole board in one line, and where the final list's emails are, for the box under it.
    steps = {s["key"]: s for s in view["steps"]}
    count = {key: s["count"] for key, s in steps.items()}
    story = [f"{rows} row{'' if rows == 1 else 's'}", f"{unique} doctor{'' if unique == 1 else 's'}",
             f"{count['qualify']} qualified", f"{count['email_available']} with an email",
             f"{count['email_verified']} verified", f"{view['final_list']} on the final list",
             f"{count['draft']} draft{'' if count['draft'] == 1 else 's'} passed",
             f"{count['approval']} approved", f"{count['send']} sent"]
    where = [(Status.READY_TO_DRAFT, steps["draft"]["waiting"]), (Status.PENDING_APPROVAL, steps["approval"]["waiting"]),
             (Status.DRAFT_FAILED, steps["draft"]["stopped"]), (Status.APPROVED, steps["send"]["waiting"]),
             (Status.SEND_FAILED, steps["send"]["stopped"])]
    emails = [board_words(code, group[code]) for code, group in where if group.get(code)]
    emails.append(board_words(Status.SENT, count["send"]) if count["send"] else "none sent yet")
    return {"rules": boxes[:5], "people": boxes[5:], "empty": not rows, "move": move, "final_list": view["final_list"],
            "story": story, "emails": "Their emails: " + ", ".join(emails) + "."}


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
