"""Runs the steps in order and works out the funnel."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .config import Settings
from .db import audit, loads, transaction, update_lead
from .drafting import Prompts, draft_for_lead
from .gates import (
    DirectoryFinder,
    EmailFinder,
    EmailVerifier,
    FixtureVerifier,
    check_history,
    email_available,
    qualify,
    verify_email,
)
from .guardrails import looks_like_injection
from .ingest import IngestSummary, ingest_csv
from .llm import LLM, make_llm
from .models import (
    WAITING,
    WORKFLOW,
    Campaign,
    GateResult,
    Status,
    gates_passed,
    load_campaign,
    on_final_list,
)
from .notify import Alert, Notifier, already_alerted, make_notifier, raise_alert


@dataclass
class Services:
    campaign: Campaign
    llm: LLM
    finder: EmailFinder
    verifier: EmailVerifier
    prompts: Prompts
    today: date
    notifier: Notifier | None = None


def build_services(settings: Settings, llm: LLM | None = None, today: date | None = None) -> Services:
    return Services(
        campaign=load_campaign(settings.campaign_path),
        llm=llm or make_llm(settings),
        finder=DirectoryFinder.from_file(settings.seed_dir / "email_directory.json"),
        verifier=FixtureVerifier.from_file(settings.seed_dir / "mail_fixture.json"),
        prompts=Prompts.load(settings.prompts_dir),
        today=today or datetime.now(timezone.utc).date(),
        notifier=make_notifier(settings),
    )


def record_gate(conn: sqlite3.Connection, lead: dict, stage: str, result: GateResult, **extra) -> None:
    results = loads(lead["gate_results"], {})
    results[stage] = {"passed": result.passed, "reason": result.reason, "evidence": result.evidence}
    fields = {"gate_results": json.dumps(results, ensure_ascii=False), "stage": stage,
              "reason": result.reason, **extra}
    if not result.passed:
        fields["status"] = result.status
    with transaction(conn):
        update_lead(conn, lead["id"], **fields)
        audit(conn, f"{stage}.{'passed' if result.passed else 'stopped'}", lead_id=lead["id"],
              detail={"reason": result.reason, **result.evidence})
    lead.update(fields)


def run_gates(conn: sqlite3.Connection, lead: dict, services: Services,
              email_confirmed_by: str | None = None) -> bool:
    """Steps 1 to 4 for one doctor. Returns True if they're ready for a draft.

    `email_confirmed_by` = a person who checked a mailbox the computer couldn't (see verify_email).
    """
    result = qualify(lead, services.campaign.targeting)
    record_gate(conn, lead, "qualify", result)
    if not result.passed:
        return False

    result, found = email_available(lead, services.finder)
    extra = {"email": found.email, "email_origin": "pattern_guess"} if found else {}
    record_gate(conn, lead, "email_available", result, **extra)
    if not result.passed:
        return False

    result = verify_email(lead["email"], services.verifier, guessed=lead["email_origin"] == "pattern_guess",
                          confirmed_by=email_confirmed_by)
    record_gate(conn, lead, "email_verified", result)
    if not result.passed:
        return False

    result = check_history(conn, lead, services.campaign, services.today)
    record_gate(conn, lead, "not_contacted", result)
    if not result.passed:
        return False

    with transaction(conn):
        update_lead(conn, lead["id"], status=Status.READY_TO_DRAFT, stage="draft",
                    reason="Passed every check. Ready for a personalised draft.")
    return True


Progress = Callable[..., None]  # progress(text, working=False): working = still going, shown with a spinner


def run_pipeline(
    conn: sqlite3.Connection,
    services: Services,
    csv_path: Path,
    on_progress: Progress | None = None,
) -> IngestSummary:
    """Import, run the four rule steps for every new doctor, then draft. Reports each result in plain English."""
    progress = on_progress or (lambda text, working=False: None)
    campaign_id = services.campaign.id

    progress("Reading the research list...", working=True)
    summary = ingest_csv(conn, campaign_id, csv_path)
    progress(f"Research list: {summary.rows_read} rows read, {summary.new_doctors} new doctors, "
             f"{summary.duplicates} duplicates merged.")

    new = conn.execute("SELECT * FROM leads WHERE campaign_id = ? AND status = 'NEW' ORDER BY id",
                       (campaign_id,)).fetchall()
    progress("Checking targeting, emails and contact history...", working=True)
    for row in new:
        run_gates(conn, dict(row), services)
    for step in funnel(conn, campaign_id)["steps"][:4]:
        progress(f"{step['label'].rstrip('?')}: {step['summary']}.")

    ready = conn.execute("SELECT * FROM leads WHERE campaign_id = ? AND status = 'READY_TO_DRAFT' ORDER BY id",
                         (campaign_id,)).fetchall()
    for i, row in enumerate(ready, start=1):
        lead = dict(row)
        if services.notifier and looks_like_injection(lead["profile_notes"])                 and not already_alerted(conn, "suspicious_profile", lead["id"]):
            raise_alert(conn, services.notifier, Alert(
                "suspicious_profile",
                f"The profile for {lead['full_name']} (from the {(lead['source'] or 'web').lower()}) tries to give "
                "the AI instructions. The checks guard the draft, but someone should look at the source.",
                lead["id"]))
            progress(f"Urgent alert sent: the profile for {lead['full_name']} tries to give the AI instructions.")
        progress(f"Writing email {i} of {len(ready)} with {services.llm.name}: {lead['full_name']}...", working=True)
        outcome = draft_for_lead(conn, lead, services.campaign, services.llm, services.prompts)
        if outcome.status == Status.PENDING_APPROVAL:
            progress(f"Email {i} of {len(ready)}, {lead['full_name']}: passed every check "
                     f"(attempt {outcome.attempts}), ready for review.")
        else:
            progress(f"Email {i} of {len(ready)}, {lead['full_name']}: failed the checks, so a person needs to fix it.")
    progress("Finished.")
    return summary


def step_threshold(index: int) -> int:
    """Gates a doctor must have passed to count at WORKFLOW[index]. Send and log happen together."""
    return min(index + 1, 7)


def funnel(conn: sqlite3.Connection, campaign_id: str) -> dict:
    """How many doctors got through each workflow step ("19 of 24"), and who is waiting or stopped at each.

    `waiting` and `stopped` count statuses (codes, not words), so each screen can choose its own wording.
    """
    rows = conn.execute("SELECT status, stage FROM leads WHERE campaign_id = ?", (campaign_id,)).fetchall()
    unique = [r for r in rows if r["status"] != Status.DUPLICATE]
    passed = [gates_passed(r["status"], r["stage"]) for r in unique]

    steps, previous = [], len(unique)
    for i, step in enumerate(WORKFLOW):
        need = step_threshold(i)
        count = sum(1 for p in passed if p >= need)
        here = [] if step["key"] == "log" else [r for r, p in zip(unique, passed) if p == need - 1]
        steps.append({
            **step,
            "count": count,
            "of": previous,
            "summary": step["summary"].format(count=count, of=previous),
            "waiting": dict(Counter(r["status"] for r in here if r["status"] in WAITING)),
            "stopped": dict(Counter(r["status"] for r in here if r["status"] not in WAITING)),
        })
        previous = count
    return {
        "rows": len(rows),
        "unique": len(unique),
        "duplicates": len(rows) - len(unique),
        "final_list": sum(1 for r in unique if on_final_list(r["status"])),
        "steps": steps,
    }


def reset_data(conn: sqlite3.Connection, seed_dir: Path) -> None:
    """Empty every table, then load the made-up outreach history and do-not-contact list."""
    with transaction(conn):
        for table in ("audit_log", "drafts", "outreach_log", "suppression", "leads"):
            conn.execute(f"DELETE FROM {table}")
        with (seed_dir / "outreach_history.csv").open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                conn.execute(
                    """INSERT INTO outreach_log (campaign_id, full_name, email, reg_number, status, outcome, sent_at)
                       VALUES (?, ?, ?, ?, 'imported', ?, ?)""",
                    (row["campaign_id"], row["full_name"], row["email"].lower(), row["reg_number"] or None,
                     row["outcome"], row["sent_at"]),
                )
        with (seed_dir / "suppression.csv").open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                conn.execute("INSERT INTO suppression (email, reason, added_at) VALUES (?, ?, ?)",
                             (row["email"].lower(), row["reason"], row["added_at"]))
        audit(conn, "demo.reset", detail={"seed_dir": seed_dir.name})
