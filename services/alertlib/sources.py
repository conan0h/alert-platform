"""Per-source fetch health.

A service that polls a dozen feeds has a failure mode the poll-cycle
counters cannot see: one source dies permanently while the cycle keeps
completing successfully. `fda-catalysts` ran that way from August to
2026-09-22 with two feeds returning 403 on every cycle. Two things went
wrong, and this module addresses both.

The condition was invisible. Nothing aggregated the failures, so there was
no answer to "which sources are actually working" short of reading the
journal by eye and noticing the same line twice.

And it was loud. Each dead source logged one WARNING per cycle — at a 45
second cadence, about 1,900 lines per source per day, two thirds of
everything the service emitted. That volume matters beyond tidiness: the
`logs` read verb captures roughly the first 24 KB of its window, so the
spam was displacing the alert output it was meant to sit alongside.

So a repeated failure is recorded every time and *logged* on a schedule:
the first one, then at widening intervals, then once more when the source
is presumed dead. Recovery is logged once. The full picture is available
as a summary line whenever the caller wants one, which is what makes
"which sources are working" answerable from a single `logs` read.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

# Consecutive failures at which a still-failing source is logged again.
# Widening on purpose: the first failure may be transient and is worth a
# line, the tenth is a pattern, and past that the useful signal is "still
# broken" at a rate that does not bury anything else.
LOG_AT_FAILURES = (1, 10, 100, 1000)

# Consecutive failures after which a source is reported as presumed dead,
# once, at error level. At a 45s cadence 20 failures is about 15 minutes —
# long enough to clear a deploy or a brief upstream blip, short enough that
# a genuinely broken feed is named within the hour.
PRESUMED_DEAD_AFTER = 20


@dataclass
class SourceState:
    """What has happened to one source since the process started."""

    name: str
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_error: str = ""
    last_success_at: float = 0.0
    presumed_dead: bool = False

    @property
    def healthy(self) -> bool:
        return self.consecutive_failures == 0


@dataclass
class _Decision:
    """Whether the caller should log, and at what level."""

    level: str = ""  # "", "warning", "error", "info"
    message: str = ""

    def __bool__(self) -> bool:
        return bool(self.level)


@dataclass
class SourceHealth:
    """Tracks fetch outcomes per source and decides when to speak.

    Thread-safe because a service may poll tiers concurrently; today none
    do, but the cost is one uncontended lock per fetch and the alternative
    is a latent bug the first time one does.
    """

    sources: dict[str, SourceState] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_summary: str = field(default="", repr=False)

    def _state(self, name: str) -> SourceState:
        return self.sources.setdefault(name, SourceState(name=name))

    def record_success(self, name: str) -> _Decision:
        """Note a good fetch. Returns a recovery line if it was failing."""
        with self._lock:
            st = self._state(name)
            was_failing = st.consecutive_failures
            st.successes += 1
            st.consecutive_failures = 0
            st.last_error = ""
            st.last_success_at = time.time()
            st.presumed_dead = False
            if was_failing:
                return _Decision(
                    "info",
                    f"source {name} recovered after {was_failing} consecutive failures",
                )
            return _Decision()

    def record_failure(self, name: str, error: str) -> _Decision:
        """Note a bad fetch. Returns a line only when one is due."""
        with self._lock:
            st = self._state(name)
            st.failures += 1
            st.consecutive_failures += 1
            st.last_error = error
            n = st.consecutive_failures

            # The presumed-dead line supersedes the routine one at the
            # threshold, so a source is never announced twice in one cycle.
            if n >= PRESUMED_DEAD_AFTER and not st.presumed_dead:
                st.presumed_dead = True
                return _Decision(
                    "error",
                    f"source {name} presumed dead: {n} consecutive failures, "
                    f"last error: {error}",
                )
            if n in LOG_AT_FAILURES:
                return _Decision(
                    "warning",
                    f"source {name} failed ({n} consecutive): {error}",
                )
            return _Decision()

    def failing(self) -> list[SourceState]:
        """Sources currently in a failing run, worst first."""
        with self._lock:
            bad = [s for s in self.sources.values() if not s.healthy]
        return sorted(bad, key=lambda s: (-s.consecutive_failures, s.name))

    def summary(self) -> str:
        """One line naming every source that is not working.

        This is the line that answers "which sources are alive" from a
        single `logs` read, so it states the healthy count rather than
        listing names that would push it past a useful length.
        """
        with self._lock:
            total = len(self.sources)
        bad = self.failing()
        if not total:
            return "no sources polled yet"
        if not bad:
            return f"all {total} sources healthy"
        detail = ", ".join(
            f"{s.name} (x{s.consecutive_failures}{', presumed dead' if s.presumed_dead else ''})"
            for s in bad
        )
        return f"{total - len(bad)}/{total} sources healthy; failing: {detail}"

    def summary_if_changed(self) -> str:
        """`summary()`, but only the first time it says something new.

        Logging the summary every cycle would trade 3,800 warning lines a day
        for 1,900 summary lines, which misses the point. The per-source
        escalation in `record_failure` is already the "still broken"
        heartbeat, so this only needs to speak when the picture moves.
        """
        current = self.summary()
        with self._lock:
            if current == self._last_summary:
                return ""
            self._last_summary = current
        return current
