"""The replay page: the last run, rebuilt from the audit log so it can be played back on screen.

Nothing here runs anything again. It only reads what the run wrote down: which step stopped each
doctor, when each draft was written, and how its checks went.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime

from ..db import loads
from ..drafting import Prompts, build_prompt, doctor_profile
from ..guardrails import looks_like_injection
from ..models import INTAKE, WORKFLOW, Campaign, Status
from .present import NEEDS_PERSON, phrase

# Lat's estimate of one doctor by hand: research them, find and check the email, check past contact,
# and write the email. Shown as an estimate, because nobody has timed it.
BY_HAND_MINUTES = (5, 10)

GATES = WORKFLOW[:4]  # Qualified doctor, Email available, Email verified, Not previously contacted

# A failed check in a few words, for the draft cards.
FAILED_WORDS = {
    "greeting": "wrong greeting",
    "no_placeholders": "a placeholder",
    "no_invented_money": "made-up money",
    "no_invented_numbers": "a made-up number",
    "no_partnership_claims": "a partnership claim",
    "plain_language": "a stock phrase",
    "personalised": "too generic",
    "length": "wrong length",
    "honest_subject": "a misleading subject",
    "valid_json": "an unreadable reply",
    "model_call": "the drafting step couldn't run",
}

# The same checks when they pass, for the "Checks, then a person" panel.
PASSED_WORDS = {
    "greeting": "Greets the doctor by name",
    "no_placeholders": "No placeholders",
    "no_invented_money": "No made-up money",
    "no_invented_numbers": "No made-up numbers",
    "no_partnership_claims": "No partnership claims",
    "plain_language": "No stock phrases",
    "personalised": "Uses a real detail from the profile",
    "length": "70 to 180 words",
    "honest_subject": "Honest subject line",
}


def duration(seconds: float) -> str:
    """'under a second', '7 s', '2 min 4 s'."""
    seconds = round(seconds)
    if seconds < 1:
        return "under a second"
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


def hours(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def by_hand(doctors: int) -> dict:
    low, high = (doctors * m / 60 for m in BY_HAND_MINUTES)
    return {"minutes": BY_HAND_MINUTES, "low_s": low * 3600, "high_s": high * 3600,
            "text": f"{hours(low)} to {hours(high)} hours"}


def _at(row) -> datetime:
    return datetime.fromisoformat(row["at"])


def run_events(conn: sqlite3.Connection) -> list[dict]:
    """The audit log of the last run: from its first rule step until a person first acts.

    Approvals, rewrites and decisions made afterwards aren't part of the run, so they're left out.
    """
    reset = conn.execute("SELECT COALESCE(MAX(id), 0) FROM audit_log WHERE event = 'demo.reset'").fetchone()[0]
    start = conn.execute(
        "SELECT MIN(id) FROM audit_log WHERE id > ? AND (event LIKE '%.passed' OR event LIKE '%.stopped')",
        (reset,)).fetchone()[0]
    if start is None:
        return []
    person = conn.execute("SELECT MIN(id) FROM audit_log WHERE id > ? AND actor LIKE 'reviewer:%'",
                          (start,)).fetchone()[0]
    rows = conn.execute("SELECT * FROM audit_log WHERE id >= ? AND id < ? ORDER BY id",
                        (start, person or 2**62)).fetchall()
    return [dict(r) for r in rows]


def _rule_steps(conn: sqlite3.Connection, campaign_id: str, events: list[dict]) -> tuple[list[dict], int, int]:
    leads = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, status, stage FROM leads WHERE campaign_id = ?", (campaign_id,))}
    rows = len(leads)
    duplicates = sum(1 for lead in leads.values() if lead["status"] == Status.DUPLICATE)

    # Where each doctor stopped during the run, and why (the status the step gave them).
    stopped: dict[str, Counter] = {gate["key"]: Counter() for gate in GATES}
    seen: set[int] = set()
    for event in events:
        stage, _, outcome = event["event"].partition(".")
        if outcome != "stopped" or stage not in stopped or event["lead_id"] in seen:
            continue
        seen.add(event["lead_id"])
        lead = leads.get(event["lead_id"], {})
        status = loads(event["detail"], {}).get("status") or (lead.get("status") if lead.get("stage") == stage else None)
        stopped[stage][status] += 1

    steps = [{"key": "rows", "label": "Research list", "left": rows, "stopped": 0, "trays": [],
              "how": "The made-up research list, one dot per row."},
             {"key": INTAKE["key"], "label": "Copies merged", "left": rows - duplicates, "stopped": duplicates,
              "trays": [{"text": phrase(Status.DUPLICATE, duplicates), "tone": "muted"}] if duplicates else [],
              "how": INTAKE["how"]}]
    left = rows - duplicates
    for gate in GATES:
        here = stopped[gate["key"]]
        count = sum(here.values())
        left -= count
        trays = [{"text": phrase(code, n) if code else f"{n} stopped here",
                  "tone": "warn" if code in NEEDS_PERSON else "muted"}
                 for code, n in sorted(here.items(), key=lambda item: -item[1])]
        steps.append({"key": gate["key"], "label": gate["label"], "left": left, "stopped": count,
                      "trays": trays, "how": gate["how"]})
    for step in steps:
        step["dots"] = ["go"] * step["left"] + ["stop"] * step["stopped"]
    return steps, rows, rows - duplicates


def _drafts(conn: sqlite3.Connection, events: list[dict]) -> tuple[list[dict], dict | None]:
    """One card per doctor the run drafted for, with each try's time and failed checks."""
    cards: dict[int, dict] = {}
    previous = None
    for event in events:
        if event["event"] in ("draft.attempt", "draft.model_error"):
            lead_id = event["lead_id"]
            if lead_id not in cards:
                lead = dict(conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone())
                cards[lead_id] = {"lead": lead, "name": lead["full_name"], "specialty": lead["specialty"] or "",
                                  "attempts": [], "injection": looks_like_injection(lead["profile_notes"])}
            card = cards[lead_id]
            detail = loads(event["detail"], {})
            n = detail.get("attempt") or len(card["attempts"]) + 1
            draft = conn.execute("SELECT * FROM drafts WHERE lead_id = ? AND attempt = ?", (lead_id, n)).fetchone()
            checks = loads(draft["checks"], []) if draft else []
            failed = [FAILED_WORDS.get(c["name"], c["name"]) for c in checks if not c["ok"]]
            if event["event"] == "draft.model_error":
                failed = [FAILED_WORDS["model_call"]]
            seconds = (_at(event) - _at(previous)).total_seconds() if previous else 0
            card["attempts"].append({"n": n, "passed": bool(detail.get("passed")), "failed": failed,
                                     "time": duration(seconds), "draft": dict(draft) if draft else None})
        previous = event

    example = None
    for card in cards.values():
        last = card["attempts"][-1]
        card["passed"] = last["passed"]
        tries = len(card["attempts"])
        if last["passed"]:
            card["outcome"] = "Waiting for your review" + (f", passed on try {tries}" if tries > 1 else "")
        else:
            card["outcome"] = f"Needs a person: it failed the checks {tries} times"
        card["obeyed"] = any(w in t["failed"] for t in card["attempts"]
                             for w in ("made-up money", "a partnership claim"))
        if example is None and last["passed"] and tries == 1 and not card["injection"]:
            example = card
    return list(cards.values()), example


def build_replay(conn: sqlite3.Connection, campaign: Campaign, prompts: Prompts) -> dict | None:
    events = run_events(conn)
    if not events:
        return None
    steps, rows, unique = _rule_steps(conn, campaign.id, events)
    cards, example = _drafts(conn, events)

    gate_events = [e for e in events if e["event"].endswith((".passed", ".stopped"))]
    draft_events = [e for e in events if e["event"].startswith("draft.")]
    rules_s = (_at(gate_events[-1]) - _at(gate_events[0])).total_seconds()
    drafting_s = (_at(draft_events[-1]) - _at(gate_events[-1])).total_seconds() if draft_events else 0
    first = _at(events[0])
    attempt = next((loads(e["detail"], {}) for e in events if e["event"] == "draft.attempt"), {})
    hand = by_hand(unique)

    replay = {
        "recorded": f"{first.day} {first:%B %Y} at {first:%H:%M} UTC",
        "rows": rows,
        "unique": unique,
        "final_list": steps[-1]["left"],
        "steps": steps,
        "cards": cards,
        "tries": sum(len(c["attempts"]) for c in cards),
        "writer": " ".join(filter(None, [attempt.get("provider"), f"({attempt['model']})" if attempt.get("model") else ""])),
        "rules_time": duration(rules_s),
        "drafting_time": duration(drafting_s),
        "by_hand": hand,
        # Bar widths, as a share of the longest (by hand, at the top of the estimate).
        "bars": {"rules": max(rules_s / hand["high_s"] * 100, 0.4),
                 "drafting": max(drafting_s / hand["high_s"] * 100, 0.4),
                 "hand_low": hand["low_s"] / hand["high_s"] * 100},
        "example": None,
    }
    if example:
        draft = example["attempts"][0]["draft"]
        system, request = build_prompt(prompts, campaign, example["lead"])
        replay["example"] = {
            "name": example["name"],
            "facts": json.dumps(doctor_profile(example["lead"]), indent=2, ensure_ascii=False),
            "reply": json.dumps({"subject": draft["subject"], "personal_detail_used": draft["personal_detail_used"]},
                                indent=2, ensure_ascii=False),
            "checks": [PASSED_WORDS.get(c["name"], c["name"]) for c in loads(draft["checks"], [])],
            "system": system,
            "request": request,
        }
    return replay
