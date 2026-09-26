"""Shared fakes for the form4-insider suites.

Two test files drive `process_filing`, and both need the same stand-in for
the platform `Service` plus a real funnel over a real `Metrics`. The real
Metrics rather than a stub, because the funnel declares counters and gauges
on it and a stub that silently accepts anything would not catch a stage
counted under a name nothing declared.
"""

from __future__ import annotations

from _loader import load_service_main

form4 = load_service_main("form4_insider", "form4_insider_main")

# After load_service_main, which is what puts `services/` and the service's
# own directory on sys.path. Re-exported so the suites import them from here
# rather than each repeating the ordering constraint.
import form4_common  # noqa: E402
from alertlib import CycleFunnel, Metrics, get_logger  # noqa: E402

__all__ = [
    "ACCESSION", "CIK", "Svc", "form4", "form4_common", "make_funnel",
]

ACCESSION = "0001193125-26-396718"
CIK = "0000826083"


class Svc:
    """Stands in for the platform Service.

    `send_alert` is the seam the service sends through: it archives the
    alert, delivers it, and records the outcome. Collecting the Alert objects
    here lets a test assert on what was sent as well as how many.
    """

    def __init__(self) -> None:
        self.metrics = Metrics("form4-insider", "test")
        self.alerts: list = []
        self.delivers = True

    def send_alert(self, alert) -> bool:
        self.alerts.append(alert)
        return self.delivers


def make_funnel(metrics: Metrics | None = None) -> CycleFunnel:
    return CycleFunnel(
        metrics if metrics is not None else Metrics("form4-insider", "test"),
        form4.FUNNEL_STAGES,
        log=get_logger("test-form4"),
    )
