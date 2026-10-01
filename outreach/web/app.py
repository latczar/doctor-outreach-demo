"""The team's web pages: overview, doctors, review, outreach log, run progress and a phone view."""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
from collections import Counter
from pathlib import Path
from urllib.parse import quote, unquote, urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Settings, get_settings
from ..db import connect, get_lead, loads
from ..drafting import Prompts, doctor_profile, draft_context
from ..export import export_csv, export_tsv
from ..guardrails import looks_like_injection, personal_overlap
from ..llm import LLM, LLMError, describe, make_llm
from ..models import INTAKE, STATUS_LABELS, WORKFLOW, Status, gates_passed, load_campaign
from ..normalise import CANONICAL_GRADES
from ..notify import make_notifier
from ..override import OverrideError, recheck, remove
from ..pipeline import build_services, funnel, reset_data, run_pipeline, step_threshold
from ..review import ReviewError, approve, regenerate, reject
from ..sending import SendError, make_sender, send_approved
from .present import (
    BUCKET_TONE,
    BUCKETS,
    GLOSSARY,
    NEEDS_PERSON,
    bucket_of,
    describe_event,
    event_detail,
    explain_terms,
    headline,
    highlight,
    highlight_notes,
    phrase,
    step_status,
    term_id,
    timeline,
    tone,
    tracker,
    where_now,
    who,
)
from .replay import build_replay

HERE = Path(__file__).parent
THEMES = ("light", "dark")
STATE_WORDS = {"done": "passed", "waiting": "waiting", "person": "needs you", "stopped": "stopped here",
               "todo": "not reached"}
FILTER_KEYS = ("q", "show", "step", "specialty", "country")


class RunState:
    """Progress of the background run, shown on the Run page as plain sentences."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.lines: list[str] = []
        self.current = ""
        self.error: str | None = None

    def progress(self, text: str, working: bool = False) -> None:
        if working:
            self.current = text
        else:
            self.lines.append(text)
            self.current = ""


def filter_leads(leads: list[dict], q: str = "", show: str = "", step: str = "", specialty: str = "",
                 country: str = "") -> tuple[list[dict], dict | None]:
    """The Doctors page's filters. Its downloads use the same function, so a file always matches the screen."""
    heading = None
    keys = [s["key"] for s in WORKFLOW]
    if step == INTAKE["key"]:
        leads, heading = [l for l in leads if l["status"] == Status.DUPLICATE], INTAKE
    elif step == "log":
        leads, heading = [l for l in leads if l["status"] == Status.SENT], WORKFLOW[-1]
    elif step in keys:
        i = keys.index(step)
        need = step_threshold(i)
        leads = [l for l in leads if l["status"] != Status.DUPLICATE
                 and gates_passed(l["status"], l["stage"]) == need - 1]
        heading = WORKFLOW[i]
    if show in BUCKETS:
        leads = [l for l in leads if bucket_of(l["status"]) == show]
    if specialty:
        leads = [l for l in leads if l["specialty"] == specialty]
    if country:
        leads = [l for l in leads if l["country"] == country]
    words = q.lower().split()
    if words:
        fields = ("title", "full_name", "employer", "specialty", "grade", "email", "reg_number", "source")
        leads = [l for l in leads if all(w in " ".join(str(l[f] or "") for f in fields).lower() for w in words)]
    return leads, heading


def restore_snapshot(settings: Settings) -> None:
    """Load the snapshot of a real run into the database, with SQLite's own backup, so it's safe while running."""
    source, target = sqlite3.connect(settings.snapshot_path), connect(settings.db_path)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()


def safe_path(path: str | None) -> str:
    """Only paths on this site, so a link can't send someone elsewhere."""
    return path if path and path.startswith("/") and not path.startswith("//") else "/"


def create_app(settings: Settings | None = None, llm: LLM | None = None) -> FastAPI:
    settings = settings or get_settings()
    campaign = load_campaign(settings.campaign_path)
    notifier = make_notifier(settings)
    online_snapshot = settings.demo_online and settings.snapshot_path.exists()
    if online_snapshot and not settings.db_path.exists():
        # A fresh online server starts from a snapshot of a real run, so the first drafts are real AI output.
        restore_snapshot(settings)
    app = FastAPI(title="Doctor outreach demo")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(
        status_label=lambda s: STATUS_LABELS[Status(s)], tone=tone, buckets=BUCKETS, bucket_tone=BUCKET_TONE,
        phrase=phrase, step_status=step_status, state_words=STATE_WORDS, needs_person=NEEDS_PERSON,
        intake=INTAKE, who=who, describe_event=describe_event, event_detail=event_detail,
        glossary=GLOSSARY, term_id=term_id,
    )
    templates.env.filters.update(fromjson=lambda value: loads(value, []), headline=headline, explain=explain_terms)
    state = RunState()
    cache: dict[str, LLM] = {}

    def get_llm() -> LLM:
        if llm is not None:
            return llm
        if "llm" not in cache:
            cache["llm"] = make_llm(settings)
        return cache["llm"]

    def model_label() -> str:
        return llm.name if llm is not None else describe(settings)

    def all_leads(conn) -> list[dict]:
        return [dict(r) for r in conn.execute("SELECT * FROM leads WHERE campaign_id = ? ORDER BY id", (campaign.id,))]

    def sending_label() -> str:
        route = {"outbox": "saved to the outbox folder", "smtp": "through SMTP",
                 "resend": "through Resend"}.get(settings.email_sender, settings.email_sender)
        return f"to the demo inbox, {route}" if settings.demo_inbox else route

    def render(request: Request, name: str, conn, **context) -> HTMLResponse:
        flash = None
        if raw := request.cookies.get("flash"):
            try:
                flash = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
            except ValueError:
                flash = None
        counts = dict(conn.execute(
            "SELECT status, COUNT(*) FROM leads WHERE campaign_id = ? GROUP BY status", (campaign.id,)
        ).fetchall())
        theme = request.cookies.get("theme")
        here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        context.update(
            campaign=campaign, model=model_label(), sending=sending_label(), demo_inbox=bool(settings.demo_inbox),
            run=state, flash=flash,
            counts=counts, reviewer=unquote(request.cookies.get("reviewer", "")),
            theme=theme if theme in THEMES else "light", here=here, demo_online=settings.demo_online,
        )
        response = templates.TemplateResponse(request, name, context)
        if flash:
            response.delete_cookie("flash")
        return response

    def redirect(url: str, message: str, kind: str = "ok", reviewer: str | None = None) -> RedirectResponse:
        response = RedirectResponse(url, status_code=303)
        payload = base64.urlsafe_b64encode(json.dumps({"text": message, "kind": kind}).encode()).decode()
        response.set_cookie("flash", payload, max_age=30, httponly=True, samesite="lax")
        if reviewer:
            response.set_cookie("reviewer", quote(reviewer), max_age=60 * 60 * 24 * 30, samesite="lax")
        return response

    # --- overview ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        conn = connect(settings.db_path)
        try:
            alerts = [dict(r) for r in conn.execute(
                "SELECT * FROM audit_log WHERE event = 'alert.raised' ORDER BY id DESC LIMIT 5")]
            return render(request, "overview.html", conn, view=funnel(conn, campaign.id), alerts=alerts)
        finally:
            conn.close()

    @app.post("/run")
    def start_run():
        with state.lock:
            if state.running:
                return RedirectResponse("/run", status_code=303)
            state.running, state.lines, state.current, state.error = True, [], "Starting...", None

        def work() -> None:
            conn = connect(settings.db_path)
            try:
                services = build_services(settings, llm=get_llm())
                run_pipeline(conn, services, settings.seed_dir / "leads_raw.csv", on_progress=state.progress)
            except LLMError as exc:
                state.error = str(exc)
            except Exception as exc:  # shown on the Run page rather than lost in a thread
                state.error = f"Unexpected error: {exc}"
                raise
            finally:
                state.running, state.current = False, ""
                conn.close()

        if settings.demo_online:
            work()  # an online server may pause between requests, so the run finishes before the page loads
        else:
            threading.Thread(target=work, daemon=True).start()
        return RedirectResponse("/run", status_code=303)

    @app.get("/run", response_class=HTMLResponse)
    def run_page(request: Request):
        conn = connect(settings.db_path)
        try:
            return render(request, "run.html", conn)
        finally:
            conn.close()

    @app.post("/send")
    def send():
        try:
            sender = make_sender(settings)
        except SendError as exc:
            return redirect("/log", str(exc), "bad")
        conn = connect(settings.db_path)
        try:
            summary = send_approved(conn, campaign, sender, settings.allowed_recipient_suffixes, notifier=notifier)
        finally:
            conn.close()
        kind = "ok" if not (summary.blocked or summary.failed) else "warn"
        return redirect("/log", summary.text(), kind)

    @app.post("/reset")
    def reset():
        if state.running:
            return redirect("/", "Wait for the current run to finish first.", "warn")
        for eml in settings.outbox_dir.glob("*.eml"):
            eml.unlink()
        state.lines, state.error = [], None
        if online_snapshot:
            # Online, the local AI model isn't available, so go back to the real drafts rather than an empty list.
            restore_snapshot(settings)
            return redirect("/", "Demo reset to the starting snapshot, with the original drafts.")
        conn = connect(settings.db_path)
        try:
            reset_data(conn, settings.seed_dir)
        finally:
            conn.close()
        return redirect("/", "Demo reset. Click Run the pipeline to start again.")

    # --- doctors, the final list and its downloads -----------------------------------

    def filtered(conn, params: dict) -> tuple[list[dict], dict | None, list[dict]]:
        everyone = all_leads(conn)
        shown, heading = filter_leads(everyone, **{k: params.get(k, "") for k in FILTER_KEYS})
        return shown, heading, everyone

    @app.get("/doctors", response_class=HTMLResponse)
    def doctors(request: Request, q: str = "", show: str = "", step: str = "", specialty: str = "",
                country: str = ""):
        filters = {"q": q, "show": show, "step": step, "specialty": specialty, "country": country}
        conn = connect(settings.db_path)
        try:
            shown, heading, everyone = filtered(conn, filters)
            keep = {k: v for k, v in filters.items() if v and k in ("q", "specialty", "country")}
            tabs = [{"label": "Everyone", "tone": "", "count": len(everyone), "on": not show,
                     "href": "/doctors?" + urlencode(keep)}]
            bucket_counts = Counter(bucket_of(l["status"]) for l in everyone)
            for key, label in BUCKETS.items():
                if bucket_counts[key]:
                    tabs.append({"label": label, "tone": BUCKET_TONE[key], "count": bucket_counts[key],
                                 "on": show == key, "href": "/doctors?" + urlencode({**keep, "show": key})})
            return render(
                request, "doctors.html", conn, filters=filters, heading=heading, tabs=tabs, total=len(everyone),
                rows=[{"lead": l, "tracker": tracker(l), "where": where_now(l), "bucket": bucket_of(l["status"])}
                      for l in shown],
                specialties=sorted({l["specialty"] for l in everyone if l["specialty"]}),
                countries=sorted({l["country"] for l in everyone if l["country"]}),
                query_string=urlencode({k: v for k, v in filters.items() if v}),
            )
        finally:
            conn.close()

    @app.get("/export.csv")
    def export_filtered(request: Request):
        conn = connect(settings.db_path)
        try:
            shown, _, _ = filtered(conn, dict(request.query_params))
            text = export_csv(conn, campaign.id, leads=shown)
        finally:
            conn.close()
        name = "final-list" if request.query_params.get("show") == "final" else "doctors"
        return Response(
            text.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",  # the BOM helps Excel read £ signs
            headers={"Content-Disposition": f'attachment; filename="{campaign.id}-{name}.csv"'},
        )

    @app.get("/export/copy", response_class=HTMLResponse)
    def export_copy(request: Request):
        conn = connect(settings.db_path)
        try:
            shown, _, _ = filtered(conn, dict(request.query_params))
            return render(request, "export_copy.html", conn, tsv=export_tsv(conn, shown), count=len(shown),
                          query_string=urlencode({k: v for k, v in request.query_params.items() if v}))
        finally:
            conn.close()

    @app.get("/export/{which}.csv")
    def export(which: str):
        which = "all" if which == "all" else "final"
        conn = connect(settings.db_path)
        try:
            text = export_csv(conn, campaign.id, which)
        finally:
            conn.close()
        return Response(
            text.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{campaign.id}-{which}.csv"'},
        )

    # --- review, one email at a time -------------------------------------------------

    def review_items(conn, statuses: tuple[str, ...]) -> list[dict]:
        placeholders = ", ".join("?" for _ in statuses)
        leads = conn.execute(
            f"SELECT * FROM leads WHERE campaign_id = ? AND status IN ({placeholders}) ORDER BY id",
            (campaign.id, *statuses),
        ).fetchall()
        items = []
        for row in leads:
            lead = dict(row)
            draft = conn.execute("SELECT * FROM drafts WHERE lead_id = ? ORDER BY id DESC LIMIT 1",
                                 (lead["id"],)).fetchone()
            draft = dict(draft) if draft else {}
            checks = loads(draft.get("checks"), [])
            # The same word matching the personalised check used, so the highlight shows what it saw.
            stems, _ = personal_overlap(draft_context(lead, campaign), draft.get("body") or "")
            items.append({
                "lead": lead,
                "draft": draft,
                "gates": loads(lead["gate_results"], {}),
                "salutation": doctor_profile(lead)["salutation"],
                "injection": looks_like_injection(lead["profile_notes"]),
                "attempts": conn.execute("SELECT COUNT(*) FROM drafts WHERE lead_id = ?", (lead["id"],)).fetchone()[0],
                "checks": checks,
                "failed": [c for c in checks if not c["ok"]],
                "fixes": loads(draft.get("fixes"), []),
                "notes_html": highlight_notes(lead["profile_notes"], stems),
                "body_html": highlight(draft.get("body"), stems),
            })
        return items

    def render_review(request: Request, errors: dict | None = None, edits: dict | None = None,
                      selected: int | None = None) -> HTMLResponse:
        conn = connect(settings.db_path)
        try:
            pending = review_items(conn, (Status.PENDING_APPROVAL,))
            attention = review_items(conn, (Status.DRAFT_FAILED,))
            queue = pending + attention
            current = next((i for i in queue if i["draft"].get("id") == selected), queue[0] if queue else None)
            index = queue.index(current) if current else -1
            return render(
                request, "review.html", conn,
                pending=pending, attention=attention, approved=review_items(conn, (Status.APPROVED,)),
                queue=queue, current=current, index=index,
                previous=queue[index - 1] if index > 0 else None,
                following=queue[index + 1] if 0 <= index < len(queue) - 1 else None,
                errors=errors or {}, edits=edits or {},
            )
        finally:
            conn.close()

    @app.get("/review", response_class=HTMLResponse)
    def review_page(request: Request, d: int | None = None):
        return render_review(request, selected=d)

    @app.post("/reviewer")
    def set_reviewer(reviewer: str = Form("")):
        name = " ".join(reviewer.split())[:60]
        if not name:
            return redirect("/review", "Enter your name to start reviewing.", "warn")
        return redirect("/review", f"Reviewing as {name}.", reviewer=name)

    @app.post("/drafts/{draft_id}/approve")
    def approve_draft(request: Request, draft_id: int, reviewer: str = Form(""), subject: str = Form(...),
                      body: str = Form(...)):
        conn = connect(settings.db_path)
        try:
            approve(conn, draft_id, reviewer, campaign, subject=subject, body=body)
        except ReviewError as exc:
            return render_review(request, errors={draft_id: str(exc)},
                                 edits={draft_id: {"subject": subject, "body": body}}, selected=draft_id)
        finally:
            conn.close()
        return redirect("/review", "Approved. It goes out when someone clicks Send approved.", reviewer=reviewer)

    @app.post("/drafts/{draft_id}/reject")
    def reject_draft(request: Request, draft_id: int, reviewer: str = Form(""), note: str = Form("")):
        conn = connect(settings.db_path)
        try:
            reject(conn, draft_id, reviewer, note)
        except ReviewError as exc:
            return render_review(request, errors={draft_id: str(exc)}, selected=draft_id)
        finally:
            conn.close()
        return redirect("/review", "Rejected. The reason is saved on the doctor's record.", reviewer=reviewer)

    @app.post("/drafts/{draft_id}/regenerate")
    def regenerate_draft(request: Request, draft_id: int, reviewer: str = Form(""), instruction: str = Form("")):
        conn = connect(settings.db_path)
        try:
            outcome = regenerate(conn, draft_id, reviewer, instruction, campaign, get_llm(),
                                 build_services(settings, llm=get_llm()).prompts)
        except (ReviewError, LLMError) as exc:
            return render_review(request, errors={draft_id: str(exc)}, selected=draft_id)
        finally:
            conn.close()
        if outcome.status == Status.PENDING_APPROVAL:
            return redirect(f"/review?d={outcome.draft_id}", "New draft ready. It passed every check.",
                            reviewer=reviewer)
        return redirect(f"/review?d={outcome.draft_id}", "The new draft still failed our checks.", "warn",
                        reviewer=reviewer)

    # --- one doctor, the log, settings ---------------------------------------------

    def render_lead(request: Request, lead_id: int, decide_error: str | None = None,
                    values: dict | None = None) -> HTMLResponse:
        conn = connect(settings.db_path)
        try:
            lead = get_lead(conn, lead_id)
            if lead is None:
                return HTMLResponse("Doctor not found.", status_code=404)
            drafts = [dict(r) for r in conn.execute("SELECT * FROM drafts WHERE lead_id = ? ORDER BY id", (lead_id,))]
            sends = [dict(r) for r in conn.execute("SELECT * FROM outreach_log WHERE lead_id = ?", (lead_id,))]
            return render(
                request, "lead.html", conn, lead=lead, drafts=drafts, sends=sends,
                steps=timeline(lead, drafts, sends), where=where_now(lead), dots=tracker(lead),
                salutation=doctor_profile(lead)["salutation"] if lead["last_name"] else "",
                notes_html=highlight_notes(lead["profile_notes"], set()),
                injection=looks_like_injection(lead["profile_notes"]),
                events=[dict(r) for r in conn.execute("SELECT * FROM audit_log WHERE lead_id = ? ORDER BY id",
                                                      (lead_id,))],
                duplicates=[dict(r) for r in conn.execute("SELECT * FROM leads WHERE duplicate_of = ? ORDER BY id",
                                                          (lead_id,))],
                merged_from=loads(lead["merge_notes"], {}),
                grades=CANONICAL_GRADES, targeted_grades=campaign.targeting.grades,
                decide_error=decide_error, values=values or {},
            )
        finally:
            conn.close()

    @app.get("/leads/{lead_id}", response_class=HTMLResponse)
    def lead_page(request: Request, lead_id: int):
        return render_lead(request, lead_id)

    @app.post("/leads/{lead_id}/recheck")
    def recheck_lead(request: Request, lead_id: int, reviewer: str = Form(""), note: str = Form(""),
                     title: str = Form(""), full_name: str = Form(""), grade: str = Form(""),
                     email: str = Form(""), confirm_email: str = Form("")):
        conn = connect(settings.db_path)
        try:
            outcome = recheck(conn, lead_id, build_services(settings, llm=get_llm()), reviewer, note,
                              title=title, full_name=full_name, grade=grade, email=email,
                              confirm_email=confirm_email == "yes")
        except (OverrideError, LLMError) as exc:
            return render_lead(request, lead_id, decide_error=str(exc),
                               values={"note": note, "title": title, "full_name": full_name, "grade": grade,
                                       "email": email, "confirm_email": confirm_email == "yes"})
        finally:
            conn.close()
        kind = "ok" if outcome.status in (Status.PENDING_APPROVAL, Status.READY_TO_DRAFT) else "warn"
        return redirect(f"/leads/{lead_id}", outcome.message, kind, reviewer=reviewer)

    @app.post("/leads/{lead_id}/remove")
    def remove_lead(request: Request, lead_id: int, reviewer: str = Form(""), note: str = Form("")):
        conn = connect(settings.db_path)
        try:
            remove(conn, lead_id, reviewer, note)
        except OverrideError as exc:
            return render_lead(request, lead_id, decide_error=str(exc), values={"remove_note": note})
        finally:
            conn.close()
        return redirect("/doctors?show=needs", "Removed from the list. Your reason is saved on the record.",
                        reviewer=reviewer)

    @app.get("/log", response_class=HTMLResponse)
    def log_page(request: Request):
        conn = connect(settings.db_path)
        try:
            return render(
                request, "log.html", conn,
                sends=[dict(r) for r in conn.execute("SELECT * FROM outreach_log ORDER BY sent_at DESC, id DESC")],
                alerts=[dict(r) for r in conn.execute(
                    "SELECT * FROM audit_log WHERE event = 'alert.raised' ORDER BY id DESC")],
                events=[dict(r) for r in conn.execute(
                    """SELECT a.*, l.full_name FROM audit_log a LEFT JOIN leads l ON l.id = a.lead_id
                       ORDER BY a.id DESC LIMIT 150""")],
                suppressed=[dict(r) for r in conn.execute("SELECT * FROM suppression ORDER BY added_at")],
            )
        finally:
            conn.close()

    @app.get("/theme/{mode}")
    def set_theme(mode: str, back: str = "/"):
        response = RedirectResponse(safe_path(back), status_code=303)
        if mode in THEMES:
            response.set_cookie("theme", mode, max_age=60 * 60 * 24 * 365, samesite="lax")
        return response

    @app.get("/replay", response_class=HTMLResponse)
    def replay_page(request: Request):
        """The last run, played back from the audit log. It reads only: nothing is drafted or sent."""
        conn = connect(settings.db_path)
        try:
            replay = build_replay(conn, campaign, Prompts.load(settings.prompts_dir))
            return render(request, "replay.html", conn, replay=replay)
        finally:
            conn.close()

    @app.get("/phone", response_class=HTMLResponse)
    def phone(request: Request, path: str = "/"):
        conn = connect(settings.db_path)
        try:
            return render(request, "phone.html", conn, target=safe_path(path))
        finally:
            conn.close()

    return app


app = create_app()
