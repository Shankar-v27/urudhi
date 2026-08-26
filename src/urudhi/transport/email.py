"""Email transport — the one real channel.

Two explicit modes, chosen by configuration and reported in ``/health``:

* ``sandbox`` — every message is written as an RFC 822 ``.eml`` file into a
  local directory. Nothing leaves the machine. This is what the demo and
  the batch runs use, and it is labelled as such everywhere.
* ``smtp`` — delivered through a configured SMTP server (a Mailtrap/MailHog
  style test inbox, or a real relay). Credentials come from the
  environment and are never logged.

Inbound replies re-enter through ``POST /inbound/email`` (see the API):
the invoice is matched by the ``[URU/…]`` token in the subject, falling
back to the sender's address.
"""

from __future__ import annotations

import os
import re
import smtplib
import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from urudhi.ledger.models import Channel, Debtor
from urudhi.observability import counters, get_logger

log = get_logger("urudhi.transport.email")

_REFERENCE_RE = re.compile(r"\[([A-Z]{2,5}/\d{4}/\d{3,6})\]")


class EmailOutbox:
    def __init__(
        self,
        mode: str = "sandbox",
        *,
        directory: str | Path = "data/outbox",
        from_addr: str = "collections@urudhi.example.in",
        smtp_host: str | None = None,
        smtp_port: int = 587,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        smtp_starttls: bool = True,
    ) -> None:
        if mode not in ("sandbox", "smtp"):
            raise ValueError(f"unknown email mode {mode!r}; use 'sandbox' or 'smtp'")
        if mode == "smtp" and not smtp_host:
            raise ValueError("email mode 'smtp' needs SMTP_HOST")
        self.mode = mode
        self._dir = Path(directory)
        self._from = from_addr
        self._smtp = (smtp_host, smtp_port, smtp_user, smtp_password, smtp_starttls)
        if mode == "sandbox":
            self._dir.mkdir(parents=True, exist_ok=True)
        log.info("transport.configured", channel="email", mode=mode)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> EmailOutbox:
        env = environ if environ is not None else os.environ
        return cls(
            env.get("URUDHI_EMAIL_MODE", "sandbox"),
            directory=env.get("URUDHI_OUTBOX_DIR", "data/outbox"),
            from_addr=env.get("URUDHI_FROM_EMAIL", "collections@urudhi.example.in"),
            smtp_host=env.get("SMTP_HOST") or None,
            smtp_port=int(env.get("SMTP_PORT", "587")),
            smtp_user=env.get("SMTP_USER") or None,
            smtp_password=env.get("SMTP_PASSWORD") or None,
            smtp_starttls=env.get("SMTP_STARTTLS", "1") != "0",
        )

    def send(self, debtor: Debtor, channel: Channel, text: str, *, subject: str,
             reference: str) -> str:
        message = EmailMessage()
        message_id = f"<{uuid.uuid4().hex}@urudhi>"
        message["Message-ID"] = message_id
        message["From"] = self._from
        message["To"] = f"{debtor.contact_name} <{debtor.email}>"
        message["Subject"] = subject
        message["Date"] = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S %z")
        message["X-Urudhi-Invoice"] = reference
        message.set_content(text)

        if self.mode == "sandbox":
            path = self._dir / f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{reference}-{uuid.uuid4().hex[:6]}.eml"
            path.write_bytes(bytes(message))
            counters.inc("email.sandbox.written")
            log.info("email.sandboxed", to=debtor.email, path=str(path))
            return message_id

        host, port, user, password, starttls = self._smtp
        with smtplib.SMTP(host, port, timeout=20) as server:
            if starttls:
                server.starttls()
            if user:
                server.login(user, password or "")
            server.send_message(message)
        counters.inc("email.smtp.sent")
        log.info("email.sent", to=debtor.email)
        return message_id


def reference_from_subject(subject: str) -> str | None:
    """``Re: Invoice URU/2026/0001 — payment reminder [URU/2026/0001]`` → ``URU/2026/0001``."""
    match = _REFERENCE_RE.search(subject or "")
    return match.group(1) if match else None
