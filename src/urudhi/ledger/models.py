"""Ledger domain models.

The ledger is the source of truth for four things:

* what is owed (``Invoice``),
* what was said about paying it (``PromiseToPay`` — typed, dated,
  confidence-scored, with the debtor's verbatim words as evidence),
* what the operator's policy agreed to give up to get paid (``Concession`` —
  a discount settlement or an installment schedule, always policy-gated), and
* what actually arrived on the rails (``Payment`` — only ever created from an
  observed Razorpay webhook, never from an agent's claim).

Keeping "said", "conceded" and "arrived" as separate record types is
deliberate: recovery is *measured* as the sum of Payments, discounts are
*costed* as ``amount_waived``, and Promises exist to be tracked as kept or
broken. The agent's credibility model lives in that gap.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from pydantic import BaseModel, Field

from urudhi.ledger.money import Paise


class Channel(enum.StrEnum):
    """Where an interaction with a debtor happened."""

    WHATSAPP = "whatsapp"
    EMAIL = "email"
    VOICE = "voice"
    SYSTEM = "system"  # internal events (webhooks, scheduled checks)


class InvoiceState(enum.StrEnum):
    OUTSTANDING = "outstanding"        # overdue, no live promise
    PROMISED = "promised"              # an OPEN promise-to-pay exists
    PARTIALLY_PAID = "partially_paid"  # some money observed, balance remains
    PAID = "paid"                      # balance cleared on the rails (or settled)
    DISPUTED = "disputed"              # debtor contests the invoice -> human
    ESCALATED = "escalated"            # handed to a human (policy or repeated breaks)
    STOP_CONTACT = "stop_contact"      # debtor asked us to stop; honored, terminal
    CLOSED = "closed"                  # a human closed / wrote it off; terminal


class PromiseState(enum.StrEnum):
    OPEN = "open"                # promised date not yet passed
    KEPT = "kept"                # matching payment observed by promised date
    PARTIALLY_KEPT = "partially_kept"  # some payment observed by promised date
    BROKEN = "broken"            # promised date passed, no matching payment
    SUPERSEDED = "superseded"    # replaced by a newer promise on the invoice
    WITHDRAWN = "withdrawn"      # debtor retracted before the promised date


class ConcessionType(enum.StrEnum):
    DISCOUNT = "discount"          # settle the balance at a reduced amount by a date
    INSTALLMENTS = "installments"  # pay the balance across a dated schedule


class ConcessionState(enum.StrEnum):
    OFFERED = "offered"      # put in front of the debtor; not yet acknowledged
    ACCEPTED = "accepted"    # debtor agreed (interpreted from their reply)
    SETTLED = "settled"      # the rails observed enough money under its terms
    EXPIRED = "expired"      # pay-by passed without settlement; nothing waived
    BROKEN = "broken"        # an installment came due and was not met
    WITHDRAWN = "withdrawn"  # superseded or cancelled by policy / a human


class Debtor(BaseModel):
    """A business that owes money. Contact rules live in policy, not here."""

    id: str
    name: str
    contact_name: str
    phone: str
    email: str
    preferred_channel: Channel = Channel.WHATSAPP
    language: str = "en"  # BCP-47-ish; "ta", "hi", "en" in the synthetic set


class Invoice(BaseModel):
    id: str
    debtor_id: str
    number: str                      # human-facing invoice number
    amount: Paise
    issued_on: date
    due_on: date
    state: InvoiceState = InvoiceState.OUTSTANDING
    amount_paid: Paise = 0           # money observed on the rails
    amount_waived: Paise = 0         # discount given up under a settled concession
    razorpay_invoice_id: str | None = None
    razorpay_virtual_account_id: str | None = None
    human_released_at: datetime | None = None  # last time a human returned it to automation

    @property
    def balance(self) -> Paise:
        """What is still owed: face value less money observed and discount waived."""
        return self.amount - self.amount_paid - self.amount_waived

    def days_overdue(self, today: date) -> int:
        return max(0, (today - self.due_on).days)


class PromiseToPay(BaseModel):
    """A dated, evidenced commitment extracted from a debtor conversation.

    ``confidence`` scores how firm the commitment actually was, judged from the
    debtor's own words: an explicit amount and date ("I will pay ₹50,000 by
    Friday") scores high; a vague deflection ("will see next week") scores low
    and is recorded as such rather than dressed up as a commitment.
    """

    id: str
    invoice_id: str
    debtor_id: str
    amount: Paise
    promised_on: date                 # the date the debtor committed to pay by
    made_at: datetime                 # when the promise was made
    channel: Channel
    verbatim: str                     # the debtor's own words, unedited
    confidence: float = Field(ge=0.0, le=1.0)
    state: PromiseState = PromiseState.OPEN
    resolved_at: datetime | None = None


class Installment(BaseModel):
    due_on: date
    amount: Paise


class Concession(BaseModel):
    """A policy-approved thing the operator gives up to get paid.

    For a DISCOUNT, ``settlement_amount`` is the reduced amount that clears the
    invoice if the rails observe it by ``pay_by``; the remainder is *waived*
    only at that moment, never before. For INSTALLMENTS, ``installments`` is
    the dated schedule and ``settlement_amount`` is the full balance.
    """

    id: str
    invoice_id: str
    debtor_id: str
    type: ConcessionType
    state: ConcessionState = ConcessionState.OFFERED
    discount_bps: int = 0
    balance_at_offer: Paise
    settlement_amount: Paise
    installments: list[Installment] = Field(default_factory=list)
    pay_by: date                      # discount: settle by; installments: last due date
    offered_at: datetime
    accepted_at: datetime | None = None
    resolved_at: datetime | None = None
    payment_link_url: str | None = None
    rationale: str = ""               # the structured reason the offer was decided

    @property
    def live(self) -> bool:
        return self.state in (ConcessionState.OFFERED, ConcessionState.ACCEPTED)

    @property
    def waivable(self) -> Paise:
        return max(0, self.balance_at_offer - self.settlement_amount)


class Payment(BaseModel):
    """Money observed on the rails. Created only from Razorpay webhook events."""

    id: str
    invoice_id: str
    amount: Paise
    method: str                       # upi / neft / imps / card / ...
    razorpay_payment_id: str
    razorpay_event_id: str            # webhook event that evidenced this payment
    observed_at: datetime
