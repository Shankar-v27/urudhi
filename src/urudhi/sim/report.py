"""Metrics: what the batch actually proved, exceptions first-class.

Every number here is computed from the ledger and the audit chain — the same
records a reviewer can verify — never from the runner's own bookkeeping.

Vocabulary the report keeps strictly apart:

* **observed payment** — a ``Payment`` row, i.e. a rail event;
* **attributed intervention** — the last message sent on that invoice within
  the attribution window before the payment (a documented rule, not a
  causal claim);
* **simulation result** — every figure in these files, produced by the
  persona model in ``sim/personas.py``;
* **real-world claim** — none is made.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from urudhi.audit.log import EventKind, verify_chain
from urudhi.ledger.models import ConcessionState, InvoiceState, PromiseState
from urudhi.ledger.money import format_inr
from urudhi.sim.batch import archetype_of
from urudhi.sim.runner import Arm, RunResult

ATTRIBUTION_WINDOW_DAYS = 7
ATTRIBUTION_RULE = (
    "A payment is attributed to the most recent message sent on the same invoice "
    f"within the previous {ATTRIBUTION_WINDOW_DAYS} days; otherwise it is unattributed "
    "(spontaneous, or outside the window). This is an accounting rule, not a causal claim."
)
_BUCKETS = [(0, 3, "0-3"), (4, 7, "4-7"), (8, 14, "8-14"), (15, 21, "15-21"), (22, 10_000, "22+")]


def _days_to_recovery(result: RunResult) -> list[int]:
    days = []
    for invoice in result.store.all_invoices():
        if invoice.state is not InvoiceState.PAID:
            continue
        payments = result.store.payments_for(invoice.id)
        if payments:
            last = max(p.observed_at.date() for p in payments)
            days.append((last - result.config.start).days)
    return days


def _histogram(days: list[int]) -> dict[str, int]:
    hist = {label: 0 for _, _, label in _BUCKETS}
    for d in days:
        for lo, hi, label in _BUCKETS:
            if lo <= d <= hi:
                hist[label] += 1
                break
    return hist


def arm_metrics(result: RunResult) -> dict[str, Any]:
    store = result.store
    invoices = store.all_invoices()
    promises = store.all_promises()
    concessions = store.all_concessions()
    events = list(store.audit_events())
    at_risk = sum(i.amount for i in invoices)
    recovered = sum(i.amount_paid for i in invoices)
    waived = sum(i.amount_waived for i in invoices)
    kept = sum(1 for p in promises if p.state is PromiseState.KEPT)
    broken = sum(1 for p in promises if p.state is PromiseState.BROKEN)
    partly = sum(1 for p in promises if p.state is PromiseState.PARTIALLY_KEPT)
    resolved = kept + broken + partly
    ttr = _days_to_recovery(result)
    return {
        "label": {Arm.NO_ACTION: "No action", Arm.BASELINE: "Fixed-cadence baseline",
                  Arm.URUDHI: f"Urudhi ({result.brain_name} brain)"}[result.arm],
        "arm": result.arm.value, "brain": result.brain_name,
        "invoices": len(invoices),
        "amount_at_risk_paise": at_risk,
        "recovered_paise": recovered,
        "recovery_rate": round(recovered / at_risk, 4) if at_risk else 0.0,
        "discount_cost_paise": waived,
        "net_recovered_paise": recovered - waived,
        "invoices_paid": sum(1 for i in invoices if i.state is InvoiceState.PAID),
        "promises_made": len(promises),
        "promises_kept": kept, "promises_broken": broken, "promises_partially_kept": partly,
        "promise_kept_rate": round(kept / resolved, 4) if resolved else None,
        "escalations": sum(1 for i in invoices if i.state is InvoiceState.ESCALATED),
        "disputes": sum(1 for i in invoices if i.state is InvoiceState.DISPUTED),
        "stop_contacts": sum(1 for i in invoices if i.state is InvoiceState.STOP_CONTACT),
        "contact_attempts": sum(1 for e in events if e.kind is EventKind.MESSAGE_SENT),
        "replies_received": sum(1 for e in events if e.kind is EventKind.MESSAGE_RECEIVED),
        "offers_made": len(concessions),
        "offers_accepted": sum(1 for c in concessions if c.state in (
            ConcessionState.ACCEPTED, ConcessionState.SETTLED)),
        "offers_settled": sum(1 for c in concessions if c.state is ConcessionState.SETTLED),
        "offers_expired": sum(1 for c in concessions if c.state is ConcessionState.EXPIRED),
        "gates_blocked": sum(1 for e in events if e.kind is EventKind.GATE_BLOCKED),
        "proposals_modified": sum(1 for e in events if e.kind is EventKind.INTERVENTION_DECIDED
                                  and e.payload.get("modified")),
        "brain_deferrals": sum(1 for e in events if e.kind is EventKind.BRAIN_FAILED),
        "days_to_recovery_median": statistics.median(ttr) if ttr else None,
        "days_to_recovery_mean": round(statistics.mean(ttr), 2) if ttr else None,
        "days_to_recovery_histogram": _histogram(ttr),
        "audit_events": len(events),
    }


def attribution(result: RunResult, window_days: int = ATTRIBUTION_WINDOW_DAYS) -> dict[str, Any]:
    store = result.store
    by_kind: dict[str, dict[str, int]] = defaultdict(lambda: {"payments": 0, "paise": 0})
    unattributed = {"payments": 0, "paise": 0}
    rows = []
    for invoice in store.all_invoices():
        sent = store.events_for(invoice.id, EventKind.MESSAGE_SENT)
        for payment in store.payments_for(invoice.id):
            window_start = payment.observed_at - timedelta(days=window_days)
            prior = [e for e in sent if window_start <= e.at <= payment.observed_at]
            if prior:
                kind = str(prior[-1].payload.get("intervention", "reminder"))
                by_kind[kind]["payments"] += 1
                by_kind[kind]["paise"] += payment.amount
                rows.append({"invoice_id": invoice.id, "payment_id": payment.id,
                             "amount": payment.amount, "attributed_to": kind,
                             "message_at": prior[-1].at.isoformat(),
                             "observed_at": payment.observed_at.isoformat()})
            else:
                unattributed["payments"] += 1
                unattributed["paise"] += payment.amount
                rows.append({"invoice_id": invoice.id, "payment_id": payment.id,
                             "amount": payment.amount, "attributed_to": None,
                             "observed_at": payment.observed_at.isoformat()})
    return {"by_intervention": dict(by_kind), "unattributed": unattributed, "payments": rows}


def timeline(result: RunResult) -> dict[str, int]:
    per_day: dict[str, int] = defaultdict(int)
    for e in result.store.events_of_kind(EventKind.PAYMENT_OBSERVED):
        per_day[e.at.date().isoformat()] += int(e.payload.get("amount", 0))
    return dict(per_day)


def build_report(result: RunResult) -> dict[str, Any]:
    """The single-arm report (kept for the Urudhi arm's own numbers)."""
    store = result.store
    invoices = store.all_invoices()
    events = list(store.audit_events())
    metrics = arm_metrics(result)
    by_state = {state.value: 0 for state in InvoiceState}
    for invoice in invoices:
        by_state[invoice.state.value] += 1
    per_archetype: dict[str, dict[str, int]] = {}
    for invoice in invoices:
        arch = archetype_of(result.cases, invoice.id).value
        bucket = per_archetype.setdefault(arch, {"invoices": 0, "outstanding": 0, "recovered": 0,
                                                 "waived": 0})
        bucket["invoices"] += 1
        bucket["outstanding"] += invoice.amount
        bucket["recovered"] += invoice.amount_paid
        bucket["waived"] += invoice.amount_waived
    exceptions = [
        {"invoice_id": i.id, "state": i.state.value, "balance": i.balance,
         "balance_inr": format_inr(i.balance), "archetype": archetype_of(result.cases, i.id).value}
        for i in invoices if i.state is not InvoiceState.PAID
    ]
    exceptions.sort(key=lambda e: (-e["balance"], e["invoice_id"]))
    return {
        "run": {"arm": result.arm.value, "brain": result.brain_name, "days": result.config.days,
                "invoices": len(invoices), "seed": result.config.seed,
                "timezone": result.config.timezone,
                "policy": result.policy.model_dump(mode="json")},
        "money": {
            "outstanding_paise": metrics["amount_at_risk_paise"],
            "recovered_paise": metrics["recovered_paise"],
            "waived_paise": metrics["discount_cost_paise"],
            "net_recovered_paise": metrics["net_recovered_paise"],
            "outstanding_inr": format_inr(metrics["amount_at_risk_paise"]),
            "recovered_inr": format_inr(metrics["recovered_paise"]),
            "recovery_rate": metrics["recovery_rate"],
            "note": "simulation result; recovered = sum of webhook-observed payments",
        },
        "metrics": metrics,
        "invoices_by_state": by_state,
        "per_archetype": per_archetype,
        "attribution": {"window_days": ATTRIBUTION_WINDOW_DAYS, "rule": ATTRIBUTION_RULE,
                        **{k: v for k, v in attribution(result).items() if k != "payments"}},
        "exceptions": exceptions,
        "audit": {"events": len(events), "chain_verified": verify_chain(events) == len(events)},
    }


def build_experiment(results: dict[Arm, RunResult], sensitivity: list[dict[str, Any]],
                     generated_by: str) -> dict[str, Any]:
    urudhi = results[Arm.URUDHI]
    arms = {arm.value: arm_metrics(r) for arm, r in results.items()}
    base, none, ours = arms.get("baseline"), arms.get("no_action"), arms["urudhi"]
    start = urudhi.config.start
    days = [(start + timedelta(days=i)).isoformat() for i in range(urudhi.config.days + 1)]
    series: dict[str, list[int]] = {}
    for arm, r in results.items():
        per_day = timeline(r)
        running, cumulative = 0, []
        for day in days:
            running += per_day.get(day, 0)
            cumulative.append(running)
        series[arm.value] = cumulative

    def uplift(a: dict | None, b: dict | None, key: str) -> int | None:
        return None if a is None or b is None else a[key] - b[key]

    return {
        "generated_by": generated_by,
        "seed": urudhi.config.seed, "days": urudhi.config.days, "count": urudhi.config.count,
        "brain": urudhi.brain_name, "policy": urudhi.policy.model_dump(mode="json"),
        "arms": arms,
        "uplift": {
            "urudhi_vs_baseline_paise": uplift(ours, base, "recovered_paise"),
            "urudhi_vs_baseline_points": (round((ours["recovery_rate"] - base["recovery_rate"]) * 100, 2)
                                          if base else None),
            "urudhi_vs_no_action_paise": uplift(ours, none, "recovered_paise"),
            "urudhi_vs_no_action_points": (round((ours["recovery_rate"] - none["recovery_rate"]) * 100, 2)
                                           if none else None),
            "net_urudhi_vs_baseline_paise": uplift(ours, base, "net_recovered_paise"),
        },
        "attribution": {
            "window_days": ATTRIBUTION_WINDOW_DAYS, "rule": ATTRIBUTION_RULE,
            "arms": {arm.value: {k: v for k, v in attribution(r).items() if k != "payments"}
                     for arm, r in results.items() if arm is not Arm.NO_ACTION},
        },
        "timeline": {"days": days, **series},
        "days_to_recovery": {
            arm: {"median": m["days_to_recovery_median"], "mean": m["days_to_recovery_mean"],
                  "histogram": m["days_to_recovery_histogram"]}
            for arm, m in arms.items()
        },
        "sensitivity": sensitivity,
        "caveats": [
            "Every figure is a simulation result from the persona model in sim/personas.py; "
            "no real-world recovery rate is claimed.",
            "Debtor behaviour reacts to the intervention received (link, discount, installment "
            "plan, request for a promise, contact fatigue); the trait ranges are stated in "
            "code and can be disputed.",
            "The three arms start from byte-identical portfolios and debtor traits; differences "
            "between arms are differences in strategy under this model, not causal evidence "
            "about real debtors.",
            "Recovered = sum of webhook-observed payments only. Discount cost = amounts waived "
            "under settled concessions. Net = recovered − waived.",
            ATTRIBUTION_RULE,
            "Sensitivity runs use the deterministic mock brain so the policy effect is isolated "
            "from LLM variance.",
            "With --brain claude the Urudhi arm is not byte-reproducible: model outputs vary.",
        ],
    }


def sensitivity_grid() -> list[tuple[str, int]]:
    return [
        ("escalate_after_broken_promises", 1), ("escalate_after_broken_promises", 2),
        ("escalate_after_broken_promises", 3),
        ("max_attempts_per_invoice", 4), ("max_attempts_per_invoice", 6),
        ("max_attempts_per_invoice", 8),
        ("max_discount_bps", 0), ("max_discount_bps", 300), ("max_discount_bps", 500),
        ("max_discount_bps", 1000),
    ]


def sensitivity_row(parameter: str, value: int, result: RunResult) -> dict[str, Any]:
    m = arm_metrics(result)
    return {
        "parameter": parameter, "value": value,
        "recovered_paise": m["recovered_paise"], "recovery_rate": m["recovery_rate"],
        "net_recovered_paise": m["net_recovered_paise"],
        "messages_sent": m["contact_attempts"], "escalations": m["escalations"],
        "discount_cost_paise": m["discount_cost_paise"], "stop_contacts": m["stop_contacts"],
        "promises_broken": m["promises_broken"],
    }


def summarize_for_stdout(experiment: dict[str, Any]) -> str:
    arms = experiment["arms"]
    order = [a for a in ("no_action", "baseline", "urudhi") if a in arms]
    lines = [f"{'metric':<28}" + "".join(f"{arms[a]['label'][:22]:>24}" for a in order)]
    rows = [
        ("amount at risk", lambda m: format_inr(m["amount_at_risk_paise"])),
        ("recovered (observed)", lambda m: format_inr(m["recovered_paise"])),
        ("recovery rate", lambda m: f"{m['recovery_rate']:.1%}"),
        ("discount cost", lambda m: format_inr(m["discount_cost_paise"])),
        ("net recovered", lambda m: format_inr(m["net_recovered_paise"])),
        ("invoices paid", lambda m: str(m["invoices_paid"])),
        ("days to recovery (median)", lambda m: str(m["days_to_recovery_median"])),
        ("promises kept / broken", lambda m: f"{m['promises_kept']} / {m['promises_broken']}"),
        ("offers made / accepted", lambda m: f"{m['offers_made']} / {m['offers_accepted']}"),
        ("contact attempts", lambda m: str(m["contact_attempts"])),
        ("escalations / disputes", lambda m: f"{m['escalations']} / {m['disputes']}"),
        ("stop-contacts", lambda m: str(m["stop_contacts"])),
    ]
    for label, fn in rows:
        lines.append(f"{label:<28}" + "".join(f"{fn(arms[a]):>24}" for a in order))
    up = experiment["uplift"]
    if up["urudhi_vs_baseline_paise"] is not None:
        lines.append(f"uplift vs baseline: {format_inr(up['urudhi_vs_baseline_paise'])} "
                     f"({up['urudhi_vs_baseline_points']:+.1f} pts); "
                     f"net {format_inr(up['net_urudhi_vs_baseline_paise'])}")
    if up["urudhi_vs_no_action_paise"] is not None:
        lines.append(f"uplift vs no action: {format_inr(up['urudhi_vs_no_action_paise'])} "
                     f"({up['urudhi_vs_no_action_points']:+.1f} pts)")
    return "\n".join(lines)


def run_days(start: date, days: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(days)]
