"""Campaign config, lead statuses and gate results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Sender(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    role: str
    organisation: str
    email: str


class Targeting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    countries: list[str]
    specialties: list[str]
    grades: list[str]


class Campaign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    purpose: str
    sender: Sender
    offer_facts: list[str]
    targeting: Targeting
    cooldown_days: int
    daily_send_cap: int
    max_draft_attempts: int


def load_campaign(path: Path) -> Campaign:
    return Campaign.model_validate_json(path.read_text(encoding="utf-8"))


class Status(StrEnum):
    NEW = "NEW"
    DUPLICATE = "DUPLICATE"
    DISQUALIFIED = "DISQUALIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NO_EMAIL = "NO_EMAIL"
    EMAIL_INVALID = "EMAIL_INVALID"
    ALREADY_CONTACTED = "ALREADY_CONTACTED"
    SUPPRESSED = "SUPPRESSED"
    READY_TO_DRAFT = "READY_TO_DRAFT"
    DRAFT_FAILED = "DRAFT_FAILED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SENT = "SENT"
    SEND_BLOCKED = "SEND_BLOCKED"
    SEND_FAILED = "SEND_FAILED"


STATUS_LABELS = {
    Status.NEW: "Not processed yet",
    Status.DUPLICATE: "Duplicate (merged)",
    Status.DISQUALIFIED: "Disqualified",
    Status.NEEDS_REVIEW: "Needs a person",
    Status.NO_EMAIL: "No email found",
    Status.EMAIL_INVALID: "Bad email",
    Status.ALREADY_CONTACTED: "Contacted recently",
    Status.SUPPRESSED: "Opted out",
    Status.READY_TO_DRAFT: "Waiting for a draft",
    Status.DRAFT_FAILED: "Draft needs attention",
    Status.PENDING_APPROVAL: "Waiting for review",
    Status.APPROVED: "Approved, not sent yet",
    Status.REJECTED: "Rejected by reviewer",
    Status.SENT: "Sent",
    Status.SEND_BLOCKED: "Blocked at send",
    Status.SEND_FAILED: "Send failed",
}

# The workflow from the brief, in its own wording. `key` matches a lead's `stage`.
WORKFLOW = [
    {"key": "qualify", "label": "Qualified doctor",
     "how": "Plain rules from the campaign file: country, specialty and grade.",
     "summary": "{count} of {of} doctors fit the campaign"},
    {"key": "email_available", "label": "Email available?",
     "how": "Taken from the research list, or guessed from the employer's email pattern.",
     "summary": "{count} of {of} have an email address"},
    {"key": "email_verified", "label": "Email verified?",
     "how": "Valid format, a named person (not info@), the domain accepts mail, and the mailbox exists.",
     "summary": "{count} of {of} emails are verified"},
    {"key": "not_contacted", "label": "Not previously contacted?",
     "how": "Not on the do-not-contact list, and not contacted within the campaign's cooldown, "
            "matched by email or registration number.",
     "summary": "{count} of {of} haven't been contacted recently"},
    {"key": "draft", "label": "Personalised email",
     "how": "The system writes a personalised draft, checks it, and rewrites it up to 3 times if a check fails.",
     "summary": "{count} of {of} drafts passed every check"},
    {"key": "approval", "label": "Human approval",
     "how": "A named person approves, edits or rejects every email.",
     "summary": "{count} of {of} emails approved by a person"},
    {"key": "send", "label": "Send",
     "how": "Checks the do-not-contact list again, sends each email once only, and keeps to the daily limit.",
     "summary": "{count} of {of} approved emails sent"},
    {"key": "log", "label": "Log outreach",
     "how": "Recorded at the moment of sending. The next campaign's history check reads this log.",
     "summary": "{count} sends logged"},
]
INTAKE = {"key": "dedupe", "label": "Research list",
          "how": "Rows are cleaned, and copies of the same doctor are merged (same registration number, "
                 "email, or name and employer)."}

# Doctors waiting at a step, as opposed to stopped there.
WAITING = {Status.NEW, Status.READY_TO_DRAFT, Status.PENDING_APPROVAL, Status.APPROVED}


def gates_passed(status: str, stage: str) -> int:
    """How many of the gates after dedupe this lead has passed (0 to 7)."""
    if status in (Status.DUPLICATE, Status.NEW):
        return 0
    if status == Status.NEEDS_REVIEW:
        return 0 if stage == "qualify" else 2
    return {
        Status.DISQUALIFIED: 0,
        Status.NO_EMAIL: 1,
        Status.EMAIL_INVALID: 2,
        Status.ALREADY_CONTACTED: 3,
        Status.SUPPRESSED: 3,
        Status.READY_TO_DRAFT: 4,
        Status.DRAFT_FAILED: 4,
        Status.PENDING_APPROVAL: 5,
        Status.REJECTED: 5,
        Status.APPROVED: 6,
        Status.SEND_BLOCKED: 6,
        Status.SEND_FAILED: 6,
        Status.SENT: 7,
    }[Status(status)]


def on_final_list(status: str) -> bool:
    """Cleared to contact: passed every check before drafting, and not removed later by a person or a rule."""
    return gates_passed(status, "") >= 4 and status not in (Status.REJECTED, Status.SEND_BLOCKED)


@dataclass
class GateResult:
    passed: bool
    reason: str
    status: Status | None = None  # set when the lead stops here
    evidence: dict = field(default_factory=dict)

    @classmethod
    def ok(cls, reason: str, **evidence) -> GateResult:
        return cls(True, reason, None, evidence)

    @classmethod
    def stop(cls, status: Status, reason: str, **evidence) -> GateResult:
        return cls(False, reason, status, evidence)
