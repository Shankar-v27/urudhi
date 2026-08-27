"""Bounded authority: the deterministic gates around the negotiating agent.

The LLM proposes; policy disposes. Every consequential action — contacting a
debtor, making a concession, accepting a promise, escalating — passes through
a gate here, and every gate returns a :class:`GateDecision` with a
machine-checkable reason, so "every money action explainable, bounded and
gated" is an architecture property, not a prompt instruction. Gates are pure
functions of config and facts; they contain no model calls and no I/O.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from urudhi.agent.intervention import (
    CONTACTING,
    PROPOSABLE,
    DecisionContext,
    InterventionKind,
    InterventionRecommendation,
)
from urudhi.ledger.models import Channel, Concession, Installment, Invoice, InvoiceState
from urudhi.ledger.money import Paise, format_inr

BPS_DENOMINATOR = 10_000


class PolicyConfig(BaseModel):
    """The full authority Urudhi's operator delegates to the agent.

    Defaults are deliberately conservative; the batch runner ships its config
    alongside results so every published number states the authority it ran with.
    """

    max_discount_bps: int = Field(default=500, ge=0, le=2_000)   # ≤ 5% early-payment discount
    max_installments: int = Field(default=3, ge=1, le=12)
    min_installment: Paise = Field(default=100_000, ge=0)        # ₹1,000 floor per installment
    max_promise_horizon_days: int = Field(default=30, ge=1)
    min_discount_days_overdue: int = Field(default=14, ge=0)     # no discounts on fresh debt
    min_installment_balance: Paise = Field(default=500_000, ge=0)  # ₹5,000+ to split

    # Executable commitments: what a debtor's promise may be turned into.
    allow_partial_commitments: bool = True
    min_commitment: Paise = Field(default=10_000, ge=0)          # ₹100 floor
    commitment_reminder_days_before: int = Field(default=1, ge=0)  # 0 = never remind

    timezone: str = "Asia/Kolkata"        # contact hours are judged in THIS zone
    contact_open: time = time(10, 0)      # RBI-style courtesy window
    contact_close: time = time(19, 0)
    max_attempts_per_invoice: int = Field(default=6, ge=1)
    max_attempts_per_day: int = Field(default=1, ge=1)
    min_days_between_contacts: int = Field(default=2, ge=0)
    escalate_after_broken_promises: int = Field(default=2, ge=1)
    # An LLM-proposed escalation is honored only once the debtor has this many
    # broken promises — the model may not remove an invoice from automation on a whim.
    recommended_escalation_min_broken: int = Field(default=1, ge=0)
    allowed_channels: frozenset[Channel] = frozenset({Channel.WHATSAPP, Channel.EMAIL})

    def local(self, now: datetime) -> datetime:
        """Convert an aware timestamp into the policy zone. Naive input is an error."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError(
                "policy needs a timezone-aware datetime; a naive value would be judged "
                "in whatever zone the caller happened to mean"
            )
        return now.astimezone(ZoneInfo(self.timezone))


class GateDecision(BaseModel):
    allowed: bool
    gate: str
    reason: str

    @classmethod
    def allow(cls, gate: str, reason: str) -> GateDecision:
        return cls(allowed=True, gate=gate, reason=reason)

    @classmethod
    def block(cls, gate: str, reason: str) -> GateDecision:
        return cls(allowed=False, gate=gate, reason=reason)


class OfferType(enum.StrEnum):
    FULL_PAYMENT = "full_payment"          # pay the balance, no concession
    DISCOUNT = "discount"                  # early-payment discount on the balance
    INSTALLMENTS = "installments"          # split the balance across dates


class Offer(BaseModel):
    """A concession the agent wants to put in front of a debtor."""

    type: OfferType
    invoice_id: str
    discount_bps: int = 0
    installment_count: int = 1
    pay_by: date                           # last date the offer asks money to arrive

    def settlement_amount(self, balance: Paise) -> Paise:
        if self.type is OfferType.DISCOUNT:
            return balance * (BPS_DENOMINATOR - self.discount_bps) // BPS_DENOMINATOR
        return balance

    def schedule(self, balance: Paise, today: date) -> list[Installment]:
        """Equal installments, evenly spaced from tomorrow-ish to ``pay_by``."""
        if self.type is not OfferType.INSTALLMENTS:
            return []
        n = self.installment_count
        span = max(1, (self.pay_by - today).days)
        per = balance // n
        schedule = []
        for i in range(1, n + 1):
            due = today + timedelta(days=max(1, span * i // n))
            amount = per if i < n else balance - per * (n - 1)
            schedule.append(Installment(due_on=due, amount=amount))
        return schedule


class ContactFacts(BaseModel):
    """Everything the contact gate needs to know, gathered by the caller."""

    now: datetime
    channel: Channel
    attempts_total: int
    attempts_today: int
    broken_promises: int
    days_since_last_contact: int | None = None


def check_contact(invoice: Invoice, facts: ContactFacts, config: PolicyConfig) -> GateDecision:
    """May the agent contact this debtor about this invoice, now, on this channel?"""
    gate = "contact"
    if invoice.state is InvoiceState.STOP_CONTACT:
        return GateDecision.block(gate, "debtor asked us to stop; stop-contact is terminal")
    if invoice.state in (InvoiceState.DISPUTED, InvoiceState.ESCALATED):
        return GateDecision.block(gate, f"invoice is {invoice.state}; a human owns it now")
    if invoice.state is InvoiceState.CLOSED:
        return GateDecision.block(gate, "invoice was closed by a human; nothing to chase")
    if invoice.state is InvoiceState.PAID:
        return GateDecision.block(gate, "invoice is settled; there is nothing to chase")
    if facts.channel not in config.allowed_channels:
        return GateDecision.block(gate, f"channel {facts.channel} is not in the allowed set")
    local = config.local(facts.now)
    if not (config.contact_open <= local.time() < config.contact_close):
        return GateDecision.block(
            gate,
            f"outside contact hours {config.contact_open:%H:%M}–{config.contact_close:%H:%M} "
            f"{config.timezone} (local time {local:%H:%M})",
        )
    if facts.attempts_today >= config.max_attempts_per_day:
        return GateDecision.block(gate, "daily attempt limit reached for this debtor")
    if facts.attempts_total >= config.max_attempts_per_invoice:
        return GateDecision.block(
            gate,
            f"attempt limit ({config.max_attempts_per_invoice}) exhausted; escalate instead",
        )
    if (
        facts.days_since_last_contact is not None
        and facts.days_since_last_contact < config.min_days_between_contacts
    ):
        return GateDecision.block(
            gate,
            f"contacted {facts.days_since_last_contact} day(s) ago; minimum spacing is "
            f"{config.min_days_between_contacts} days",
        )
    return GateDecision.allow(gate, "within contact hours, spacing and attempt limits")


def check_offer(invoice: Invoice, offer: Offer, today: date, config: PolicyConfig) -> GateDecision:
    """Is this concession within the agent's delegated authority?"""
    gate = "offer"
    if offer.invoice_id != invoice.id:
        return GateDecision.block(gate, "offer references a different invoice")
    if invoice.state not in (
        InvoiceState.OUTSTANDING, InvoiceState.PROMISED, InvoiceState.PARTIALLY_PAID
    ):
        return GateDecision.block(gate, f"no offers on an invoice in state {invoice.state}")
    if offer.pay_by <= today:
        return GateDecision.block(gate, "offer must give the debtor at least a day to pay")
    horizon = (offer.pay_by - today).days
    if horizon > config.max_promise_horizon_days:
        return GateDecision.block(
            gate,
            f"pay-by {horizon} days out exceeds the {config.max_promise_horizon_days}-day horizon",
        )

    if offer.type is OfferType.DISCOUNT:
        if offer.discount_bps <= 0:
            return GateDecision.block(gate, "discount offer with no discount")
        if offer.discount_bps > config.max_discount_bps:
            return GateDecision.block(
                gate,
                f"discount {offer.discount_bps}bps exceeds delegated cap "
                f"of {config.max_discount_bps}bps",
            )
        if invoice.days_overdue(today) < config.min_discount_days_overdue:
            return GateDecision.block(
                gate,
                f"no discounts before {config.min_discount_days_overdue} days overdue",
            )
    elif offer.discount_bps != 0:
        return GateDecision.block(gate, f"{offer.type} offers may not carry a discount")

    if offer.type is OfferType.INSTALLMENTS:
        if offer.installment_count < 2:
            return GateDecision.block(gate, "installment offer needs at least 2 installments")
        if offer.installment_count > config.max_installments:
            return GateDecision.block(
                gate,
                f"{offer.installment_count} installments exceeds cap "
                f"of {config.max_installments}",
            )
        per_installment = invoice.balance // offer.installment_count
        if per_installment < config.min_installment:
            return GateDecision.block(
                gate,
                f"installments of {format_inr(per_installment)} fall below the "
                f"{format_inr(config.min_installment)} floor",
            )
        if invoice.balance < config.min_installment_balance:
            return GateDecision.block(
                gate,
                f"balance {format_inr(invoice.balance)} is below the "
                f"{format_inr(config.min_installment_balance)} installment threshold",
            )
    elif offer.installment_count != 1:
        return GateDecision.block(gate, f"{offer.type} offers must be single-payment")

    return GateDecision.allow(gate, f"{offer.type} within delegated authority")


def should_escalate(facts: ContactFacts, config: PolicyConfig) -> GateDecision:
    """Has this invoice earned a human? (Checked after every resolved promise.)"""
    gate = "escalation"
    if facts.broken_promises >= config.escalate_after_broken_promises:
        return GateDecision.allow(
            gate,
            f"{facts.broken_promises} broken promises ≥ threshold "
            f"of {config.escalate_after_broken_promises}",
        )
    if facts.attempts_total >= config.max_attempts_per_invoice:
        return GateDecision.allow(gate, "contact attempts exhausted without recovery")
    return GateDecision.block(gate, "within thresholds; agent continues")


# -- intervention decision --------------------------------------------------

class Decision(BaseModel):
    """Policy's final word on a brain's proposal.

    ``modified`` is True when the final action differs from the proposal —
    a blocked concession degrades to a plain reminder, never to a softer
    concession; a proposal over a running promise degrades to waiting.
    """

    proposed: InterventionRecommendation
    final: InterventionKind
    offer: Offer | None = None
    gates: list[GateDecision] = Field(default_factory=list)
    modified: bool = False
    reasons: list[str] = Field(default_factory=list)  # human-readable, structured


def decide_intervention(
    invoice: Invoice,
    context: DecisionContext,
    proposal: InterventionRecommendation,
    facts: ContactFacts,
    config: PolicyConfig,
) -> Decision:
    """Turn a proposal into an allowed action, modifying or blocking as policy requires."""
    today = context.today
    gates: list[GateDecision] = []
    reasons: list[str] = []
    final = proposal.action
    offer: Offer | None = None

    def degrade(to: InterventionKind, why: str) -> None:
        nonlocal final
        reasons.append(f"{final} → {to}: {why}")
        final = to

    if final not in PROPOSABLE:
        degrade(InterventionKind.REMINDER, "lifecycle messages are issued by the loop, not proposed")

    # 1. A running commitment always wins: never chase over a live promise/plan.
    running = (
        context.open_promise_on is not None
        or context.active_commitment is not None
        or (context.live_concession is not None
            and context.live_concession.startswith("installments"))
    )
    if running and final is not InterventionKind.ESCALATE_HUMAN:
        if final is not InterventionKind.WAIT_FOR_PROMISE:
            degrade(InterventionKind.WAIT_FOR_PROMISE, "a promise or plan is still running")
        gates.append(GateDecision.allow("commitment", "waiting on the debtor's own word"))
        return Decision(proposed=proposal, final=final, gates=gates,
                        modified=final is not proposal.action, reasons=reasons)

    # 2. Escalation is a policy privilege, not a model whim.
    if final is InterventionKind.ESCALATE_HUMAN:
        earned = should_escalate(facts, config)
        if earned.allowed:
            gates.append(earned)
        elif facts.broken_promises >= config.recommended_escalation_min_broken and (
            proposal.confidence >= 0.7
        ):
            gates.append(GateDecision.allow(
                "escalation",
                f"recommended with confidence {proposal.confidence:.2f} after "
                f"{facts.broken_promises} broken promise(s)",
            ))
        else:
            gates.append(GateDecision.block(
                "escalation",
                "proposal to escalate rejected: no broken promises and thresholds not met",
            ))
            degrade(InterventionKind.REMINDER, "escalation not earned under policy")

    # 3. Anything that contacts the debtor needs the contact gate.
    if final in CONTACTING:
        contact = check_contact(invoice, facts, config)
        gates.append(contact)
        if not contact.allowed:
            degrade(InterventionKind.NO_ACTION, contact.reason)
            return Decision(proposed=proposal, final=final, gates=gates,
                            modified=True, reasons=reasons)

    # 4. Concessions must be priced inside delegated authority.
    if final is InterventionKind.DISCOUNT_OFFER:
        bps = proposal.proposed_discount_bps or 0
        pay_by = proposal.proposed_pay_by or today + timedelta(days=7)
        offer = Offer(type=OfferType.DISCOUNT, invoice_id=invoice.id,
                      discount_bps=bps, pay_by=pay_by)
        verdict = check_offer(invoice, offer, today, config)
        gates.append(verdict)
        if not verdict.allowed:
            offer = None
            degrade(InterventionKind.REMINDER, verdict.reason)
    elif final is InterventionKind.INSTALLMENT_OFFER:
        n = proposal.proposed_installments or 2
        pay_by = proposal.proposed_pay_by or today + timedelta(days=min(28, config.max_promise_horizon_days))
        offer = Offer(type=OfferType.INSTALLMENTS, invoice_id=invoice.id,
                      installment_count=n, pay_by=pay_by)
        verdict = check_offer(invoice, offer, today, config)
        gates.append(verdict)
        if not verdict.allowed:
            offer = None
            degrade(InterventionKind.REMINDER, verdict.reason)
    elif final is InterventionKind.PAYMENT_LINK and not context.payment_links_available:
        degrade(InterventionKind.REMINDER, "no payment rail configured for links")

    if final in CONTACTING and not any(g.gate == "offer" for g in gates):
        gates.append(GateDecision.allow("offer", "no concession proposed"))

    return Decision(
        proposed=proposal, final=final, offer=offer, gates=gates,
        modified=final is not proposal.action, reasons=reasons,
    )


# -- commitment gate ----------------------------------------------------------

class CommitmentVerdict(BaseModel):
    """Policy's ruling on turning a promise into an executable commitment.

    ``checks`` is the full checklist — every line, allowed or not — so the
    dashboard can show *why* a commitment exists or why it was refused.
    """

    allowed: bool
    checks: list[GateDecision] = Field(default_factory=list)
    reason: str

    @property
    def gate(self) -> GateDecision:
        return GateDecision(allowed=self.allowed, gate="commitment", reason=self.reason)


def check_commitment(
    invoice: Invoice,
    amount: Paise,
    due_on: date,
    today: date,
    config: PolicyConfig,
    live_concession: Concession | None = None,
) -> CommitmentVerdict:
    """May this (amount, deadline) become an executable commitment on this invoice?"""
    checks: list[GateDecision] = []

    def check(ok: bool, name: str, reason: str) -> None:
        checks.append(GateDecision(allowed=ok, gate=name, reason=reason))

    check(invoice.state in ACTIVE_INVOICE_STATES, "invoice_active",
          f"invoice is {invoice.state}" + ("" if invoice.state in ACTIVE_INVOICE_STATES
                                           else "; not the agent's to arrange"))
    check(invoice.state is not InvoiceState.STOP_CONTACT, "not_stop_contact",
          "debtor has not asked us to stop" if invoice.state is not InvoiceState.STOP_CONTACT
          else "debtor asked us to stop; no arrangements")
    check(invoice.state is not InvoiceState.DISPUTED, "no_dispute",
          "no dispute recorded" if invoice.state is not InvoiceState.DISPUTED
          else "invoice is disputed; a human owns it")
    check(amount > 0, "amount_positive", f"amount {format_inr(amount)} is positive"
          if amount > 0 else f"amount {format_inr(amount)} is not positive")
    check(amount <= invoice.balance, "amount_within_balance",
          f"{format_inr(amount)} ≤ balance {format_inr(invoice.balance)}" if amount <= invoice.balance
          else f"{format_inr(amount)} exceeds balance {format_inr(invoice.balance)}")
    partial = 0 < amount < invoice.balance
    if partial:
        check(config.allow_partial_commitments, "partial_allowed",
              "partial payments are allowed by policy" if config.allow_partial_commitments
              else "policy does not allow partial commitments")
        check(amount >= config.min_commitment, "amount_floor",
              f"{format_inr(amount)} ≥ floor {format_inr(config.min_commitment)}"
              if amount >= config.min_commitment
              else f"{format_inr(amount)} is below the {format_inr(config.min_commitment)} floor")
    else:
        check(True, "partial_allowed", "full balance committed")
    horizon = (due_on - today).days
    check(horizon >= 0, "deadline_not_past",
          f"deadline {due_on.isoformat()} is today or later" if horizon >= 0
          else f"deadline {due_on.isoformat()} is in the past")
    check(horizon <= config.max_promise_horizon_days, "deadline_within_horizon",
          f"{horizon} day(s) out, within the {config.max_promise_horizon_days}-day horizon"
          if horizon <= config.max_promise_horizon_days
          else f"{horizon} day(s) out exceeds the {config.max_promise_horizon_days}-day horizon")
    if live_concession is not None and live_concession.state.value == "accepted":
        conflict = (
            live_concession.type.value == "installments"
            or amount < live_concession.settlement_amount
        )
        check(not conflict, "consistent_with_offer",
              "consistent with the accepted offer" if not conflict
              else "an accepted arrangement is already running; this would undercut it")
    else:
        check(True, "consistent_with_offer", "no accepted offer to conflict with")

    failed = [c for c in checks if not c.allowed]
    if failed:
        return CommitmentVerdict(allowed=False, checks=checks,
                                 reason="; ".join(c.reason for c in failed))
    return CommitmentVerdict(
        allowed=True, checks=checks,
        reason=f"{format_inr(amount)} by {due_on.isoformat()} within delegated authority",
    )


ACTIVE_INVOICE_STATES = frozenset(
    {InvoiceState.OUTSTANDING, InvoiceState.PROMISED, InvoiceState.PARTIALLY_PAID}
)
