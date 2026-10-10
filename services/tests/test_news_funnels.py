"""Tests for the edgar-mna and fda-catalysts candidate funnels (backlog #44).

Both services alert, but what they examine and drop was unmeasured, so a
category filter that is too tight could not be told apart from a quiet news
day. Each test feeds one fetch of a known feed through the real fetch and
send path and checks every stage count, so a drop branch that forgets its
count, or counts twice, fails here rather than on the host.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from _loader import load_service_main  # noqa: E402
from alertlib.funnel import METRIC_PREFIX, CycleFunnel  # noqa: E402
from alertlib.health import Metrics  # noqa: E402
from alertlib.sources import SourceHealth  # noqa: E402

edgar = load_service_main("edgar_mna", "edgar_mna_main")
fda = load_service_main("fda_catalysts", "fda_catalysts_main")


def _rss(*titles: str) -> bytes:
    items = "".join(
        f"<item><title>{t}</title><link>https://example.invalid/{i}</link>"
        f"<description>stub</description></item>"
        for i, t in enumerate(titles)
    )
    return f"<rss><channel>{items}</channel></rss>".encode()


class _Resp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


class _Svc:
    def __init__(self, delivered: bool):
        self.metrics = Metrics("test", "v0.0.0")
        self.delivered = delivered

    def send_alert(self, _alert) -> bool:
        return self.delivered


def _bind(module, monkeypatch, tmp_path, content: bytes, delivered: bool = True):
    svc = _Svc(delivered)
    monkeypatch.setattr(module, "SVC", svc)
    monkeypatch.setattr(module, "SOURCES", SourceHealth())
    monkeypatch.setattr(module, "FUNNEL", CycleFunnel(
        svc.metrics, module.FUNNEL_STAGES, log=logging.getLogger("test"), cohort=False))
    monkeypatch.setattr(module, "DB_PATH", str(tmp_path / "seen.db"))
    monkeypatch.setattr(module.requests, "get", lambda *a, **k: _Resp(content))
    return svc


def _counts(svc, stages) -> dict[str, int]:
    return {s: int(svc.metrics.get(f"{METRIC_PREFIX}_{s}_total")) for s in stages}


EDGAR_TITLES = (
    "Acme Corp announces Form 8.3 disclosure",               # disclosure_noise
    "Miner signs letter of intent to acquire claims",        # letter_of_intent
    "Acme reports quarterly earnings",                       # unclassified
    "Bidco commences tender offer for Target Inc",           # matched
    "Board rejects unsolicited proposal from Rival",         # matched
)


def test_edgar_every_entry_lands_in_exactly_one_stage(monkeypatch, tmp_path):
    svc = _bind(edgar, monkeypatch, tmp_path, _rss(*EDGAR_TITLES))
    monkeypatch.setattr(edgar, "enrich_hit", lambda hit: None)
    conn = edgar.init_db()

    edgar._process_hits(conn, edgar.fetch_feed("wire", "https://example.invalid/feed"))

    assert _counts(svc, edgar.FUNNEL_STAGES) == {
        "entries": 5, "disclosure_noise": 1, "letter_of_intent": 1,
        "litigation_notice": 0, "unclassified": 1, "matched": 2, "already_seen": 0, "sent": 2, "send_failed": 0,
    }


def test_edgar_second_poll_of_the_same_feed_is_already_seen(monkeypatch, tmp_path):
    svc = _bind(edgar, monkeypatch, tmp_path, _rss(*EDGAR_TITLES))
    monkeypatch.setattr(edgar, "enrich_hit", lambda hit: None)
    conn = edgar.init_db()

    for _ in range(2):
        edgar._process_hits(conn, edgar.fetch_feed("wire", "https://example.invalid/feed"))

    c = _counts(svc, edgar.FUNNEL_STAGES)
    assert (c["entries"], c["matched"], c["already_seen"], c["sent"]) == (10, 4, 2, 2)


def test_edgar_typed_filing_feed_skips_classification(monkeypatch, tmp_path):
    """SC 13D entries are matched by form type, whatever their title says."""
    svc = _bind(edgar, monkeypatch, tmp_path, _rss("SC 13D - Acme Corp", "SC 13D - Beta Inc"))
    edgar.fetch_edgar("EDGAR-13D", "https://example.invalid/atom", "SC 13D")

    c = _counts(svc, edgar.FUNNEL_STAGES)
    assert (c["entries"], c["unclassified"], c["matched"]) == (2, 0, 2)


def test_edgar_failed_delivery_is_send_failed(monkeypatch, tmp_path):
    svc = _bind(edgar, monkeypatch, tmp_path, _rss(EDGAR_TITLES[3]), delivered=False)
    monkeypatch.setattr(edgar, "enrich_hit", lambda hit: None)
    edgar._process_hits(edgar.init_db(), edgar.fetch_feed("wire", "https://example.invalid/feed"))

    c = _counts(svc, edgar.FUNNEL_STAGES)
    assert (c["sent"], c["send_failed"]) == (0, 1)


FDA_TITLES = (
    "FDA approves Acme's drug for rare disease",             # matched
    "Acme to present at healthcare conference",              # unclassified
    "Beta receives Complete Response Letter from FDA",       # matched
)


def test_fda_every_entry_lands_in_exactly_one_stage(monkeypatch, tmp_path):
    for title, expected in zip(FDA_TITLES, (True, False, True), strict=True):
        assert (fda.classify(title)[0] is not None) is expected, title

    svc = _bind(fda, monkeypatch, tmp_path, _rss(*FDA_TITLES))
    conn = fda.init_db()
    for _ in range(2):
        fda._process_hits(conn, fda.fetch_feed("wire", "https://example.invalid/feed"))

    assert _counts(svc, fda.FUNNEL_STAGES) == {
        "entries": 6, "litigation_notice": 0, "unclassified": 2, "matched": 4,
        "already_seen": 2, "sent": 2, "send_failed": 0,
    }


def test_fda_edgar_8k_path_counts_too(monkeypatch, tmp_path):
    svc = _bind(fda, monkeypatch, tmp_path, _rss(*FDA_TITLES))
    fda.fetch_edgar_8k("EDGAR-8K", "https://example.invalid/atom")

    c = _counts(svc, fda.FUNNEL_STAGES)
    assert (c["entries"], c["unclassified"], c["matched"]) == (3, 1, 2)


@pytest.mark.parametrize("module", [edgar, fda], ids=["edgar-mna", "fda-catalysts"])
def test_every_counted_stage_is_declared(module):
    """`count` raises on an undeclared stage, but only when the branch runs.
    Read the source so a rare branch with a typo fails here instead."""
    source = Path(module.__file__).read_text()
    counted = set(re.findall(r'FUNNEL\.count\("(\w+)"', source))
    assert counted == set(module.FUNNEL_STAGES)


class _Cfg:
    poll_interval_sec = 45

    def secret(self, _name):
        return "stub"

    def polling(self, _key, default):
        return default


class _Telegram:
    def send(self, _text):
        return True


class _StoppedService(_Svc):
    """Enough of `Service` for `main()` to start and leave its loop at once."""

    def __init__(self, tmp_path):
        super().__init__(delivered=True)
        self.cfg = _Cfg()
        self.telegram = _Telegram()
        self._tmp = tmp_path
        self.sources = None

    def state_file(self, name):
        return str(self._tmp / name)

    def running(self):
        return False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.mark.parametrize("module", [edgar, fda], ids=["edgar-mna", "fda-catalysts"])
def test_main_binds_the_funnel(module, monkeypatch, tmp_path):
    """Every fetch counts into FUNNEL. Unbound, each would raise inside the
    per-feed `except` and the service would log errors and never alert."""
    svc = _StoppedService(tmp_path)
    for name in ("SVC", "FUNNEL", "DB_PATH", "EDGAR_USER_AGENT"):
        monkeypatch.setattr(module, name, getattr(module, name))
    # edgar-mna's main() writes the SEC User-Agent into this shared dict.
    monkeypatch.setitem(module.HTTP_HEADERS_DEFAULT, "User-Agent",
                        module.HTTP_HEADERS_DEFAULT["User-Agent"])
    monkeypatch.setattr(module.Service, "from_env", classmethod(lambda cls: svc))

    module.main()

    assert isinstance(module.FUNNEL, CycleFunnel)
    assert module.FUNNEL.stages == module.FUNNEL_STAGES
    assert f"{METRIC_PREFIX}_entries_total" in svc.metrics.snapshot()
    # Bound, or the snapshot would never name a failing feed.
    assert svc.sources is module.SOURCES
