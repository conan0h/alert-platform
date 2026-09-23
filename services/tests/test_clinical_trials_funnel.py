"""Tests for clinical-trials' per-cycle accounting.

The service streams ~399 trials every cycle and has alerted on none of them
for weeks (backlog #35). Nothing between "streamed 399" and "alerted 0" was
recorded, so a filter that is too tight, a feed returning the same rows every
cycle, and a transition observed one cycle too late all looked identical from
the journal. These tests pin the counts that separate them.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""
from __future__ import annotations

import pytest
from _loader import load_service_main  # noqa: E402

ct = load_service_main("clinical_trials", "clinical_trials_main")


def trial(status: str, nct_id: str = "NCT00000001") -> dict:
    return {"nct_id": nct_id, "status": status, "has_results": 0}


def stored(status: str, alerted: str | None = None) -> dict:
    return {"status": status, "alerted_status": alerted or status, "has_results": 0,
            "first_seen": "2026-09-01T00:00:00+00:00"}


# --- observation_stages ----------------------------------------------------

def test_a_trial_we_have_never_seen_is_first_sight():
    assert ct.observation_stages(None, trial("RECRUITING")) == ("first_sight",)


def test_first_sight_already_completed_is_counted_separately():
    """The count that tests the leading explanation for zero alerts.

    detect_signal drops a COMPLETED it has no previous status for, so these
    trials are seen and deliberately not alerted on. If the number is large
    every cycle, the filter is not too tight — the transition is arriving
    before we do.
    """
    assert ct.observation_stages(None, trial("COMPLETED")) == (
        "first_sight", "first_sight_completed",
    )
    # And detect_signal really does decline it, which is what makes the
    # counter meaningful rather than decorative.
    assert ct.detect_signal(None, trial("COMPLETED")) is None


def test_a_known_trial_at_the_same_status_is_only_known():
    assert ct.observation_stages(stored("RECRUITING"), trial("RECRUITING")) == ("known",)


def test_a_known_trial_that_moved_is_counted_as_changed():
    assert ct.observation_stages(stored("RECRUITING"), trial("TERMINATED")) == (
        "known", "changed",
    )
    assert ct.detect_signal(stored("RECRUITING"), trial("TERMINATED"))[0] == "TERMINATED"


def test_every_stage_returned_is_one_the_funnel_declares():
    """A stage name that drifted from FUNNEL_STAGES would raise in the poll
    loop, in production, on whichever cycle first hit that branch."""
    cases = [
        (None, trial("RECRUITING")),
        (None, trial("COMPLETED")),
        (stored("RECRUITING"), trial("RECRUITING")),
        (stored("RECRUITING"), trial("COMPLETED")),
    ]
    for prev, curr in cases:
        for stage in ct.observation_stages(prev, curr):
            assert stage in ct.FUNNEL_STAGES


# --- fetch_recent_changes --------------------------------------------------

class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _pages(monkeypatch, pages):
    """Serve `pages` in order, one per requests.get call."""
    calls = {"n": 0}

    def fake_get(*_a, **_kw):
        i = calls["n"]
        calls["n"] += 1
        if i >= len(pages):
            raise AssertionError(f"asked for page {i + 1}; only {len(pages)} staged")
        return _Response(pages[i])

    monkeypatch.setattr(ct.requests, "get", fake_get)
    monkeypatch.setattr(ct.time, "sleep", lambda *_: None)
    return calls


def _study(i):
    return {"protocolSection": {"identificationModule": {"nctId": f"NCT{i:08d}"}}}


def test_the_query_paginates_until_the_token_runs_out(monkeypatch, caplog):
    caplog.set_level("INFO", logger="clinical-trials")
    calls = _pages(monkeypatch, [
        {"studies": [_study(i) for i in range(200)], "nextPageToken": "t1",
         "totalCount": 399},
        {"studies": [_study(i) for i in range(200, 399)]},
    ])

    got = list(ct.fetch_recent_changes(days_back=2))

    assert len(got) == 399
    assert calls["n"] == 2
    assert "Streamed 399 of 399" in caplog.text
    assert "in 2 page(s)" in caplog.text
    assert "page cap" not in caplog.text


def test_hitting_the_page_cap_says_so(monkeypatch, caplog):
    """A truncated window must not read like a complete one."""
    caplog.set_level("INFO", logger="clinical-trials")
    _pages(monkeypatch, [
        {"studies": [_study(i)], "nextPageToken": f"t{i}", "totalCount": 5000}
        for i in range(ct.MAX_PAGES)
    ])

    got = list(ct.fetch_recent_changes(days_back=2))

    assert len(got) == ct.MAX_PAGES
    assert "page cap reached" in caplog.text
    # The count the cap makes actionable: read 10, of 5000 that matched.
    assert f"Streamed {ct.MAX_PAGES} of 5000" in caplog.text


def test_a_failure_partway_still_reports_what_it_read(monkeypatch, caplog):
    """The regression. The tally used to sit after the loop, so both early
    returns skipped it and a cycle that died on page two logged nothing —
    indistinguishable from a cycle that never ran."""
    caplog.set_level("INFO", logger="clinical-trials")

    calls = {"n": 0}

    def fake_get(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Response({"studies": [_study(i) for i in range(200)],
                              "nextPageToken": "t1"})
        raise RuntimeError("connection reset")

    monkeypatch.setattr(ct.requests, "get", fake_get)
    monkeypatch.setattr(ct.time, "sleep", lambda *_: None)

    got = list(ct.fetch_recent_changes(days_back=2))

    assert len(got) == 200
    assert "Streamed 200 of unknown" in caplog.text
    assert "in 1 page(s)" in caplog.text


def test_an_abandoned_generator_still_reports(monkeypatch, caplog):
    """A caller that stops early — a `break`, or an exception in the poll
    loop — must not silently lose the cycle's tally."""
    caplog.set_level("INFO", logger="clinical-trials")
    _pages(monkeypatch, [
        {"studies": [_study(i) for i in range(200)], "nextPageToken": "t1"},
        {"studies": [_study(i) for i in range(200, 399)]},
    ])

    gen = ct.fetch_recent_changes(days_back=2)
    next(gen)
    gen.close()

    assert "Streamed 1 " in caplog.text


@pytest.mark.parametrize("status", ["COMPLETED", "TERMINATED", "SUSPENDED", "WITHDRAWN"])
def test_parse_trial_survives_the_statuses_the_signals_care_about(status):
    raw = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT12345678", "briefTitle": "A study"},
            "statusModule": {"overallStatus": status, "lastUpdatePostDate": "2026-09-22"},
        },
        "hasResults": False,
    }
    parsed = ct.parse_trial(raw)
    assert parsed is not None
    assert parsed["status"] == status
