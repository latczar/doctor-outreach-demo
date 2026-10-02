"""Step 0: read the research CSV, tidy each row and merge rows that are the same doctor."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .db import audit, now_iso, transaction
from .models import Status
from .normalise import (
    clean,
    employer_key,
    name_key,
    normalise_country,
    normalise_email,
    normalise_grade,
    normalise_specialty,
    split_name,
)

FIELDS = [
    "title",
    "full_name",
    "grade",
    "specialty",
    "employer",
    "country",
    "reg_number",
    "email",
    "source",
    "profile_notes",
]

MATCH_LABELS = {
    "reg": "registration number",
    "email": "email address",
    "name_employer": "name and employer",
}


@dataclass
class Row:
    number: int  # the line in the CSV, counting the header as line 1
    values: dict[str, str]

    def completeness(self) -> int:
        return sum(1 for v in self.values.values() if v)

    def match_keys(self) -> list[tuple[str, str]]:
        keys = []
        if self.values["reg_number"]:
            keys.append(("reg", self.values["reg_number"].upper()))
        if self.values["email"]:
            keys.append(("email", self.values["email"]))
        name, employer = name_key(self.values["full_name"]), employer_key(self.values["employer"])
        if name and employer:
            keys.append(("name_employer", f"{name}|{employer}"))
        return keys


@dataclass
class Group:
    primary: Row
    duplicates: list[tuple[Row, str]] = field(default_factory=list)  # (row, matched on)
    merged: dict[str, str] = field(default_factory=dict)
    filled_from: dict[str, int] = field(default_factory=dict)  # field -> row number


@dataclass
class IngestSummary:
    rows_read: int = 0
    new_doctors: int = 0
    duplicates: int = 0
    already_imported: int = 0


# --- a research list someone uploads ------------------------------------------------------

UPLOAD_LIMIT_BYTES = 1_000_000
UPLOAD_LIMIT_ROWS = 500
ONLINE_UPLOAD_ROWS = 50  # the public demo: made-up lists only, and smaller
MADE_UP = (".example", ".test")
NEEDED_COLUMNS = ("full_name", "grade", "specialty", "country", "email")

# Made-up rows that show the format. Replace them with people who have agreed to take part.
TEMPLATE_ROWS = [
    {"title": "Dr", "full_name": "Sam Example", "grade": "Consultant", "specialty": "Cardiology",
     "employer": "Example Hospital NHS Trust", "country": "United Kingdom", "reg_number": "",
     "email": "sam.example@examplehospital.example", "source": "Trust website",
     "profile_notes": "Leads the trust's cardiology teaching for medical students."},
    {"title": "Dr", "full_name": "Jo Sample", "grade": "GP Partner", "specialty": "General Practice",
     "employer": "Sample Street Surgery", "country": "United Kingdom", "reg_number": "",
     "email": "jo.sample@samplestreet.example", "source": "Practice website",
     "profile_notes": "Trains GP registrars and hosts student placements."},
]


def template_csv() -> str:
    """A blank research list with the right columns and two made-up rows to replace."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(TEMPLATE_ROWS)
    return out.getvalue()


def check_research_list(data: bytes, made_up_only: bool = False, limit: int = UPLOAD_LIMIT_ROWS) -> str:
    """What's wrong with an uploaded research list, in words a person can act on, or "" if nothing is.

    `made_up_only` is for the public demo: anyone with the link can see what's uploaded there, so every
    address must be a made-up one.
    """
    if len(data) > UPLOAD_LIMIT_BYTES:
        return f"That file is over 1 MB. Upload up to {UPLOAD_LIMIT_ROWS} doctors at a time."
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "That file isn't saved as CSV text. In Excel or Google Sheets, save it as CSV (UTF-8)."
    reader = csv.DictReader(io.StringIO(text))
    columns = {name.strip() for name in reader.fieldnames or []}
    missing = [name for name in NEEDED_COLUMNS if name not in columns]
    if missing:
        return (f"That file is missing these columns: {', '.join(missing)}. Use the column names from the "
                "template exactly.")
    rows = 0
    for number, row in enumerate(reader, start=2):
        rows += 1
        email = (row.get("email") or "").strip().lower()
        if made_up_only and email and not email.endswith(MADE_UP):
            return (f"Row {number} has a real email address. This public demo only takes made-up addresses "
                    "ending .example or .test. Use the laptop copy for a real list.")
    if rows == 0:
        return "That file has the column names but no doctors under them."
    if rows > limit:
        return f"That file has {rows} doctors. Upload up to {limit} at a time."
    return ""


def read_rows(path: Path) -> list[Row]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for i, raw in enumerate(reader, start=2):
            values = {name: clean(raw.get(name)) for name in FIELDS}
            values["email"] = normalise_email(values["email"])
            rows.append(Row(i, values))
        return rows


def group_duplicates(rows: list[Row]) -> list[Group]:
    """Rows sharing any match key are the same doctor (union-find over keys)."""
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_seen: dict[tuple[str, str], int] = {}
    for i, row in enumerate(rows):
        for key in row.match_keys():
            if key in first_seen:
                parent[find(i)] = find(first_seen[key])
            else:
                first_seen[key] = i

    members: dict[int, list[int]] = {}
    for i in range(len(rows)):
        members.setdefault(find(i), []).append(i)

    groups = []
    for indexes in members.values():
        group_rows = [rows[i] for i in indexes]
        primary = max(group_rows, key=lambda r: (r.completeness(), -r.number))
        group = Group(primary=primary, merged=dict(primary.values))
        for i in indexes:
            row = rows[i]
            if row is primary:
                continue
            shared = [k for k in row.match_keys() if k in primary.match_keys()]
            matched = MATCH_LABELS[shared[0][0]] if shared else "details shared with another copy"
            group.duplicates.append((row, matched))
            for name, value in row.values.items():
                if value and not group.merged[name]:
                    group.merged[name] = value
                    group.filled_from[name] = row.number
        groups.append(group)
    return sorted(groups, key=lambda g: g.primary.number)


def find_existing(conn: sqlite3.Connection, campaign_id: str, values: dict) -> tuple[int, str] | None:
    """A doctor already imported for this campaign from an earlier file."""
    checks = [
        ("reg", "UPPER(reg_number) = ?", values["reg_number"].upper()),
        ("email", "email = ?", values["email"]),
    ]
    for label, where, value in checks:
        if not value:
            continue
        row = conn.execute(
            f"SELECT id FROM leads WHERE campaign_id = ? AND status != 'DUPLICATE' AND {where}",
            (campaign_id, value),
        ).fetchone()
        if row:
            return row["id"], MATCH_LABELS[label]
    return None


def _insert(conn, campaign_id: str, source_file: str, row: Row, values: dict, **extra) -> int:
    first, last = split_name(values["full_name"])
    stamp = now_iso()
    cursor = conn.execute(
        """
        INSERT INTO leads (
            campaign_id, source_file, source_row, title, full_name, first_name, last_name,
            grade_raw, grade, specialty_raw, specialty, employer, country, reg_number,
            email, email_origin, source, profile_notes, status, stage, reason,
            duplicate_of, merge_notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            campaign_id,
            source_file,
            row.number,
            values["title"],
            values["full_name"],
            first,
            last,
            values["grade"],
            normalise_grade(values["grade"]),
            values["specialty"],
            normalise_specialty(values["specialty"]),
            values["employer"],
            normalise_country(values["country"]),
            values["reg_number"].upper(),
            values["email"],
            "provided" if values["email"] else None,
            values["source"],
            values["profile_notes"],
            extra.get("status", Status.NEW),
            "dedupe",
            extra.get("reason"),
            extra.get("duplicate_of"),
            extra.get("merge_notes"),
            stamp,
            stamp,
        ),
    )
    return cursor.lastrowid


def ingest_csv(conn: sqlite3.Connection, campaign_id: str, path: Path) -> IngestSummary:
    rows = read_rows(path)
    summary = IngestSummary(rows_read=len(rows))
    source_file = path.name
    with transaction(conn):
        for group in group_duplicates(rows):
            already = conn.execute(
                "SELECT 1 FROM leads WHERE campaign_id = ? AND source_file = ? AND source_row = ?",
                (campaign_id, source_file, group.primary.number),
            ).fetchone()
            if already:
                summary.already_imported += 1 + len(group.duplicates)
                continue

            existing = find_existing(conn, campaign_id, group.merged)
            if existing:
                existing_id, matched = existing
                for row in [group.primary] + [r for r, _ in group.duplicates]:
                    _insert(
                        conn, campaign_id, source_file, row, row.values,
                        status=Status.DUPLICATE, duplicate_of=existing_id,
                        reason=f"Same doctor as #{existing_id} from an earlier import (matched on {matched}).",
                    )
                    summary.duplicates += 1
                continue

            notes = None
            if group.filled_from:
                notes = json.dumps(
                    {name: f"row {number}" for name, number in group.filled_from.items()}
                )
            primary_id = _insert(
                conn, campaign_id, source_file, group.primary, group.merged, merge_notes=notes
            )
            summary.new_doctors += 1
            audit(
                conn,
                "ingest.imported",
                lead_id=primary_id,
                detail={"row": group.primary.number, "filled_from": group.filled_from},
            )
            for row, matched in group.duplicates:
                dup_id = _insert(
                    conn, campaign_id, source_file, row, row.values,
                    status=Status.DUPLICATE, duplicate_of=primary_id,
                    reason=f"Same doctor as #{primary_id} (row {group.primary.number}), matched on {matched}. Details merged.",
                )
                summary.duplicates += 1
                audit(
                    conn,
                    "ingest.duplicate",
                    lead_id=dup_id,
                    detail={"row": row.number, "duplicate_of": primary_id, "matched_on": matched},
                )
    return summary
