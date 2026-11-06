"""Transactional email delivery behind a small provider interface.

- BrevoProvider: Brevo (ex-Sendinblue) transactional API, POST /v3/smtp/email.
- ConsoleProvider: development; logs the message (including links) instead of sending.
- MemoryProvider: tests; keeps an outbox in memory.

Sending is synchronous for now (one HTTP call, 10 s timeout). It moves to a RabbitMQ worker
with retries in Milestone 2. Callers decide whether a failure should surface to the user.
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.config import get_settings

log = logging.getLogger("repintel.email")


@dataclass(frozen=True)
class EmailMessage:
    to_email: str
    to_name: str
    subject: str
    html: str
    text: str
    tags: tuple[str, ...] = field(default_factory=tuple)


class EmailDeliveryError(Exception):
    pass


class EmailProvider(Protocol):
    def send(self, msg: EmailMessage) -> None: ...


class BrevoProvider:
    def __init__(self, api_key: str, url: str, sender_email: str, sender_name: str,
                 client: httpx.Client | None = None):
        self._key = api_key
        self._url = url
        self._sender = {"email": sender_email, "name": sender_name}
        self._client = client or httpx.Client(timeout=10.0)

    def send(self, msg: EmailMessage) -> None:
        payload = {
            "sender": self._sender,
            "to": [{"email": msg.to_email, "name": msg.to_name or msg.to_email}],
            "subject": msg.subject,
            "htmlContent": msg.html,
            "textContent": msg.text,
            "tags": list(msg.tags),
        }
        try:
            r = self._client.post(self._url, json=payload, headers={
                "api-key": self._key, "accept": "application/json",
                "content-type": "application/json",
            })
        except httpx.HTTPError as e:
            log.error("email_send_failed", extra={"provider": "brevo", "error": type(e).__name__})
            raise EmailDeliveryError("Email provider unreachable") from e
        if r.status_code >= 300:
            # Brevo error bodies are {code, message}; never log our request (it has the key).
            log.error("email_send_rejected", extra={"provider": "brevo",
                                                    "status": r.status_code,
                                                    "body": r.text[:300]})
            raise EmailDeliveryError(f"Email provider returned {r.status_code}")
        log.info("email_sent", extra={"provider": "brevo", "tags": list(msg.tags),
                                      "message_id": r.json().get("messageId")})


class ConsoleProvider:
    def send(self, msg: EmailMessage) -> None:
        # Development only (config refuses console in staging/production).
        log.info("email_console", extra={"to": msg.to_email, "subject": msg.subject,
                                         "text": msg.text})


class MemoryProvider:
    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []
        self._lock = threading.Lock()

    def send(self, msg: EmailMessage) -> None:
        with self._lock:
            self.outbox.append(msg)

    def clear(self) -> None:
        with self._lock:
            self.outbox.clear()


_provider: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        if s.email_provider == "brevo":
            _provider = BrevoProvider(s.brevo_api_key.get_secret_value(), s.brevo_api_url,
                                      s.email_from_address, s.email_from_name)
        elif s.email_provider == "memory":
            _provider = MemoryProvider()
        else:
            _provider = ConsoleProvider()
    return _provider


def queue_email(db, msg: EmailMessage, *, tenant_id=None) -> None:  # noqa: ANN001
    """Send through the job queue (retries with backoff). The job is created in the caller's
    transaction, so nothing is sent if that transaction rolls back. The payload (which may
    contain a sign-in link) is wiped once the email is delivered."""
    from app.jobs.queue import enqueue  # local import: jobs.handlers imports this module

    enqueue(db, "email.send", {
        "to_email": msg.to_email, "to_name": msg.to_name, "subject": msg.subject,
        "html": msg.html, "text": msg.text, "tags": list(msg.tags),
    }, tenant_id=tenant_id, max_attempts=6, sensitive=True)
