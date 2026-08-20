"""Telegram delivery.

Replaces four near-identical `send_telegram` functions, one per bot, each
with slightly different timeout and error handling. Differences between
copies of the same function are how a fix lands in three services and
gets forgotten in the fourth.

Two behaviours the originals lacked:

* **Rate limiting.** `delivery.rate_limit_per_min` was declared in every
  spec and enforced by nobody. Telegram's own limit is ~30 messages/sec
  to different chats but much lower per chat, and a burst of Form 4
  filings could trip it. A token bucket here means the spec value is real.

* **Retry on 429.** Telegram returns `retry_after` in the body; honouring
  it converts a dropped alert into a delayed one. Retries are capped so a
  wedged send can never stall the poll loop past one cycle.
"""

from __future__ import annotations

import threading
import time

import requests

from .log import get_logger

log = get_logger("alertlib.telegram")

API = "https://api.telegram.org"


class _TokenBucket:
    """Simple per-minute bucket. Blocks rather than dropping — an alert
    delivered ten seconds late is still worth sending."""

    def __init__(self, per_min: int) -> None:
        self.capacity = max(1, per_min)
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def take(self) -> float:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(
                self.capacity,
                self.tokens + (now - self.updated) * self.capacity / 60.0,
            )
            self.updated = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return 0.0
            wait = (1.0 - self.tokens) * 60.0 / self.capacity
            self.tokens = 0.0
            self.updated = now + wait
            return wait


class TelegramClient:
    def __init__(self, token: str, chat_id: str, rate_limit_per_min: int = 20,
                 timeout: int = 10, max_retries: int = 3,
                 metrics=None) -> None:
        if not token or not chat_id:
            raise ValueError("TelegramClient requires both a token and a chat id")
        self._token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.max_retries = max_retries
        self.bucket = _TokenBucket(rate_limit_per_min)
        self.metrics = metrics
        self.session = requests.Session()

    def send(self, text: str, parse_mode: str = "HTML",
             disable_preview: bool = False) -> bool:
        """Send one message. Returns success; never raises.

        Callers use the return value to decide whether to mark an item as
        alerted, so a False here must mean "not delivered, safe to retry
        next cycle" — hence no exception ever escapes.
        """
        wait = self.bucket.take()
        if wait > 0:
            log.debug("rate limit: sleeping %.2fs", wait)
            time.sleep(wait)

        url = f"{API}/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(url, json=payload, timeout=self.timeout)
                if resp.status_code == 429:
                    retry_after = _retry_after(resp)
                    log.warning("telegram 429; retrying",
                                extra={"retry_after": retry_after, "attempt": attempt})
                    time.sleep(min(retry_after, 30))
                    continue
                resp.raise_for_status()
                if self.metrics:
                    self.metrics.inc("alert_alerts_sent_total")
                return True
            except Exception as exc:
                log.error("telegram send failed",
                          extra={"attempt": attempt, "error": str(exc)})
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)

        if self.metrics:
            self.metrics.inc("alert_delivery_failures_total")
        return False

    def __repr__(self) -> str:  # never leak the token via repr in a traceback
        return f"<TelegramClient chat_id={self.chat_id} token=***>"


def _retry_after(resp) -> float:
    try:
        return float(resp.json().get("parameters", {}).get("retry_after", 5))
    except Exception:
        return 5.0
