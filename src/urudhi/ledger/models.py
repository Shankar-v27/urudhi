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
    DECLINED = "declined"        # recorded as said, but policy refused it as a commitment


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
    commitment_id: str | None = None  # the commitment this money was applied to, if any
    matched_by: str | None = None     # "instrument" (link/VA carried the commitment id) or "invoice"


class CommitmentSource(enum.StrEnum):
    PROMISE = "promise"            # the debtor's own words, interpreted and policy-approved
    CONCESSION = "concession"      # acceptance of a policy-approved discount settlement
    INSTALLMENT = "installment"    # one dated installment of an accepted plan
    HUMAN = "human"                # an arrangement a person approved after escalation


class CommitmentState(enum.StrEnum):
    ACTIVE = "active"                          # approved, instrument issued, deadline ahead
    PARTIALLY_FULFILLED = "partially_fulfilled"  # some rail money before the deadline
    FULFILLED = "fulfilled"                    # committed amount observed on the rails
    MISSED = "missed"                          # deadline passed without the committed amount
    CANCELLED = "cancelled"                    # stop-contact / dispute / escalation / human
    SUPERSEDED = "superseded"                  # replaced by a newer commitment on the invoice


class InstrumentType(enum.StrEnum):
    PAYMENT_LINK = "payment_link"
    VIRTUAL_ACCOUNT = "virtual_account"


class InstrumentMode(enum.StrEnum):
    RAZORPAY_TEST = "razorpay_test"   # issued by Razorpay's test-mode API; the URL is theirs
    SANDBOX = "sandbox"               # issued by the fake rail; no checkout exists anywhere


class RecordOrigin(enum.StrEnum):
    LIVE_TEST = "live_test"           # created against Razorpay test mode by the real app
    SIMULATION = "simulation"         # created by the synthetic batch runner


class PaymentCommitment(BaseModel):
    """What Urudhi *accepted* as a bounded, executable recovery arrangement.

    Three things stay apart on purpose:

    * a :class:`PromiseToPay` is what the debtor **said** (verbatim, scored);
    * a ``PaymentCommitment`` is what deterministic policy **accepted** from
      it — an exact amount, an exact deadline, and the rail-side instrument
      (a Razorpay Payment Link tagged with this id) the debtor can pay through;
    * a :class:`Payment` is what the rails **verified**.

    A commitment never moves money. It is fulfilled only by ``Payment`` rows
    matched to it by the webhook path; it is missed by the calendar.
    """

    id: str
    invoice_id: str
    debtor_id: str
    promise_id: str | None = None        # the promise it was accepted from, if any
    concession_id: str | None = None     # the concession it executes, if any
    installment_index: int | None = None  # 1-based position in an installment plan
    source: CommitmentSource
    committed_amount: Paise
    currency: str = "INR"
    due_on: date                          # last calendar day to pay (policy timezone)
    due_at: datetime                      # end of that day, timezone-aware
    state: CommitmentState = CommitmentState.ACTIVE
    instrument_type: InstrumentType | None = None
    instrument_id: str | None = None
    payment_url: str | None = None        # the rail's own customer-facing URL, verbatim
    instrument_mode: InstrumentMode | None = None  # explicit: which rail issued it
    instrument_failed: bool = False       # the rail refused; nothing was issued
    instrument_failure: str = ""
    origin: RecordOrigin | None = None    # live_test / simulation — set when created
    instrument_sent: bool = False         # the debtor has been told about the instrument
    reminder_sent: bool = False
    created_at: datetime
    accepted_at: datetime | None = None   # when the debtor's acceptance was interpreted
    fulfilled_at: datetime | None = None
    missed_at: datetime | None = None
    resolved_at: datetime | None = None
    amount_received: Paise = 0            # rail money applied to this commitment
    days_late: int = 0                    # >0 when fulfilled after the deadline
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    evidence: str = ""                    # the debtor's verbatim words
    rationale: str = ""                   # policy's one-line reason for accepting
    cancel_reason: str = ""

    @property
    def amount_remaining(self) -> Paise:
        return max(0, self.committed_amount - self.amount_received)

    @property
    def live(self) -> bool:
        return self.state in (CommitmentState.ACTIVE, CommitmentState.PARTIALLY_FULFILLED)
