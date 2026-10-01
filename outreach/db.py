"""SQLite storage. One file, no server. Autocommit, with explicit transactions where several writes must land together."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY,
    campaign_id     TEXT NOT NULL,
    source_file     TEXT NOT NULL,
    source_row      INTEGER NOT NULL,
    title           TEXT,
    full_name       TEXT,
    first_name      TEXT,
    last_name       TEXT,
    grade_raw       TEXT,
    grade           TEXT,
    specialty_raw   TEXT,
    specialty       TEXT,
    employer        TEXT,
    country         TEXT,
    reg_number      TEXT,
    email           TEXT,
    email_origin    TEXT,
    source          TEXT,
    profile_notes   TEXT,
    status          TEXT NOT NULL,
    stage           TEXT NOT NULL,
    reason          TEXT,
    gate_results    TEXT NOT NULL DEFAULT '{}',
    duplicate_of    INTEGER REFERENCES leads(id),
    merge_notes     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (campaign_id, source_file, source_row)
);

CREATE TABLE IF NOT EXISTS drafts (
    id                   INTEGER PRIMARY KEY,
    lead_id              INTEGER NOT NULL REFERENCES leads(id),
    attempt              INTEGER NOT NULL,
    provider             TEXT NOT NULL,
    model                TEXT,
    prompt_version       TEXT NOT NULL,
    subject              TEXT,
    body                 TEXT,
    footer               TEXT,
    personal_detail_used TEXT,
    raw_output           TEXT,
    checks               TEXT NOT NULL DEFAULT '[]',
    fixes                TEXT NOT NULL DEFAULT '[]',
    reviewer_instruction TEXT,
    status               TEXT NOT NULL,
    edited_by_reviewer   INTEGER NOT NULL DEFAULT 0,
    reviewer             TEXT,
    review_note          TEXT,
    reviewed_at          TEXT,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach_log (
    id               INTEGER PRIMARY KEY,
    campaign_id      TEXT NOT NULL,
    lead_id          INTEGER,
    full_name        TEXT,
    email            TEXT NOT NULL,
    reg_number       TEXT,
    status           TEXT NOT NULL,
    idempotency_key  TEXT UNIQUE,
    message_id       TEXT,
    error            TEXT,
    outcome          TEXT,
    sent_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppression (
    email     TEXT PRIMARY KEY,
    reason    TEXT NOT NULL,
    added_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL,
    actor    TEXT NOT NULL,
    lead_id  INTEGER,
    event    TEXT NOT NULL,
    detail   TEXT
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads (campaign_id, status);
CREATE INDEX IF NOT EXISTS idx_log_email ON outreach_log (email);
CREATE INDEX IF NOT EXISTS idx_log_reg ON outreach_log (reg_number);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """All writes inside land together or not at all."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def audit(
    conn: sqlite3.Connection,
    event: str,
    *,
    lead_id: int | None = None,
    actor: str = "system",
    detail: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (at, actor, lead_id, event, detail) VALUES (?, ?, ?, ?, ?)",
        (now_iso(), actor, lead_id, event, json.dumps(detail or {}, ensure_ascii=False)),
    )


def update_lead(conn: sqlite3.Connection, lead_id: int, **fields) -> None:
    fields["updated_at"] = now_iso()
    columns = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(f"UPDATE leads SET {columns} WHERE id = ?", (*fields.values(), lead_id))


def get_lead(conn: sqlite3.Connection, lead_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return dict(row) if row else None


def loads(value: str | None, default):
    return json.loads(value) if value else default
