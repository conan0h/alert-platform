"""Headlines that matched a category phrase without its context (#45).

Each WRONG case was sent, or has the shape of one that was sent, under a label
that claims more than the story says. Each KEEP case is a headline the same
category must still claim, so a fix that only narrows a pattern fails here.
"""
from __future__ import annotations

import pytest
from test_news_funnels import edgar, fda

EDGAR_CASES = (
    # Sent as SIGNED_DEAL 10-11T23:26Z. An unconfirmed report is a rumour.
    ("Amazon in talks to acquire AI startup Decart for $7B: WSJ", "TAKEOVER_RUMOR"),
    ("Acme Corp in advanced talks to acquire Beta Inc. for $12 per share", "TAKEOVER_RUMOR"),
    ("KKR nears deal to acquire Acme for $3 billion, people familiar say", "TAKEOVER_RUMOR"),
    ("KKR in talks to acquire Acme", "TAKEOVER_RUMOR"),
    # Sent as STRATEGIC_REVIEW 10-09. A review that ended is not a sale process.
    ("MTY Food Group Announces End of Strategic Review", None),
    ("Acme Concludes Review of Strategic Alternatives", None),
    ("Acme Completes Strategic Review; Will Remain Independent", None),
    # [A-Z] under IGNORECASE matched any word, so a share purchase read as a deal.
    ("Acme to acquire additional shares for $5 million", None),
    # Must still classify as before.
    ("Acme Corp to acquire Beta Inc. for $12 per share in cash", "SIGNED_DEAL"),
    ("Viatris Agrees to Acquire Pacira BioSciences, Advancing Its Innovative Medicines", "SIGNED_DEAL"),
    ("Drilling Tools International Corp. Signs Definitive Agreement to Acquire Saltire", "SIGNED_DEAL"),
    ("Blackstone to acquire Acme Holdings for $4.2 billion", "PE_TAKEPRIVATE"),
    ("Chipotle Mexican jumps on report Starbucks has explored takeover", "TAKEOVER_RUMOR"),
    ("Acme Announces Review of Strategic Alternatives", "STRATEGIC_REVIEW"),
    ("Acme Concludes Strategic Review with Definitive Agreement to be Acquired by Beta", "SIGNED_DEAL"),
)

FDA_CASES = (
    # Atossa's 10-09 CVR release was sent as PRIORITY_REVIEW. Its summary was
    # not recorded; a voucher mention is the only PRIORITY_REVIEW pattern a CVR
    # release plausibly contains, and this is that shape.
    ("Atossa Therapeutics Executes Contingent Value Rights Agreement\n"
     "Holders receive proceeds from any sale of the priority review voucher.", None),
    ("Acme sells priority review voucher for $105 million", None),
    # Must still classify.
    ("FDA grants priority review to Acme's BLA for lead candidate", "PRIORITY_REVIEW"),
    ("Acme receives rare pediatric disease designation and priority review voucher eligibility", "RARE_PEDIATRIC"),
)


@pytest.mark.parametrize("text,expected", EDGAR_CASES)
def test_edgar_category(text, expected):
    assert edgar.classify(text)[0] == expected


@pytest.mark.parametrize("text,expected", FDA_CASES)
def test_fda_category(text, expected):
    assert fda.classify(text)[0] == expected


def test_hedge_in_summary_also_counts():
    """classify() sees title and summary; a hedge in either makes it a rumour."""
    blob = "Acme to acquire Beta Inc. for $12 per share\nThe companies are in talks, people familiar said."
    assert edgar.classify(blob)[0] == "TAKEOVER_RUMOR"


def test_wire_suffix_hedge_is_anchored_to_a_line_end():
    """': Reuters' ends a headline; 'Reuters' inside a signed-deal release does not hedge it."""
    blob = "Acme to acquire Beta Inc. for $12 per share\nAcme, a Reuters data partner, said the deal closes in Q1."
    assert edgar.classify(blob)[0] == "SIGNED_DEAL"


def test_otherwise_names_a_category():
    names = {c["name"] for c in edgar.CATEGORIES}
    for cat in edgar.CATEGORIES:
        assert cat.get("otherwise") in names | {None}, cat["name"]
