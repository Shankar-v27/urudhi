"""Ledger state transitions.

Pure functions over the domain models: each takes current state, validates the
transition, and returns updated copies. Nothing here mutates in place and
nothing here does I/O — persistence and webhook plumbing live elsewhere. This
is the layer where every rule the pitch makes ("a broken promise is recorded,
not forgiven", "a discount is waived only when the settlement lands on the
rails") is enforced in code, so the rules are testable in isolation.
"""

from __future__ import annotations

from datetime import date, datetime

from urudhi.ledger.models import (
    Concession,
    ConcessionState,
    ConcessionType,
    Installment,
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
    {
        InvoiceState.PAID, InvoiceState.DISPUTED, InvoiceState.ESCALATED,
        InvoiceState.STOP_CONTACT, InvoiceState.CLOSED,
    }
)

# States a human may hand back to automation.
HUMAN_OWNED_STATES = frozenset({InvoiceState.DISPUTED, InvoiceState.ESCALATED})


class InvalidTransition(Exception):
    """Raised when an event is applied to a state that does not allow it."""


def _require_active(invoice: Invoice, event: str) -> None:
    if invoice.state not in ACTIVE_STATES:
        raise InvalidTransition(
            f"cannot {event} on invoice {invoice.id} in state {invoice.state}"
        )


def _settled_state(invoice: Invoice, amount_paid: int, amount_waived: int) -> InvoiceState:
    if amount_paid + amount_waived >= invoice.amount:
        return InvoiceState.PAID
    if invoice.state in HANDS_OFF_STATES:
        return invoice.state  # a human owns it; partial money changes nothing
    return InvoiceState.PARTIALLY_PAID


# -- promises ---------------------------------------------------------------

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

    if invoice.state in HANDS_OFF_STATES:
        next_state = invoice.state
    elif invoice.amount_paid > 0:
        next_state = InvoiceState.PARTIALLY_PAID
    else:
        next_state = InvoiceState.OUTSTANDING
    updated_invoice = invoice.model_copy(update={"state": next_state})
    return updated_invoice, updated_promise


# -- concessions ------------------------------------------------------------

def offer_concession(invoice: Invoice, concession: Concession) -> Concession:
    """Validate a policy-approved concession against the ledger before recording it."""
    _require_active(invoice, "offer a concession")
    if concession.invoice_id != invoice.id:
        raise InvalidTransition("concession references a different invoice")
    if concession.balance_at_offer != invoice.balance:
        raise InvalidTransition(
            f"concession was priced on balance {concession.balance_at_offer}, "
            f"invoice balance is {invoice.balance}"
        )
    if concession.type is ConcessionType.DISCOUNT:
        if not (0 < concession.settlement_amount < invoice.balance):
            raise InvalidTransition("discount settlement must be positive and below balance")
    else:
        if sum(i.amount for i in concession.installments) != invoice.balance:
            raise InvalidTransition("installment schedule must sum to the balance")
        if concession.settlement_amount != invoice.balance:
            raise InvalidTransition("installment settlement must equal the balance")
        if [i.due_on for i in concession.installments] != sorted(
            i.due_on for i in concession.installments
        ):
            raise InvalidTransition("installments must be in due-date order")
    return concession.model_copy(update={"state": ConcessionState.OFFERED})


def accept_concession(invoice: Invoice, concession: Concession, now: datetime) -> Concession:
    """The debtor agreed to the terms (as interpreted from their reply)."""
    _require_active(invoice, "accept a concession")
    if concession.state is not ConcessionState.OFFERED:
        raise InvalidTransition(f"concession {concession.id} is {concession.state}, not offered")
    if now.date() > concession.pay_by:
        raise InvalidTransition("cannot accept a concession after its pay-by date")
    return concession.model_copy(
        update={"state": ConcessionState.ACCEPTED, "accepted_at": now}
    )


def withdraw_concession(concession: Concession, now: datetime) -> Concession:
    if not concession.live:
        return concession
    return concession.model_copy(
        update={"state": ConcessionState.WITHDRAWN, "resolved_at": now}
    )


def installment_statuses(
    concession: Concession, paid_since_offer: int, today: date
) -> list[tuple[Installment, str]]:
    """Per-installment status: ``kept`` / ``partial`` / ``missed`` / ``pending``.

    Money is allocated to installments in schedule order — the rails don't
    say which installment a transfer was for, so the earliest open one is.
    """
    statuses: list[tuple[Installment, str]] = []
    remaining = paid_since_offer
    for installment in concession.installments:
        covered = min(remaining, installment.amount)
        remaining -= covered
        if covered >= installment.amount:
            status = "kept"
        elif today <= installment.due_on:
            status = "pending"
        elif covered > 0:
            status = "partial"
        else:
            status = "missed"
        statuses.append((installment, status))
    return statuses


def expire_concession(
    invoice: Invoice,
    concession: Concession,
    today: date,
    paid_since_offer: int,
    now: datetime,
) -> Concession | None:
    """Rule on a live concession the calendar has caught up with.

    A discount whose pay-by has passed unsettled EXPIRES — nothing is waived,
    the full balance stands. An installment plan with a due-and-unmet
    installment is BROKEN. Returns the updated concession, or ``None`` if
    there is nothing to rule on yet.
    """
    if not concession.live:
        return None
    if concession.type is ConcessionType.DISCOUNT:
        if today <= concession.pay_by:
            return None
        return concession.model_copy(
            update={"state": ConcessionState.EXPIRED, "resolved_at": now}
        )
    for _installment, status in installment_statuses(concession, paid_since_offer, today):
        if status in ("missed", "partial"):
            return concession.model_copy(
                update={"state": ConcessionState.BROKEN, "resolved_at": now}
            )
    return None


# -- payments ---------------------------------------------------------------

def record_payment(
    invoice: Invoice,
    payment: Payment,
    open_promise: PromiseToPay | None = None,
    paid_against_promise: int = 0,
    concession: Concession | None = None,
    paid_since_offer: int = 0,
) -> tuple[Invoice, PromiseToPay | None, Concession | None]:
    """Apply an observed payment; resolve the open promise and live concession.

    ``paid_against_promise`` / ``paid_since_offer`` are the cumulative amounts
    (excluding this payment) observed since the promise / concession was made.

    A promise is KEPT only when the committed amount has arrived on or before
    the committed date. A DISCOUNT concession SETTLES — and only then is the
    remainder waived — when the settlement amount has arrived by its pay-by;
    paying the discounted amount late clears nothing extra. Paying the full
    balance always settles regardless of any offer, with nothing waived.

    Unlike every other event, payments are accepted in *any* state except
    PAID: money observed on the rails is a fact to record, even for invoices
    a human owns. A partial payment never pulls a hands-off invoice back into
    the chase pool — only clearing the balance changes its state, to PAID.
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
    amount_waived = invoice.amount_waived
    resolved_concession = None
    paid_on = payment.observed_at.date()

    if concession is not None and concession.live:
        toward = paid_since_offer + payment.amount
        cleared_in_full = amount_paid + amount_waived >= invoice.amount
        if concession.type is ConcessionType.DISCOUNT:
            in_window = paid_on <= concession.pay_by
            if in_window and toward >= concession.settlement_amount:
                # Settlement reached under the offer's terms: waive the rest now.
                amount_waived += max(0, invoice.amount - amount_paid - amount_waived)
                resolved_concession = concession.model_copy(
                    update={"state": ConcessionState.SETTLED, "resolved_at": payment.observed_at}
                )
            elif cleared_in_full:
                resolved_concession = concession.model_copy(
                    update={"state": ConcessionState.SETTLED, "resolved_at": payment.observed_at}
                )
        elif cleared_in_full:
            resolved_concession = concession.model_copy(
                update={"state": ConcessionState.SETTLED, "resolved_at": payment.observed_at}
            )

    new_state = _settled_state(invoice, amount_paid, amount_waived)

    resolved_promise = None
    if open_promise is not None and open_promise.state is PromiseState.OPEN:
        on_time = paid_on <= open_promise.promised_on
        covered = paid_against_promise + payment.amount >= open_promise.amount
        if on_time and (covered or new_state is InvoiceState.PAID):
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
        update={"amount_paid": amount_paid, "amount_waived": amount_waived, "state": new_state}
    )
    return updated_invoice, resolved_promise, resolved_concession


# -- stand-downs and human actions -------------------------------------------

def record_dispute(invoice: Invoice) -> Invoice:
    """Debtor contests the invoice: the agent stands down, a human takes over."""
    _require_active(invoice, "record a dispute")
    return invoice.model_copy(update={"state": InvoiceState.DISPUTED})


def stop_contact(invoice: Invoice) -> Invoice:
    """Debtor asked us to stop. Honored immediately and terminally, from any state."""
    if invoice.state in (InvoiceState.STOP_CONTACT, InvoiceState.PAID, InvoiceState.CLOSED):
        return invoice
    return invoice.model_copy(update={"state": InvoiceState.STOP_CONTACT})


def escalate(invoice: Invoice) -> Invoice:
    """Hand the invoice to a human (attempt limits, repeated broken promises)."""
    _require_active(invoice, "escalate")
    return invoice.model_copy(update={"state": InvoiceState.ESCALATED})


def human_release(invoice: Invoice, now: datetime) -> Invoice:
    """A human hands a DISPUTED / ESCALATED invoice back to automated recovery.

    The release timestamp lets policy count attempts and broken promises from
    a clean slate — otherwise the invoice would re-escalate on the next tick.
    """
    if invoice.state not in HUMAN_OWNED_STATES:
        raise InvalidTransition(
            f"only a disputed or escalated invoice can be released; {invoice.id} is {invoice.state}"
        )
    next_state = (
        InvoiceState.PARTIALLY_PAID if invoice.amount_paid > 0 else InvoiceState.OUTSTANDING
    )
    return invoice.model_copy(update={"state": next_state, "human_released_at": now})


def human_close(invoice: Invoice) -> Invoice:
    """A human closes the invoice (written off, settled outside the rails, void).

    Nothing is waived on the ledger — ``amount_paid`` stays what the rails saw
    and the unpaid remainder is simply no longer chased.
    """
    if invoice.state is InvoiceState.PAID:
        raise InvalidTransition(f"invoice {invoice.id} is paid; nothing to close")
    if invoice.state is InvoiceState.CLOSED:
        return invoice
    return invoice.model_copy(update={"state": InvoiceState.CLOSED})
