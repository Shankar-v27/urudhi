"""Webhook verification and ingestion.

This is the only place money enters the ledger. A payment exists in Urudhi's
numbers if and only if a signature-verified Razorpay webhook said so; nothing
the negotiating agent believes or claims can create one.

Every delivery gets exactly one ruling, remembered by event id:

* ``recorded``  — a new payment on a known invoice; the ledger moved;
* ``replay``    — we ruled on this event id before; nothing changes;
* ``unmatched`` — signed and well-formed, but no ledger invoice matches; the
  event is audited with its ids so a person can reconcile it;
* ``rejected``  — signed, but the ledger refused it (already settled, overpay,
  malformed amount); audited with the reason.

None of the last three is an error to the sender: Razorpay retries on non-2xx
and retrying cannot change any of them, so the receiver acknowledges.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from urudhi.audit.log import Actor, EventKind
from urudhi.ledger.models import CommitmentState, Payment, PaymentCommitment
from urudhi.ledger.transitions import (
    InvalidTransition,
    apply_payment_to_commitments,
    note_late_payment,
    record_payment,
)
from urudhi.observability import counters, get_logger
from urudhi.rails.razorpay_client import payment_amount
from urudhi.store import Store

log = get_logger("urudhi.webhooks")

HANDLED_EVENTS = frozenset({"payment.captured", "payment_link.paid", "virtual_account.credited"})


class WebhookError(Exception):
    """Signature failure or a payload we refuse to interpret."""


class IngestStatus(enum.StrEnum):
    RECORDED = "recorded"
    REPLAY = "replay_ignored"
    UNMATCHED = "unmatched"
    REJECTED = "rejected"
    IGNORED = "ignored"  # an event type we don't handle


class IngestResult(BaseModel):
    status: IngestStatus
    payment: Payment | None = None
    reason: str = ""
    event_id: str = ""


def verify_signature(body: bytes, signature: str, secret: str) -> None:
    """Razorpay signs the raw body with HMAC-SHA256(hex). Constant-time compare."""
    if not secret:
        raise WebhookError("webhook secret is not configured; refusing to verify")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookError("webhook signature verification failed")


def extract_payment(event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Pull (event_id, payment_entity) out of a payment-carrying event."""
    event_id = event.get("id") or ""
    if not event_id:
        raise WebhookError("webhook event carries no id; cannot be idempotent")
    if event.get("event") not in HANDLED_EVENTS:
        raise WebhookError(f"unhandled event type: {event.get('event')!r}")
    entity = (event.get("payload", {}).get("payment", {}) or {}).get("entity", {})
    if not entity.get("id"):
        raise WebhookError("webhook payload carries no payment entity")
    return event_id, entity


def resolve_invoice_id(store: Store, event: dict[str, Any], entity: dict[str, Any]) -> str | None:
    """Find the ledger invoice a payment belongs to.

    Order: the payment's own ``notes.invoice_id`` (payment links carry their
    notes through to payments); the payment link entity's notes/reference;
    the Smart Collect virtual account's ``notes.invoice_id``; finally the
    virtual account id stored on the invoice when the VA was created.
    """
    notes = entity.get("notes") or {}
    if isinstance(notes, dict) and notes.get("invoice_id"):
        return str(notes["invoice_id"])
    payload = event.get("payload", {}) or {}
    link = (payload.get("payment_link", {}) or {}).get("entity", {}) or {}
    link_notes = link.get("notes") or {}
    if isinstance(link_notes, dict) and link_notes.get("invoice_id"):
        return str(link_notes["invoice_id"])
    if link.get("reference_id"):
        return str(link["reference_id"])
    va = (payload.get("virtual_account", {}) or {}).get("entity", {}) or {}
    va_notes = va.get("notes") or {}
    if isinstance(va_notes, dict) and va_notes.get("invoice_id"):
        return str(va_notes["invoice_id"])
    va_id = va.get("id") or entity.get("virtual_account_id")
    if va_id:
        for invoice in store.all_invoices():
            if invoice.razorpay_virtual_account_id == va_id:
                return invoice.id
    return None


def resolve_commitment_id(event: dict[str, Any], entity: dict[str, Any]) -> str | None:
    """The commitment a payment instrument was issued for, if the rails carried it."""
    notes = entity.get("notes") or {}
    if isinstance(notes, dict) and notes.get("commitment_id"):
        return str(notes["commitment_id"])
    link = ((event.get("payload", {}) or {}).get("payment_link", {}) or {}).get("entity", {}) or {}
    link_notes = link.get("notes") or {}
    if isinstance(link_notes, dict) and link_notes.get("commitment_id"):
        return str(link_notes["commitment_id"])
    reference = str(link.get("reference_id") or "")
    if reference.startswith("cmt_"):
        return reference
    return None


def _match_commitments(
    store: Store, invoice_id: str, amount: int, now: datetime, instrument_cid: str | None,
) -> tuple[list[PaymentCommitment], str | None, str | None, list[dict[str, Any]]]:
    """Decide which commitment(s) this money fulfils.

    Hierarchy: an instrument tagged with a commitment id is an *exact* match;
    otherwise the invoice's live commitments are paid earliest-deadline first
    (an *invoice* match). Returns (touched, primary_commitment_id, matched_by,
    audit_rows).
    """
    live = store.live_commitments_for(invoice_id)
    audit: list[dict[str, Any]] = []
    matched_by: str | None = None
    primary: str | None = None
    touched: list[PaymentCommitment] = []
    remaining = amount

    if instrument_cid is not None:
        try:
            target = store.get_commitment(instrument_cid)
        except KeyError:
            target = None
        if target is not None and target.invoice_id == invoice_id:
            primary = target.id
            if target.live:
                matched_by = "instrument"
                first, remaining = apply_payment_to_commitments([target], amount, now)
                touched.extend(first)
                live = [c for c in live if c.id != target.id]
            elif target.state is CommitmentState.MISSED:
                matched_by = "instrument-late"
                late = note_late_payment(target, amount)
                touched.append(late)
                audit.append({"commitment_id": target.id, "applied": amount, "late": True,
                              "state": late.state, "matched_by": matched_by})
                remaining = 0
            else:
                matched_by = "instrument-stale"
                audit.append({"commitment_id": target.id, "applied": 0,
                              "state": target.state, "matched_by": matched_by,
                              "reason": f"instrument belongs to a {target.state} commitment"})
    if remaining > 0 and live:
        more, remaining = apply_payment_to_commitments(live, remaining, now)
        if more:
            touched.extend(more)
            matched_by = matched_by or "invoice"
            primary = primary or more[0].id
    for c in touched:
        if c.state is CommitmentState.MISSED:
            continue
        audit.append({"commitment_id": c.id, "state": c.state,
                      "amount_received": c.amount_received,
                      "amount_remaining": c.amount_remaining, "days_late": c.days_late,
                      "matched_by": "instrument" if c.id == instrument_cid else "invoice"})
    return touched, primary, matched_by, audit


def ingest_payment_event(
    store: Store,
    event: dict[str, Any],
    *,
    now: datetime | None = None,
) -> IngestResult:
    """Apply one signature-verified webhook event to the ledger, exactly once.

    Raises :class:`WebhookError` only for payloads that are not payment events
    at all (no id, unhandled type, no payment entity). Everything else —
    unknown invoice, refused amount, already-settled invoice — is *ruled on*,
    audited, remembered, and returned as a status.
    """
    now = now or datetime.now(UTC)
    event_id, entity = extract_payment(event)

    previous = store.webhook_event_status(event_id)
    if previous is not None:
        counters.inc("webhook.replay")
        return IngestResult(status=IngestStatus.REPLAY, event_id=event_id, reason=previous)

    def rule(status: IngestStatus, kind: EventKind, reason: str,
             invoice_id: str | None = None, debtor_id: str | None = None) -> IngestResult:
        diagnostic = {
            "razorpay_event_id": event_id, "razorpay_payment_id": entity.get("id"),
            "amount": entity.get("amount"), "currency": entity.get("currency"),
            "method": entity.get("method"), "reason": reason,
        }
        if not store.record_webhook_event(event_id, status.value, now, diagnostic):
            counters.inc("webhook.replay")
            return IngestResult(status=IngestStatus.REPLAY, event_id=event_id)
        store.append_event(at=now, actor=Actor.RAILS, kind=kind, invoice_id=invoice_id,
                           debtor_id=debtor_id, payload=diagnostic)
        counters.inc(f"webhook.{status.value}")
        log.info("webhook.ruled", status=status.value, event_id=event_id, reason=reason)
        return IngestResult(status=status, event_id=event_id, reason=reason)

    try:
        amount = payment_amount(entity)
    except ValueError as error:
        return rule(IngestStatus.REJECTED, EventKind.PAYMENT_REJECTED, str(error))

    invoice_id = resolve_invoice_id(store, event, entity)
    if invoice_id is None:
        return rule(IngestStatus.UNMATCHED, EventKind.PAYMENT_UNMATCHED,
                    "no invoice reference on payment, payment link or virtual account")
    try:
        invoice = store.get_invoice(invoice_id)
    except KeyError:
        return rule(IngestStatus.UNMATCHED, EventKind.PAYMENT_UNMATCHED,
                    f"invoice {invoice_id!r} is not in the ledger")

    instrument_cid = resolve_commitment_id(event, entity)
    touched, primary_cid, matched_by, commitment_audit = _match_commitments(
        store, invoice_id, amount, now, instrument_cid,
    )
    payment = Payment(
        id=f"pay_{event_id}", invoice_id=invoice_id, amount=amount,
        method=entity.get("method", "unknown"), razorpay_payment_id=entity["id"],
        razorpay_event_id=event_id, observed_at=now,
        commitment_id=primary_cid, matched_by=matched_by,
    )
    open_promise = store.open_promise_for(invoice_id)
    concession = store.live_concession_for(invoice_id)
    paid_against = (
        store.paid_between(invoice_id, open_promise.made_at, open_promise.promised_on.isoformat())
        if open_promise is not None else 0
    )
    paid_since_offer = (
        store.paid_since(invoice_id, concession.offered_at) if concession is not None else 0
    )
    try:
        updated_invoice, resolved_promise, resolved_concession = record_payment(
            invoice, payment, open_promise=open_promise, paid_against_promise=paid_against,
            concession=concession, paid_since_offer=paid_since_offer,
        )
    except InvalidTransition as error:
        return rule(IngestStatus.REJECTED, EventKind.PAYMENT_REJECTED, str(error),
                    invoice_id=invoice_id, debtor_id=invoice.debtor_id)

    with store.transaction():
        if not store.record_payment_row(payment):
            return IngestResult(status=IngestStatus.REPLAY, event_id=event_id)
        store.record_webhook_event(event_id, IngestStatus.RECORDED.value, now,
                                   {"payment_id": payment.id, "invoice_id": invoice_id})
        store.put_invoice(updated_invoice)
        store.append_event(
            at=now, actor=Actor.RAILS, kind=EventKind.PAYMENT_OBSERVED,
            invoice_id=invoice_id, debtor_id=invoice.debtor_id,
            payload={
                "amount": payment.amount, "method": payment.method,
                "razorpay_payment_id": payment.razorpay_payment_id,
                "razorpay_event_id": event_id,
                "invoice_state": updated_invoice.state,
                "amount_waived": updated_invoice.amount_waived,
                "commitment_id": primary_cid, "matched_by": matched_by,
            },
        )
        for c in touched:
            store.put_commitment(c)
            if c.state is CommitmentState.MISSED:
                continue
            kind = (EventKind.COMMITMENT_FULFILLED if c.state is CommitmentState.FULFILLED
                    else EventKind.COMMITMENT_PARTIALLY_FULFILLED)
            store.append_event(
                at=now, actor=Actor.RAILS, kind=kind,
                invoice_id=invoice_id, debtor_id=invoice.debtor_id,
                payload={"commitment_id": c.id, "committed_amount": c.committed_amount,
                         "amount_received": c.amount_received,
                         "amount_remaining": c.amount_remaining, "days_late": c.days_late,
                         "matched_by": "instrument" if c.id == instrument_cid else "invoice",
                         "razorpay_payment_id": payment.razorpay_payment_id,
                         "razorpay_event_id": event_id, "outcome": c.state},
            )
            counters.inc(f"commitment.{c.state.value}")
        for row in commitment_audit:
            if row.get("late") or row.get("matched_by") == "instrument-stale":
                store.append_event(
                    at=now, actor=Actor.RAILS, kind=EventKind.COMMITMENT_PARTIALLY_FULFILLED
                    if row.get("late") else EventKind.PAYMENT_OBSERVED,
                    invoice_id=invoice_id, debtor_id=invoice.debtor_id,
                    payload={**row, "note": "payment against a non-live commitment",
                             "razorpay_event_id": event_id, "amount": payment.amount,
                             "outcome": row.get("state")},
                )
        if resolved_promise is not None:
            store.put_promise(resolved_promise)
            store.append_event(
                at=now, actor=Actor.RAILS, kind=EventKind.PROMISE_RESOLVED,
                invoice_id=invoice_id, debtor_id=invoice.debtor_id,
                payload={"promise_id": resolved_promise.id, "outcome": resolved_promise.state},
            )
        if resolved_concession is not None:
            store.put_concession(resolved_concession)
            store.append_event(
                at=now, actor=Actor.RAILS, kind=EventKind.CONCESSION_RESOLVED,
                invoice_id=invoice_id, debtor_id=invoice.debtor_id,
                payload={
                    "concession_id": resolved_concession.id,
                    "type": resolved_concession.type, "outcome": resolved_concession.state,
                    "amount_waived": updated_invoice.amount_waived - invoice.amount_waived,
                },
            )
    counters.inc("webhook.recorded")
    counters.inc("recovery.paise_observed", payment.amount)
    log.info("payment.observed", invoice=invoice_id, amount=payment.amount,
             state=updated_invoice.state.value)
    return IngestResult(status=IngestStatus.RECORDED, payment=payment, event_id=event_id)
