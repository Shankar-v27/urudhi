"""Ledger domain models.

The ledger is the source of truth for three things:

* what is owed (``Invoice``),
* what was said about paying it (``PromiseToPay`` — typed, dated,
  confidence-scored, with the debtor's verbatim words as evidence), and
* what actually arrived on the rails (``Payment`` — only ever created from an
  observed Razorpay webhook, never from an agent's claim).

Keeping "said" and "arrived" as separate record types is deliberate: recovery
is *measured* as the sum of Payments, while Promises exist to be tracked as
kept or broken. The agent's credibility model lives in that gap.
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
    PAID = "paid"                      # balance cleared on the rails
    DISPUTED = "disputed"              # debtor contests the invoice -> human
    ESCALATED = "escalated"            # handed to a human (policy or repeated breaks)
    STOP_CONTACT = "stop_contact"      # debtor asked us to stop; honored, terminal


class PromiseState(enum.StrEnum):
    OPEN = "open"                # promised date not yet passed
    KEPT = "kept"                # matching payment observed by promised date
    PARTIALLY_KEPT = "partially_kept"  # some payment observed by promised date
    BROKEN = "broken"            # promised date passed, no matching payment
    SUPERSEDED = "superseded"    # replaced by a newer promise on the invoice
    WITHDRAWN = "withdrawn"      # debtor retracted before the promised date


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
    amount_paid: Paise = 0
    razorpay_invoice_id: str | None = None
    razorpay_virtual_account_id: str | None = None

    @property
    def balance(self) -> Paise:
        return self.amount - self.amount_paid

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


class Payment(BaseModel):
    """Money observed on the rails. Created only from Razorpay webhook events."""

    id: str
    invoice_id: str
    amount: Paise
    method: str                       # upi / neft / imps / card / ...
    razorpay_payment_id: str
    razorpay_event_id: str            # webhook event that evidenced this payment
    observed_at: datetime
