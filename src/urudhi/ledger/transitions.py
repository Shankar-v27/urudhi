"""Ledger state transitions.

Pure functions over the domain models: each takes current state, validates the
transition, and returns updated copies. Nothing here mutates in place and
nothing here does I/O — persistence and webhook plumbing live elsewhere. This
is the layer where every rule the pitch makes ("a broken promise is recorded,
not forgiven") is enforced in code, so the rules are testable in isolation.
"""

from __future__ import annotations

from datetime import date, datetime

from urudhi.ledger.models import (
    Invoice,
    InvoiceState,
    Payment,
    PromiseState,
    PromiseToPay,
)

# States in which the agent may still interact with an invoice.
ACTIVE_STATES = frozenset(
    {InvoiceState.OUTSTANDING, InvoiceState.PROMISED, InvoiceState.PARTIALLY_PAID}
)

# Terminal or human-owned states: the agent must not act further.
HANDS_OFF_STATES = frozenset(
    {InvoiceState.PAID, InvoiceState.DISPUTED, InvoiceState.ESCALATED, InvoiceState.STOP_CONTACT}
)


class InvalidTransition(Exception):
    """Raised when an event is applied to a state that does not allow it."""


def _require_active(invoice: Invoice, event: str) -> None:
    if invoice.state not in ACTIVE_STATES:
        raise InvalidTransition(
            f"cannot {event} on invoice {invoice.id} in state {invoice.state}"
        )


def record_promise(
    invoice: Invoice,
    promise: PromiseToPay,
    open_promise: PromiseToPay | None = None,
) -> tuple[Invoice, PromiseToPay, PromiseToPay | None]:
    """Attach a new promise-to-pay; any existing open promise is superseded.

    Returns ``(invoice, promise, superseded_promise_or_None)``.
    """
    _require_active(invoice, "record a promise")
    if promise.invoice_id != invoice.id:
        raise InvalidTransition(
            f"promise {promise.id} is for invoice {promise.invoice_id}, not {invoice.id}"
        )
    if promise.amount <= 0 or promise.amount > invoice.balance:
        raise InvalidTransition(
            f"promise amount {promise.amount} outside (0, balance={invoice.balance}]"
        )

    superseded = None
    if open_promise is not None and open_promise.state is PromiseState.OPEN:
        superseded = open_promise.model_copy(
            update={"state": PromiseState.SUPERSEDED, "resolved_at": promise.made_at}
        )
    updated_invoice = invoice.model_copy(update={"state": InvoiceState.PROMISED})
    return updated_invoice, promise, superseded


def record_payment(
    invoice: Invoice,
    payment: Payment,
    open_promise: PromiseToPay | None = None,
    paid_against_promise: int = 0,
) -> tuple[Invoice, PromiseToPay | None]:
    """Apply an observed payment; resolve the open promise if it is now kept.

    ``paid_against_promise`` is the cumulative amount (excluding this payment)
    observed since the open promise was made. A promise is KEPT only when the
    committed amount has arrived on or before the committed date — partial
    money keeps it OPEN until :func:`expire_promise` rules on it.

    Unlike every other event, payments are accepted in *any* state except
    PAID: money observed on the rails is a fact to record, even for invoices
    a human owns (escalated, disputed) or where contact has stopped. A
    partial payment never pulls a hands-off invoice back into the chase
    pool — only clearing the balance changes its state, to PAID.
    """
    if invoice.state is InvoiceState.PAID:
        raise InvalidTransition(
            f"invoice {invoice.id} is already settled; refusing further payments"
        )
    if payment.invoice_id != invoice.id:
        raise InvalidTransition(
            f"payment {payment.id} is for invoice {payment.invoice_id}, not {invoice.id}"
        )
    if payment.amount <= 0:
        raise InvalidTransition(f"payment amount must be positive, got {payment.amount}")
    if payment.amount > invoice.balance:
        raise InvalidTransition(
            f"payment {payment.amount} exceeds balance {invoice.balance} "
            f"on invoice {invoice.id}; refusing to overpay silently"
        )

    amount_paid = invoice.amount_paid + payment.amount
    if amount_paid == invoice.amount:
        new_state = InvoiceState.PAID
    elif invoice.state in HANDS_OFF_STATES:
        new_state = invoice.state  # a human owns it; partial money changes nothing
    else:
        new_state = InvoiceState.PARTIALLY_PAID

    resolved_promise = None
    if open_promise is not None and open_promise.state is PromiseState.OPEN:
        on_time = payment.observed_at.date() <= open_promise.promised_on
        covered = paid_against_promise + payment.amount >= open_promise.amount
        if on_time and covered:
            resolved_promise = open_promise.model_copy(
                update={"state": PromiseState.KEPT, "resolved_at": payment.observed_at}
            )
        elif new_state is InvoiceState.PAID:
            # Invoice cleared late or beyond the promise window: promise wasn't
            # kept as made, but there is nothing left to chase.
            resolved_promise = open_promise.model_copy(
                update={"state": PromiseState.PARTIALLY_KEPT, "resolved_at": payment.observed_at}
            )

    updated_invoice = invoice.model_copy(
        update={"amount_paid": amount_paid, "state": new_state}
    )
    return updated_invoice, resolved_promise


def expire_promise(
    invoice: Invoice,
    promise: PromiseToPay,
    today: date,
    paid_against_promise: int,
    now: datetime,
) -> tuple[Invoice, PromiseToPay] | None:
    """Rule on an OPEN promise whose committed date has passed.

    Returns updated ``(invoice, promise)`` or ``None`` if not yet expirable.
    Money received during the window makes it PARTIALLY_KEPT; none makes it
    BROKEN. Either way the invoice returns to the chaseable pool.
    """
    if promise.state is not PromiseState.OPEN or today <= promise.promised_on:
        return None

    outcome = PromiseState.PARTIALLY_KEPT if paid_against_promise > 0 else PromiseState.BROKEN
    updated_promise = promise.model_copy(update={"state": outcome, "resolved_at": now})

    next_state = (
        InvoiceState.PARTIALLY_PAID if invoice.amount_paid > 0 else InvoiceState.OUTSTANDING
    )
    updated_invoice = invoice.model_copy(update={"state": next_state})
    return updated_invoice, updated_promise


def record_dispute(invoice: Invoice) -> Invoice:
    """Debtor contests the invoice: the agent stands down, a human takes over."""
    _require_active(invoice, "record a dispute")
    return invoice.model_copy(update={"state": InvoiceState.DISPUTED})


def stop_contact(invoice: Invoice) -> Invoice:
    """Debtor asked us to stop. Honored immediately and terminally."""
    if invoice.state is InvoiceState.STOP_CONTACT:
        return invoice
    return invoice.model_copy(update={"state": InvoiceState.STOP_CONTACT})


def escalate(invoice: Invoice) -> Invoice:
    """Hand the invoice to a human (attempt limits, repeated broken promises)."""
    _require_active(invoice, "escalate")
    return invoice.model_copy(update={"state": InvoiceState.ESCALATED})
