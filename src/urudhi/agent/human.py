"""Human-in-the-loop actions on escalated and disputed invoices.

A person can do a small, bounded set of things — acknowledge, note, release
back to automation, close — and every one of them goes through the same
domain transitions and audit chain as the agent's own actions. Nothing here
mutates state outside :mod:`urudhi.ledger.transitions`.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field

from urudhi.audit.log import Actor, EventKind
from urudhi.ledger.models import InvoiceState
from urudhi.ledger.transitions import (
    HUMAN_OWNED_STATES,
    InvalidTransition,
    human_close,
    human_release,
    withdraw_concession,
)
from urudhi.observability import counters
from urudhi.store import Store


class HumanAction(enum.StrEnum):
    ACKNOWLEDGE = "acknowledge"    # "I have this one" — no state change
    NOTE = "note"                  # resolution notes, no state change
    RELEASE = "release"            # dispute resolved / plan agreed: back to automation
    CLOSE = "close"                # written off, void, settled off-rails: terminal


class HumanRequest(BaseModel):
    action: HumanAction
    operator: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=2000)


def apply_human_action(store: Store, invoice_id: str, request: HumanRequest,
                       now: datetime) -> dict:
    """Validate and apply one human action; returns the audit payload."""
    invoice = store.get_invoice(invoice_id)
    from_state = invoice.state
    if request.action in (HumanAction.ACKNOWLEDGE, HumanAction.NOTE):
        if invoice.state not in HUMAN_OWNED_STATES and request.action is HumanAction.ACKNOWLEDGE:
            raise InvalidTransition(
                f"only escalated or disputed invoices can be acknowledged; "
                f"{invoice.id} is {invoice.state}"
            )
        updated = invoice
    elif request.action is HumanAction.RELEASE:
        if not request.notes.strip():
            raise InvalidTransition("releasing to automation needs a note saying why")
        updated = human_release(invoice, now)
    elif request.action is HumanAction.CLOSE:
        if not request.notes.strip():
            raise InvalidTransition("closing an invoice needs a note saying why")
        updated = human_close(invoice)
    else:  # pragma: no cover - enum is exhaustive
        raise InvalidTransition(f"unknown action {request.action}")

    with store.transaction():
        if updated is not invoice:
            store.put_invoice(updated)
        if updated.state is InvoiceState.CLOSED:
            live = store.live_concession_for(invoice.id)
            if live is not None:
                store.put_concession(withdraw_concession(live, now))
        payload = {
            "action": request.action.value, "operator": request.operator,
            "notes": request.notes, "from_state": from_state.value,
            "to_state": updated.state.value,
        }
        store.append_event(
            at=now, actor=Actor.HUMAN, kind=EventKind.HUMAN_ACTION,
            invoice_id=invoice.id, debtor_id=invoice.debtor_id, payload=payload,
        )
    counters.inc(f"human.{request.action.value}")
    return payload


def escalation_queue(store: Store) -> list[dict]:
    """Invoices a person owns right now, with the reason and what's happened since."""
    queue = []
    for invoice in store.invoices_in_state(InvoiceState.ESCALATED.value,
                                           InvoiceState.DISPUTED.value):
        events = store.events_for(invoice.id)
        reason_event = next(
            (e for e in reversed(events)
             if e.kind in (EventKind.ESCALATED, EventKind.DISPUTE_RECORDED)), None
        )
        human = [e for e in events if e.kind is EventKind.HUMAN_ACTION]
        queue.append({
            "invoice_id": invoice.id, "number": invoice.number, "state": invoice.state.value,
            "balance": invoice.balance, "amount_paid": invoice.amount_paid,
            "since": reason_event.at.isoformat() if reason_event else None,
            "reason": (reason_event.payload.get("reason") or reason_event.payload.get("summary")
                       if reason_event else None),
            "verbatim": reason_event.payload.get("verbatim") if reason_event else None,
            "acknowledged": any(e.payload.get("action") == "acknowledge" for e in human),
            "human_actions": [e.payload | {"at": e.at.isoformat()} for e in human],
        })
    queue.sort(key=lambda q: (-q["balance"], q["invoice_id"]))
    return queue
