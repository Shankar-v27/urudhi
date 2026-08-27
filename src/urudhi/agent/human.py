"""Human-in-the-loop actions on escalated and disputed invoices.

A person can do a small, bounded set of things — acknowledge, note, approve a
new arrangement, release back to automation, close — and every one of them
goes through the same domain transitions, policy gate and audit chain as the
agent's own actions. Nothing here mutates state outside
:mod:`urudhi.ledger.transitions`; an arrangement a human approves still has
to pass :func:`urudhi.agent.policy.check_commitment`.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from urudhi.audit.log import Actor, EventKind
from urudhi.ledger.commitments import profile_for
from urudhi.ledger.models import CommitmentSource, CommitmentState, InvoiceState
from urudhi.ledger.transitions import (
    HUMAN_OWNED_STATES,
    InvalidTransition,
    human_close,
    human_release,
    withdraw_concession,
)
from urudhi.observability import counters
from urudhi.store import Store

if TYPE_CHECKING:  # pragma: no cover
    from urudhi.agent.loop import RecoveryAgent


class HumanAction(enum.StrEnum):
    ACKNOWLEDGE = "acknowledge"    # "I have this one" — no state change
    NOTE = "note"                  # resolution notes, no state change
    ARRANGE = "arrange"            # approve a new arrangement: commitment + release
    RELEASE = "release"            # dispute resolved / plan agreed: back to automation
    CLOSE = "close"                # written off, void, settled off-rails: terminal


class HumanRequest(BaseModel):
    action: HumanAction
    operator: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=2000)
    amount: int | None = Field(default=None, gt=0)   # paise; ARRANGE only
    due_on: date | None = None                        # ARRANGE only


def apply_human_action(store: Store, invoice_id: str, request: HumanRequest,
                       now: datetime, agent: RecoveryAgent | None = None) -> dict:
    """Validate and apply one human action; returns the audit payload."""
    invoice = store.get_invoice(invoice_id)
    from_state = invoice.state
    commitment_id: str | None = None
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
    elif request.action is HumanAction.ARRANGE:
        if not request.notes.strip():
            raise InvalidTransition("approving an arrangement needs a note saying what was agreed")
        if request.amount is None or request.due_on is None:
            raise InvalidTransition("an arrangement needs an amount (paise) and a due date")
        if agent is None:
            raise InvalidTransition("no recovery agent configured to open the arrangement")
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
            for c in store.live_commitments_for(invoice.id):
                from urudhi.ledger.transitions import cancel_commitment

                store.put_commitment(cancel_commitment(c, now, "closed by a human"))
        payload = {
            "action": request.action.value, "operator": request.operator,
            "notes": request.notes, "from_state": from_state.value,
            "to_state": updated.state.value,
        }
        if request.action is HumanAction.ARRANGE:
            payload |= {"amount": request.amount, "due_on": request.due_on.isoformat()}
        store.append_event(
            at=now, actor=Actor.HUMAN, kind=EventKind.HUMAN_ACTION,
            invoice_id=invoice.id, debtor_id=invoice.debtor_id, payload=payload,
        )
    if request.action is HumanAction.ARRANGE:
        assert agent is not None and request.amount is not None and request.due_on is not None
        debtor = store.get_debtor(invoice.debtor_id)
        outcome = agent.open_commitment(
            updated, debtor, amount=request.amount, due_on=request.due_on, now=now,
            source=CommitmentSource.HUMAN, confidence=1.0,
            evidence=f"arrangement approved by {request.operator}: {request.notes.strip()}",
            actor=Actor.HUMAN,
        )
        if outcome.commitment is None:
            # Policy refused the human's arrangement; undo the release so the
            # invoice stays with the person and say why.
            store.put_invoice(invoice)
            raise InvalidTransition(f"arrangement refused by policy: {outcome.verdict.reason}")
        commitment_id = outcome.commitment.id
        payload["commitment_id"] = commitment_id
    counters.inc(f"human.{request.action.value}")
    return payload


def escalation_queue(store: Store) -> list[dict]:
    """Invoices a person owns right now, with the reason, the commitment record
    and what has happened since."""
    queue = []
    for invoice in store.invoices_in_state(InvoiceState.ESCALATED.value,
                                           InvoiceState.DISPUTED.value):
        events = store.events_for(invoice.id)
        reason_event = next(
            (e for e in reversed(events)
             if e.kind in (EventKind.ESCALATED, EventKind.DISPUTE_RECORDED)), None
        )
        human = [e for e in events if e.kind is EventKind.HUMAN_ACTION]
        commitments = store.commitments_for(invoice.id)
        profile = profile_for(store.commitments_for_debtor(invoice.debtor_id))
        last = next((c for c in reversed(commitments)
                     if c.state is not CommitmentState.SUPERSEDED), None)
        queue.append({
            "invoice_id": invoice.id, "number": invoice.number, "state": invoice.state.value,
            "balance": invoice.balance, "amount_paid": invoice.amount_paid,
            "since": reason_event.at.isoformat() if reason_event else None,
            "reason": (reason_event.payload.get("reason") or reason_event.payload.get("summary")
                       if reason_event else None),
            "verbatim": reason_event.payload.get("verbatim") if reason_event else None,
            "acknowledged": any(e.payload.get("action") == "acknowledge" for e in human),
            "human_actions": [e.payload | {"at": e.at.isoformat()} for e in human],
            "last_commitment": None if last is None else {
                "id": last.id, "committed_amount": last.committed_amount,
                "amount_received": last.amount_received, "due_on": last.due_on.isoformat(),
                "state": last.state.value, "evidence": last.evidence,
            },
            "commitments_missed": profile.missed,
            "commitments_fulfilled": profile.fulfilled,
            "credibility": profile.credibility,
            "recommended_action": "human review",
        })
    queue.sort(key=lambda q: (-q["balance"], q["invoice_id"]))
    return queue
