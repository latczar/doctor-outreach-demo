"""Deployment entrypoint for Vercel.

Vercel looks for a FastAPI instance called `app` in a file such as index.py at the repository root.
The app lives in outreach/web/app.py; this file only re-exports it. On Vercel (which sets VERCEL=1),
the settings switch to the online demo mode: files in /tmp, a snapshot of a real run, runs in one go.
"""

from outreach.web.app import app

__all__ = ["app"]
