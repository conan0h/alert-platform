"""Tests for per-source fetch health.

These pin the two properties the fda-catalysts dead-source incident needed:
a permanently broken source is *named* rather than left to be inferred from a
repeated log line, and it stops shouting once it has been named.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alertlib.sources import (  # noqa: E402
    LOG_AT_FAILURES,
    PRESUMED_DEAD_AFTER,
    SourceHealth,
)


@pytest.fixture
def health():
    return SourceHealth()


def test_first_failure_is_logged_at_warning(health):
    d = health.record_failure("FiercePharma", "403 Client Error")
    assert d.level == "warning"
    assert "FiercePharma" in d.message
    assert "403 Client Error" in d.message


def test_success_is_silent(health):
    assert not health.record_success("FDA-PressReleases")


def test_a_dead_source_does_not_log_every_cycle(health):
    """The regression the incident was about.

    Before this, each failing source emitted one WARNING per poll cycle —
    about 1,900 lines a day at a 45s cadence, two thirds of the service's
    entire output. A thousand cycles must now produce a handful of lines.
    """
    logged = [
        health.record_failure("FiercePharma", "403")
        for _ in range(1000)
    ]
    spoke = [d for d in logged if d]

    # One line per escalation threshold at or below 1000, plus the
    # presumed-dead line. Nothing else.
    expected = len([n for n in LOG_AT_FAILURES if n <= 1000]) + 1
    assert len(spoke) == expected, [d.message for d in spoke]
    assert len(spoke) < 10  # the point: not 1,000

    # And 1,000 consecutive failures is unambiguously reported as dead.
    assert health.sources["FiercePharma"].presumed_dead


def test_presumed_dead_is_announced_once_at_error(health):
    errors = []
    for _ in range(PRESUMED_DEAD_AFTER * 3):
        d = health.record_failure("EndpointsNews", "403")
        if d.level == "error":
            errors.append(d)
    assert len(errors) == 1
    assert "presumed dead" in errors[0].message
    assert str(PRESUMED_DEAD_AFTER) in errors[0].message


def test_threshold_and_dead_lines_do_not_collide(health):
    """At a threshold that is also the dead threshold, exactly one line."""
    h = SourceHealth()
    spoke = []
    for _ in range(PRESUMED_DEAD_AFTER):
        d = h.record_failure("X", "boom")
        if d:
            spoke.append(d)
    # The failure count that trips presumed-dead yields the error line only,
    # never an error and a warning for the same fetch.
    at_threshold = [d for d in spoke if str(PRESUMED_DEAD_AFTER) in d.message]
    assert len(at_threshold) == 1
    assert at_threshold[0].level == "error"


def test_recovery_is_logged_once_then_silent(health):
    for _ in range(5):
        health.record_failure("BioPharmaDive", "timeout")

    d = health.record_success("BioPharmaDive")
    assert d.level == "info"
    assert "recovered after 5" in d.message

    # A second success says nothing.
    assert not health.record_success("BioPharmaDive")
    assert health.sources["BioPharmaDive"].healthy


def test_recovery_clears_presumed_dead_and_rearms_it(health):
    for _ in range(PRESUMED_DEAD_AFTER):
        health.record_failure("Flaky", "503")
    assert health.sources["Flaky"].presumed_dead

    health.record_success("Flaky")
    assert not health.sources["Flaky"].presumed_dead

    # Dying again must be announced again, not swallowed by the old flag.
    errors = [
        d for _ in range(PRESUMED_DEAD_AFTER)
        if (d := health.record_failure("Flaky", "503")).level == "error"
    ]
    assert len(errors) == 1


def test_summary_names_the_failing_sources_worst_first(health):
    health.record_success("FDA-Drugs")
    for _ in range(3):
        health.record_failure("FiercePharma", "403")
    for _ in range(30):
        health.record_failure("EndpointsNews", "403")

    s = health.summary()
    assert "1/3 sources healthy" in s
    # Worst first, so the most broken source is read first.
    assert s.index("EndpointsNews") < s.index("FiercePharma")
    assert "presumed dead" in s          # EndpointsNews crossed the threshold
    assert "FDA-Drugs" not in s          # healthy sources are counted, not listed


def test_summary_when_everything_works(health):
    for name in ("a", "b", "c"):
        health.record_success(name)
    assert health.summary() == "all 3 sources healthy"


def test_summary_before_any_fetch(health):
    assert health.summary() == "no sources polled yet"


def test_summary_if_changed_speaks_only_on_change(health):
    health.record_success("a")
    first = health.summary_if_changed()
    assert first == "all 1 sources healthy"

    # Nothing moved.
    assert health.summary_if_changed() == ""
    health.record_success("a")
    assert health.summary_if_changed() == ""

    # Something moved.
    health.record_failure("a", "403")
    changed = health.summary_if_changed()
    assert "failing: a" in changed
    assert health.summary_if_changed() == ""


def test_counts_accumulate_across_runs(health):
    health.record_failure("a", "x")
    health.record_success("a")
    health.record_failure("a", "y")

    st = health.sources["a"]
    assert st.failures == 2
    assert st.successes == 1
    assert st.consecutive_failures == 1   # the run, not the total
    assert st.last_error == "y"
    assert st.last_success_at > 0


def test_summary_is_silent_while_a_source_stays_broken(health):
    """The bug the deployed output showed on 2026-09-22.

    The first version compared the rendered summary, which contains the
    consecutive-failure count — so it changed every cycle a source stayed
    broken and printed about 1,900 lines a day, the exact volume the
    escalation schedule exists to avoid.
    """
    health.record_success("ok-source")
    assert health.summary_if_changed()          # first picture is news

    health.record_failure("FiercePharma", "403")
    assert "FiercePharma" in health.summary_if_changed()

    # More failures of the same source, staying below the presumed-dead
    # threshold: the count climbs, the shape does not, so nothing is printed.
    # (Crossing into presumed-dead *is* a change, and has its own test.)
    spoke = []
    for _ in range(PRESUMED_DEAD_AFTER - 5):
        health.record_failure("FiercePharma", "403")
        spoke.append(health.summary_if_changed())
    assert [x for x in spoke if x] == [], spoke

    # A *second* source failing is a change in the set, so it speaks again.
    health.record_failure("EndpointsNews", "403")
    line = health.summary_if_changed()
    assert "EndpointsNews" in line and "FiercePharma" in line


def test_summary_speaks_when_a_source_becomes_presumed_dead(health):
    """Crossing into presumed-dead changes the shape even though the set does not."""
    health.record_success("ok")
    health.summary_if_changed()
    for _ in range(PRESUMED_DEAD_AFTER - 1):
        health.record_failure("x", "boom")
    first = health.summary_if_changed()
    assert "x" in first and "presumed dead" not in first

    health.record_failure("x", "boom")          # crosses the threshold
    crossed = health.summary_if_changed()
    assert "presumed dead" in crossed


def test_summary_speaks_on_recovery(health):
    health.record_failure("x", "boom")
    health.summary_if_changed()
    health.record_success("x")
    assert health.summary_if_changed() == "all 1 sources healthy"
