"""Interventions: what the agent may propose, and the facts it proposes from.

The brain (LLM or mock) sees a :class:`DecisionContext` — structured facts
about the invoice, the debtor's promise record, prior interventions and the
limits policy delegates — and returns an :class:`InterventionRecommendation`.
The recommendation is a *proposal*: nothing in it reaches a debtor, a ledger
or a rail until :func:`urudhi.agent.policy.decide_intervention` has validated
it deterministically and possibly modified or blocked it.

Only interventions the domain genuinely implements exist here.
"""

from __future__ import annotations

import enum
from datetime import date

from pydantic import BaseModel, Field

from urudhi.ledger.money import Paise


class InterventionKind(enum.StrEnum):
    NO_ACTION = "no_action"                # nothing today (e.g. contacted very recently)
    REMINDER = "reminder"                  # courteous nudge with the facts
    PAYMENT_LINK = "payment_link"          # nudge carrying a rail-side link for the balance
    REQUEST_PROMISE = "request_promise"    # ask explicitly for an amount and a date
    DISCOUNT_OFFER = "discount_offer"      # early-settlement discount (policy-capped)
    INSTALLMENT_OFFER = "installment_offer"  # dated schedule (policy-capped)
    WAIT_FOR_PROMISE = "wait_for_promise"  # a promise / plan is running; don't chase over it
    ESCALATE_HUMAN = "escalate_human"      # hand to a person now


# Interventions that put words in front of the debtor.
CONTACTING = frozenset({
    InterventionKind.REMINDER, InterventionKind.PAYMENT_LINK,
    InterventionKind.REQUEST_PROMISE, InterventionKind.DISCOUNT_OFFER,
    InterventionKind.INSTALLMENT_OFFER,
})


class PriorIntervention(BaseModel):
    """One earlier intervention and what came of it, for the brain's context."""

    on: date
    kind: InterventionKind
    outcome: str  # e.g. "no reply", "promise recorded", "paid ₹x", "disputed"


class DecisionContext(BaseModel):
    """Everything the brain may consider when proposing an intervention.

    Facts only — no prose from the debtor except their last reply's typed
    intent, so a hostile reply can't steer the proposal through the context.
    """

    invoice_number: str
    balance: Paise
    original_amount: Paise
    days_overdue: int
    today: date
    invoice_state: str
    attempts_total: int
    attempts_allowed: int
    days_since_last_contact: int | None = None
    promises_kept: int = 0
    promises_broken: int = 0
    promises_partially_kept: int = 0
    open_promise_amount: Paise | None = None
    open_promise_on: date | None = None
    last_intent: str | None = None          # typed intent of the most recent reply
    last_reply_summary: str = ""            # the brain's own one-line summary of it
    live_concession: str | None = None      # "discount 3% by 2026-09-01" etc.
    concession_history: list[str] = Field(default_factory=list)
    prior_interventions: list[PriorIntervention] = Field(default_factory=list)
    # Delegated limits, so the brain proposes inside them rather than at random.
    max_discount_bps: int
    max_installments: int
    min_installment: Paise
    max_horizon_days: int
    payment_links_available: bool = False


class InterventionRecommendation(BaseModel):
    """What the brain proposes. Validated by policy before anything happens."""

    action: InterventionKind
    rationale: list[str] = Field(default_factory=list)  # short structured reasons
    proposed_discount_bps: int | None = None
    proposed_installments: int | None = None
    proposed_pay_by: date | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
