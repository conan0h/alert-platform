"""Tests for Form 4 XML parsing: the fields the alert filter decides on.

`should_alert` drops Rule 10b5-1 planned sales, but it can only drop what the
parser flags. SEC's April 2023 schema carries that flag as one document-level
element, `<aff10b5One>`. The parser read two per-transaction tags instead,
so no sale was ever flagged: `alert_funnel_planned_sale_total` read 0 across
515 transactions on 2026-09-28, while 46 sales and buys over $1M alerted.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""

from __future__ import annotations

import pytest
from _form4 import form4, form4_backfill


def filing(aff10b5one: str | None, codes: tuple[str, ...] = ("S",)) -> bytes:
    """A minimal Form 4 in the shape EDGAR serves, one transaction per code."""
    checkbox = "" if aff10b5one is None else f"<aff10b5One>{aff10b5one}</aff10b5One>"
    txs = "".join(f"""
      <nonDerivativeTransaction>
        <securityTitle><value>Common Stock</value></securityTitle>
        <transactionDate><value>2026-09-25</value></transactionDate>
        <transactionCoding>
          <transactionFormType>4</transactionFormType>
          <transactionCode>{code}</transactionCode>
          <equitySwapInvolved>0</equitySwapInvolved>
        </transactionCoding>
        <transactionAmounts>
          <transactionShares><value>20000</value></transactionShares>
          <transactionPricePerShare><value>100.00</value></transactionPricePerShare>
          <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
        </transactionAmounts>
      </nonDerivativeTransaction>""" for code in codes)
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0508</schemaVersion>
  <documentType>4</documentType>
  <periodOfReport>2026-09-25</periodOfReport>
  {checkbox}
  <issuer>
    <issuerCik>0000826083</issuerCik>
    <issuerName>Example Corp</issuerName>
    <issuerTradingSymbol>exmp</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001234567</rptOwnerCik>
      <rptOwnerName>Person A</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isOfficer>1</isOfficer>
      <officerTitle>Chief Executive Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>{txs}
  </nonDerivativeTable>
</ownershipDocument>""".encode()


@pytest.mark.parametrize(("checkbox", "expected"), [
    ("1", 1),
    ("true", 1),
    ("0", 0),
    ("false", 0),
    (None, 0),       # pre-2023 filings have no checkbox
])
def test_the_filing_level_checkbox_flags_every_transaction(checkbox, expected):
    parsed = form4_backfill.parse_form4_xml(filing(checkbox, codes=("S", "S")))
    assert [t["is_10b5_1"] for t in parsed["transactions"]] == [expected, expected]


def test_a_planned_sale_over_one_million_no_longer_alerts():
    """The regression: a $2M 10b5-1 sale reached the large-trade branch."""
    (sale,) = form4_backfill.parse_form4_xml(filing("1"))["transactions"]
    assert sale["usd_value"] == 2_000_000.0
    decision, _ = form4.should_alert(sale, None, None)
    assert decision == "planned_sale"


def test_a_discretionary_sale_over_one_million_still_alerts():
    (sale,) = form4_backfill.parse_form4_xml(filing("0"))["transactions"]
    decision, _ = form4.should_alert(sale, None, None)
    assert decision == "large_trade"


def test_a_purchase_in_a_plan_filing_is_not_dropped_as_a_planned_sale():
    """The planned-sale branch is for sales; a plan buy is still a buy."""
    (buy,) = form4_backfill.parse_form4_xml(filing("1", codes=("P",)))["transactions"]
    decision, _ = form4.should_alert(buy, None, None)
    assert decision == "large_trade"


def test_the_rest_of_the_filing_still_parses():
    parsed = form4_backfill.parse_form4_xml(filing("1"))
    assert parsed["ticker"] == "EXMP"
    assert parsed["insider_name"] == "Person A"
    assert parsed["relationship"] == "Chief Executive Officer"
