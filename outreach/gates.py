"""Steps 1 to 4: plain rules that decide who we may contact. No AI here."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Protocol

import dns.exception
import dns.resolver

from .models import Campaign, GateResult, Status, Targeting
from .normalise import ascii_lower, employer_key

# --- Step 1: qualified doctor? -------------------------------------------------


def qualify(lead: dict, targeting: Targeting) -> GateResult:
    if not lead["full_name"]:
        return GateResult.stop(
            Status.NEEDS_REVIEW,
            "No name on this row, so we can't address an email. A person should check it.",
        )

    not_a_fit, unclear = [], []
    if lead["country"] not in targeting.countries:
        not_a_fit.append(f"based in {lead['country'] or 'an unknown country'}")

    if lead["grade"] is None:
        unclear.append(f"grade '{lead['grade_raw'] or 'blank'}' isn't one we recognise")
    elif lead["grade"] not in targeting.grades:
        not_a_fit.append(f"grade {lead['grade']} isn't targeted")

    if lead["specialty"] is None:
        unclear.append(f"specialty '{lead['specialty_raw'] or 'blank'}' isn't one we recognise")
    elif lead["specialty"] not in targeting.specialties:
        not_a_fit.append(f"{lead['specialty']} isn't a target specialty")

    if not_a_fit:
        return GateResult.stop(Status.DISQUALIFIED, "Not a fit: " + "; ".join(not_a_fit) + ".")
    if unclear:
        return GateResult.stop(
            Status.NEEDS_REVIEW, "Can't tell yet: " + "; ".join(unclear) + ". A person should decide."
        )
    return GateResult.ok(
        f"Matches the campaign: {lead['grade']}, {lead['specialty']}, {lead['country']}.",
        grade=f"{lead['grade_raw']} -> {lead['grade']}",
        specialty=f"{lead['specialty_raw']} -> {lead['specialty']}",
        country=lead["country"],
    )


# --- Step 2: email available? --------------------------------------------------


@dataclass
class FoundEmail:
    email: str
    domain: str
    pattern: str


class EmailFinder(Protocol):
    def find(self, first_name: str, last_name: str, employer: str) -> FoundEmail | None: ...


class DirectoryFinder:
    """Guesses an address from the employer's known pattern. A real system would call an enrichment API here."""

    def __init__(self, employers: dict[str, dict]):
        self.employers = employers

    @classmethod
    def from_file(cls, path: Path) -> DirectoryFinder:
        return cls(json.loads(path.read_text(encoding="utf-8"))["employers"])

    def find(self, first_name: str, last_name: str, employer: str) -> FoundEmail | None:
        entry = self.employers.get(employer_key(employer))
        first = re.sub(r"[^a-z]", "", ascii_lower(first_name))
        last = re.sub(r"[^a-z]", "", ascii_lower(last_name))
        if not entry or not first or not last:
            return None
        local = {"first.last": f"{first}.{last}", "f.last": f"{first[0]}.{last}"}[entry["pattern"]]
        return FoundEmail(f"{local}@{entry['domain']}", entry["domain"], entry["pattern"])


def email_available(lead: dict, finder: EmailFinder) -> tuple[GateResult, FoundEmail | None]:
    if lead["email"]:
        return GateResult.ok("Email supplied in the research list.", origin="provided"), None
    found = finder.find(lead["first_name"], lead["last_name"], lead["employer"])
    if found is None:
        return (
            GateResult.stop(
                Status.NO_EMAIL,
                f"No email in the list, and we don't know {lead['employer'] or 'the employer'}'s email pattern.",
            ),
            None,
        )
    return (
        GateResult.ok(
            f"No email in the list. Guessed {found.email} from the {found.pattern} pattern at "
            f"{found.domain}. It must still pass verification.",
            origin="pattern_guess",
            pattern=found.pattern,
        ),
        found,
    )


# --- Step 3: email verified? ---------------------------------------------------

EMAIL_FORMAT = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
SHARED_INBOXES = {
    "info", "admin", "enquiries", "enquiry", "office", "reception", "contact", "hello",
    "hr", "noreply", "no-reply", "team", "support", "mail", "secretary",
}


@dataclass
class MailboxCheck:
    domain_accepts_mail: bool
    catch_all: bool
    mailbox_exists: bool | None  # None = can't tell
    problem: str = ""  # set when the domain couldn't be checked at all, for example with no internet


class EmailVerifier(Protocol):
    def check(self, email: str) -> MailboxCheck: ...


class FixtureVerifier:
    """Answers from a local file so the demo runs offline. A real system would call ZeroBounce or similar."""

    def __init__(self, domains: dict[str, dict]):
        self.domains = domains

    @classmethod
    def from_file(cls, path: Path) -> FixtureVerifier:
        return cls(json.loads(path.read_text(encoding="utf-8"))["domains"])

    def check(self, email: str) -> MailboxCheck:
        local, _, domain = email.partition("@")
        info = self.domains.get(domain)
        if not info or not info["mx"]:
            return MailboxCheck(False, False, False)
        if info["catch_all"]:
            return MailboxCheck(True, True, None)
        return MailboxCheck(True, False, local in info["mailboxes"])


class DomainCheckError(Exception):
    """The domain couldn't be looked up just now, for example with no internet or a slow DNS server."""


def mail_servers(domain: str) -> list[str]:
    """The domain's mail servers, from its MX record. [] means it has none, so email to it would bounce."""
    try:
        answer = dns.resolver.resolve(domain, "MX", lifetime=5)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except dns.exception.DNSException as exc:
        raise DomainCheckError(type(exc).__name__) from exc
    # A "null MX" (a single "." record) is how a domain says it never accepts email.
    return [host for host in (str(record.exchange).rstrip(".") for record in answer) if host]


class DnsVerifier:
    """Checks real domains for real: does the domain have a mail server?

    Made-up domains (.example and .test) keep their fixture answers, so the demo data behaves the same
    offline. No free check can confirm that a mailbox exists, so for a real address the mailbox comes back
    as "can't tell", and a person confirms it on the doctor's page.
    """

    def __init__(self, fixture: EmailVerifier, made_up: tuple[str, ...] = (".example", ".test"),
                 lookup: Callable[[str], list[str]] | None = None):
        self.fixture, self.made_up, self.lookup = fixture, made_up, lookup

    def check(self, email: str) -> MailboxCheck:
        domain = email.partition("@")[2].lower()
        if domain.endswith(self.made_up):
            return self.fixture.check(email)
        try:
            servers = (self.lookup or mail_servers)(domain)
        except DomainCheckError as exc:
            return MailboxCheck(False, False, None, problem=f"the lookup failed: {exc}")
        return MailboxCheck(bool(servers), False, None if servers else False)


def verify_email(
    email: str, verifier: EmailVerifier, guessed: bool = False, confirmed_by: str | None = None
) -> GateResult:
    """`confirmed_by` = a person who checked the mailbox themselves, for example by calling the trust.

    It only covers what a computer can't check: a catch-all domain. The format, shared inboxes, the mail
    server and a mailbox the server says doesn't exist still decide, whoever is asking.
    """
    if not EMAIL_FORMAT.match(email):
        return GateResult.stop(Status.EMAIL_INVALID, f"'{email}' isn't a valid email address.")
    local, _, domain = email.partition("@")
    if local in SHARED_INBOXES or any(part in SHARED_INBOXES for part in re.split(r"[._+-]", local)):
        return GateResult.stop(
            Status.EMAIL_INVALID, f"{email} is a shared inbox, not a named doctor."
        )
    result = verifier.check(email)
    if result.problem:
        if confirmed_by:
            return GateResult.ok(
                f"We couldn't check {domain} automatically ({result.problem}), so a person checked this "
                f"mailbox: confirmed by {confirmed_by}.",
                confirmed_by=confirmed_by,
            )
        return GateResult.stop(
            Status.NEEDS_REVIEW,
            f"We couldn't check whether {domain} accepts email ({result.problem}). Re-check it later, "
            "or confirm the mailbox yourself.",
            lookup_failed=True,
        )
    if not result.domain_accepts_mail:
        return GateResult.stop(
            Status.EMAIL_INVALID, f"{domain} has no mail server (no MX record), so this would bounce."
        )
    # A computer can't confirm the mailbox on a catch-all domain, or on any real domain without a paid
    # service. A person who checked it can.
    if (result.catch_all or result.mailbox_exists is None) and confirmed_by:
        why = "accepts every address (catch-all)" if result.catch_all else "accepts email, but no free check can confirm a mailbox"
        return GateResult.ok(
            f"{domain} {why}, so a person checked this mailbox: confirmed by {confirmed_by}.",
            confirmed_by=confirmed_by,
        )
    if result.catch_all:
        extra = " It was also guessed, so it may be wrong." if guessed else ""
        return GateResult.stop(
            Status.NEEDS_REVIEW,
            f"{domain} accepts every address (catch-all), so we can't confirm this mailbox exists.{extra} "
            "A person should check before we send.",
            catch_all=True,
        )
    if result.mailbox_exists is None:
        return GateResult.stop(
            Status.NEEDS_REVIEW,
            f"{domain} accepts email (we checked its mail server), but no free check can confirm this "
            "mailbox exists. A person should confirm it before we send.",
            live_check=True,
        )
    if not result.mailbox_exists:
        return GateResult.stop(
            Status.EMAIL_INVALID,
            f"The mail server says {email} doesn't exist. The doctor may have moved on.",
        )
    return GateResult.ok(
        "Verified: valid format, named person, domain accepts mail, mailbox exists.",
        guessed=guessed,
    )


# --- Step 4: not previously contacted? -----------------------------------------


def check_history(
    conn: sqlite3.Connection, lead: dict, campaign: Campaign, today: date
) -> GateResult:
    email, reg = lead["email"], lead["reg_number"] or None

    suppressed = conn.execute(
        "SELECT reason, added_at FROM suppression WHERE email = ?", (email,)
    ).fetchone()
    if suppressed:
        return GateResult.stop(
            Status.SUPPRESSED,
            f"On the do-not-contact list since {suppressed['added_at'][:10]}: {suppressed['reason']}.",
        )

    previous = conn.execute(
        """
        SELECT campaign_id, email, reg_number, sent_at FROM outreach_log
        WHERE status IN ('sent', 'sending', 'imported')
          AND (email = ? OR (? IS NOT NULL AND reg_number = ?))
          AND (lead_id IS NULL OR lead_id != ?)
        ORDER BY sent_at DESC
        """,
        (email, reg, reg, lead["id"]),
    ).fetchall()
    if not previous:
        return GateResult.ok("No previous outreach found for this email or registration number.")

    latest = previous[0]
    sent_on = date.fromisoformat(latest["sent_at"][:10])
    days = (today - sent_on).days
    matched = "email" if latest["email"] == email else "registration number (they had a different email then)"
    if any(row["campaign_id"] == campaign.id for row in previous):
        return GateResult.stop(
            Status.ALREADY_CONTACTED, f"Already contacted in this campaign on {sent_on}."
        )
    if days < campaign.cooldown_days:
        return GateResult.stop(
            Status.ALREADY_CONTACTED,
            f"Contacted {days} days ago ({sent_on}, campaign '{latest['campaign_id']}'), matched on {matched}. "
            f"We wait {campaign.cooldown_days} days between contacts.",
        )
    return GateResult.ok(
        f"Last contacted {days} days ago ({sent_on}), outside the {campaign.cooldown_days}-day gap.",
        last_contacted=str(sent_on),
    )
