"""'Why this action?' and 'Commitment integrity' — structured evidence for one invoice.

Everything here is assembled from the ledger and the audit chain: the
priority score with its components, the latest proposal and policy decision
with every gate, the promise / commitment / concession history, the observed
payments. For each commitment the provenance chain is reconstructed —
what was said, what the brain understood, what policy allowed, what
instrument was created, what money arrived, the final outcome — every step
pointing at the audit event that proves it. No chain-of-thought is stored or
shown; only the typed rationale the brain returned and the reasons policy
recorded.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from urudhi.agent.policy import PolicyConfig
from urudhi.audit.log import AuditEvent, EventKind
from urudhi.ledger.commitments import profile_for
from urudhi.ledger.models import PaymentCommitment, PromiseState
from urudhi.ledger.money import format_inr
from urudhi.scoring.priority import score_invoice
from urudhi.store import Store


def _score_reasons(components: dict[str, float], invoice, promises, commitments, attempts,
                   today: date) -> list[str]:
    reasons = []
    reasons.append(("+" if components["value"] >= 0.5 else "−")
                   + f" balance {format_inr(invoice.balance)} (value {components['value']:.2f})")
    reasons.append(("+" if components["urgency"] >= 0.3 else "−")
                   + f" {invoice.days_overdue(today)} days overdue (urgency {components['urgency']:.2f})")
    profile = profile_for(commitments)
    broken = sum(1 for p in promises if p.state is PromiseState.BROKEN)
    if any(c.live for c in commitments) or any(p.state is PromiseState.OPEN for p in promises):
        reasons.append("− a commitment is running; chasing would burn goodwill")
    elif profile.fulfilled or profile.missed:
        sign = "+" if profile.missed > profile.fulfilled else "−"
        reasons.append(f"{sign} {profile.fulfilled} of {profile.fulfilled + profile.missed} "
                       f"commitments fulfilled (credibility {profile.credibility:.2f})")
        if profile.missed:
            reasons.append(f"+ {profile.missed} missed commitment(s); words have stopped working")
    elif broken:
        reasons.append(f"+ {broken} broken promise(s); words have stopped working")
    else:
        reasons.append("· no commitment history either way")
    reasons.append(("−" if attempts >= 3 else "+")
                   + f" {attempts} attempt(s) used (fatigue {components['fatigue']:.2f})")
    return reasons


def _event_ref(e: AuditEvent | None) -> dict[str, Any] | None:
    if e is None:
        return None
    return {"seq": e.seq, "at": e.at.isoformat(), "kind": e.kind.value, "hash": e.hash[:16]}


def commitment_integrity(store: Store, commitment: PaymentCommitment,
                         events: list[AuditEvent]) -> dict[str, Any]:
    """The provenance chain for one commitment, each step backed by an audit event."""
    mine = [e for e in events if e.payload.get("commitment_id") == commitment.id
            or commitment.id in (e.payload.get("commitment_ids") or [])]
    promise = None
    if commitment.promise_id:
        promise = next((p for p in store.promises_for(commitment.invoice_id)
                        if p.id == commitment.promise_id), None)
    said_event = next((e for e in events if e.kind is EventKind.MESSAGE_RECEIVED
                       and e.payload.get("verbatim") == commitment.evidence), None)
    proposed = next((e for e in events if e.kind is EventKind.COMMITMENT_PROPOSED
                     and e.payload.get("promise_id") == commitment.promise_id
                     and e.payload.get("installment_index") == commitment.installment_index
                     and e.payload.get("amount") == commitment.committed_amount), None)
    approved = next((e for e in events if e.kind is EventKind.COMMITMENT_APPROVED
                     and e.payload.get("promise_id") == commitment.promise_id
                     and e.payload.get("amount") == commitment.committed_amount
                     and e.payload.get("due_on") == commitment.due_on.isoformat()), None)
    created = next((e for e in mine if e.kind is EventKind.COMMITMENT_CREATED), None)
    instrument = next((e for e in mine if e.kind is EventKind.PAYMENT_INSTRUMENT_CREATED), None)
    confirmation = next((e for e in mine if e.kind is EventKind.MESSAGE_SENT
                         and e.payload.get("intervention") == "commitment_confirmation"), None)
    payments = [p for p in store.payments_for(commitment.invoice_id)
                if p.commitment_id == commitment.id]
    rail_events = [e for e in mine if e.kind in (
        EventKind.COMMITMENT_FULFILLED, EventKind.COMMITMENT_PARTIALLY_FULFILLED)]
    outcome_event = next((e for e in reversed(mine) if e.kind in (
        EventKind.COMMITMENT_FULFILLED, EventKind.COMMITMENT_MISSED,
        EventKind.COMMITMENT_CANCELLED, EventKind.COMMITMENT_SUPERSEDED)), None)
    return {
        "id": commitment.id, "state": commitment.state.value, "source": commitment.source.value,
        "invoice_id": commitment.invoice_id, "installment_index": commitment.installment_index,
        "committed_amount": commitment.committed_amount,
        "amount_received": commitment.amount_received,
        "amount_remaining": commitment.amount_remaining,
        "due_on": commitment.due_on.isoformat(), "due_at": commitment.due_at.isoformat(),
        "created_at": commitment.created_at.isoformat(),
        "fulfilled_at": commitment.fulfilled_at.isoformat() if commitment.fulfilled_at else None,
        "missed_at": commitment.missed_at.isoformat() if commitment.missed_at else None,
        "days_late": commitment.days_late, "confidence": commitment.confidence,
        "cancel_reason": commitment.cancel_reason or None,
        "said": {
            "verbatim": commitment.evidence, "promise_id": commitment.promise_id,
            "promise_state": promise.state.value if promise else None,
            "at": (said_event.at.isoformat() if said_event
                   else commitment.created_at.isoformat()),
            "event": _event_ref(said_event),
        },
        "understood": {
            "intent": said_event.payload.get("intent") if said_event else commitment.source.value,
            "amount": (said_event.payload.get("promised_amount") if said_event
                       else commitment.committed_amount),
            "on": (said_event.payload.get("promised_on") if said_event
                   else commitment.due_on.isoformat()),
            "confidence": commitment.confidence,
            "flags": said_event.payload.get("flags", []) if said_event else [],
            "brain": said_event.payload.get("brain") if said_event else None,
            "partial": commitment.committed_amount < (proposed.payload.get("amount", 0)
                                                     if False else commitment.committed_amount) or
                       bool(proposed and proposed.payload.get("partial")),
            "event": _event_ref(proposed),
        },
        "policy": {
            "allowed": True, "reason": commitment.rationale,
            "checks": approved.payload.get("checks", []) if approved else [],
            "event": _event_ref(approved),
        },
        "instrument": {
            "type": commitment.instrument_type.value if commitment.instrument_type else None,
            "id": commitment.instrument_id, "url": commitment.payment_url,
            "amount": commitment.committed_amount, "expires": commitment.due_at.isoformat(),
            "notes": instrument.payload.get("notes") if instrument else None,
            "reference_id": instrument.payload.get("reference_id") if instrument else None,
            "sent": commitment.instrument_sent,
            "event": _event_ref(instrument),
            "confirmation": _event_ref(confirmation),
        },
        "rail": [
            {"payment_id": p.id, "razorpay_payment_id": p.razorpay_payment_id,
             "razorpay_event_id": p.razorpay_event_id, "amount": p.amount,
             "method": p.method, "observed_at": p.observed_at.isoformat(),
             "matched_by": p.matched_by}
            for p in payments
        ] + [
            {"event": _event_ref(e), "outcome": e.payload.get("outcome"),
             "amount_received": e.payload.get("amount_received"),
             "matched_by": e.payload.get("matched_by")}
            for e in rail_events if not payments
        ],
        "outcome": {
            "state": commitment.state.value,
            "promise_state": promise.state.value if promise else None,
            "event": _event_ref(outcome_event),
            "created_event": _event_ref(created),
        },
        "timeline": [_event_ref(e) | {"detail": e.payload.get("reason") or e.payload.get("outcome")
                                      or e.payload.get("intervention") or ""} for e in mine],
    }


def explain_invoice(store: Store, invoice_id: str, policy: PolicyConfig,
                    now: datetime | None = None) -> dict:
    invoice = store.get_invoice(invoice_id)
    today = (now or datetime.now()).date()
    promises = store.promises_for(invoice_id)
    concessions = store.concessions_for(invoice_id)
    payments = store.payments_for(invoice_id)
    commitments = store.commitments_for(invoice_id)
    debtor_commitments = store.commitments_for_debtor(invoice.debtor_id)
    attempts, _, _ = store.attempt_facts(invoice_id, today.isoformat(), invoice.human_released_at)
    score = score_invoice(invoice, promises, attempts, policy.max_attempts_per_invoice, today,
                          commitments=debtor_commitments)
    events = store.events_for(invoice_id)
    profile = profile_for(debtor_commitments, invoice.human_released_at)

    decided = [e for e in events if e.kind is EventKind.INTERVENTION_DECIDED]
    latest = decided[-1] if decided else None
    escalation = next((e for e in reversed(events) if e.kind is EventKind.ESCALATED), None)
    dispute = next((e for e in reversed(events) if e.kind is EventKind.DISPUTE_RECORDED), None)
    brain_failures = [e for e in events if e.kind is EventKind.BRAIN_FAILED]
    blocked = [e for e in events if e.kind is EventKind.COMMITMENT_BLOCKED]

    def gate_lines(payload: dict) -> list[dict]:
        return [{"ok": g["allowed"], "gate": g["gate"], "reason": g["reason"]}
                for g in payload.get("gates", [])]

    return {
        "invoice_id": invoice.id,
        "priority": {
            "score": round(score.score * 100),
            "components": score.components,
            "reasons": _score_reasons(score.components, invoice, promises, debtor_commitments,
                                      attempts, today),
        },
        "credibility": profile.model_dump() | {"summary": profile.describe()},
        "latest_decision": None if latest is None else {
            "at": latest.at.isoformat(),
            "proposed": latest.payload.get("proposed"),
            "final": latest.payload.get("final"),
            "modified": latest.payload.get("modified"),
            "rationale": latest.payload.get("rationale", []),
            "confidence": latest.payload.get("confidence"),
            "policy_reasons": latest.payload.get("reasons", []),
            "gates": gate_lines(latest.payload),
            "offer": latest.payload.get("offer"),
        },
        "decision_history": [
            {"at": e.at.isoformat(), "proposed": e.payload.get("proposed"),
             "final": e.payload.get("final"), "modified": e.payload.get("modified")}
            for e in decided
        ],
        "promises": [
            {"id": p.id, "amount": p.amount, "promised_on": p.promised_on.isoformat(),
             "made_at": p.made_at.isoformat(), "state": p.state.value,
             "confidence": p.confidence, "verbatim": p.verbatim}
            for p in promises
        ],
        "commitments": [commitment_integrity(store, c, events) for c in commitments],
        "blocked_commitments": [
            {"at": e.at.isoformat(), "amount": e.payload.get("amount"),
             "due_on": e.payload.get("due_on"), "promise_id": e.payload.get("promise_id"),
             "reason": e.payload.get("reason"), "checks": e.payload.get("checks", []),
             "event": _event_ref(e)}
            for e in blocked
        ],
        "concessions": [
            {"id": c.id, "type": c.type.value, "state": c.state.value,
             "discount_bps": c.discount_bps, "settlement_amount": c.settlement_amount,
             "balance_at_offer": c.balance_at_offer, "pay_by": c.pay_by.isoformat(),
             "installments": [i.model_dump(mode="json") for i in c.installments],
             "payment_link_url": c.payment_link_url, "rationale": c.rationale}
            for c in concessions
        ],
        "payments": [
            {"id": p.id, "amount": p.amount, "method": p.method,
             "observed_at": p.observed_at.isoformat(), "event_id": p.razorpay_event_id,
             "commitment_id": p.commitment_id, "matched_by": p.matched_by}
            for p in payments
        ],
        "amount_waived": invoice.amount_waived,
        "escalation": None if escalation is None else {
            "at": escalation.at.isoformat(), "reason": escalation.payload.get("reason")},
        "dispute": None if dispute is None else {
            "at": dispute.at.isoformat(), "reason": dispute.payload.get("reason"),
            "verbatim": dispute.payload.get("verbatim")},
        "brain_failures": len(brain_failures),
        "interventions": [
            {"at": e.at.isoformat(), "kind": e.payload.get("intervention"),
             "responding": e.payload.get("responding", False),
             "payment_url": e.payload.get("payment_url"), "brain": e.payload.get("brain"),
             "commitment_id": e.payload.get("commitment_id")}
            for e in events if e.kind is EventKind.MESSAGE_SENT
        ],
    }
