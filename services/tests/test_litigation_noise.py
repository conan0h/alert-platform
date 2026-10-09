"""Plaintiff law-firm releases are dropped before classification (#45).

The drop cases are the shapes these releases take on the wires; the first is
verbatim from `fda-catalysts`' 2026-10-08 digest, where it was sent as
CLINICAL_HOLD. The keep cases are headlines both services sent in the same
week and must keep sending.
"""
from __future__ import annotations

import _loader  # noqa: F401  puts services/ on sys.path
import pytest
from alertlib import is_litigation_notice
from test_news_funnels import _bind, _counts, _rss, edgar, fda

LITIGATION = (
    "AARD Shareholder Alert: Investors With Losses May Seek to Lead the Class Action",
    "SHAREHOLDER ALERT: Pomerantz Law Firm Investigates Claims On Behalf of Investors of Acme Corp - ACME",
    "ROSEN, A LEADING LAW FIRM, Encourages Acme Inc. Investors to Secure Counsel Before Important Deadline",
    "The Gross Law Firm Notifies Shareholders of Acme of a Class Action Lawsuit and a Lead Plaintiff Deadline",
    "INVESTOR ALERT: Acme Therapeutics Receives Complete Response Letter; Kessler Topaz Reminds Investors",
    "Halper Sadeh LLC Investigates Whether the Sale of Acme Is Fair to Shareholders",
    "Kahn Swick & Foti Investigates the Adequacy of the Price in Acme's Proposed Buyout",
    "Acme Securities Fraud Investigation: Faruqi & Faruqi Encourages Investors Who Lost Money to Contact the Firm",
    "Stockholder Notice: Acme Holders Who Suffered Losses Should Contact Counsel",
    "Bragar Eagel & Squire, P.C. Reminds Investors That a Class-Action Lawsuit Has Been Filed Against Acme",
)

EDGAR_KEEP = (
    "Viatris Agrees to Acquire Pacira BioSciences, Advancing Its Innovative Medicines",
    "Chipotle Mexican jumps on report Starbucks has explored takeover",
    "Thoma Bravo-backed BlueMatrix to Acquire Aiera, Advancing Governed AI Distribution",
    "Drilling Tools International Corp. Signs Definitive Agreement to Acquire Saltire",
    "Board rejects unsolicited proposal from Rival; urges shareholders to take no action",
)
FDA_KEEP = (
    "FDA approves Roche’s Tecentriq in combination with a fluoropyrimidine and oxaliplatin",
    "Haleon Issues Voluntary Nationwide Recall of Robitussin Honey CF Max Day Adult",
    "Nuevocor Doses First Patient in SUNBEAM-LMNA Phase 1/2 Trial and Receives FDA Fast Track",
    "Acme receives Complete Response Letter from FDA for lead candidate",
)
# Matched on its summary in production (TENDER_OFFER); here only the title matters.
GENUINE = EDGAR_KEEP + FDA_KEEP + (
    "Sun Life cautions shareholders regarding Ocehan LLC's below-market bid for shares",
)


@pytest.mark.parametrize("title", LITIGATION)
def test_law_firm_release_is_litigation(title):
    assert is_litigation_notice(title)


@pytest.mark.parametrize("title", GENUINE)
def test_genuine_headline_is_not_litigation(title):
    assert not is_litigation_notice(title)


def test_genuine_headlines_still_classify():
    """The keep set is only evidence if each title reaches a category."""
    for title in EDGAR_KEEP:
        assert edgar.classify(title)[0] is not None, title
    for title in FDA_KEEP:
        assert fda.classify(title)[0] is not None, title


def test_litigation_title_would_otherwise_alert():
    """Without the filter this is an FDA_CRL alert, not unclassified noise."""
    assert fda.classify(LITIGATION[4])[0] == "FDA_CRL"


def test_edgar_counts_litigation_notice(monkeypatch, tmp_path):
    svc = _bind(edgar, monkeypatch, tmp_path, _rss(LITIGATION[5], EDGAR_KEEP[0]))
    monkeypatch.setattr(edgar, "enrich_hit", lambda hit: None)
    edgar._process_hits(edgar.init_db(), edgar.fetch_feed("wire", "https://example.invalid/feed"))

    c = _counts(svc, edgar.FUNNEL_STAGES)
    assert (c["entries"], c["litigation_notice"], c["matched"], c["sent"]) == (2, 1, 1, 1)


def test_fda_counts_litigation_notice(monkeypatch, tmp_path):
    svc = _bind(fda, monkeypatch, tmp_path, _rss(LITIGATION[0], LITIGATION[4], FDA_KEEP[0]))
    fda._process_hits(fda.init_db(), fda.fetch_feed("wire", "https://example.invalid/feed"))

    c = _counts(svc, fda.FUNNEL_STAGES)
    assert (c["entries"], c["litigation_notice"], c["matched"], c["sent"]) == (3, 2, 1, 1)
