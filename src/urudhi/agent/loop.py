"""The recovery loop: one bounded, audited negotiation engine.

Flow per invoice per day::

    scoring picks who to chase
      -> contact gate (deterministic)
      -> brain proposes an intervention from structured facts
      -> policy decides: allow / modify / block (audited, with reasons)
      -> concession recorded, payment link created, message drafted
      -> outbound slot CLAIMED in the database, then sent, then audited
    debtor replies
      -> brain interprets into a typed intent
      -> a promise hits the ledger as *what was said*
      -> policy rules on it (check_commitment); if allowed, an executable
         PaymentCommitment is opened — exact amount, exact deadline, a
         Razorpay Payment Link tagged with the commitment id — and the
         debtor is told; if refused, the promise stays as evidence and the
         refusal is audited
      -> offers are accepted (becoming commitments), disputes and
         stop-contact stand the agent down and cancel live commitments,
         term requests get an immediate gated answer
    daily tick
      -> commitments past their deadline are MISSED, promises BROKEN,
         concessions EXPIRED / BROKEN; escalation where earned; pending
         confirmations and bounded near-deadline reminders go out

Three invariants hold everywhere in this module:

* nothing is sent and no concession or commitment is made without an
  ``allowed`` gate decision, and every decision — allowed or blocked — is
  audited;
* the loop never touches payment state. Money moves only in
  :mod:`urudhi.rails.webhooks`; a commitment is fulfilled only by rail
  events matched to it, so recovery cannot be self-reported;
* a brain failure defers; it never becomes permission.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from urudhi.agent.brain import (
    Brain,
    BrainUnavailable,
    Intent,
    MessageContext,
    ReplyInterpretation,
)
from urudhi.agent.intervention import (
    CONTACTING,
    DecisionContext,
    InterventionKind,
    PriorIntervention,
)
from urudhi.agent.policy import (
    CommitmentVerdict,
    ContactFacts,
    Decision,
    GateDecision,
    Offer,
    OfferType,
    PolicyConfig,
    check_commitment,
    check_contact,
    decide_intervention,
    should_escalate,
)
from urudhi.audit.log import Actor, EventKind
from urudhi.ledger.commitments import describe_commitment, profile_for
from urudhi.ledger.models import (
    Channel,
    CommitmentSource,
    Concession,
    ConcessionState,
    ConcessionType,
    Debtor,
    InstrumentType,
    Invoice,
    InvoiceState,
    PaymentCommitment,
    PromiseState,
    PromiseToPay,
)
from urudhi.ledger.money import format_inr
from urudhi.ledger.transitions import (
    ACTIVE_STATES,
    accept_concession,
    cancel_commitment,
    escalate,
    expire_commitment,
    expire_concession,
    expire_promise,
    offer_concession,
    open_commitment,
    record_dispute,
    record_promise,
    stop_contact,
    withdraw_concession,
)
from urudhi.observability import counters, get_logger
from urudhi.rails.razorpay_client import RailsClient
from urudhi.store import Store

log = get_logger("urudhi.loop")


class Outbox(Protocol):
    """Where outgoing messages go (simulated channel, or a real gateway)."""

    def send(self, debtor: Debtor, channel: Channel, text: str, *, subject: str,
             reference: str) -> str:
        """Deliver; return a provider message id. Raise on failure."""
        ...


class Action(enum.StrEnum):
    BLOCKED = "blocked"
    WAITED = "waited"                  # a commitment is running; nothing sent
    NO_ACTION = "no_action"
    MESSAGE_SENT = "message_sent"
    MESSAGE_FAILED = "message_failed"
    DEFERRED = "deferred"              # brain unavailable; try again next tick
    PROMISE_RECORDED = "promise_recorded"
    COMMITMENT_CREATED = "commitment_created"
    OFFER_ACCEPTED = "offer_accepted"
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
    decision: Decision | None = None
    intervention: InterventionKind | None = None
    commitment_id: str | None = None
    commitment_verdict: CommitmentVerdict | None = None


_RESPONDING_INTENTS = frozenset({Intent.REQUEST_TERMS, Intent.QUESTION})


class RecoveryAgent:
    def __init__(
        self,
        store: Store,
        brain: Brain,
        outbox: Outbox,
        config: PolicyConfig | None = None,
        rails: RailsClient | None = None,
    ) -> None:
        self._store = store
        self._brain = brain
        self._outbox = outbox
        self._config = config or PolicyConfig()
        self._rails = rails

    @property
    def config(self) -> PolicyConfig:
        return self._config

    @property
    def brain_name(self) -> str:
        return getattr(self._brain, "name", type(self._brain).__name__)

    # -- outbound ----------------------------------------------------------

    def chase(self, invoice_id: str, now: datetime) -> TurnResult:
        """One scheduled attempt: gate, propose, decide, execute."""
        return self._turn(invoice_id, now, responding_to=None)

    def _turn(self, invoice_id: str, now: datetime,
              responding_to: ReplyInterpretation | None) -> TurnResult:
        invoice = self._store.get_invoice(invoice_id)
        debtor = self._store.get_debtor(invoice.debtor_id)
        facts = self._contact_facts(invoice, debtor.preferred_channel, now,
                                    responding=responding_to is not None)

        # Deterministic pre-check: no brain call when contact is impossible.
        contact = check_contact(invoice, facts, self._config)
        self._audit_gate(contact, invoice, now)
        if not contact.allowed:
            escalation = should_escalate(facts, self._config)
            if escalation.allowed and invoice.state in ACTIVE_STATES:
                return self._escalate(invoice, escalation.reason, now)
            return TurnResult(invoice_id=invoice_id, action=Action.BLOCKED,
                              detail=contact.reason, gate=contact)

        context = self._decision_context(invoice, now, responding_to)
        running = (
            context.open_promise_on is not None
            or context.active_commitment is not None
            or (context.live_concession or "").startswith("installments")
        )
        if running:
            self._store.append_event(
                at=now, actor=Actor.POLICY, kind=EventKind.INTERVENTION_DECIDED,
                invoice_id=invoice.id, debtor_id=debtor.id,
                payload={"proposed": None, "final": InterventionKind.WAIT_FOR_PROMISE,
                         "modified": False, "reasons": ["a commitment is running"],
                         "gates": [], "context": context.model_dump(mode="json")},
            )
            return TurnResult(invoice_id=invoice_id, action=Action.WAITED,
                              intervention=InterventionKind.WAIT_FOR_PROMISE,
                              detail="a commitment is running; not chasing over it")

        try:
            proposal = self._brain.recommend_intervention(context)
        except BrainUnavailable as error:
            return self._defer(invoice, debtor, now, "recommend", str(error))
        self._store.append_event(
            at=now, actor=Actor.AGENT, kind=EventKind.INTERVENTION_PROPOSED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={"brain": self.brain_name, **proposal.model_dump(mode="json")},
        )
        decision = decide_intervention(invoice, context, proposal, facts, self._config)
        for gate in decision.gates:
            if gate.gate != "contact":  # already audited above
                self._audit_gate(gate, invoice, now)
        self._store.append_event(
            at=now, actor=Actor.POLICY, kind=EventKind.INTERVENTION_DECIDED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={
                "proposed": proposal.action, "final": decision.final,
                "modified": decision.modified, "reasons": decision.reasons,
                "gates": [g.model_dump() for g in decision.gates],
                "offer": decision.offer.model_dump(mode="json") if decision.offer else None,
                "rationale": proposal.rationale, "confidence": proposal.confidence,
                "context": context.model_dump(mode="json"),
            },
        )
        counters.inc(f"intervention.{decision.final.value}")
        if decision.modified:
            counters.inc("intervention.modified_by_policy")

        final = decision.final
        if final is InterventionKind.ESCALATE_HUMAN:
            return self._escalate(invoice, "; ".join(proposal.rationale) or "recommended", now)
        if final in (InterventionKind.NO_ACTION, InterventionKind.WAIT_FOR_PROMISE):
            return TurnResult(invoice_id=invoice_id, action=Action.NO_ACTION,
                              decision=decision, intervention=final,
                              detail="; ".join(decision.reasons) or "nothing to do today")
        return self._execute_contact(invoice, debtor, facts, decision, now, responding_to)

    def _execute_contact(
        self, invoice: Invoice, debtor: Debtor, facts: ContactFacts, decision: Decision,
        now: datetime, responding_to: ReplyInterpretation | None,
    ) -> TurnResult:
        final = decision.final
        today = now.date()
        concession: Concession | None = None
        offer_text: str | None = None
        payment_url: str | None = None

        if decision.offer is not None:
            concession = self._build_concession(invoice, debtor, decision.offer, today, now,
                                                "; ".join(decision.proposed.rationale))
            concession = offer_concession(invoice, concession)
            offer_text = self._offer_text(invoice, decision.offer, concession)

        if self._rails is not None and final in (
            InterventionKind.PAYMENT_LINK, InterventionKind.DISCOUNT_OFFER,
            InterventionKind.INSTALLMENT_OFFER,
        ):
            amount = invoice.balance
            if concession is not None:
                amount = (concession.installments[0].amount
                          if concession.type is ConcessionType.INSTALLMENTS
                          else concession.settlement_amount)
            try:
                link = self._rails.create_payment_link(
                    amount=amount, description=f"Invoice {invoice.number} — {debtor.name}",
                    invoice_id=invoice.id, customer_name=debtor.name,
                    customer_email=debtor.email, customer_contact=debtor.phone,
                    reference_id=f"{invoice.id}/{self._next_link_seq(invoice)}",
                )
            except Exception as error:  # the rail is external; its failure is a fact to audit
                return self._rail_failed(invoice, debtor, now, "payment_link", amount, error)
            payment_url = link.get("short_url")
            if concession is not None:
                concession = concession.model_copy(update={"payment_link_url": payment_url})

        context = self._message_context(invoice, debtor, now, offer_text, payment_url, final)
        try:
            text = self._brain.draft_message(context)
        except BrainUnavailable as error:
            return self._defer(invoice, debtor, now, "draft", str(error))

        extra = {"intervention": final.value, "responding": responding_to is not None}
        if concession is not None:
            self._store.put_concession(concession)
            self._store.append_event(
                at=now, actor=Actor.AGENT, kind=EventKind.OFFER_MADE,
                invoice_id=invoice.id, debtor_id=debtor.id,
                payload={
                    "concession_id": concession.id, "type": concession.type,
                    "discount_bps": concession.discount_bps,
                    "settlement_amount": concession.settlement_amount,
                    "installments": [i.model_dump(mode="json") for i in concession.installments],
                    "pay_by": concession.pay_by.isoformat(), "text": offer_text,
                    "payment_link_url": payment_url,
                },
            )
            counters.inc(f"offer.{concession.type.value}")
            extra["concession_id"] = concession.id
        return self._send(invoice, debtor, facts, text, final, now, decision=decision,
                          payment_url=payment_url, extra=extra)

    def _send(
        self, invoice: Invoice, debtor: Debtor, facts: ContactFacts, text: str,
        kind: InterventionKind, now: datetime, *, decision: Decision | None = None,
        payment_url: str | None = None, extra: dict | None = None,
    ) -> TurnResult:
        """Claim the attempt slot BEFORE sending — a crash after delivery can't resend."""
        local_day = self._config.local(now).date().isoformat()
        key = f"{invoice.id}:{local_day}:{facts.attempts_total + 1}"
        claimed = self._store.claim_outbound(
            key, invoice.id, local_day, now, debtor.preferred_channel.value,
            {"intervention": kind.value, **{k: v for k, v in (extra or {}).items()
                                            if k in ("responding", "commitment_id")}},
        )
        if not claimed:
            return TurnResult(invoice_id=invoice.id, action=Action.BLOCKED, decision=decision,
                              intervention=kind,
                              detail=f"attempt {key} already claimed; not sending twice")
        try:
            message_id = self._outbox.send(
                debtor, debtor.preferred_channel, text,
                subject=f"Invoice {invoice.number} — payment reminder [{invoice.number}]",
                reference=invoice.id,
            )
        except Exception as error:
            self._store.mark_outbound(key, "failed")
            self._store.append_event(
                at=now, actor=Actor.SYSTEM, kind=EventKind.MESSAGE_FAILED,
                invoice_id=invoice.id, debtor_id=debtor.id,
                payload={"channel": debtor.preferred_channel, "outbound_key": key,
                         "error": type(error).__name__, "intervention": kind},
            )
            counters.inc("message.failed")
            log.warning("message.failed", invoice=invoice.id, error=type(error).__name__)
            return TurnResult(invoice_id=invoice.id, action=Action.MESSAGE_FAILED,
                              decision=decision, intervention=kind, detail=str(error))
        self._store.mark_outbound(key, "sent")
        self._store.append_event(
            at=now, actor=Actor.AGENT, kind=EventKind.MESSAGE_SENT,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={
                "channel": debtor.preferred_channel, "text": text, "intervention": kind,
                "outbound_key": key, "message_id": message_id, "brain": self.brain_name,
                "payment_url": payment_url, **(extra or {}),
            },
        )
        counters.inc("message.sent")
        return TurnResult(
            invoice_id=invoice.id, action=Action.MESSAGE_SENT, decision=decision,
            intervention=kind, detail=f"{kind} sent on {debtor.preferred_channel}",
            commitment_id=(extra or {}).get("commitment_id"),
        )

    # -- inbound -----------------------------------------------------------

    def handle_reply(self, invoice_id: str, reply: str, now: datetime) -> TurnResult:
        invoice = self._store.get_invoice(invoice_id)
        debtor = self._store.get_debtor(invoice.debtor_id)
        context = self._message_context(invoice, debtor, now, None, None, None)
        try:
            interpretation = self._brain.interpret_reply(context, reply)
        except BrainUnavailable as error:
            self._store.append_event(
                at=now, actor=Actor.AGENT, kind=EventKind.MESSAGE_RECEIVED,
                invoice_id=invoice.id, debtor_id=debtor.id,
                payload={"verbatim": reply, "intent": None, "deferred": True},
            )
            return self._defer(invoice, debtor, now, "interpret", str(error))
        counters.inc(f"reply.{interpretation.intent.value}")
        self._store.append_event(
            at=now, actor=Actor.AGENT, kind=EventKind.MESSAGE_RECEIVED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={
                "verbatim": interpretation.verbatim,
                "intent": interpretation.intent,
                "confidence": interpretation.confidence,
                "summary": interpretation.summary,
                "promised_amount": interpretation.promised_amount,
                "promised_on": (interpretation.promised_on.isoformat()
                                if interpretation.promised_on else None),
                "flags": interpretation.flags, "brain": self.brain_name,
            },
        )

        if interpretation.intent is Intent.STOP_CONTACT:
            return self._stop(invoice, debtor, interpretation, now)

        if invoice.state not in ACTIVE_STATES:
            # A human owns it, or it is settled/closed: log, never act.
            return TurnResult(
                invoice_id=invoice_id, action=Action.NOTED,
                detail=f"reply logged; invoice is {invoice.state} and not the agent's to act on",
            )

        match interpretation.intent:
            case Intent.DISPUTE | Intent.CLAIMS_PAID:
                return self._dispute(invoice, debtor, interpretation, now)
            case Intent.PROMISE:
                return self._record_promise(invoice, debtor, interpretation, now)
            case Intent.ACCEPT_OFFER:
                return self._accept_offer(invoice, debtor, interpretation, now)
            case Intent.REQUEST_TERMS | Intent.QUESTION:
                # Answer now: the brain proposes, policy gates (responding mode
                # relaxes spacing/daily limits, never hours or the total cap).
                result = self._turn(invoice_id, now, responding_to=interpretation)
                if result.action is Action.MESSAGE_SENT:
                    return TurnResult(
                        invoice_id=invoice_id, action=Action.COUNTER_OFFERED,
                        decision=result.decision, intervention=result.intervention,
                        detail=f"answered with {result.intervention}",
                    )
                return result
            case _:
                return TurnResult(
                    invoice_id=invoice_id, action=Action.NOTED,
                    detail=f"{interpretation.intent}: {interpretation.summary}",
                )

    # -- daily housekeeping ------------------------------------------------

    def daily_tick(self, today: date, now: datetime) -> list[TurnResult]:
        """Rule on lapsed commitments, promises and concessions; escalate where earned;
        send pending commitment confirmations and bounded near-deadline reminders."""
        results: list[TurnResult] = []
        for invoice in self._store.all_invoices():
            touched = False

            for commitment in self._store.live_commitments_for(invoice.id):
                missed = expire_commitment(commitment, today, now)
                if missed is None:
                    continue
                self._store.put_commitment(missed)
                self._store.append_event(
                    at=now, actor=Actor.SYSTEM, kind=EventKind.COMMITMENT_MISSED,
                    invoice_id=invoice.id, debtor_id=invoice.debtor_id,
                    payload={"commitment_id": missed.id, "committed_amount": missed.committed_amount,
                             "amount_received": missed.amount_received,
                             "amount_remaining": missed.amount_remaining,
                             "due_on": missed.due_on.isoformat(), "source": missed.source,
                             "reason": "deadline passed without the committed amount on the rails"},
                )
                counters.inc("commitment.missed")
                touched = True

            promise = self._store.open_promise_for(invoice.id)
            if promise is not None:
                paid = self._store.paid_between(
                    invoice.id, promise.made_at, promise.promised_on.isoformat()
                )
                expired = expire_promise(invoice, promise, today, paid, now)
                if expired is not None:
                    invoice, resolved = expired
                    self._store.put_invoice(invoice)
                    self._store.put_promise(resolved)
                    self._store.append_event(
                        at=now, actor=Actor.SYSTEM, kind=EventKind.PROMISE_RESOLVED,
                        invoice_id=invoice.id, debtor_id=invoice.debtor_id,
                        payload={"promise_id": resolved.id, "outcome": resolved.state,
                                 "paid_in_window": paid},
                    )
                    counters.inc(f"promise.{resolved.state.value}")
                    touched = True

            concession = self._store.live_concession_for(invoice.id)
            if concession is not None:
                paid = self._store.paid_since(invoice.id, concession.offered_at)
                ruled = expire_concession(invoice, concession, today, paid, now)
                if ruled is not None:
                    self._store.put_concession(ruled)
                    self._store.append_event(
                        at=now, actor=Actor.SYSTEM, kind=EventKind.CONCESSION_RESOLVED,
                        invoice_id=invoice.id, debtor_id=invoice.debtor_id,
                        payload={"concession_id": ruled.id, "type": ruled.type,
                                 "outcome": ruled.state, "paid_since_offer": paid},
                    )
                    counters.inc(f"concession.{ruled.state.value}")
                    touched = True

            if invoice.state in ACTIVE_STATES:
                results.extend(self._commitment_messages(invoice, today, now))

            if not touched or invoice.state not in ACTIVE_STATES:
                continue
            facts = self._contact_facts(invoice, Channel.SYSTEM, now)
            escalation = should_escalate(facts, self._config)
            self._audit_gate(escalation, invoice, now)
            if escalation.allowed:
                results.append(self._escalate(invoice, escalation.reason, now))
            else:
                results.append(TurnResult(
                    invoice_id=invoice.id, action=Action.NOTED,
                    detail="commitment lapsed; invoice back in chase pool",
                ))
        return results

    def _commitment_messages(self, invoice: Invoice, today: date, now: datetime) -> list[TurnResult]:
        """Confirmations that could not go out earlier, and one bounded reminder."""
        out: list[TurnResult] = []
        for commitment in self._store.live_commitments_for(invoice.id):
            if not commitment.instrument_sent:
                out.append(self._confirm_commitment(invoice, commitment, now))
                continue
            days_left = (commitment.due_on - today).days
            if (self._config.commitment_reminder_days_before > 0 and not commitment.reminder_sent
                    and 0 <= days_left <= self._config.commitment_reminder_days_before):
                out.append(self._remind_commitment(invoice, commitment, now))
        return out

    # -- reply handlers ----------------------------------------------------

    def _stop(self, invoice: Invoice, debtor: Debtor, interpretation: ReplyInterpretation,
              now: datetime) -> TurnResult:
        updated = stop_contact(invoice)
        self._store.put_invoice(updated)
        self._withdraw_live_concession(invoice, now, "stop-contact")
        self._cancel_live_commitments(invoice, now, "debtor asked us to stop")
        self._store.append_event(
            at=now, actor=Actor.POLICY, kind=EventKind.STOP_CONTACT_HONORED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={"verbatim": interpretation.verbatim},
        )
        counters.inc("stop_contact.honored")
        return TurnResult(invoice_id=invoice.id, action=Action.STOP_CONTACT_HONORED)

    def _dispute(self, invoice: Invoice, debtor: Debtor, interpretation: ReplyInterpretation,
                 now: datetime) -> TurnResult:
        updated = record_dispute(invoice)
        self._store.put_invoice(updated)
        self._withdraw_live_concession(invoice, now, "dispute")
        self._cancel_live_commitments(invoice, now, "invoice disputed; a human owns it")
        claims_paid = interpretation.intent is Intent.CLAIMS_PAID
        self._store.append_event(
            at=now, actor=Actor.POLICY, kind=EventKind.DISPUTE_RECORDED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={
                "verbatim": interpretation.verbatim, "summary": interpretation.summary,
                "kind": "claims_paid" if claims_paid else "dispute",
                "rails_observed": invoice.amount_paid,
                "reason": ("debtor says it is paid; nothing matching on the rails — "
                           "human to reconcile" if claims_paid else "debtor contests the invoice"),
            },
        )
        counters.inc("dispute.recorded")
        return TurnResult(
            invoice_id=invoice.id, action=Action.DISPUTE_STOOD_DOWN,
            detail="invoice handed to human review"
                   + (" (claims already paid; verify on rails)" if claims_paid else ""),
        )

    def _record_promise(
        self, invoice: Invoice, debtor: Debtor,
        interpretation: ReplyInterpretation, now: datetime,
        *, source: CommitmentSource = CommitmentSource.PROMISE,
        concession: Concession | None = None,
    ) -> TurnResult:
        """Record what was said; then ask policy whether it may become a commitment.

        The promise is *always* recorded — it is evidence. Only policy decides
        whether it becomes an executable commitment; if not, the promise is
        marked DECLINED, the invoice stays chaseable, and the refusal (with
        every checklist line) is audited.
        """
        promised_on = interpretation.promised_on or (now.date() + timedelta(days=7))
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
                "date_inferred": interpretation.promised_on is None,
                "partial": promise.amount < invoice.balance,
                "superseded": superseded.id if superseded else None,
            },
        )
        counters.inc("promise.recorded")

        outcome = self.open_commitment(
            updated_invoice, debtor, amount=promise.amount, due_on=promise.promised_on, now=now,
            source=source, promise=promise, concession=concession,
            confidence=interpretation.confidence, evidence=interpretation.verbatim,
        )
        if outcome.commitment is not None:
            return TurnResult(
                invoice_id=invoice.id, action=Action.COMMITMENT_CREATED,
                commitment_id=outcome.commitment.id, commitment_verdict=outcome.verdict,
                intervention=(InterventionKind.COMMITMENT_CONFIRMATION
                              if outcome.confirmation and outcome.confirmation.action is Action.MESSAGE_SENT
                              else None),
                detail=f"{describe_commitment(outcome.commitment)} "
                       f"(promise confidence {promise.confidence:.2f})",
            )
        # Declined: the words stay on the ledger; the invoice does not wait on them.
        self._store.put_promise(promise.model_copy(
            update={"state": PromiseState.DECLINED, "resolved_at": now}))
        self._store.put_invoice(updated_invoice.model_copy(update={
            "state": (InvoiceState.PARTIALLY_PAID if invoice.amount_paid > 0
                      else InvoiceState.OUTSTANDING)}))
        return TurnResult(
            invoice_id=invoice.id, action=Action.PROMISE_RECORDED,
            commitment_verdict=outcome.verdict,
            detail=f"{format_inr(promise.amount)} by {promise.promised_on} recorded as said; "
                   f"no commitment: {outcome.verdict.reason}",
        )

    def _accept_offer(
        self, invoice: Invoice, debtor: Debtor,
        interpretation: ReplyInterpretation, now: datetime,
    ) -> TurnResult:
        concession = self._store.live_concession_for(invoice.id)
        if concession is None or concession.state is not ConcessionState.OFFERED:
            if interpretation.promised_amount or interpretation.promised_on:
                return self._record_promise(invoice, debtor, interpretation, now)
            return TurnResult(invoice_id=invoice.id, action=Action.NOTED,
                              detail="agreement noted, but no offer is open to accept")
        accepted = accept_concession(invoice, concession, now)
        self._store.put_concession(accepted)
        self._store.append_event(
            at=now, actor=Actor.AGENT, kind=EventKind.CONCESSION_ACCEPTED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={"concession_id": accepted.id, "type": accepted.type,
                     "verbatim": interpretation.verbatim,
                     "confidence": interpretation.confidence},
        )
        counters.inc("offer.accepted")
        if accepted.type is ConcessionType.DISCOUNT:
            # Accepting a settlement is a promise to pay the settlement amount by
            # pay-by — and, if policy agrees, an executable commitment for it.
            promised_on = min(interpretation.promised_on or accepted.pay_by, accepted.pay_by)
            commitment = interpretation.model_copy(update={
                "promised_amount": accepted.settlement_amount, "promised_on": promised_on,
                "confidence": max(interpretation.confidence, 0.8),
            })
            result = self._record_promise(invoice, debtor, commitment, now,
                                          source=CommitmentSource.CONCESSION, concession=accepted)
            return TurnResult(
                invoice_id=invoice.id, action=Action.OFFER_ACCEPTED,
                commitment_id=result.commitment_id, commitment_verdict=result.commitment_verdict,
                detail=f"discount settlement accepted → {result.detail}",
            )
        created = self._open_installment_commitments(invoice, debtor, accepted, interpretation, now)
        return TurnResult(
            invoice_id=invoice.id, action=Action.OFFER_ACCEPTED,
            commitment_id=created[0].id if created else None,
            detail=f"installment plan accepted: {len(created)} commitments opened",
        )

    # -- commitments -------------------------------------------------------

    class _CommitmentOutcome(BaseModel):
        verdict: CommitmentVerdict
        commitment: PaymentCommitment | None = None
        confirmation: TurnResult | None = None

    def open_commitment(
        self, invoice: Invoice, debtor: Debtor, *, amount: int, due_on: date, now: datetime,
        source: CommitmentSource, promise: PromiseToPay | None = None,
        concession: Concession | None = None, installment_index: int | None = None,
        confidence: float = 0.0, evidence: str = "", confirm: bool = True,
        actor: Actor = Actor.POLICY,
    ) -> RecoveryAgent._CommitmentOutcome:
        """Promise → policy → executable commitment → rail instrument → tell the debtor.

        The verdict and every checklist line are audited whether or not the
        commitment is created; a refused commitment leaves the promise on the
        ledger as evidence.
        """
        live_concession = self._store.live_concession_for(invoice.id)
        self._store.append_event(
            at=now, actor=Actor.AGENT, kind=EventKind.COMMITMENT_PROPOSED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={"source": source, "amount": amount, "due_on": due_on.isoformat(),
                     "promise_id": promise.id if promise else None,
                     "concession_id": concession.id if concession else None,
                     "installment_index": installment_index,
                     "confidence": confidence, "verbatim": evidence,
                     "partial": amount < invoice.balance},
        )
        verdict = check_commitment(
            invoice, amount, due_on, now.date(), self._config,
            live_concession=None if concession is not None else live_concession,
        )
        checks = [c.model_dump() for c in verdict.checks]
        if not verdict.allowed:
            self._store.append_event(
                at=now, actor=actor, kind=EventKind.COMMITMENT_BLOCKED,
                invoice_id=invoice.id, debtor_id=debtor.id,
                payload={"source": source, "amount": amount, "due_on": due_on.isoformat(),
                         "promise_id": promise.id if promise else None,
                         "reason": verdict.reason, "checks": checks},
            )
            counters.inc("commitment.blocked")
            return self._CommitmentOutcome(verdict=verdict)
        self._store.append_event(
            at=now, actor=actor, kind=EventKind.COMMITMENT_APPROVED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={"source": source, "amount": amount, "due_on": due_on.isoformat(),
                     "promise_id": promise.id if promise else None,
                     "reason": verdict.reason, "checks": checks},
        )

        n = len(self._store.commitments_for(invoice.id)) + 1
        cid = f"cmt_{invoice.id}_{n}"
        due_at = datetime.combine(due_on, time(23, 59, 59), tzinfo=ZoneInfo(self._config.timezone))
        instrument_type = instrument_id = payment_url = None
        link = None
        if self._rails is not None:
            try:
                link = self._rails.create_payment_link(
                    amount=amount,
                    description=f"Invoice {invoice.number} — {format_inr(amount)} by {due_on:%d %b %Y}",
                    invoice_id=invoice.id, commitment_id=cid, customer_name=debtor.name,
                    customer_email=debtor.email, customer_contact=debtor.phone,
                    expire_by=int(due_at.timestamp()),
                )
            except Exception as error:
                # The commitment is what policy accepted; the instrument is how it is paid.
                # A rail failure is audited and the commitment stands without a link —
                # money can still arrive on the invoice and be matched to it.
                self._rail_failed(invoice, debtor, now, "commitment_link", amount, error, cid)
                link = None
        if link is not None:
            instrument_type, instrument_id, payment_url = (
                InstrumentType.PAYMENT_LINK, link.get("id"), link.get("short_url"),
            )
            self._store.append_event(
                at=now, actor=Actor.RAILS, kind=EventKind.PAYMENT_INSTRUMENT_CREATED,
                invoice_id=invoice.id, debtor_id=debtor.id,
                payload={"commitment_id": cid, "instrument_type": instrument_type,
                         "instrument_id": instrument_id, "payment_url": payment_url,
                         "amount": amount, "expire_by": due_at.isoformat(),
                         "notes": link.get("notes"), "reference_id": link.get("reference_id")},
            )
            counters.inc("commitment.instrument_created")

        commitment = PaymentCommitment(
            id=cid, invoice_id=invoice.id, debtor_id=debtor.id,
            promise_id=promise.id if promise else None,
            concession_id=concession.id if concession else None,
            installment_index=installment_index, source=source,
            committed_amount=amount, due_on=due_on, due_at=due_at,
            instrument_type=instrument_type, instrument_id=instrument_id, payment_url=payment_url,
            created_at=now, accepted_at=now if promise is not None else None,
            confidence=confidence, evidence=evidence, rationale=verdict.reason,
        )
        commitment, superseded = open_commitment(
            invoice, commitment, self._store.live_commitments_for(invoice.id)
        )
        with self._store.transaction():
            for old in superseded:
                self._store.put_commitment(old)
                self._store.append_event(
                    at=now, actor=Actor.POLICY, kind=EventKind.COMMITMENT_SUPERSEDED,
                    invoice_id=invoice.id, debtor_id=debtor.id,
                    payload={"commitment_id": old.id, "superseded_by": cid,
                             "committed_amount": old.committed_amount,
                             "amount_received": old.amount_received},
                )
            self._store.put_commitment(commitment)
            self._store.append_event(
                at=now, actor=Actor.POLICY, kind=EventKind.COMMITMENT_CREATED,
                invoice_id=invoice.id, debtor_id=debtor.id,
                payload={"commitment_id": cid, "source": source,
                         "committed_amount": amount, "due_on": due_on.isoformat(),
                         "due_at": due_at.isoformat(), "instrument_type": instrument_type,
                         "instrument_id": instrument_id, "payment_url": payment_url,
                         "promise_id": promise.id if promise else None,
                         "concession_id": concession.id if concession else None,
                         "installment_index": installment_index,
                         "confidence": confidence, "reason": verdict.reason},
            )
        counters.inc("commitment.created")
        log.info("commitment.created", invoice=invoice.id, commitment=cid, amount=amount,
                 due=due_on.isoformat(), source=source.value)
        confirmation = self._confirm_commitment(invoice, commitment, now) if confirm else None
        return self._CommitmentOutcome(verdict=verdict, commitment=commitment,
                                       confirmation=confirmation)

    def _open_installment_commitments(
        self, invoice: Invoice, debtor: Debtor, plan: Concession,
        interpretation: ReplyInterpretation, now: datetime,
    ) -> list[PaymentCommitment]:
        created: list[PaymentCommitment] = []
        for index, installment in enumerate(plan.installments, start=1):
            outcome = self.open_commitment(
                invoice, debtor, amount=installment.amount, due_on=installment.due_on, now=now,
                source=CommitmentSource.INSTALLMENT, concession=plan, installment_index=index,
                confidence=interpretation.confidence, evidence=interpretation.verbatim,
                confirm=False,
            )
            if outcome.commitment is not None:
                created.append(outcome.commitment)
        if created:
            self._confirm_commitment(invoice, created[0], now, plan=created)
        return created

    def _confirm_commitment(self, invoice: Invoice, commitment: PaymentCommitment,
                            now: datetime, plan: list[PaymentCommitment] | None = None) -> TurnResult:
        """Tell the debtor what was agreed and hand over the instrument (responding mode)."""
        debtor = self._store.get_debtor(invoice.debtor_id)
        facts = self._contact_facts(invoice, debtor.preferred_channel, now, responding=True)
        contact = check_contact(invoice, facts, self._config)
        self._audit_gate(contact, invoice, now)
        if not contact.allowed:
            return TurnResult(invoice_id=invoice.id, action=Action.BLOCKED, gate=contact,
                              commitment_id=commitment.id,
                              intervention=InterventionKind.COMMITMENT_CONFIRMATION,
                              detail=f"confirmation held: {contact.reason}")
        items = plan or [commitment]
        lines = [f"You agreed to pay {format_inr(c.committed_amount)} by {c.due_on:%d %b %Y}"
                 + (f" — pay here: {c.payment_url}" if c.payment_url and len(items) > 1 else "")
                 for c in items]
        offer_text = ("Confirming what we agreed: " + "; ".join(lines) + ".")
        context = self._message_context(invoice, debtor, now, offer_text,
                                        commitment.payment_url,
                                        InterventionKind.COMMITMENT_CONFIRMATION)
        try:
            text = self._brain.draft_message(context)
        except BrainUnavailable as error:
            return self._defer(invoice, debtor, now, "draft", str(error))
        result = self._send(invoice, debtor, facts, text, InterventionKind.COMMITMENT_CONFIRMATION,
                            now, payment_url=commitment.payment_url,
                            extra={"commitment_id": commitment.id, "responding": True,
                                   "commitment_ids": [c.id for c in items]})
        if result.action is Action.MESSAGE_SENT:
            for c in items:
                self._store.put_commitment(c.model_copy(update={"instrument_sent": True}))
            counters.inc("commitment.confirmed")
        return result

    def _remind_commitment(self, invoice: Invoice, commitment: PaymentCommitment,
                           now: datetime) -> TurnResult:
        """One bounded nudge before the deadline — a normal contact, fully gated."""
        debtor = self._store.get_debtor(invoice.debtor_id)
        facts = self._contact_facts(invoice, debtor.preferred_channel, now)
        contact = check_contact(invoice, facts, self._config)
        self._audit_gate(contact, invoice, now)
        # A reminder never earns a second chance: mark it so the tick doesn't retry daily.
        self._store.put_commitment(commitment.model_copy(update={"reminder_sent": True}))
        if not contact.allowed:
            return TurnResult(invoice_id=invoice.id, action=Action.BLOCKED, gate=contact,
                              commitment_id=commitment.id,
                              intervention=InterventionKind.COMMITMENT_REMINDER,
                              detail=f"reminder skipped: {contact.reason}")
        offer_text = (f"A gentle reminder: {format_inr(commitment.amount_remaining)} is due by "
                      f"{commitment.due_on:%d %b %Y} as agreed.")
        context = self._message_context(invoice, debtor, now, offer_text, commitment.payment_url,
                                        InterventionKind.COMMITMENT_REMINDER)
        try:
            text = self._brain.draft_message(context)
        except BrainUnavailable as error:
            return self._defer(invoice, debtor, now, "draft", str(error))
        result = self._send(invoice, debtor, facts, text, InterventionKind.COMMITMENT_REMINDER, now,
                            payment_url=commitment.payment_url,
                            extra={"commitment_id": commitment.id, "responding": False})
        if result.action is Action.MESSAGE_SENT:
            counters.inc("commitment.reminded")
        return result

    def _cancel_live_commitments(self, invoice: Invoice, now: datetime, reason: str) -> None:
        for commitment in self._store.live_commitments_for(invoice.id):
            cancelled = cancel_commitment(commitment, now, reason)
            self._store.put_commitment(cancelled)
            self._store.append_event(
                at=now, actor=Actor.POLICY, kind=EventKind.COMMITMENT_CANCELLED,
                invoice_id=invoice.id, debtor_id=invoice.debtor_id,
                payload={"commitment_id": cancelled.id, "reason": reason,
                         "committed_amount": cancelled.committed_amount,
                         "amount_received": cancelled.amount_received},
            )
            counters.inc("commitment.cancelled")

    # -- internals ---------------------------------------------------------

    def _escalate(self, invoice: Invoice, reason: str, now: datetime) -> TurnResult:
        updated = escalate(invoice)
        self._store.put_invoice(updated)
        self._withdraw_live_concession(invoice, now, "escalation")
        self._cancel_live_commitments(invoice, now, f"escalated: {reason}")
        self._store.append_event(
            at=now, actor=Actor.POLICY, kind=EventKind.ESCALATED,
            invoice_id=invoice.id, debtor_id=invoice.debtor_id,
            payload={"reason": reason},
        )
        counters.inc("escalated")
        return TurnResult(invoice_id=invoice.id, action=Action.ESCALATED, detail=reason,
                          intervention=InterventionKind.ESCALATE_HUMAN)

    def _rail_failed(self, invoice: Invoice, debtor: Debtor, now: datetime, job: str,
                     amount: int, error: Exception, commitment_id: str | None = None) -> TurnResult:
        self._store.append_event(
            at=now, actor=Actor.RAILS, kind=EventKind.RAIL_FAILED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={"job": job, "amount": amount, "commitment_id": commitment_id,
                     "error": f"{type(error).__name__}: {str(error)[:160]}",
                     "reason": "payment rail refused or failed; nothing issued"},
        )
        counters.inc("rail.failed")
        log.warning("rail.failed", invoice=invoice.id, job=job, error=type(error).__name__)
        return TurnResult(invoice_id=invoice.id, action=Action.DEFERRED, commitment_id=commitment_id,
                          detail=f"payment rail failed during {job}; nothing sent")

    def _defer(self, invoice: Invoice, debtor: Debtor, now: datetime, job: str,
               error: str) -> TurnResult:
        self._store.append_event(
            at=now, actor=Actor.SYSTEM, kind=EventKind.BRAIN_FAILED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={"job": job, "brain": self.brain_name, "error": error[:200],
                     "reason": "brain unavailable; no action taken"},
        )
        counters.inc("brain.deferred")
        log.warning("brain.deferred", invoice=invoice.id, job=job)
        return TurnResult(invoice_id=invoice.id, action=Action.DEFERRED,
                          detail=f"brain unavailable during {job}; nothing done")

    def _withdraw_live_concession(self, invoice: Invoice, now: datetime, why: str) -> None:
        concession = self._store.live_concession_for(invoice.id)
        if concession is None:
            return
        withdrawn = withdraw_concession(concession, now)
        self._store.put_concession(withdrawn)
        self._store.append_event(
            at=now, actor=Actor.POLICY, kind=EventKind.CONCESSION_RESOLVED,
            invoice_id=invoice.id, debtor_id=invoice.debtor_id,
            payload={"concession_id": withdrawn.id, "type": withdrawn.type,
                     "outcome": withdrawn.state, "reason": why},
        )

    def _build_concession(self, invoice: Invoice, debtor: Debtor, offer: Offer, today: date,
                          now: datetime, rationale: str) -> Concession:
        n = len(self._store.concessions_for(invoice.id)) + 1
        ctype = (ConcessionType.DISCOUNT if offer.type is OfferType.DISCOUNT
                 else ConcessionType.INSTALLMENTS)
        return Concession(
            id=f"con_{invoice.id}_{n}", invoice_id=invoice.id, debtor_id=debtor.id,
            type=ctype, discount_bps=offer.discount_bps, balance_at_offer=invoice.balance,
            settlement_amount=offer.settlement_amount(invoice.balance),
            installments=offer.schedule(invoice.balance, today), pay_by=offer.pay_by,
            offered_at=now, rationale=rationale,
        )

    def _next_link_seq(self, invoice: Invoice) -> int:
        return len(self._store.events_for(invoice.id, EventKind.MESSAGE_SENT)) + 1

    def _contact_facts(self, invoice: Invoice, channel: Channel, now: datetime,
                       responding: bool = False) -> ContactFacts:
        local_day = self._config.local(now).date().isoformat()
        since = invoice.human_released_at
        total, today, last = self._store.attempt_facts(invoice.id, local_day, since)
        broken = sum(
            1 for p in self._store.promises_for(invoice.id)
            if p.state is PromiseState.BROKEN and (since is None or p.made_at >= since)
        ) + sum(
            1 for c in self._store.concessions_for(invoice.id)
            if c.state is ConcessionState.BROKEN and (since is None or c.offered_at >= since)
        )
        days_since = (now.date() - last.date()).days if last is not None else None
        if responding:
            # Answering the debtor's own message is not another nudge.
            today, days_since = 0, None
        return ContactFacts(
            now=now, channel=channel, attempts_total=total, attempts_today=today,
            broken_promises=broken, days_since_last_contact=days_since,
        )

    def _decision_context(self, invoice: Invoice, now: datetime,
                          responding_to: ReplyInterpretation | None) -> DecisionContext:
        promises = self._store.promises_for(invoice.id)
        concessions = self._store.concessions_for(invoice.id)
        commitments = self._store.commitments_for_debtor(invoice.debtor_id)
        profile = profile_for(commitments, invoice.human_released_at)
        active = self._store.live_commitments_for(invoice.id)
        open_promise = next((p for p in promises if p.state is PromiseState.OPEN), None)
        live = next((c for c in concessions if c.live), None)
        total, _, last = self._store.attempt_facts(invoice.id, self._config.local(now).date().isoformat(),
                                                   invoice.human_released_at)
        received = self._store.events_for(invoice.id, EventKind.MESSAGE_RECEIVED)
        last_intent = responding_to.intent if responding_to else (
            received[-1].payload.get("intent") if received else None
        )
        last_summary = responding_to.summary if responding_to else (
            received[-1].payload.get("summary", "") if received else ""
        )
        return DecisionContext(
            invoice_number=invoice.number, balance=invoice.balance,
            original_amount=invoice.amount, days_overdue=invoice.days_overdue(now.date()),
            today=now.date(), invoice_state=invoice.state.value,
            attempts_total=total, attempts_allowed=self._config.max_attempts_per_invoice,
            days_since_last_contact=(now.date() - last.date()).days if last else None,
            promises_kept=sum(1 for p in promises if p.state is PromiseState.KEPT),
            promises_broken=sum(1 for p in promises if p.state is PromiseState.BROKEN),
            promises_partially_kept=sum(
                1 for p in promises if p.state is PromiseState.PARTIALLY_KEPT
            ),
            open_promise_amount=open_promise.amount if open_promise else None,
            open_promise_on=open_promise.promised_on if open_promise else None,
            commitments_total=profile.commitments,
            commitments_fulfilled=profile.fulfilled,
            commitments_partially_fulfilled=profile.partially_fulfilled,
            commitments_missed=profile.missed,
            commitment_fulfillment_rate=profile.fulfillment_rate,
            commitment_average_delay_days=profile.average_delay_days,
            active_commitment=describe_commitment(active[0]) if active else None,
            last_intent=last_intent, last_reply_summary=last_summary or "",
            live_concession=_describe_concession(live) if live else None,
            concession_history=[f"{_describe_concession(c)} → {c.state}" for c in concessions
                                if not c.live],
            prior_interventions=self._prior_interventions(invoice),
            max_discount_bps=self._config.max_discount_bps,
            max_installments=self._config.max_installments,
            min_installment=self._config.min_installment,
            max_horizon_days=self._config.max_promise_horizon_days,
            payment_links_available=self._rails is not None,
        )

    def _prior_interventions(self, invoice: Invoice) -> list[PriorIntervention]:
        events = self._store.events_for(invoice.id)
        out: list[PriorIntervention] = []
        for i, event in enumerate(events):
            if event.kind is not EventKind.MESSAGE_SENT:
                continue
            kind = event.payload.get("intervention") or InterventionKind.REMINDER
            outcome = "no reply"
            for later in events[i + 1:]:
                if later.kind is EventKind.MESSAGE_SENT:
                    break
                if later.kind is EventKind.MESSAGE_RECEIVED:
                    outcome = f"replied: {later.payload.get('intent')}"
                elif later.kind is EventKind.COMMITMENT_CREATED:
                    outcome = f"committed {format_inr(later.payload.get('committed_amount', 0))}"
                elif later.kind is EventKind.PAYMENT_OBSERVED:
                    outcome = f"paid {format_inr(later.payload.get('amount', 0))}"
                    break
                elif later.kind is EventKind.COMMITMENT_MISSED:
                    outcome = "commitment missed"
                elif later.kind is EventKind.PROMISE_RESOLVED:
                    outcome = f"promise {later.payload.get('outcome')}"
            out.append(PriorIntervention(on=event.at.date(), kind=InterventionKind(kind),
                                         outcome=outcome))
        return out[-6:]

    def _message_context(
        self, invoice: Invoice, debtor: Debtor, now: datetime,
        offer_text: str | None, payment_url: str | None, purpose: InterventionKind | None,
    ) -> MessageContext:
        return MessageContext(
            debtor_name=debtor.name, contact_name=debtor.contact_name,
            invoice_number=invoice.number, balance=invoice.balance,
            days_overdue=invoice.days_overdue(now.date()), today=now.date(),
            language=debtor.language, approved_offer_text=offer_text,
            payment_url=payment_url, history_summary=self._history_summary(invoice),
            purpose=(purpose or InterventionKind.REMINDER).value,
        )

    def _history_summary(self, invoice: Invoice) -> str:
        parts: list[str] = []
        commitments = self._store.commitments_for(invoice.id)
        missed = [c for c in commitments if c.state.value == "missed"]
        if missed:
            last = missed[-1]
            parts.append(
                f"On {last.created_at:%d %b} you committed to {format_inr(last.committed_amount)} by "
                f"{last.due_on:%d %b}; we have not seen it on our side."
            )
        else:
            promises = self._store.promises_for(invoice.id)
            broken = [p for p in promises if p.state is PromiseState.BROKEN]
            if broken:
                last_p = broken[-1]
                parts.append(
                    f"On {last_p.made_at:%d %b} you committed to {format_inr(last_p.amount)} by "
                    f"{last_p.promised_on:%d %b}; we have not seen it on our side."
                )
        if invoice.amount_paid > 0:
            parts.append(f"Thank you for the {format_inr(invoice.amount_paid)} received so far.")
        return " ".join(parts)

    def _offer_text(self, invoice: Invoice, offer: Offer, concession: Concession) -> str:
        if offer.type is OfferType.DISCOUNT:
            return (
                f"Clear the balance by {offer.pay_by:%d %b} and it settles at "
                f"{format_inr(concession.settlement_amount)} — an early-payment discount of "
                f"{offer.discount_bps / 100:.2f}% ({format_inr(concession.waivable)} waived). "
                f"After {offer.pay_by:%d %b} the full balance applies."
            )
        if offer.type is OfferType.INSTALLMENTS:
            schedule = ", ".join(
                f"{format_inr(i.amount)} by {i.due_on:%d %b}" for i in concession.installments
            )
            return f"We can split the balance into {len(concession.installments)} instalments: {schedule}."
        return f"The full balance of {format_inr(invoice.balance)} is due by {offer.pay_by:%d %b}."

    def _audit_gate(self, decision: GateDecision, invoice: Invoice, now: datetime) -> None:
        kind = EventKind.GATE_ALLOWED if decision.allowed else EventKind.GATE_BLOCKED
        self._store.append_event(
            at=now, actor=Actor.POLICY, kind=kind,
            invoice_id=invoice.id, debtor_id=invoice.debtor_id,
            payload={"gate": decision.gate, "reason": decision.reason},
        )
        counters.inc("gate.allowed" if decision.allowed else "gate.blocked")


def _describe_concession(c: Concession) -> str:
    if c.type is ConcessionType.DISCOUNT:
        return (f"discount {c.discount_bps / 100:.2f}% → settle {format_inr(c.settlement_amount)} "
                f"by {c.pay_by}")
    return f"installments x{len(c.installments)} until {c.pay_by}"


def chaseable(store: Store) -> list[Invoice]:
    """Invoices the agent may act on today."""
    return [i for i in store.all_invoices() if i.state in ACTIVE_STATES]


__all__ = [
    "Action", "CONTACTING", "Outbox", "RecoveryAgent", "TurnResult", "chaseable",
]
