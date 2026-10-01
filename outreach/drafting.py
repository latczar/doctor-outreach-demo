"""Step 5: ask the model for a draft, check it, and retry with the reasons if it fails."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from string import Template

from .db import audit, now_iso, transaction, update_lead
from .guardrails import DRAFT_SCHEMA, DraftContext, build_footer, evaluate
from .llm import LLM, LLMError
from .models import Campaign, Status
from .normalise import salutation

PROMPT_VERSION = "v1"


@dataclass
class Prompts:
    system: str
    draft: str
    version: str = PROMPT_VERSION

    @classmethod
    def load(cls, folder: Path) -> Prompts:
        return cls(
            system=(folder / "system.md").read_text(encoding="utf-8"),
            draft=(folder / "draft.md").read_text(encoding="utf-8"),
            version=folder.name,
        )


@dataclass
class DraftOutcome:
    status: Status
    draft_id: int | None
    attempts: int
    reason: str


def doctor_profile(lead: dict) -> dict:
    """Only the fields the model needs. Data minimisation: no email, no registration number."""
    return {
        "salutation": salutation(lead["title"], lead["last_name"]),
        "full_name": lead["full_name"],
        "grade": lead["grade"],
        "specialty": lead["specialty"],
        "employer": lead["employer"],
        "source": lead["source"],
        "profile_notes": lead["profile_notes"] or "",
    }


def draft_context(lead: dict, campaign: Campaign) -> DraftContext:
    profile = doctor_profile(lead)
    return DraftContext(
        salutation=profile["salutation"],
        specialty=lead["specialty"] or "",
        profile_notes=profile["profile_notes"],
        profile_text=" ".join(str(v) for v in profile.values() if v),
        campaign=campaign,
        job_words=" ".join(filter(None, [lead["grade"], lead["grade_raw"], lead["employer"]])),
    )


def build_prompt(
    prompts: Prompts,
    campaign: Campaign,
    lead: dict,
    feedback: list[str] | None = None,
    reviewer_instruction: str | None = None,
) -> tuple[str, str]:
    system = Template(prompts.system).safe_substitute(organisation=campaign.sender.organisation)
    facts = {
        "organisation": campaign.sender.organisation,
        "sender_name": campaign.sender.name,
        "facts": campaign.offer_facts,
    }
    extra = ""
    if reviewer_instruction:
        extra += (
            "\nA reviewer on our team asked for this change. Follow it unless it breaks the rules:\n"
            f"<reviewer_note>\n{reviewer_instruction.strip()}\n</reviewer_note>\n"
        )
    if feedback:
        extra += "\nYour previous draft failed these checks. Fix every one:\n"
        extra += "".join(f"- {item}\n" for item in feedback)
    prompt = Template(prompts.draft).safe_substitute(
        purpose=campaign.purpose,
        facts_json=json.dumps(facts, indent=2, ensure_ascii=False),
        profile_json=json.dumps(doctor_profile(lead), indent=2, ensure_ascii=False),
        extra=extra,
    )
    return system, prompt


def _insert_draft(conn, lead_id: int, attempt: int, **values) -> int:
    values.setdefault("checks", "[]")
    values.setdefault("fixes", "[]")
    columns = ["lead_id", "attempt", "created_at", *values]
    cursor = conn.execute(
        f"INSERT INTO drafts ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        (lead_id, attempt, now_iso(), *values.values()),
    )
    return cursor.lastrowid


def draft_for_lead(
    conn: sqlite3.Connection,
    lead: dict,
    campaign: Campaign,
    llm: LLM,
    prompts: Prompts,
    *,
    reviewer_instruction: str | None = None,
    actor: str = "system",
) -> DraftOutcome:
    ctx = draft_context(lead, campaign)
    footer = build_footer(campaign, lead["source"])
    previous = conn.execute(
        "SELECT COALESCE(MAX(attempt), 0) AS n FROM drafts WHERE lead_id = ?", (lead["id"],)
    ).fetchone()["n"]

    feedback: list[str] = []
    for n in range(1, campaign.max_draft_attempts + 1):
        attempt = previous + n
        system, prompt = build_prompt(prompts, campaign, lead, feedback, reviewer_instruction)

        # The model call happens outside any transaction, so a slow model never holds a database lock.
        try:
            reply = llm.generate_json(system, prompt, DRAFT_SCHEMA)
        except LLMError as exc:
            reason = f"The model call failed: {exc}"
            with transaction(conn):
                draft_id = _insert_draft(
                    conn, lead["id"], attempt,
                    provider=llm.name, model=getattr(llm, "model", None),
                    prompt_version=prompts.version, status="FAILED_CHECKS",
                    checks=json.dumps([{"name": "model_call", "ok": False, "detail": str(exc)}]),
                    reviewer_instruction=reviewer_instruction,
                )
                update_lead(conn, lead["id"], status=Status.DRAFT_FAILED, stage="draft", reason=reason)
                audit(conn, "draft.model_error", lead_id=lead["id"], actor=actor, detail={"error": str(exc)})
            return DraftOutcome(Status.DRAFT_FAILED, draft_id, n, reason)

        result = evaluate(reply.text, ctx)
        with transaction(conn):
            draft_id = _insert_draft(
                conn, lead["id"], attempt,
                provider=reply.provider, model=reply.model, prompt_version=prompts.version,
                subject=result.subject, body=result.body, footer=footer,
                personal_detail_used=result.personal_detail_used, raw_output=reply.text,
                checks=json.dumps([c.as_dict() for c in result.checks], ensure_ascii=False),
                fixes=json.dumps(result.fixes, ensure_ascii=False),
                reviewer_instruction=reviewer_instruction,
                status="PENDING_APPROVAL" if result.passed else "FAILED_CHECKS",
            )
            audit(
                conn, "draft.attempt", lead_id=lead["id"], actor=actor,
                detail={"attempt": attempt, "passed": result.passed, "failures": result.failures,
                        "fixes": result.fixes, "provider": reply.provider, "model": reply.model},
            )
            if result.passed:
                reason = f"Draft passed every check on attempt {n}. Waiting for a person to review it."
                update_lead(conn, lead["id"], status=Status.PENDING_APPROVAL, stage="approval", reason=reason)
        if result.passed:
            return DraftOutcome(Status.PENDING_APPROVAL, draft_id, n, reason)
        feedback = result.failures

    reason = (
        f"The draft failed our checks {campaign.max_draft_attempts} times, so it needs a person. "
        f"Last problems: {' '.join(feedback)}"
    )
    with transaction(conn):
        update_lead(conn, lead["id"], status=Status.DRAFT_FAILED, stage="draft", reason=reason)
        audit(conn, "draft.gave_up", lead_id=lead["id"], actor=actor, detail={"failures": feedback})
    return DraftOutcome(Status.DRAFT_FAILED, draft_id, campaign.max_draft_attempts, reason)
