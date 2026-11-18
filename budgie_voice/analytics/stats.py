"""Productivity statistics, computed live from the saved sessions. Plain Python, no Qt.

Nothing is cached or stored: delete a row and every number follows. One query returns one line per
local calendar day; the all-time totals and the chart (which shows the last 7 or 30 of those days) are
all sums of those lines.

Metric definitions (the only place they are written down):

* **Words**     sum over every saved session. Words that were really typed count even when the session
                was cancelled or failed afterwards.
* **Sessions**  completed sessions that typed at least one word. Empty dictations and cancelled
                previews are saved (honest history) but are not sessions in this sense.
* **Time**      active recording time of those same sessions.
* **WPM**       total words / total recording minutes, over every session that typed words and has a
                duration. It is a ratio of totals, NOT an average of per-session rates. It is words per
                minute of *recording*, so pauses lower it. ``None`` when there is no recording time.
* **Day**       the local calendar day the session started, fixed when it was saved.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from budgie_voice.analytics.store import HistoryStore

_DAILY = """
SELECT local_date,
  SUM(words_count),
  SUM(status = 'completed' AND words_count > 0),
  SUM(CASE WHEN status = 'completed' AND words_count > 0 THEN duration_ms ELSE 0 END),
  SUM(CASE WHEN words_count > 0 AND duration_ms > 0 THEN words_count ELSE 0 END),
  SUM(CASE WHEN words_count > 0 AND duration_ms > 0 THEN duration_ms ELSE 0 END)
FROM dictation_sessions
GROUP BY local_date
ORDER BY local_date
"""


@dataclass(frozen=True)
class Stats:
    """The sums for one day or one range. Combine with ``+`` (or :func:`total`)."""

    words: int = 0
    sessions: int = 0
    time_ms: int = 0        # recording time of the counted sessions
    rate_words: int = 0     # WPM numerator: words of sessions that have a duration
    rate_ms: int = 0        # WPM denominator

    def __add__(self, other: Stats) -> Stats:
        return Stats(self.words + other.words, self.sessions + other.sessions, self.time_ms + other.time_ms,
                     self.rate_words + other.rate_words, self.rate_ms + other.rate_ms)

    @property
    def wpm(self) -> float | None:
        return self.rate_words / (self.rate_ms / 60_000) if self.rate_ms > 0 else None


def daily(store: HistoryStore) -> dict[date, Stats]:
    """Stats for every local day that has sessions (days with none are absent)."""
    return {date.fromisoformat(r[0]): Stats(*(int(v or 0) for v in r[1:])) for r in store.read(_DAILY)}


def total(days: Iterable[Stats]) -> Stats:
    return sum(days, Stats())
