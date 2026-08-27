"""Chase prioritization.

Given the day's chaseable invoices, decide who to contact first. The score is
a deliberate, explainable weighted sum — not a model — because a number that
decides who gets a collections message must be defensible line by line. Every
score ships with its component breakdown for the audit log and the dashboard.

Components (each normalized to [0, 1]):

* ``value``     — balance at stake, log-scaled so one lakh doesn't drown
                  twenty small invoices worth two.
* ``urgency``   — days overdue, saturating at 180 days.
* ``credibility`` — the debtor's commitment record: missed commitments and
                  broken promises push urgency up (act now, words aren't
                  working); fulfilled commitments pull it down (their word
                  has been good); a debtor currently holding a live
                  commitment or open promise scores near zero (let it run —
                  chasing mid-commitment burns goodwill).
* ``fatigue``   — attempts already spent, discounting invoices the agent has
                  hammered without result.
"""

from __future__ import annotations

import math
from datetime import date

from pydantic import BaseModel, Field

from urudhi.ledger.commitments import profile_for
from urudhi.ledger.models import Invoice, PaymentCommitment, PromiseState, PromiseToPay
from urudhi.ledger.money import PAISE_PER_RUPEE

# Component weights; kept as data so the config ships with published results.
WEIGHTS = {"value": 0.35, "urgency": 0.30, "credibility": 0.25, "fatigue": 0.10}

_VALUE_SATURATION_RUPEES = 10_00_000   # ₹10 lakh -> value component ~1.0
_URGENCY_SATURATION_DAYS = 180


class PriorityScore(BaseModel):
    invoice_id: str
    score: float = Field(ge=0.0, le=1.0)
    components: dict[str, float]

    def explain(self) -> str:
        parts = ", ".join(f"{k}={v:.2f}" for k, v in self.components.items())
        return f"score {self.score:.3f} ({parts})"


def _value_component(balance_paise: int) -> float:
    rupees = max(balance_paise, 0) / PAISE_PER_RUPEE
    if rupees <= 0:
        return 0.0
    return min(1.0, math.log1p(rupees) / math.log1p(_VALUE_SATURATION_RUPEES))


def _urgency_component(days_overdue: int) -> float:
    return min(1.0, max(days_overdue, 0) / _URGENCY_SATURATION_DAYS)


def _credibility_component(promises: list[PromiseToPay],
                           commitments: list[PaymentCommitment] | None = None) -> float:
    """0 = leave alone (a commitment is running), 1 = words have stopped working.

    With commitment history the record of *accepted arrangements* drives the
    number: each missed commitment adds 0.25, each fulfilled one subtracts
    0.15 (floored so a perfect record still gets a routine 0.2). Without any
    commitments the older promise-only rule applies.
    """
    if any(p.state is PromiseState.OPEN for p in promises):
        return 0.05
    if commitments and any(c.live for c in commitments):
        return 0.05
    partly = sum(1 for p in promises if p.state is PromiseState.PARTIALLY_KEPT)
    profile = profile_for(commitments or [])
    if profile.fulfilled or profile.missed:
        score = 0.5 + 0.25 * profile.missed - 0.15 * profile.fulfilled + 0.10 * partly
        return round(min(1.0, max(0.2, score)), 4)
    broken = sum(1 for p in promises if p.state is PromiseState.BROKEN)
    if broken == 0 and partly == 0:
        return 0.5  # no history either way
    return min(1.0, 0.5 + 0.25 * broken + 0.10 * partly)


def _fatigue_component(attempts: int, max_attempts: int) -> float:
    """1 = fresh invoice, 0 = attempts exhausted."""
    if max_attempts <= 0:
        return 0.0
    return max(0.0, 1.0 - attempts / max_attempts)


def score_invoice(
    invoice: Invoice,
    promises: list[PromiseToPay],
    attempts: int,
    max_attempts: int,
    today: date,
    commitments: list[PaymentCommitment] | None = None,
) -> PriorityScore:
    components = {
        "value": _value_component(invoice.balance),
        "urgency": _urgency_component(invoice.days_overdue(today)),
        "credibility": _credibility_component(promises, commitments),
        "fatigue": _fatigue_component(attempts, max_attempts),
    }
    score = sum(WEIGHTS[name] * value for name, value in components.items())
    return PriorityScore(
        invoice_id=invoice.id,
        score=round(score, 6),
        components={k: round(v, 4) for k, v in components.items()},
    )


def rank(scores: list[PriorityScore]) -> list[PriorityScore]:
    """Highest score first; invoice id breaks ties deterministically."""
    return sorted(scores, key=lambda s: (-s.score, s.invoice_id))
