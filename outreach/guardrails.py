"""Checks on every draft. The model writes; this code decides whether the draft is fit to show a person."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, ValidationError

from .models import Campaign


class DraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str
    body: str
    personal_detail_used: str = ""


DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "personal_detail_used": {"type": "string"},
    },
    "required": ["subject", "body", "personal_detail_used"],
    "additionalProperties": False,
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class DraftContext:
    """What the checks compare a draft against."""

    salutation: str
    specialty: str
    profile_notes: str
    profile_text: str  # every profile value, for grounding numbers
    campaign: Campaign
    job_words: str = ""  # grade and employer: true, but not personal


@dataclass
class Evaluation:
    subject: str = ""
    body: str = ""
    personal_detail_used: str = ""
    checks: list[Check] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def failures(self) -> list[str]:
        return [c.detail for c in self.checks if not c.ok]


# --- patterns ------------------------------------------------------------------

PLACEHOLDER = re.compile(
    r"\[[^\]]{0,40}\]|\{\{?[^}]{0,40}\}?\}|<[^>]{1,40}>|\b(your name|insert name|lorem ipsum|tbc)\b",
    re.IGNORECASE,
)
MONEY = re.compile(
    r"(?:£|\$|€|\bgbp ?|\busd ?)(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?) ?(?:pounds|dollars|euros)\b",
    re.IGNORECASE,
)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
AFFILIATION = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(official|approved|accredited|endorsed|recognised|recognized)\b[^.\n]{0,40}\b(nhs|partner|hospital|trust|university|gmc|royal college)\b",
        r"\bin (partnership|collaboration|association) with\b",
        r"\bpartner(ed|s|ship)? with\b",
        r"\bnhs[- ](approved|endorsed|partner|accredited)\b",
        r"\bon behalf of (the )?nhs\b",
        r"\baffiliated with\b",
        r"\bendorsed by\b",
    )
]
STOCK_PHRASES = [
    "hope this email finds you well",
    "hope this message finds you well",
    "hope this finds you well",
    "hope you are doing well",
    "hope you are well",
    "hope you're well",
    "delve",
    "tapestry",
    "in today's",
    "game-changer",
    "game changer",
    "cutting-edge",
    "esteemed",
    "don't hesitate to",
]
SIGN_OFF = re.compile(
    r"^((best|kind|warm|warmest|with best)\s+(regards|wishes)|regards|best|many thanks|thanks|thank you|"
    r"with thanks|cheers|sincerely|yours\s+(sincerely|faithfully|truly))\b[\s,!.]*$",
    re.IGNORECASE,
)
DASHES = re.compile("\\s*[\\u2013\\u2014]\\s*")  # en and em dashes, written as escapes on purpose

US_SPELLINGS = {
    "program": "programme", "programs": "programmes", "organization": "organisation",
    "organizations": "organisations", "center": "centre", "pediatric": "paediatric",
    "pediatrics": "paediatrics", "anesthesia": "anaesthesia", "anesthetics": "anaesthetics",
    "specialize": "specialise", "specialized": "specialised", "recognize": "recognise",
    "recognized": "recognised", "prioritize": "prioritise", "enrollment": "enrolment",
}

# A whole filler sentence, which code can delete safely without changing what the email says.
FILLER = re.compile(
    r"\bI (hope|trust) (that )?(this|my) (email|message|note) finds you well[^.!\n]*[.!]\s*"
    r"|\bI (hope|trust) (that )?you('re| are) (doing )?well[^.!\n]*[.!]\s*",
    re.IGNORECASE,
)
INJECTION_HINT = re.compile(
    r"ignore (all |any )?(previous|prior|above|earlier) (instructions|rules)|disregard (the|your|all) "
    r"(rules|instructions)|you are now|system prompt|new instructions",
    re.IGNORECASE,
)
STOPWORDS = {
    "about", "after", "again", "their", "there", "these", "those", "which", "while", "would",
    "where", "other", "every", "being", "doctor", "doctors", "local", "already", "recently",
    # outreach words any email might use, so they don't count as a personal detail
    "teach", "teaching", "mentor", "mentoring", "mentors", "experience", "expertise",
    "opportunity", "interest", "interested", "working",
}


def looks_like_injection(text: str | None) -> bool:
    """Shown as a warning on the review page. The fencing and checks are the real defence."""
    return bool(INJECTION_HINT.search(text or ""))


def word_stem(word: str) -> str | None:
    """A rough stem for matching: 'teaching' and 'teaches' both become 'teachi'/'teache'. Short words are ignored."""
    word = word.lower()
    return word[:6] if len(word) >= 5 and word not in STOPWORDS else None


def _stems(text: str) -> set[str]:
    return {s for w in re.findall(r"[a-z]+", text.lower()) if (s := word_stem(w))}


def _amounts(text: str) -> set[str]:
    return {(a or b).replace(",", "") for a, b in MONEY.findall(text)}


WORD_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "twelve": "12", "fifteen": "15", "twenty": "20",
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5", "sixth": "6",
}


def _numbers(text: str) -> set[str]:
    """Digits in the text, plus number words, so "two to four weeks" allows "2 to 4 weeks"."""
    digits = {n.replace(",", "").rstrip(".") for n in NUMBER.findall(text)}
    words = {WORD_NUMBERS[w] for w in re.findall(r"[a-z]+", text.lower()) if w in WORD_NUMBERS}
    return digits | words


# --- parse, tidy, check ---------------------------------------------------------


def parse_reply(text: str) -> tuple[DraftOutput | None, str | None]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return DraftOutput.model_validate(json.loads(cleaned)), None
    except (json.JSONDecodeError, ValidationError):
        return None, "The reply wasn't JSON with exactly the keys subject, body and personal_detail_used."


def tidy(subject: str, body: str, sender_name: str = "") -> tuple[str, str, list[str]]:
    """Small, safe fixes made by code (and logged) instead of a retry."""
    fixes = []
    subject = subject.strip().strip('"').strip()
    body = body.replace("\r\n", "\n").strip()

    # The model's own sign-off, from the bottom up: "Best regards,\nAlex Morgan", "Thank you, Alex Morgan".
    lines = body.split("\n")
    first_name = sender_name.split()[0].lower() if sender_name else ""
    removed_sign_off = False
    while lines:
        line = lines[-1].strip()
        lowered = line.lower().strip(" ,.!")
        signed = bool(sender_name) and (
            (sender_name.lower() in lowered and len(line.split()) <= 6) or lowered == first_name
        )
        if not line or SIGN_OFF.match(line) or signed:
            removed_sign_off = removed_sign_off or bool(line)
            lines.pop()
            continue
        break
    if removed_sign_off:
        body = "\n".join(lines).rstrip()
        fixes.append("Removed the model's own sign-off, because we add ours.")

    body, removed = FILLER.subn("", body)
    if removed:
        fixes.append('Removed a stock pleasantry such as "I hope you are well".')

    for us, uk in US_SPELLINGS.items():
        pattern = re.compile(rf"\b{us}\b", re.IGNORECASE)
        if pattern.search(body):
            body = pattern.sub(lambda m, uk=uk: uk.capitalize() if m.group(0)[0].isupper() else uk, body)
            fixes.append(f"British spelling: {us} -> {uk}.")

    if DASHES.search(body):
        body = DASHES.sub(", ", body)
        fixes.append("Replaced dashes with commas (house style).")
    return subject, body, fixes


def personal_overlap(ctx: DraftContext, body: str) -> tuple[set[str], int]:
    """Word stems the email shares with the profile notes, and how many it needs to count as personal.

    Words every email would contain anyway (the offer, our name, their job) don't count.
    The review page highlights exactly these stems, so the screen shows what the check saw.
    """
    generic = _stems(" ".join([
        ctx.campaign.purpose, " ".join(ctx.campaign.offer_facts), ctx.campaign.sender.organisation,
        ctx.specialty, ctx.job_words,
    ]))
    note_words = _stems(ctx.profile_notes) - generic
    needed = 0 if not note_words else 1 if len(note_words) <= 3 else 2  # 0 = nothing specific to use
    return note_words & _stems(body), needed


def run_checks(subject: str, body: str, personal_detail: str, ctx: DraftContext) -> list[Check]:
    checks = []
    facts_text = " ".join(ctx.campaign.offer_facts)
    full = f"{subject}\n{body}"

    greeting = f"Dear {ctx.salutation},"
    checks.append(Check(
        "greeting",
        body.startswith(greeting),
        f'Starts with "{greeting}"' if body.startswith(greeting) else f'Must start with "{greeting}" exactly.',
    ))

    found = PLACEHOLDER.search(full)
    checks.append(Check(
        "no_placeholders",
        found is None,
        "No placeholders" if found is None else f"Contains a placeholder: {found.group(0)}",
    ))

    allowed_money = _amounts(facts_text)
    bad_money = [m.group(0).strip() for m in MONEY.finditer(full)
                 if (m.group(1) or m.group(2)).replace(",", "") not in allowed_money]
    checks.append(Check(
        "no_invented_money",
        not bad_money,
        "Money matches the campaign facts" if not bad_money
        else f"Mentions an amount that isn't in the campaign facts: {', '.join(bad_money)}.",
    ))

    allowed_numbers = _numbers(facts_text) | _numbers(ctx.profile_text) | _amounts(" ".join(bad_money))
    bad_numbers = sorted(_numbers(full) - allowed_numbers)
    checks.append(Check(
        "no_invented_numbers",
        not bad_numbers,
        "Every number comes from the facts or the profile" if not bad_numbers
        else f"Mentions numbers that aren't in the facts or the profile: {', '.join(bad_numbers)}.",
    ))

    claim = next((m.group(0) for p in AFFILIATION if (m := p.search(full))), None)
    checks.append(Check(
        "no_partnership_claims",
        claim is None,
        "No partnership or endorsement claims" if claim is None
        else f'Claims an affiliation we can\'t back up: "{claim}".',
    ))

    stock = next((p for p in STOCK_PHRASES if p in full.lower()), None)
    checks.append(Check(
        "plain_language",
        stock is None,
        "No stock phrases" if stock is None else f'Uses a stock phrase: "{stock}".',
    ))

    overlap, needed = personal_overlap(ctx, body) if ctx.profile_notes else (set(), 0)
    if needed:
        personal = len(overlap) >= needed
        detail = (
            "Uses a real detail from the profile" if personal
            else "Doesn't mention anything specific from profile_notes. Their job title alone is not enough."
        )
    else:
        personal = ctx.specialty.lower() in body.lower()
        detail = "No specific profile detail to use, so checked the specialty is mentioned" if personal else (
            "Doesn't mention the doctor's specialty."
        )
    checks.append(Check("personalised", personal, detail))

    words = len(body.split())
    if 70 <= words <= 180:
        length_detail = f"{words} words"
    elif words < 20:
        length_detail = f"Body is only {words} words. Put the whole email in the body field, not just the greeting."
    else:
        length_detail = f"Body is {words} words; it should be 70 to 180."
    checks.append(Check("length", 70 <= words <= 180, length_detail))

    subject_ok = 0 < len(subject) <= 90 and not re.match(r"^(re|fw|fwd)\s*:", subject, re.IGNORECASE)
    checks.append(Check(
        "honest_subject",
        subject_ok,
        "Subject is clear" if subject_ok
        else "Subject must be 1 to 90 characters and must not pretend to be a reply (RE:/FW:).",
    ))
    return checks


def evaluate(reply_text: str, ctx: DraftContext) -> Evaluation:
    parsed, error = parse_reply(reply_text)
    if parsed is None:
        return Evaluation(checks=[Check("valid_json", False, error)])
    subject, body, fixes = tidy(parsed.subject, parsed.body, ctx.campaign.sender.name)
    return Evaluation(
        subject=subject,
        body=body,
        personal_detail_used=parsed.personal_detail_used,
        checks=run_checks(subject, body, parsed.personal_detail_used, ctx),
        fixes=fixes,
    )


def build_footer(campaign: Campaign, source: str | None) -> str:
    """Added by code to every email, so it can never be missing or wrong."""
    sender = campaign.sender
    where = f"the {source.lower()}" if source else "a public listing"
    return (
        "Best wishes,\n"
        f"{sender.name}\n"
        f"{sender.role}, {sender.organisation}\n"
        f"{sender.email}\n\n"
        f"Why you're getting this: we found your work details on {where}. "
        'If you\'d rather not hear from us, reply "unsubscribe" and we won\'t contact you again.'
    )
