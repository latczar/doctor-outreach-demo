"""Command line: python -m outreach <reset|run|funnel|send|export|serve>."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import get_settings
from .db import connect
from .export import export_csv
from .llm import LLMError, describe
from .models import STATUS_LABELS, Status, load_campaign
from .pipeline import build_services, funnel, reset_data, run_pipeline
from .notify import make_notifier
from .sending import make_sender, send_approved


def print_funnel(conn, campaign_id: str) -> None:
    view = funnel(conn, campaign_id)
    print(f"\n  Research list: {view['rows']} rows, {view['unique']} doctors after merging "
          f"{view['duplicates']} duplicates\n")
    for step in view["steps"]:
        bar = "#" * round(25 * step["count"] / max(view["rows"], 1))
        print(f"  {step['label']:<28} {step['count']:>3}  {bar}")
        for code, n in {**step["waiting"], **step["stopped"]}.items():
            print(f"  {'':<28}      - {n} {STATUS_LABELS[Status(code)].lower()}")
    print()


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(prog="python -m outreach", description="Doctor outreach demo")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("reset", help="empty the database and load the made-up history and opt-outs")
    run = commands.add_parser("run", help="import the research list, run every check and write drafts")
    run.add_argument("--file", type=Path, help="research CSV (default: data/seed/leads_raw.csv)")
    commands.add_parser("funnel", help="show how many doctors passed each step")
    commands.add_parser("send", help="send every approved email")
    export = commands.add_parser("export", help="write the final list as CSV")
    export.add_argument("--all", action="store_true", help="include every doctor, not just the final list")
    serve = commands.add_parser("serve", help="start the web dashboard")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    settings = get_settings()
    conn = connect(settings.db_path)
    campaign = load_campaign(settings.campaign_path)

    if args.command == "reset":
        reset_data(conn, settings.seed_dir)
        for eml in settings.outbox_dir.glob("*.eml"):
            eml.unlink()
        print("Database emptied. Loaded the made-up outreach history and do-not-contact list.")
        print("Next: python -m outreach run")

    elif args.command == "run":
        try:
            services = build_services(settings)
        except LLMError as exc:
            print(f"Can't start: {exc}")
            return 1
        print(f"Model: {describe(settings)}")
        run_pipeline(conn, services, args.file or settings.seed_dir / "leads_raw.csv",
                     on_progress=lambda text, working=False: print(text))
        print_funnel(conn, campaign.id)
        print("Next: review the drafts at http://localhost:8000/review  (python -m outreach serve)")

    elif args.command == "funnel":
        print_funnel(conn, campaign.id)

    elif args.command == "send":
        summary = send_approved(conn, campaign, make_sender(settings), settings.allowed_recipient_suffixes,
                                notifier=make_notifier(settings))
        print(summary.text())

    elif args.command == "export":
        which = "all" if args.all else "final"
        settings.exports_dir.mkdir(parents=True, exist_ok=True)
        path = settings.exports_dir / f"{campaign.id}-{which}.csv"
        path.write_text(export_csv(conn, campaign.id, which), encoding="utf-8-sig")
        print(f"Wrote {path}")

    elif args.command == "serve":
        import uvicorn

        print(f"Dashboard: http://localhost:{args.port}   (Ctrl+C to stop)")
        uvicorn.run("outreach.web.app:app", host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
