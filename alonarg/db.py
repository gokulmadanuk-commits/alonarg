"""SQLite persistence for Alonarg (stdlib ``sqlite3``).

A single :class:`Database` wraps one shared connection (opened with
``check_same_thread=False``) and serializes writes with a lock so it is safe to
share across the server's worker threads. Recordings are stored as flat rows;
the transcript and summary are kept as JSON blobs and parsed back into plain
dicts on read.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alonarg import config
from alonarg.types import SummaryResult, TranscriptResult

# Columns a caller is allowed to update via ``update_recording``. Notably
# excludes ``id`` (the primary key) and includes the JSON blob columns so the
# dedicated setters can reuse the same write path.
_UPDATABLE_COLUMNS = frozenset(
    {
        "title",
        "status",
        "error",
        "duration_s",
        "mic_path",
        "system_path",
        "mixed_path",
        "transcript_json",
        "summary_json",
        "meta_json",
        "state_json",
        "created_at",
    }
)

# Columns returned for ``list_recordings`` rows; we deliberately omit the
# (potentially large) ``transcript_json`` body from list views.
_LIST_COLUMNS = (
    "id",
    "title",
    "created_at",
    "duration_s",
    "status",
    "error",
    "mic_path",
    "system_path",
    "mixed_path",
    "summary_json",
    "meta_json",
    "state_json",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT,
    created_at      TEXT,
    duration_s      REAL,
    status          TEXT,
    error           TEXT,
    mic_path        TEXT,
    system_path     TEXT,
    mixed_path      TEXT,
    transcript_json TEXT,
    summary_json    TEXT,
    meta_json       TEXT,
    state_json      TEXT
);
"""

# Semantic-search chunks (the "second brain"): transcript/summary slices with a
# local embedding stored as a packed float32 blob, plus a timestamp for citations.
_CHUNKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id  INTEGER NOT NULL,
    source_type   TEXT,
    chunk_index   INTEGER,
    start_s       REAL,
    end_s         REAL,
    speaker       TEXT,
    text          TEXT,
    embedding     BLOB,
    embed_model   TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_recording ON chunks(recording_id);
"""


def _utc_now_iso() -> str:
    """Return the current time as an ISO-8601 UTC string."""
    return datetime.now(timezone.utc).isoformat()


def _loads_or_none(value: str | None) -> dict | None:
    """Parse a JSON column value into a dict, tolerating NULL/empty/bad data."""
    if not value:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


class Database:
    """SQLite-backed store for recordings and their derived artifacts."""

    def __init__(self, path: str | Path = config.DB_PATH) -> None:
        self.path = str(path)
        # A new sqlite file is created lazily on connect; make sure the parent
        # directory exists first (except for the special in-memory database).
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.executescript(_CHUNKS_SCHEMA)
            # Migrate older databases that predate newer columns.
            existing = {row[1] for row in self._conn.execute("PRAGMA table_info(recordings)")}
            if "meta_json" not in existing:
                self._conn.execute("ALTER TABLE recordings ADD COLUMN meta_json TEXT")
            if "state_json" not in existing:
                self._conn.execute("ALTER TABLE recordings ADD COLUMN state_json TEXT")
            self._conn.commit()

    # --- writes -----------------------------------------------------------

    def create_recording(
        self,
        *,
        title: str = "Untitled recording",
        status: str = "recording",
        created_at: str | None = None,
        mic_path: str | None = None,
        system_path: str | None = None,
        mixed_path: str | None = None,
        duration_s: float = 0.0,
    ) -> int:
        """Insert a new recording row and return its id."""
        if created_at is None:
            created_at = _utc_now_iso()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO recordings
                    (title, created_at, duration_s, status, error,
                     mic_path, system_path, mixed_path,
                     transcript_json, summary_json)
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL)
                """,
                (
                    title,
                    created_at,
                    float(duration_s),
                    status,
                    mic_path,
                    system_path,
                    mixed_path,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_recording(self, rec_id: int, **fields: Any) -> None:
        """Update whitelisted columns of a recording. Unknown columns raise."""
        if not fields:
            return
        unknown = set(fields) - _UPDATABLE_COLUMNS
        if unknown:
            raise ValueError(f"Cannot update unknown column(s): {sorted(unknown)}")
        assignments = ", ".join(f"{col} = ?" for col in fields)
        values = list(fields.values())
        values.append(rec_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE recordings SET {assignments} WHERE id = ?",
                values,
            )
            self._conn.commit()

    def set_status(self, rec_id: int, status: str, error: str | None = None) -> None:
        """Set a recording's status (and optionally its error message)."""
        self.update_recording(rec_id, status=status, error=error)

    def set_transcript(self, rec_id: int, transcript: TranscriptResult) -> None:
        """Store a transcript as JSON in the recording's ``transcript_json``."""
        self.update_recording(
            rec_id, transcript_json=json.dumps(transcript.to_dict())
        )

    def set_summary(self, rec_id: int, summary: SummaryResult) -> None:
        """Store a summary as JSON in the recording's ``summary_json``."""
        self.update_recording(rec_id, summary_json=json.dumps(summary.to_dict()))

    def set_meta(self, rec_id: int, meta: dict) -> None:
        """Store call metadata (people, contacts, ...) as JSON in ``meta_json``."""
        self.update_recording(rec_id, meta_json=json.dumps(meta))

    def set_state(self, rec_id: int, state: dict) -> None:
        """Store per-recording UI state (pinned, tags, done_actions) in ``state_json``."""
        self.update_recording(rec_id, state_json=json.dumps(state))

    def delete_recording(self, rec_id: int) -> bool:
        """Delete a recording (and its semantic chunks). True if a row was removed."""
        with self._lock:
            self._conn.execute("DELETE FROM chunks WHERE recording_id = ?", (rec_id,))
            cur = self._conn.execute(
                "DELETE FROM recordings WHERE id = ?", (rec_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    # --- semantic chunks (second brain) -----------------------------------

    def replace_chunks(self, rec_id: int, chunks: list[dict]) -> None:
        """Replace all semantic chunks for a recording."""
        with self._lock:
            self._conn.execute("DELETE FROM chunks WHERE recording_id = ?", (rec_id,))
            for c in chunks:
                self._conn.execute(
                    """INSERT INTO chunks
                       (recording_id, source_type, chunk_index, start_s, end_s, speaker, text, embedding, embed_model)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (rec_id, c.get("source_type"), c.get("chunk_index"), c.get("start_s"),
                     c.get("end_s"), c.get("speaker"), c.get("text"), c.get("embedding"),
                     c.get("embed_model", "")),
                )
            self._conn.commit()

    def all_chunks(self) -> list[dict]:
        """All chunks (id, recording_id, source_type, start/end, text, embedding blob)."""
        rows = self._conn.execute(
            "SELECT id, recording_id, source_type, start_s, end_s, text, embedding FROM chunks"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_chunks(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    def recordings_with_chunks(self) -> set:
        return {r[0] for r in self._conn.execute("SELECT DISTINCT recording_id FROM chunks")}

    # --- reads ------------------------------------------------------------

    def get_recording(self, rec_id: int) -> dict | None:
        """Return all columns for a recording plus parsed transcript/summary."""
        row = self._conn.execute(
            "SELECT * FROM recordings WHERE id = ?", (rec_id,)
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["transcript"] = _loads_or_none(record.get("transcript_json"))
        record["summary"] = _loads_or_none(record.get("summary_json"))
        record["meta"] = _loads_or_none(record.get("meta_json"))
        record["state"] = _loads_or_none(record.get("state_json"))
        return record

    def list_recordings(self) -> list[dict]:
        """Return recordings newest-first, each with a parsed ``summary``.

        The large ``transcript_json`` body is omitted from list rows.
        """
        cols = ", ".join(_LIST_COLUMNS)
        rows = self._conn.execute(
            f"SELECT {cols} FROM recordings ORDER BY created_at DESC, id DESC"
        ).fetchall()
        result: list[dict] = []
        for row in rows:
            record = dict(row)
            record["summary"] = _loads_or_none(record.get("summary_json"))
            record["meta"] = _loads_or_none(record.get("meta_json"))
            record["state"] = _loads_or_none(record.get("state_json"))
            result.append(record)
        return result

    def search_recordings(self, query: str) -> list[dict]:
        """Return recordings whose title, summary, or transcript matches ``query``.

        Case-insensitive substring match across the title and the summary/
        transcript JSON blobs, newest-first. An empty query returns everything
        (same as :meth:`list_recordings`). The transcript body is omitted from
        rows, like :meth:`list_recordings`.
        """
        q = (query or "").strip()
        if not q:
            return self.list_recordings()
        like = f"%{q}%"
        cols = ", ".join(_LIST_COLUMNS)
        rows = self._conn.execute(
            f"""
            SELECT {cols} FROM recordings
            WHERE title LIKE ? OR summary_json LIKE ? OR transcript_json LIKE ?
            ORDER BY created_at DESC, id DESC
            """,
            (like, like, like),
        ).fetchall()
        result: list[dict] = []
        for row in rows:
            record = dict(row)
            record["summary"] = _loads_or_none(record.get("summary_json"))
            record["meta"] = _loads_or_none(record.get("meta_json"))
            record["state"] = _loads_or_none(record.get("state_json"))
            result.append(record)
        return result

    def related_recordings(self, rec_id: int, limit: int = 8) -> list[dict]:
        """Other recordings that share this one's title (a recurring-meeting thread).

        Case-insensitive exact match on the trimmed title, newest-first,
        excluding the recording itself. Empty if the title is blank.
        """
        row = self._conn.execute(
            "SELECT title FROM recordings WHERE id = ?", (rec_id,)
        ).fetchone()
        if row is None:
            return []
        title = (row["title"] or "").strip()
        if not title:
            return []
        rows = self._conn.execute(
            """
            SELECT id, title, created_at, status, duration_s FROM recordings
            WHERE id != ? AND lower(trim(title)) = lower(?)
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (rec_id, title, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._conn.close()
