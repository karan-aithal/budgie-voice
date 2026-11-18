"""SQLite file for dictation history. Failure-isolated: nothing here ever raises into a dictation.

One short-lived connection per call. WAL, so a Settings window can read while Budgie writes. If the
file cannot be used (locked, read-only disk, corrupt, written by a newer Budgie) the store disables
itself for the run and leaves the file exactly as it found it.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

_LOG = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STATUSES = ("completed", "cancelled", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dictation_sessions (
  id               TEXT PRIMARY KEY,
  started_at       TEXT NOT NULL,
  local_date       TEXT NOT NULL,
  duration_ms      INTEGER NOT NULL CHECK (duration_ms >= 0),
  words_count      INTEGER NOT NULL CHECK (words_count >= 0),
  characters_count INTEGER NOT NULL CHECK (characters_count >= 0),
  status           TEXT NOT NULL CHECK (status IN ('completed','cancelled','failed'))
);
CREATE INDEX IF NOT EXISTS idx_sessions_local_date ON dictation_sessions(local_date);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON dictation_sessions(started_at);
"""


@dataclass(frozen=True)
class SessionRecord:
    """One finished dictation. Two integers and a status: no field could hold text."""

    id: str
    started_at: datetime   # timezone-aware; stored as UTC
    local_date: date       # the local calendar day it started, fixed when it is written
    duration_ms: int       # active recording time
    words: int             # words actually typed into the target
    chars: int             # characters actually typed into the target
    status: str            # one of STATUSES


class HistoryStore:
    """Args:
        path: The database file. Created (owner-only) on first use.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.available = False
        self._warned = False
        try:
            self._init()
            self.available = True
        except Exception as exc:  # noqa: BLE001 — a history problem must never cost the user the app
            self._problem("history disabled for this run", exc)

    @property
    def healthy(self) -> bool:
        """False when the file could not be used at all, or the most recent read or write failed.

        Lets a page tell "nothing saved yet" from "could not read what is saved"."""
        return self.available and not self._warned

    # -- setup -------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=1.0, isolation_level=None)  # autocommit; we BEGIN ourselves
        con.execute("PRAGMA synchronous=NORMAL")  # per connection; safe with WAL
        return con

    def _init(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.close(os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600))  # owner-only from the first byte
        with closing(self._connect()) as con:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise _NewerSchema
            if version < SCHEMA_VERSION:
                con.execute("PRAGMA journal_mode=WAL")  # persists in the file; not allowed inside a transaction
                con.execute("BEGIN IMMEDIATE")  # an error leaves it open; closing the connection rolls it back
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        con.execute(statement)
                con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                con.execute("COMMIT")

    # -- writing -----------------------------------------------------------

    def add(self, rec: SessionRecord) -> bool:
        """Save *rec* once (a repeated id is ignored). True if the row is now in the file."""
        if not self.available:
            return False
        try:
            with closing(self._connect()) as con:
                con.execute("BEGIN IMMEDIATE")  # a failure leaves it open; closing the connection rolls it back
                con.execute(
                    # Only a repeated id is ignored. (OR IGNORE would also hide a failed CHECK.)
                    "INSERT INTO dictation_sessions VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                    (rec.id, _utc(rec.started_at), rec.local_date.isoformat(), rec.duration_ms,
                     rec.words, rec.chars, rec.status),
                )
                con.execute("COMMIT")
        except Exception as exc:  # noqa: BLE001 — see class docstring
            self._problem("could not save a dictation to history", exc, rec.id)
            return False
        self._warned = False  # healthy again: the next failure is worth a warning
        return True

    # -- reading -----------------------------------------------------------

    def read(self, sql: str, params: tuple = ()) -> list[tuple]:
        """Run one SELECT. ``[]`` when history is unavailable or the query fails: a statistics page
        must never raise. Metric definitions live in ``stats.py``, which owns the SQL."""
        if not self.available:
            return []
        try:
            with closing(self._connect()) as con:
                rows = con.execute(sql, params).fetchall()
        except Exception as exc:  # noqa: BLE001 — see class docstring
            self._problem("could not read history", exc)
            return []
        self._warned = False
        return rows

    # -- problems ----------------------------------------------------------

    def _problem(self, what: str, exc: Exception, session_id: str = "") -> None:
        """Metadata only: the class of the error and a session id, never a count or any text."""
        reason = "the file was written by a newer Budgie" if isinstance(exc, _NewerSchema) else type(exc).__name__
        suffix = f" [{session_id}]" if session_id else ""
        _LOG.log(logging.DEBUG if self._warned else logging.WARNING, "%s (%s)%s", what, reason, suffix)
        self._warned = True


class _NewerSchema(Exception):
    """The database has a newer schema than this code knows: leave it alone."""


def _utc(moment: datetime) -> str:
    """2026-11-18T09:15:02.481Z"""
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
