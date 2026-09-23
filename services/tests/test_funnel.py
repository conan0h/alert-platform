"""Tests for per-cycle funnel accounting.

The property that matters is that each cycle's line reports *that cycle*
while `/metrics` keeps the running total. A funnel that quietly accumulated
into its own line would render a number that looks like a rate and is a
total — the same class of mistake as the source-health summary that compared
a string containing a counter, which twelve green tests failed to catch
because none of them ran more than one iteration.
"""

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alertlib.funnel import METRIC_PREFIX, UNKNOWN, CycleFunnel  # noqa: E402
from alertlib.health import Metrics  # noqa: E402

STAGES = ("streamed", "parsed", "sent")


@pytest.fixture
def metrics():
    return Metrics("clinical-trials", "v0.4.0")


@pytest.fixture
def lines():
    return []


@pytest.fixture
def log(lines):
    logger = logging.getLogger("funnel-test")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)

    class Capture(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    logger.addHandler(Capture())
    logger.propagate = False
    return logger


@pytest.fixture
def funnel(metrics, log):
    return CycleFunnel(metrics, STAGES, log=log)


def test_render_names_every_stage_in_order(funnel):
    funnel.count("streamed", 399)
    funnel.count("parsed", 398)
    assert funnel.render() == "streamed=399 parsed=398 sent=0 new_in_window=?"


def test_a_cycle_logs_one_line(funnel, lines):
    with funnel.cycle():
        funnel.count("streamed", 7)
    assert lines == ["funnel: streamed=7 parsed=0 sent=0 new_in_window=?"]


def test_each_cycle_reports_only_its_own_counts(funnel, lines):
    """The per-iteration property, exercised over many iterations.

    One cycle proves the first cycle works. Twenty prove the twentieth is
    still reporting a per-cycle number, which is the whole claim.
    """
    for i in range(1, 21):
        with funnel.cycle():
            funnel.count("streamed", i)

    assert len(lines) == 20
    for i, line in enumerate(lines, start=1):
        assert line.startswith(f"funnel: streamed={i} ")


def test_metrics_keep_the_running_total(funnel, metrics):
    for _ in range(20):
        with funnel.cycle():
            funnel.count("streamed", 5)
    assert metrics.get(f"{METRIC_PREFIX}_streamed_total") == 100


def test_stages_are_declared_on_metrics_before_any_count(metrics, log):
    CycleFunnel(metrics, STAGES, log=log)
    rendered = metrics.render()
    for stage in STAGES:
        assert f"{METRIC_PREFIX}_{stage}_total" in rendered


def test_the_line_survives_a_failing_cycle(funnel, lines):
    """A cycle that died partway is when the partial funnel is worth most."""
    with pytest.raises(RuntimeError), funnel.cycle():
        funnel.count("streamed", 3)
        raise RuntimeError("upstream fell over")
    assert lines == ["funnel: streamed=3 parsed=0 sent=0 new_in_window=?"]


def test_an_undeclared_stage_raises(funnel):
    with pytest.raises(KeyError):
        funnel.count("strreamed")


def test_a_funnel_needs_distinct_stages(metrics, log):
    with pytest.raises(ValueError):
        CycleFunnel(metrics, (), log=log)
    with pytest.raises(ValueError):
        CycleFunnel(metrics, ("a", "a"), log=log)


def test_first_cohort_is_unknown_not_zero(funnel):
    """Zero is a real answer a later cycle can give, so it cannot mean
    "nothing to compare against"."""
    assert funnel.observe_cohort(["NCT1", "NCT2"]) == 0
    assert funnel.render().endswith(f"new_in_window={UNKNOWN}")


def test_an_unchanging_feed_reports_no_new_candidates(funnel):
    funnel.observe_cohort(["NCT1", "NCT2", "NCT3"])
    for _ in range(10):
        assert funnel.observe_cohort(["NCT1", "NCT2", "NCT3"]) == 0
    assert funnel.render().endswith("new_in_window=0")


def test_a_moving_window_reports_the_arrivals(funnel, metrics):
    funnel.observe_cohort(["NCT1", "NCT2"])
    assert funnel.observe_cohort(["NCT2", "NCT3", "NCT4"]) == 2
    assert funnel.render().endswith("new_in_window=2")
    assert metrics.get(f"{METRIC_PREFIX}_new_in_window") == 2


def test_cohort_comparison_ignores_order_and_duplicates(funnel):
    funnel.observe_cohort(["NCT1", "NCT2"])
    assert funnel.observe_cohort(["NCT2", "NCT1", "NCT1"]) == 0
