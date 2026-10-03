"""The research lists in samples/, for uploading in the demo: every row ends where the demo script says it will."""

from outreach.config import ROOT
from outreach.models import Status
from outreach.notify import FileNotifier
from outreach.pipeline import run_pipeline

SAMPLES = ROOT / "samples"


def statuses(conn) -> list[tuple[str, str]]:
    return [(r["full_name"], r["status"]) for r in conn.execute("SELECT full_name, status FROM leads ORDER BY id")]


def test_the_quick_list_merges_a_copy_turns_away_a_misfit_and_drafts_four_emails(conn, services):
    run_pipeline(conn, services, SAMPLES / "demo-quick.csv")
    assert statuses(conn) == [
        ("Ruth Adeyinka", Status.PENDING_APPROVAL),
        ("Ruth Adeyinka", Status.DUPLICATE),  # the same registration number, from a second source
        ("Samir Haddad", Status.PENDING_APPROVAL),
        ("Ellen Ford", Status.PENDING_APPROVAL),
        ("Clare Dunmore", Status.PENDING_APPROVAL),
        ("Peter Lund", Status.DISQUALIFIED),  # dermatology isn't targeted
    ]


def test_the_every_rule_list_stops_someone_at_each_step_for_a_different_reason(conn, services, tmp_path):
    services.notifier = FileNotifier(tmp_path / "alerts.log")
    run_pipeline(conn, services, SAMPLES / "demo-every-rule.csv")
    assert statuses(conn) == [
        ("Amara Osei", Status.PENDING_APPROVAL),
        ("Amara Osei", Status.DUPLICATE),  # same registration number, email in capitals
        ("Rory Kearns", Status.PENDING_APPROVAL),  # "ST6", "A&E" and "England" are understood
        ("Owen Hale", Status.PENDING_APPROVAL),  # no email in the list: worked out from the trust's pattern
        ("Fiona Ashdown", Status.DISQUALIFIED),  # FY2 is too junior
        ("Hugo Laurent", Status.DISQUALIFIED),  # based in France
        ("Megan Holt", Status.NEEDS_REVIEW),  # "Clinical Lead" isn't a grade the rules know
        ("Ravi Sethi", Status.NO_EMAIL),  # Hillcrest Surgery's email pattern is unknown
        ("Joanna Hartley", Status.EMAIL_INVALID),  # enquiries@ is a shared inbox
        ("Tomasz Nowak", Status.EMAIL_INVALID),  # the domain has no mail server
        ("Alice Brennan", Status.NEEDS_REVIEW),  # catch-all domain: a person confirms the mailbox
        ("Ben Carter", Status.ALREADY_CONTACTED),  # emailed in August, by an earlier campaign
        ("Laura Simmons", Status.SUPPRESSED),  # asked not to be contacted
        # The planted instruction raises the alert. The template writer copies its £500 and "endorsed by the GMC"
        # into the draft, and the money and affiliation checks stop it every time. The real model usually ignores it.
        ("Kofi Mensah", Status.DRAFT_FAILED),
    ]
    owen = conn.execute("""SELECT l.email_origin, d.body FROM leads l JOIN drafts d ON d.lead_id = l.id
                           WHERE l.full_name = 'Owen Hale'""").fetchone()
    assert owen["email_origin"] == "pattern_guess" and owen["body"].startswith("Dear Mr Hale,")
    alert = conn.execute("SELECT detail FROM audit_log WHERE event = 'alert.raised'").fetchall()
    assert len(alert) == 1 and "Kofi Mensah" in alert[0]["detail"]
