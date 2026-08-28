"""Issuing a payment instrument for an already-approved commitment.

One code path, used by the recovery loop at commitment time and by the
provisioning command afterwards, so a Razorpay Payment Link is always created
the same way: amount = the approved commitment's amount (never a model's
number), ``reference_id`` = the commitment id, ``notes`` = {invoice_id,
commitment_id}, ``expire_by`` = the commitment deadline. The rail's returned
customer-facing URL is stored verbatim — it is never rebuilt from an id — and
the rail's explicit ``mode`` is persisted with it. A refusal is a fact to
audit, not an exception to propagate.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from urudhi.audit.log import Actor, EventKind
from urudhi.ledger.models import Debtor, InstrumentMode, InstrumentType, Invoice, PaymentCommitment
from urudhi.ledger.money import format_inr
from urudhi.observability import counters, get_logger

log = get_logger("urudhi.instruments")


def issue_instrument(store, rails: Any, invoice: Invoice, debtor: Debtor,
                     commitment: PaymentCommitment, now: datetime,
                     actor: Actor = Actor.RAILS) -> PaymentCommitment:
    """Create the rail-side instrument for ``commitment`` and return the updated commitment.

    Persists nothing itself beyond audit events; the caller stores the result.
    """
    mode = getattr(rails, "mode", None)
    try:
        link = rails.create_payment_link(
            amount=commitment.committed_amount,
            description=f"Invoice {invoice.number} — {format_inr(commitment.committed_amount)} "
                        f"by {commitment.due_on:%d %b %Y}",
            invoice_id=invoice.id, commitment_id=commitment.id, customer_name=debtor.name,
            customer_email=debtor.email, customer_contact=debtor.phone,
            expire_by=int(commitment.due_at.timestamp()),
        )
    except Exception as error:  # the rail is external; its refusal is a fact to record
        reason = f"{type(error).__name__}: {str(error)[:160]}"
        store.append_event(
            at=now, actor=Actor.RAILS, kind=EventKind.RAIL_FAILED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={"job": "commitment_link", "amount": commitment.committed_amount,
                     "commitment_id": commitment.id, "rail": mode, "error": reason,
                     "reason": "payment rail refused or failed; nothing issued"},
        )
        counters.inc("rail.failed")
        log.warning("rail.failed", commitment=commitment.id, rail=mode, error=type(error).__name__)
        return commitment.model_copy(update={"instrument_failed": True, "instrument_failure": reason,
                                             "instrument_mode": None})
    updated = commitment.model_copy(update={
        "instrument_type": InstrumentType.PAYMENT_LINK, "instrument_id": link.get("id"),
        "payment_url": link.get("short_url"), "instrument_mode": InstrumentMode(mode) if mode else None,
        "instrument_failed": False, "instrument_failure": "",
    })
    store.append_event(
        at=now, actor=actor, kind=EventKind.PAYMENT_INSTRUMENT_CREATED,
        invoice_id=invoice.id, debtor_id=debtor.id,
        payload={"commitment_id": commitment.id, "instrument_type": updated.instrument_type,
                 "instrument_id": updated.instrument_id, "payment_url": updated.payment_url,
                 "instrument_mode": mode, "amount": commitment.committed_amount,
                 "expire_by": commitment.due_at.isoformat(),
                 "notes": link.get("notes"), "reference_id": link.get("reference_id")},
    )
    counters.inc("commitment.instrument_created")
    log.info("instrument.issued", commitment=commitment.id, rail=mode, instrument=updated.instrument_id)
    return updated
