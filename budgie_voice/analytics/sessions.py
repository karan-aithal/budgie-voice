"""Read saved sessions for the History page: filtered, sorted, paged. Plain Python, no Qt.

Paging is by keyset, not OFFSET: the cursor is the last row's ``(started_at, id)``, so a page never
skips or repeats a row even if a dictation is saved between two "Load more" clicks. Filter values are
always bound parameters; the only text spliced into SQL is fixed here in this file.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from budgie_voice.analytics.store import HistoryStore

_LOG = logging.getLogger(__name__)

_COLUMNS = "id, started_at, local_date, duration_ms, words_count, characters_count, status"

Cursor = tuple[str, str]  # (started_at, id) exactly as stored


@dataclass(frozen=True)
class SessionRow:
    """One saved dictation. Metadata only: there is no field that could hold what was said."""

    id: str
    started_at: datetime   # aware, UTC
    local_date: date       # the local day it started, as saved
    duration_ms: int
    words: int
    chars: int
    status: str            # completed | cancelled | failed
    cursor: Cursor

    @property
    def wpm(self) -> float | None:
        """Words per minute of this session's recording; None when it typed nothing or has no duration."""
        return self.words / (self.duration_ms / 60_000) if self.words > 0 and self.duration_ms > 0 else None


@dataclass(frozen=True)
class Filter:
    """Both dates are inclusive local days. ``None`` means no limit."""

    since: date | None = None
    until: date | None = None
    status: str | None = None


def _where(flt: Filter) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    if flt.since is not None:
        clauses.append("local_date >= ?")
        params.append(flt.since.isoformat())
    if flt.until is not None:
        clauses.append("local_date <= ?")
        params.append(flt.until.isoformat())
    if flt.status is not None:
        clauses.append("status = ?")
        params.append(flt.status)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _row(r: tuple) -> SessionRow | None:
    try:
        return SessionRow(r[0], datetime.fromisoformat(r[1]), date.fromisoformat(r[2]), int(r[3]), int(r[4]),
                          int(r[5]), r[6], (r[1], r[0]))
    except (ValueError, TypeError):  # a hand-edited file: skip the row, never fail the page
        _LOG.debug("skipped an unreadable history row")
        return None


def page(
    store: HistoryStore, flt: Filter = Filter(), *, newest_first: bool = True,
    after: Cursor | None = None, limit: int = 50,
) -> tuple[list[SessionRow], bool]:
    """Up to *limit* rows after the cursor *after* (the start when None), and whether more follow."""
    where, params = _where(flt)
    if after is not None:
        where += (" AND" if where else " WHERE") + f" (started_at, id) {'<' if newest_first else '>'} (?, ?)"
        params += list(after)
    order = "DESC" if newest_first else "ASC"
    rows = store.read(
        f"SELECT {_COLUMNS} FROM dictation_sessions{where} ORDER BY started_at {order}, id {order} LIMIT ?",
        tuple(params + [limit + 1]),
    )
    parsed = [s for s in (_row(r) for r in rows[:limit]) if s is not None]
    return parsed, len(rows) > limit


def count(store: HistoryStore, flt: Filter = Filter()) -> int:
    where, params = _where(flt)
    rows = store.read(f"SELECT COUNT(*) FROM dictation_sessions{where}", tuple(params))
    return int(rows[0][0]) if rows else 0
