"""Headlines that name a market event without being one.

Plaintiff law firms issue press releases on the same wires the news services
poll, and their titles carry the ticker and the event they are suing over
("AARD Shareholder Alert: Investors With Losses May Seek to Lead the Class
Action", 2026-10-08). The category phrases in `edgar-mna` and
`fda-catalysts` matched them as CLINICAL_HOLD, FDA_CRL and SIGNED_DEAL
(backlog #45). They announce litigation about an event the market has
already priced, so they are dropped before classification.

Title only: a genuine deal or FDA release can mention litigation in its body,
and a body match would silence it.
"""

from __future__ import annotations

import re

_LITIGATION_TITLE_RE = re.compile(
    r"|".join((
        r"\b(?:shareholder|stockholder|investor)s?\s+(?:alert|notice|reminder|investigation)\b",
        r"\bclass[\s-]+action\b",
        r"\blead[\s-]+plaintiff\b",
        r"\bsecurities\s+(?:fraud|litigation)\b",
        r"\binvestors?\s+(?:with|who\s+(?:lost|suffered))\s+(?:substantial\s+|significant\s+)?losses\b",
        r"\blaw\s+(?:firm|group|offices?)\b",
        r"\b(?:secure|contact)\s+(?:counsel|the\s+firm)\b",
        r"\binvestigat(?:es|ion|ing)\b.{0,80}\bon\s+behalf\s+of\b",
        r"\binvestigates?\s+(?:whether|the\s+(?:fairness|adequacy))\b",
    )),
    re.IGNORECASE,
)


def is_litigation_notice(title: str) -> bool:
    """True when a headline is a plaintiff firm's solicitation, not the event."""
    return bool(_LITIGATION_TITLE_RE.search(title or ""))
