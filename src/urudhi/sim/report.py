"""Metrics: what the batch actually proved, exceptions first-class.

Every number here is computed from the ledger and the audit chain — the same
records a reviewer can verify — never from the runner's own bookkeeping.
"""

from __future__ import annotations

from typing import Any

from urudhi.audit.log import EventKind, verify_chain
from urudhi.ledger.models import InvoiceState, PromiseState
from urudhi.ledger.money import format_inr
from urudhi.sim.batch import archetype_of
from urudhi.sim.runner import RunResult


def build_report(result: RunResult) -> dict[str, Any]:
    store = result.store
    invoices = store.all_invoices()
    promises = store.all_promises()
    events = list(store.audit_events())

    total_outstanding = sum(i.amount for i in invoices)
    recovered = sum(i.amount_paid for i in invoices)
    by_state = {state.value: 0 for state in InvoiceState}
    for invoice in invoices:
        by_state[invoice.state.value] += 1

    promise_states = {state.value: 0 for state in PromiseState}
    for promise in promises:
        promise_states[promise.state.value] += 1
    resolved = (
        promise_states["kept"] + promise_states["partially_kept"] + promise_states["broken"]
    )

    per_archetype: dict[str, dict[str, int]] = {}
    for invoice in invoices:
        arch = archetype_of(result.cases, invoice.id).value
        bucket = per_archetype.setdefault(
            arch, {"invoices": 0, "outstanding": 0, "recovered": 0}
        )
        bucket["invoices"] += 1
        bucket["outstanding"] += invoice.amount
        bucket["recovered"] += invoice.amount_paid

    # Exceptions: every invoice the agent could not resolve, and why.
    exceptions = [
        {
            "invoice_id": i.id,
            "state": i.state.value,
            "balance": i.balance,
            "balance_inr": format_inr(i.balance),
            "archetype": archetype_of(result.cases, i.id).value,
        }
        for i in invoices
        if i.state is not InvoiceState.PAID
    ]
    exceptions.sort(key=lambda e: (-e["balance"], e["invoice_id"]))

    return {
        "run": {
            "days": result.config.days,
            "invoices": len(invoices),
            "seed": result.config.seed,
            "policy": result.policy.model_dump(mode="json"),
        },
        "money": {
            "outstanding_paise": total_outstanding,
            "recovered_paise": recovered,
            "outstanding_inr": format_inr(total_outstanding),
            "recovered_inr": format_inr(recovered),
            "recovery_rate": round(recovered / total_outstanding, 4),
        },
        "invoices_by_state": by_state,
        "promises": {
            "made": len(promises),
            **promise_states,
            "kept_rate_of_resolved": (
                round(promise_states["kept"] / resolved, 4) if resolved else None
            ),
        },
        "contact": {
            "messages_sent": sum(1 for e in events if e.kind is EventKind.MESSAGE_SENT),
            "replies_received": sum(
                1 for e in events if e.kind is EventKind.MESSAGE_RECEIVED
            ),
            "gates_blocked": sum(1 for e in events if e.kind is EventKind.GATE_BLOCKED),
            "offers_made": sum(1 for e in events if e.kind is EventKind.OFFER_MADE),
        },
        "exceptions": exceptions,
        "audit": {
            "events": len(events),
            "chain_verified": verify_chain(events) == len(events),
        },
    }
