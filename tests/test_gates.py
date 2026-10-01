from outreach.gates import check_history, email_available, qualify, verify_email
from outreach.models import Status

from .conftest import TODAY, make_lead


# --- qualified doctor? ---

def test_a_consultant_cardiologist_in_the_uk_qualifies(campaign):
    assert qualify(make_lead(), campaign.targeting).passed


def test_wrong_grade_or_country_disqualifies_with_every_reason(campaign):
    result = qualify(make_lead(grade="Foundation doctor", country="Canada"), campaign.targeting)
    assert result.status == Status.DISQUALIFIED
    assert "Canada" in result.reason and "Foundation doctor" in result.reason


def test_unrecognised_grade_goes_to_a_person_not_the_bin(campaign):
    result = qualify(make_lead(grade=None, grade_raw="Clinical Fellow"), campaign.targeting)
    assert result.status == Status.NEEDS_REVIEW
    assert "Clinical Fellow" in result.reason


def test_missing_name_goes_to_a_person(campaign):
    assert qualify(make_lead(full_name=""), campaign.targeting).status == Status.NEEDS_REVIEW


# --- email available? ---

def test_supplied_email_is_used(services):
    result, found = email_available(make_lead(), services.finder)
    assert result.passed and found is None


def test_missing_email_is_guessed_from_the_employer_pattern(services):
    lead = make_lead(email="", first_name="Daniel", last_name="Price")
    result, found = email_available(lead, services.finder)
    assert result.passed
    assert found.email == "daniel.price@northbridge.nhs.example"
    assert "must still pass verification" in result.reason


def test_unknown_employer_means_no_email(services):
    lead = make_lead(email="", employer="Ashdown Community Hospital")
    result, found = email_available(lead, services.finder)
    assert result.status == Status.NO_EMAIL and found is None


# --- email verified? ---

def test_named_mailbox_that_exists_is_verified(services):
    assert verify_email("priya.raman@northbridge.nhs.example", services.verifier).passed


def test_shared_inbox_is_rejected(services):
    result = verify_email("info@staldrics.nhs.example", services.verifier)
    assert result.status == Status.EMAIL_INVALID and "shared inbox" in result.reason


def test_domain_without_mail_server_is_rejected(services):
    result = verify_email("f.noor@westmere-health.example", services.verifier)
    assert result.status == Status.EMAIL_INVALID and "MX" in result.reason


def test_mailbox_that_does_not_exist_is_rejected(services):
    result = verify_email("oliver.grant@northbridge.nhs.example", services.verifier)
    assert result.status == Status.EMAIL_INVALID


def test_catch_all_domain_goes_to_a_person(services):
    result = verify_email("hannah.lewis@caldervalley.nhs.example", services.verifier, guessed=True)
    assert result.status == Status.NEEDS_REVIEW
    assert "catch-all" in result.reason and "guessed" in result.reason


def test_bad_format_is_rejected(services):
    assert verify_email("not-an-email", services.verifier).status == Status.EMAIL_INVALID


# --- not previously contacted? ---

def test_opted_out_doctor_is_suppressed(conn, campaign):
    lead = make_lead(email="laura.simmons@harrowgatepractice.example", reg_number="")
    assert check_history(conn, lead, campaign, TODAY).status == Status.SUPPRESSED


def test_contact_inside_the_cooldown_is_skipped(conn, campaign):
    lead = make_lead(email="ben.carter@riversideclinic.example", reg_number="TST100116")
    result = check_history(conn, lead, campaign, TODAY)
    assert result.status == Status.ALREADY_CONTACTED and "42 days ago" in result.reason


def test_doctor_with_a_new_email_is_matched_on_registration_number(conn, campaign):
    lead = make_lead(email="rachel.moore@staldrics.nhs.example", reg_number="TST100119")
    result = check_history(conn, lead, campaign, TODAY)
    assert result.status == Status.ALREADY_CONTACTED
    assert "registration number" in result.reason


def test_contact_outside_the_cooldown_is_allowed(conn, campaign):
    lead = make_lead(email="k.asante@northbridge.nhs.example", reg_number="TST100118")
    result = check_history(conn, lead, campaign, TODAY)
    assert result.passed and "outside the 180-day gap" in result.reason


def test_never_contacted_doctor_passes(conn, campaign):
    assert check_history(conn, make_lead(), campaign, TODAY).passed
