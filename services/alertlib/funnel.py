"""Per-cycle accounting for the candidate pipeline of a polling service.

`clinical-trials` streams 399 trials every cycle and has alerted on none of
them for weeks (backlog #35). Both halves of that are invisible from the
outside: the service logs how many candidates it streamed and how many alerts
it fired, and nothing in between. So "the filter is too tight", "the feed
hands back the same rows every cycle" and "the transition is never observed"
all produce byte-identical output, and no amount of reading that output
distinguishes them.

This module makes the middle visible. A service declares the stages its
candidates pass through, counts each one during a cycle, and emits a single
line per cycle naming every stage:

    funnel: streamed=399 parsed=399 known=399 changed=0 signals=0 sent=0
            new_in_window=0

Every stage is also a cumulative counter on `/metrics`, so the same question
is answerable from Prometheus without reading the journal — which matters
because the `logs` read verb returns roughly the first 24 KB of its window
(backlog #32).

`new_in_window` is the second half, and it answers a question the stage
counts cannot. A candidate can be known to the service's database and still
be a fresh arrival in the polling window, so "how many are new to us" does
not test whether the upstream query is stuck. Comparing this cycle's id set
against the previous cycle's does test it directly: a feed returning the same
rows every cycle reports zero forever, and a moving window does not.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import logging

    from .health import Metrics

METRIC_PREFIX = "alert_funnel"

# Rendered for `new_in_window` before any cohort has been compared. The first
# cycle has nothing to compare against, and every integer — 0 included — is a
# real answer some other cycle could produce, so the sentinel has to be a
# value the domain cannot return.
UNKNOWN = "?"


class CycleFunnel:
    """Ordered stage counts for one poll cycle, logged once per cycle.

    Stages are declared up front and a count against an undeclared stage
    raises. A funnel that quietly grows a stage on a typo would render a line
    that looks like a measurement and is not one, which is the failure this
    module exists to end rather than to repeat.
    """

    def __init__(
        self,
        metrics: Metrics,
        stages: Sequence[str],
        *,
        log: logging.Logger,
        label: str = "funnel",
    ) -> None:
        if not stages:
            raise ValueError("a funnel needs at least one stage")
        if len(set(stages)) != len(stages):
            raise ValueError(f"duplicate stage names: {list(stages)}")

        self._stages = tuple(stages)
        self._metrics = metrics
        self._log = log
        self._label = label
        self._lock = threading.Lock()
        self._counts: dict[str, int] = dict.fromkeys(self._stages, 0)
        self._prev_ids: frozenset[str] | None = None
        self._new_in_window: int | None = None

        for stage in self._stages:
            metrics.declare_counter(
                f"{METRIC_PREFIX}_{stage}_total",
                f"Candidates that reached the {stage} stage of the poll cycle.",
            )
        metrics.declare_gauge(
            f"{METRIC_PREFIX}_new_in_window",
            "Candidates in the most recent cycle that the previous cycle did not return.",
        )

    @property
    def stages(self) -> tuple[str, ...]:
        return self._stages

    def count(self, stage: str, amount: int = 1) -> None:
        """Record `amount` candidates reaching `stage`."""
        if stage not in self._counts:
            raise KeyError(f"{stage!r} is not a declared stage: {list(self._stages)}")
        with self._lock:
            self._counts[stage] += amount
        self._metrics.inc(f"{METRIC_PREFIX}_{stage}_total", amount)

    def observe_cohort(self, ids: Iterable[str]) -> int:
        """Compare this cycle's candidate ids against the previous cycle's.

        Returns the number not present last cycle, or 0 on the first cycle,
        where there is nothing to compare and `render` reports `?` instead.
        """
        cohort = frozenset(ids)
        with self._lock:
            previous = self._prev_ids
            self._prev_ids = cohort
            new = 0 if previous is None else len(cohort - previous)
            self._new_in_window = None if previous is None else new
        self._metrics.set(f"{METRIC_PREFIX}_new_in_window", new)
        return new

    def render(self) -> str:
        with self._lock:
            parts = [f"{stage}={self._counts[stage]}" for stage in self._stages]
            new = self._new_in_window
        parts.append(f"new_in_window={UNKNOWN if new is None else new}")
        return " ".join(parts)

    @contextmanager
    def cycle(self):
        """Zero the per-cycle counts, run the body, then log the line.

        The line is emitted even when the body raises. A cycle that died
        partway is precisely when the partial funnel is worth having, and it
        is also the case a caller writing its own log call would miss.
        """
        with self._lock:
            self._counts = dict.fromkeys(self._stages, 0)
            self._new_in_window = None
        try:
            yield self
        finally:
            self._log.info("%s: %s", self._label, self.render())
