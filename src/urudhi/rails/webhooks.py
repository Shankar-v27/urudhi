"""Webhook verification and ingestion.

This is the only place money enters the ledger. A payment exists in Urudhi's
numbers if and only if a signature-verified Razorpay webhook said so; nothing
the negotiating agent believes or claims can create one. Replayed deliveries
are dropped on the event id, so at-least-once delivery can't double-count.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from urudhi.audit.log import Actor, EventKind
from urudhi.ledger.models import Payment
from urudhi.ledger.transitions import record_payment
from urudhi.store import Store


class WebhookError(Exception):
    """Signature failure or a payload we refuse to interpret."""


def verify_signature(body: bytes, signature: str, secret: str) -> None:
    """Razorpay signs the raw body with HMAC-SHA256(hex). Constant-time compare."""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookError("webhook signature verification failed")


def extract_payment(event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Pull (event_id, payment_entity) out of a payment.captured-style event."""
    event_id = event.get("id") or ""
    if not event_id:
        raise WebhookError("webhook event carries no id; cannot be idempotent")
    if event.get("event") not in ("payment.captured", "payment_link.paid",
                                  "virtual_account.credited"):
        raise WebhookError(f"unhandled event type: {event.get('event')!r}")
    entity = (event.get("payload", {}).get("payment", {}) or {}).get("entity", {})
    if not entity.get("id"):
        raise WebhookError("webhook payload carries no payment entity")
    return event_id, entity


def resolve_invoice_id(entity: dict[str, Any]) -> str:
    """Invoices are tagged in notes.invoice_id when links/VAs are created."""
    invoice_id = (entity.get("notes") or {}).get("invoice_id", "")
    if not invoice_id:
        raise WebhookError("payment entity has no notes.invoice_id tag")
    return invoice_id


def ingest_payment_event(
    store: Store,
    event: dict[str, Any],
    *,
    now: datetime | None = None,
) -> Payment | None:
    """Apply one verified webhook event to the ledger.

    Returns the recorded :class:`Payment`, or ``None`` for a replay. Raises
    :class:`WebhookError` on payloads that cannot be safely interpreted, and
    lets :class:`InvalidTransition` propagate — a payment against a closed
    invoice is an exception to report, not to swallow.
    """
    now = now or datetime.now(UTC)
    event_id, entity = extract_payment(event)
    invoice_id = resolve_invoice_id(entity)

    invoice = store.get_invoice(invoice_id)
    payment = Payment(
        id=f"pay_{event_id}",
        invoice_id=invoice_id,
        amount=entity["amount"],
        method=entity.get("method", "unknown"),
        razorpay_payment_id=entity["id"],
        razorpay_event_id=event_id,
        observed_at=now,
    )

    if not store.record_payment_row(payment):
        return None  # replay: already observed this event

    open_promise = store.open_promise_for(invoice_id)
    paid_against = (
        store.paid_between(
            invoice_id, open_promise.made_at, open_promise.promised_on.isoformat()
        ) - payment.amount
        if open_promise is not None
        else 0
    )
    updated_invoice, resolved_promise = record_payment(
        invoice, payment, open_promise=open_promise,
        paid_against_promise=max(0, paid_against),
    )
    store.put_invoice(updated_invoice)
    store.append_event(
        at=now, actor=Actor.RAILS, kind=EventKind.PAYMENT_OBSERVED,
        invoice_id=invoice_id, debtor_id=invoice.debtor_id,
        payload={
            "amount": payment.amount, "method": payment.method,
            "razorpay_payment_id": payment.razorpay_payment_id,
            "razorpay_event_id": event_id,
            "invoice_state": updated_invoice.state,
        },
    )
    if resolved_promise is not None:
        store.put_promise(resolved_promise)
        store.append_event(
            at=now, actor=Actor.RAILS, kind=EventKind.PROMISE_RESOLVED,
            invoice_id=invoice_id, debtor_id=invoice.debtor_id,
            payload={"promise_id": resolved_promise.id, "outcome": resolved_promise.state},
        )
    return payment
