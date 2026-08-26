"""'Why this action?' — structured evidence for one invoice.

Everything here is assembled from the ledger and the audit chain: the
priority score with its components, the latest proposal and policy decision
with every gate, the promise and concession history, the observed payments.
No chain-of-thought is stored or shown; only the typed rationale the brain
returned and the reasons policy recorded.
"""

from __future__ import annotations

from datetime import date, datetime

from urudhi.agent.policy import PolicyConfig
from urudhi.audit.log import EventKind
from urudhi.ledger.models import PromiseState
from urudhi.ledger.money import format_inr
from urudhi.scoring.priority import score_invoice
from urudhi.store import Store


def _score_reasons(components: dict[str, float], invoice, promises, attempts, today: date) -> list[str]:
    reasons = []
    reasons.append(("+" if components["value"] >= 0.5 else "−")
                   + f" balance {format_inr(invoice.balance)} (value {components['value']:.2f})")
    reasons.append(("+" if components["urgency"] >= 0.3 else "−")
                   + f" {invoice.days_overdue(today)} days overdue (urgency {components['urgency']:.2f})")
    broken = sum(1 for p in promises if p.state is PromiseState.BROKEN)
    if any(p.state is PromiseState.OPEN for p in promises):
        reasons.append("− an open promise is running; chasing would burn goodwill")
    elif broken:
        reasons.append(f"+ {broken} broken promise(s); words have stopped working")
    else:
        reasons.append("· no promise history either way")
    reasons.append(("−" if attempts >= 3 else "+")
                   + f" {attempts} attempt(s) used (fatigue {components['fatigue']:.2f})")
    return reasons


def explain_invoice(store: Store, invoice_id: str, policy: PolicyConfig,
                    now: datetime | None = None) -> dict:
    invoice = store.get_invoice(invoice_id)
    today = (now or datetime.now()).date()
    promises = store.promises_for(invoice_id)
    concessions = store.concessions_for(invoice_id)
    payments = store.payments_for(invoice_id)
    attempts, _, _ = store.attempt_facts(invoice_id, today.isoformat(), invoice.human_released_at)
    score = score_invoice(invoice, promises, attempts, policy.max_attempts_per_invoice, today)
    events = store.events_for(invoice_id)

    decided = [e for e in events if e.kind is EventKind.INTERVENTION_DECIDED]
    latest = decided[-1] if decided else None
    escalation = next((e for e in reversed(events) if e.kind is EventKind.ESCALATED), None)
    dispute = next((e for e in reversed(events) if e.kind is EventKind.DISPUTE_RECORDED), None)
    brain_failures = [e for e in events if e.kind is EventKind.BRAIN_FAILED]

    def gate_lines(payload: dict) -> list[dict]:
        return [{"ok": g["allowed"], "gate": g["gate"], "reason": g["reason"]}
                for g in payload.get("gates", [])]

    return {
        "invoice_id": invoice.id,
        "priority": {
            "score": round(score.score * 100),
            "components": score.components,
            "reasons": _score_reasons(score.components, invoice, promises, attempts, today),
        },
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
             "observed_at": p.observed_at.isoformat(), "event_id": p.razorpay_event_id}
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
             "payment_url": e.payload.get("payment_url"), "brain": e.payload.get("brain")}
            for e in events if e.kind is EventKind.MESSAGE_SENT
        ],
    }
