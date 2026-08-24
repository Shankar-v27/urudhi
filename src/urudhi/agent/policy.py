"""Bounded authority: the deterministic gates around the negotiating agent.

The LLM proposes; policy disposes. Every consequential action — contacting a
debtor, making a concession, accepting a promise — passes through a gate here,
and every gate returns a :class:`GateDecision` with a machine-checkable reason,
so "every money action explainable, bounded and gated" is an architecture
property, not a prompt instruction. Gates are pure functions of config and
facts; they contain no model calls and no I/O.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time

from pydantic import BaseModel, Field

from urudhi.ledger.models import Channel, Invoice, InvoiceState
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

    contact_open: time = time(10, 0)      # RBI-style courtesy window
    contact_close: time = time(19, 0)
    max_attempts_per_invoice: int = Field(default=6, ge=1)
    max_attempts_per_day: int = Field(default=1, ge=1)
    escalate_after_broken_promises: int = Field(default=2, ge=1)
    allowed_channels: frozenset[Channel] = frozenset({Channel.WHATSAPP, Channel.EMAIL})


class GateDecision(BaseModel):
    allowed: bool
    gate: str
    reason: str

    @classmethod
    def allow(cls, gate: str, reason: str) -> "GateDecision":
        return cls(allowed=True, gate=gate, reason=reason)

    @classmethod
    def block(cls, gate: str, reason: str) -> "GateDecision":
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


class ContactFacts(BaseModel):
    """Everything the contact gate needs to know, gathered by the caller."""

    now: datetime
    channel: Channel
    attempts_total: int
    attempts_today: int
    broken_promises: int


def check_contact(invoice: Invoice, facts: ContactFacts, config: PolicyConfig) -> GateDecision:
    """May the agent contact this debtor about this invoice, now, on this channel?"""
    gate = "contact"
    if invoice.state is InvoiceState.STOP_CONTACT:
        return GateDecision.block(gate, "debtor asked us to stop; stop-contact is terminal")
    if invoice.state in (InvoiceState.DISPUTED, InvoiceState.ESCALATED):
        return GateDecision.block(gate, f"invoice is {invoice.state}; a human owns it now")
    if invoice.state is InvoiceState.PAID:
        return GateDecision.block(gate, "invoice is settled; there is nothing to chase")
    if facts.channel not in config.allowed_channels:
        return GateDecision.block(gate, f"channel {facts.channel} is not in the allowed set")
    if not (config.contact_open <= facts.now.time() < config.contact_close):
        return GateDecision.block(
            gate,
            f"outside contact hours {config.contact_open:%H:%M}–{config.contact_close:%H:%M}",
        )
    if facts.attempts_today >= config.max_attempts_per_day:
        return GateDecision.block(gate, "daily attempt limit reached for this debtor")
    if facts.attempts_total >= config.max_attempts_per_invoice:
        return GateDecision.block(
            gate,
            f"attempt limit ({config.max_attempts_per_invoice}) exhausted; escalate instead",
        )
    return GateDecision.allow(gate, "within contact hours and attempt limits")


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
