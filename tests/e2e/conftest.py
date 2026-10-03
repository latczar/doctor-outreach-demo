"""Browser tests run against a real copy of the app, started on a free port for each test."""

import socket
import threading
import time
from dataclasses import replace

import pytest

pytest.importorskip("playwright", reason="the browser tests need Playwright: pip install -r requirements-dev.txt")

import uvicorn  # noqa: E402  (after the skip, so the rest of the suite runs without Playwright)

from outreach.llm.fake import TemplateLLM  # noqa: E402
from outreach.web.app import create_app  # noqa: E402


@pytest.fixture
def app_url(settings, conn):
    """A fresh copy of the app: its own database holding the made-up research list, the template writer instead
    of the local model, and emails saved to the outbox folder. Your demo data and inbox are never touched."""
    app = create_app(replace(settings), llm=TemplateLLM())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("The test copy of the app didn't start within 10 seconds.")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
