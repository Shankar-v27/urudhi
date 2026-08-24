"""The recovery loop: one bounded, audited negotiation engine.

Flow per invoice per day:

    scoring picks who to chase -> contact gate -> brain drafts -> send
    -> debtor replies -> brain interprets -> the *typed* interpretation is
    routed here: promises hit the ledger, disputes and stop-contact stand the
    agent down, concession requests go back through the offer gate.

Two invariants hold everywhere in this module:

* nothing is sent and no concession is made without an ``allowed`` gate
  decision, and every decision — allowed or blocked — is audited;
* the loop never touches payment state. Money moves only in
  :mod:`urudhi.rails.webhooks`, so recovery cannot be self-reported.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel

from urudhi.agent.brain import Brain, Intent, MessageContext, ReplyInterpretation
from urudhi.agent.policy import (
    ContactFacts,
    GateDecision,
    Offer,
    OfferType,
    PolicyConfig,
    check_contact,
    check_offer,
    should_escalate,
)
from urudhi.audit.log import Actor, EventKind
from urudhi.ledger.models import Channel, Debtor, Invoice, PromiseState, PromiseToPay
from urudhi.ledger.money import format_inr
from urudhi.ledger.transitions import (
    ACTIVE_STATES,
    escalate,
    expire_promise,
    record_dispute,
    record_promise,
    stop_contact,
)
from urudhi.store import Store


class Outbox(Protocol):
    """Where outgoing messages go (simulated channel, or a real gateway)."""

    def send(self, debtor: Debtor, channel: Channel, text: str) -> None: ...


class Action(enum.StrEnum):
    BLOCKED = "blocked"
    MESSAGE_SENT = "message_sent"
    PROMISE_RECORDED = "promise_recorded"
    COUNTER_OFFERED = "counter_offered"
    DISPUTE_STOOD_DOWN = "dispute_stood_down"
    STOP_CONTACT_HONORED = "stop_contact_honored"
    ESCALATED = "escalated"
    NOTED = "noted"


class TurnResult(BaseModel):
    invoice_id: str
    action: Action
    detail: str = ""
    gate: GateDecision | None = None


class RecoveryAgent:
    def __init__(
        self,
        store: Store,
        brain: Brain,
        outbox: Outbox,
        config: PolicyConfig | None = None,
    ) -> None:
        self._store = store
        self._brain = brain
        self._outbox = outbox
        self._config = config or PolicyConfig()

    # -- outbound ----------------------------------------------------------

    def chase(
        self,
        invoice_id: str,
        now: datetime,
        offer: Offer | None = None,
        payment_url: str | None = None,
    ) -> TurnResult:
        """One outbound attempt: gate, draft, send. Offer only if gated through."""
        invoice = self._store.get_invoice(invoice_id)
        debtor = self._store.get_debtor(invoice.debtor_id)
        facts = self._contact_facts(invoice, debtor.preferred_channel, now)

        decision = check_contact(invoice, facts, self._config)
        self._audit_gate(decision, invoice, now)
        if not decision.allowed:
            escalation = should_escalate(facts, self._config)
            if escalation.allowed:
                return self._escalate(invoice, escalation.reason, now)
            return TurnResult(
                invoice_id=invoice_id, action=Action.BLOCKED,
                detail=decision.reason, gate=decision,
            )

        offer_text = None
        if offer is not None:
            offer_decision = check_offer(invoice, offer, now.date(), self._config)
            self._audit_gate(offer_decision, invoice, now)
            if not offer_decision.allowed:
                # A blocked concession never silently degrades the message —
                # the attempt proceeds with no offer at all.
                offer = None
            else:
                offer_text = self._offer_text(invoice, offer)
                self._store.append_event(
                    at=now, actor=Actor.AGENT, kind=EventKind.OFFER_MADE,
                    invoice_id=invoice.id, debtor_id=debtor.id,
                    payload=offer.model_dump(mode="json") | {"text": offer_text},
                )

        context = self._context(invoice, debtor, now, offer_text, payment_url)
        text = self._brain.draft_message(context)
        self._outbox.send(debtor, debtor.preferred_channel, text)
        self._store.append_event(
            at=now, actor=Actor.AGENT, kind=EventKind.MESSAGE_SENT,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={"channel": debtor.preferred_channel, "text": text},
        )
        return TurnResult(
            invoice_id=invoice_id, action=Action.MESSAGE_SENT, gate=decision,
            detail=f"sent on {debtor.preferred_channel}",
        )

    # -- inbound -----------------------------------------------------------

    def handle_reply(self, invoice_id: str, reply: str, now: datetime) -> TurnResult:
        invoice = self._store.get_invoice(invoice_id)
        debtor = self._store.get_debtor(invoice.debtor_id)
        context = self._context(invoice, debtor, now, None, None)
        interpretation = self._brain.interpret_reply(context, reply)
        self._store.append_event(
            at=now, actor=Actor.AGENT, kind=EventKind.MESSAGE_RECEIVED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={
                "verbatim": interpretation.verbatim,
                "intent": interpretation.intent,
                "confidence": interpretation.confidence,
                "summary": interpretation.summary,
            },
        )

        match interpretation.intent:
            case Intent.STOP_CONTACT:
                updated = stop_contact(invoice)
                self._store.put_invoice(updated)
                self._store.append_event(
                    at=now, actor=Actor.POLICY, kind=EventKind.STOP_CONTACT_HONORED,
                    invoice_id=invoice.id, debtor_id=debtor.id,
                    payload={"verbatim": interpretation.verbatim},
                )
                return TurnResult(invoice_id=invoice_id, action=Action.STOP_CONTACT_HONORED)

            case Intent.DISPUTE:
                updated = record_dispute(invoice)
                self._store.put_invoice(updated)
                self._store.append_event(
                    at=now, actor=Actor.POLICY, kind=EventKind.DISPUTE_RECORDED,
                    invoice_id=invoice.id, debtor_id=debtor.id,
                    payload={"verbatim": interpretation.verbatim,
                             "summary": interpretation.summary},
                )
                return TurnResult(
                    invoice_id=invoice_id, action=Action.DISPUTE_STOOD_DOWN,
                    detail="invoice handed to human review",
                )

            case Intent.PROMISE:
                return self._record_promise(invoice, debtor, interpretation, now)

            case Intent.REQUEST_TERMS:
                return TurnResult(
                    invoice_id=invoice_id, action=Action.COUNTER_OFFERED,
                    detail="concession requested; next chase may carry a gated offer",
                )

            case _:
                return TurnResult(
                    invoice_id=invoice_id, action=Action.NOTED,
                    detail=f"{interpretation.intent}: {interpretation.summary}",
                )

    # -- daily housekeeping ------------------------------------------------

    def daily_tick(self, today: date, now: datetime) -> list[TurnResult]:
        """Expire lapsed promises, then escalate invoices that earned a human."""
        results: list[TurnResult] = []
        for invoice in self._store.all_invoices():
            promise = self._store.open_promise_for(invoice.id)
            if promise is None:
                continue
            paid = self._store.paid_between(
                invoice.id, promise.made_at, promise.promised_on.isoformat()
            )
            expired = expire_promise(invoice, promise, today, paid, now)
            if expired is None:
                continue
            updated_invoice, resolved = expired
            self._store.put_invoice(updated_invoice)
            self._store.put_promise(resolved)
            self._store.append_event(
                at=now, actor=Actor.SYSTEM, kind=EventKind.PROMISE_RESOLVED,
                invoice_id=invoice.id, debtor_id=invoice.debtor_id,
                payload={"promise_id": resolved.id, "outcome": resolved.state,
                         "paid_in_window": paid},
            )
            facts = self._contact_facts(updated_invoice, Channel.SYSTEM, now)
            escalation = should_escalate(facts, self._config)
            self._audit_gate(escalation, updated_invoice, now)
            if escalation.allowed:
                results.append(self._escalate(updated_invoice, escalation.reason, now))
            else:
                results.append(TurnResult(
                    invoice_id=invoice.id, action=Action.NOTED,
                    detail=f"promise {resolved.state}; invoice back in chase pool",
                ))
        return results

    # -- internals ---------------------------------------------------------

    def _record_promise(
        self, invoice: Invoice, debtor: Debtor,
        interpretation: ReplyInterpretation, now: datetime,
    ) -> TurnResult:
        promised_on = interpretation.promised_on or (now.date() + timedelta(days=7))
        horizon = (promised_on - now.date()).days
        if horizon > self._config.max_promise_horizon_days:
            decision = GateDecision.block(
                "promise_horizon",
                f"promised date {horizon} days out exceeds "
                f"{self._config.max_promise_horizon_days}-day horizon",
            )
            self._audit_gate(decision, invoice, now)
            return TurnResult(
                invoice_id=invoice.id, action=Action.COUNTER_OFFERED,
                detail="promise too far out; agent must counter with a nearer date",
                gate=decision,
            )

        amount = min(interpretation.promised_amount or invoice.balance, invoice.balance)
        existing = self._store.open_promise_for(invoice.id)
        promise = PromiseToPay(
            id=f"ptp_{invoice.id}_{len(self._store.promises_for(invoice.id)) + 1}",
            invoice_id=invoice.id, debtor_id=debtor.id, amount=amount,
            promised_on=promised_on, made_at=now,
            channel=debtor.preferred_channel,
            verbatim=interpretation.verbatim,
            confidence=interpretation.confidence,
        )
        updated_invoice, promise, superseded = record_promise(
            invoice, promise, open_promise=existing
        )
        self._store.put_invoice(updated_invoice)
        self._store.put_promise(promise)
        if superseded is not None:
            self._store.put_promise(superseded)
        self._store.append_event(
            at=now, actor=Actor.AGENT, kind=EventKind.PROMISE_RECORDED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={
                "promise_id": promise.id, "amount": promise.amount,
                "promised_on": promise.promised_on.isoformat(),
                "confidence": promise.confidence, "verbatim": promise.verbatim,
                "superseded": superseded.id if superseded else None,
            },
        )
        return TurnResult(
            invoice_id=invoice.id, action=Action.PROMISE_RECORDED,
            detail=f"{format_inr(promise.amount)} by {promise.promised_on} "
                   f"(confidence {promise.confidence:.2f})",
        )

    def _escalate(self, invoice: Invoice, reason: str, now: datetime) -> TurnResult:
        updated = escalate(invoice)
        self._store.put_invoice(updated)
        self._store.append_event(
            at=now, actor=Actor.POLICY, kind=EventKind.ESCALATED,
            invoice_id=invoice.id, debtor_id=invoice.debtor_id,
            payload={"reason": reason},
        )
        return TurnResult(invoice_id=invoice.id, action=Action.ESCALATED, detail=reason)

    def _contact_facts(self, invoice: Invoice, channel: Channel, now: datetime) -> ContactFacts:
        sent = [
            e for e in self._store.audit_events()
            if e.kind is EventKind.MESSAGE_SENT and e.invoice_id == invoice.id
        ]
        broken = sum(
            1 for p in self._store.promises_for(invoice.id)
            if p.state is PromiseState.BROKEN
        )
        return ContactFacts(
            now=now, channel=channel,
            attempts_total=len(sent),
            attempts_today=sum(1 for e in sent if e.at.date() == now.date()),
            broken_promises=broken,
        )

    def _context(
        self, invoice: Invoice, debtor: Debtor, now: datetime,
        offer_text: str | None, payment_url: str | None,
    ) -> MessageContext:
        return MessageContext(
            debtor_name=debtor.name, contact_name=debtor.contact_name,
            invoice_number=invoice.number, balance=invoice.balance,
            days_overdue=invoice.days_overdue(now.date()), today=now.date(),
            language=debtor.language, approved_offer_text=offer_text,
            payment_url=payment_url,
        )

    def _offer_text(self, invoice: Invoice, offer: Offer) -> str:
        if offer.type is OfferType.DISCOUNT:
            discounted = invoice.balance * (10_000 - offer.discount_bps) // 10_000
            return (
                f"Clear the balance by {offer.pay_by:%d %b} and it settles at "
                f"{format_inr(discounted)} — an early-payment discount of "
                f"{offer.discount_bps / 100:.2f}%."
            )
        if offer.type is OfferType.INSTALLMENTS:
            per = invoice.balance // offer.installment_count
            return (
                f"We can split the balance into {offer.installment_count} instalments "
                f"of {format_inr(per)}, first due {offer.pay_by:%d %b}."
            )
        return f"The full balance of {format_inr(invoice.balance)} is due by {offer.pay_by:%d %b}."

    def _audit_gate(self, decision: GateDecision, invoice: Invoice, now: datetime) -> None:
        kind = EventKind.GATE_ALLOWED if decision.allowed else EventKind.GATE_BLOCKED
        self._store.append_event(
            at=now, actor=Actor.POLICY, kind=kind,
            invoice_id=invoice.id, debtor_id=invoice.debtor_id,
            payload={"gate": decision.gate, "reason": decision.reason},
        )


def chaseable(store: Store) -> list[Invoice]:
    """Invoices the agent may act on today."""
    return [i for i in store.all_invoices() if i.state in ACTIVE_STATES]
