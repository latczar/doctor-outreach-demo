"""The workflow clicked through in a real browser, as a reviewer would. Laptop only.

Run them with `pytest -m e2e`, or watch them with `pytest -m e2e --headed --slowmo 500`.
"""

import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

PHONE = {"width": 390, "height": 844}


def run_the_pipeline(page: Page, app_url: str) -> None:
    page.goto(app_url + "/")
    page.get_by_role("button", name="Run the pipeline").click()
    # The Run page reports each step, and refreshes itself until the run is done.
    expect(page.get_by_role("heading", name="Run finished")).to_be_visible(timeout=30_000)


def start_reviewing(page: Page, app_url: str, name: str = "Sam Tester") -> None:
    page.goto(app_url + "/review")
    page.get_by_label("Your name").fill(name)
    page.get_by_role("button", name="Start reviewing").click()
    expect(page.get_by_role("status")).to_contain_text(f"Reviewing as {name}")


def test_the_whole_workflow_from_research_list_to_outreach_log(page: Page, app_url: str):
    run_the_pipeline(page, app_url)
    expect(page.get_by_text("Qualified doctor: 19 of 24 doctors fit the campaign")).to_be_visible()
    expect(page.get_by_text("Not previously contacted: 11 of 14")).to_be_visible()

    start_reviewing(page, app_url)
    expect(page.get_by_text("Email 1 of 11")).to_be_visible()
    page.get_by_role("button", name="Approve this email").click()
    expect(page.get_by_text("Approved. It goes out when someone clicks Send approved.")).to_be_visible()

    # Sending can't be undone, so the browser asks first.
    asked = []

    def say_yes(dialog):
        asked.append(dialog.message)
        dialog.accept()

    page.once("dialog", say_yes)
    page.get_by_role("button", name="Send approved (1)").click()
    expect(page.get_by_text("1 email saved to the outbox folder.")).to_be_visible()
    assert asked == ["Send 1 approved email now? This can’t be undone."]
    # Sending wrote to the outreach log, which the next campaign's "Not previously contacted?" step reads.
    expect(page.locator("#sent tbody").get_by_text("sent", exact=True)).to_be_visible()


def test_an_edit_that_leaves_a_placeholder_in_is_refused_and_kept(page: Page, app_url: str):
    run_the_pipeline(page, app_url)
    start_reviewing(page, app_url)
    page.get_by_text("Edit before approving").click()
    body = page.get_by_label("Body")
    body.fill(body.input_value() + "\n\nCould we speak on [DATE]?")
    page.get_by_role("button", name="Approve this email").click()

    expect(page.get_by_role("alert")).to_contain_text("This email can't be approved yet")
    expect(page.get_by_label("Body")).to_have_value(re.compile(r"\[DATE\]"))  # the edit isn't lost


def test_confirming_a_mailbox_puts_the_doctor_on_the_final_list(page: Page, app_url: str):
    run_the_pipeline(page, app_url)
    page.goto(app_url + "/doctors?show=needs")
    page.get_by_role("link", name="Dr Hannah Lewis").click()

    recheck = page.locator("form", has=page.get_by_role("button", name="Re-check with these details"))
    recheck.get_by_label("I've confirmed this mailbox exists").check()
    recheck.get_by_label("What did you check?").fill("Called the trust switchboard")
    recheck.get_by_label("Your name").fill("Sam Tester")
    recheck.get_by_role("button", name="Re-check with these details").click()

    expect(page.get_by_text("Now on the final list. The system wrote an email")).to_be_visible()


def test_no_main_page_scrolls_sideways_on_a_phone(page: Page, app_url: str):
    run_the_pipeline(page, app_url)
    page.set_viewport_size(PHONE)
    for path in ("/", "/doctors", "/review", "/log", "/replay", "/architecture"):
        page.goto(app_url + path)
        width = page.evaluate("document.documentElement.scrollWidth")
        assert width <= PHONE["width"], f"{path} is {width}px wide on a {PHONE['width']}px phone"
