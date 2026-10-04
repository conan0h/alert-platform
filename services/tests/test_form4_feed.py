"""Tests for which entries of EDGAR's current-filings feed reach `new`.

The feed URL asks for `type=4`, which EDGAR matches as a form-type prefix, so
the feed can also carry 424B2, 497 and other forms. Without this filter each
is counted as a new Form 4 filing and then lost at `no_form4_xml` or at
parsing (backlog #46).

Run: python3 -m pytest services/tests -q     (from the repo root)
"""

from __future__ import annotations

import logging

import pytest
from _form4 import form4


def _entry(title: str, accession: str, cik: str, form_type: str | None) -> str:
    nodash = accession.replace("-", "")
    category = (
        f'<category scheme="https://www.sec.gov/" label="form type" term="{form_type}"/>'
        if form_type is not None else ""
    )
    return f"""
<entry>
<title>{title}</title>
<link rel="alternate" type="text/html"
 href="https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/{accession}-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-10-02 &lt;b&gt;AccNo:&lt;/b&gt; {accession}</summary>
<updated>2026-10-02T16:05:01-04:00</updated>
{category}
<id>urn:tag:sec.gov,2008:accession-number={accession}</id>
</entry>"""


def _feed(*entries: str) -> bytes:
    return f"""<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings - Sat, 04 Oct 2026 04:00:00 EDT</title>
{''.join(entries)}
</feed>""".encode("iso-8859-1")


FORM4_REPORTING = _entry("4 - Smith Jane (0001234567) (Reporting)",
                         "0001209191-26-012345", "1234567", "4")
FORM4_ISSUER = _entry("4 - Acme Corp (0000320193) (Issuer)",
                      "0001209191-26-012345", "320193", "4")
FORM4_AMENDED = _entry("4/A - Doe John (0001111111) (Reporting)",
                       "0001209191-26-012346", "1111111", "4/A")
PROSPECTUS = _entry("424B2 - Big Bank Notes (0000019617) (Filer)",
                    "0001213900-26-099999", "19617", "424B2")
FUND = _entry("497K - Some Fund Trust (0000900000) (Filer)",
              "0000894189-26-001234", "900000", "497K")


@pytest.fixture(autouse=True)
def fresh_type_log(monkeypatch):
    monkeypatch.setattr(form4, "_dropped_types_logged", set())


def test_only_form4_and_amendments_are_returned():
    entries, not_form4 = form4.parse_form4_feed(
        _feed(FORM4_REPORTING, PROSPECTUS, FORM4_ISSUER, FUND, FORM4_AMENDED))

    assert entries == [
        ("0001209191-26-012345", "1234567"),
        ("0001209191-26-012345", "320193"),
        ("0001209191-26-012346", "1111111"),
    ]
    assert not_form4 == 2


@pytest.mark.parametrize(("title", "form_type", "expected"), [
    ("424B2 - Big Bank Notes (0000019617) (Filer)", "424B2", "424B2"),
    # No category: the title prefix decides.
    ("424B2 - Big Bank Notes (0000019617) (Filer)", None, "424B2"),
    ("4 - Smith Jane (0001234567) (Reporting)", None, "4"),
    # Neither: unknown, which the filter keeps.
    ("Smith Jane (0001234567)", None, None),
])
def test_form_type_comes_from_the_category_then_the_title(title, form_type, expected):
    import feedparser
    [entry] = feedparser.parse(
        _feed(_entry(title, "0001209191-26-012345", "1234567", form_type))).entries
    assert form4.entry_form_type(entry) == expected


def test_an_entry_with_no_form_type_is_kept():
    """A changed feed layout must degrade to the old behaviour, not to silence."""
    unlabelled = _entry("Smith Jane (0001234567)", "0001209191-26-012345", "1234567", None)

    entries, not_form4 = form4.parse_form4_feed(_feed(unlabelled))

    assert entries == [("0001209191-26-012345", "1234567")]
    assert not_form4 == 0


def test_each_dropped_type_is_logged_once(caplog):
    """The feed repeats every poll; the log should name each type once."""
    with caplog.at_level(logging.INFO):
        form4.parse_form4_feed(_feed(PROSPECTUS, FUND, PROSPECTUS))
        form4.parse_form4_feed(_feed(PROSPECTUS, FUND))

    logged = [r.form_type for r in caplog.records
              if r.getMessage() == "feed entry is not a Form 4"]
    assert sorted(logged) == ["424B2", "497K"]


def test_dropped_entries_are_funnel_stages():
    assert {"entries", "not_form4", "new", "unparsed", "parsed"} <= set(form4.FUNNEL_STAGES)
    stages = list(form4.FUNNEL_STAGES)
    assert stages.index("not_form4") < stages.index("new")
    assert stages.index("fetched") < stages.index("unparsed") < stages.index("parsed")
