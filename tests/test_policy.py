from datetime import date, datetime

import pytest

from urudhi.agent.policy import (
    ContactFacts,
    Offer,
    OfferType,
    PolicyConfig,
    check_contact,
    check_offer,
    should_escalate,
)
from urudhi.ledger.models import Channel, Invoice, InvoiceState

TODAY = date(2026, 8, 24)
CONFIG = PolicyConfig()


def make_invoice(**overrides) -> Invoice:
    defaults = dict(
        id="inv_1", debtor_id="deb_1", number="URU/2026/001",
        amount=1_000_000, issued_on=date(2026, 6, 1), due_on=date(2026, 7, 1),
    )
    return Invoice(**{**defaults, **overrides})


def make_facts(**overrides) -> ContactFacts:
    defaults = dict(
        now=datetime(2026, 8, 24, 11, 0), channel=Channel.WHATSAPP,
        attempts_total=0, attempts_today=0, broken_promises=0,
    )
    return ContactFacts(**{**defaults, **overrides})


class TestContactGate:
    def test_allows_within_hours_and_limits(self):
        decision = check_contact(make_invoice(), make_facts(), CONFIG)
        assert decision.allowed

    def test_stop_contact_is_absolute(self):
        decision = check_contact(
            make_invoice(state=InvoiceState.STOP_CONTACT), make_facts(), CONFIG
        )
        assert not decision.allowed
        assert "stop-contact" in decision.reason

    @pytest.mark.parametrize("state", [InvoiceState.DISPUTED, InvoiceState.ESCALATED])
    def test_human_owned_states_block(self, state):
        assert not check_contact(make_invoice(state=state), make_facts(), CONFIG).allowed

    @pytest.mark.parametrize("hour", [9, 19, 22])
    def test_outside_contact_hours_blocks(self, hour):
        facts = make_facts(now=datetime(2026, 8, 24, hour, 0))
        decision = check_contact(make_invoice(), facts, CONFIG)
        assert not decision.allowed
        assert "contact hours" in decision.reason

    def test_daily_limit(self):
        assert not check_contact(make_invoice(), make_facts(attempts_today=1), CONFIG).allowed

    def test_total_attempt_limit(self):
        decision = check_contact(make_invoice(), make_facts(attempts_total=6), CONFIG)
        assert not decision.allowed
        assert "escalate" in decision.reason

    def test_disallowed_channel(self):
        assert not check_contact(make_invoice(), make_facts(channel=Channel.VOICE), CONFIG).allowed


class TestOfferGate:
    def offer(self, **overrides) -> Offer:
        defaults = dict(
            type=OfferType.DISCOUNT, invoice_id="inv_1",
            discount_bps=300, installment_count=1, pay_by=date(2026, 8, 28),
        )
        return Offer(**{**defaults, **overrides})

    def test_discount_within_cap_allowed(self):
        assert check_offer(make_invoice(), self.offer(), TODAY, CONFIG).allowed

    def test_discount_above_cap_blocked(self):
        decision = check_offer(make_invoice(), self.offer(discount_bps=800), TODAY, CONFIG)
        assert not decision.allowed
        assert "exceeds delegated cap" in decision.reason

    def test_installments_within_authority(self):
        offer = self.offer(
            type=OfferType.INSTALLMENTS, discount_bps=0, installment_count=3,
        )
        assert check_offer(make_invoice(), offer, TODAY, CONFIG).allowed

    def test_too_many_installments_blocked(self):
        offer = self.offer(type=OfferType.INSTALLMENTS, discount_bps=0, installment_count=4)
        assert not check_offer(make_invoice(), offer, TODAY, CONFIG).allowed

    def test_installments_below_floor_blocked(self):
        offer = self.offer(type=OfferType.INSTALLMENTS, discount_bps=0, installment_count=3)
        small = make_invoice(amount=240_000)  # ₹2,400 -> ₹800 per installment
        decision = check_offer(small, offer, TODAY, CONFIG)
        assert not decision.allowed
        assert "floor" in decision.reason

    def test_full_payment_with_discount_blocked(self):
        offer = self.offer(type=OfferType.FULL_PAYMENT, discount_bps=100)
        assert not check_offer(make_invoice(), offer, TODAY, CONFIG).allowed

    def test_horizon_cap(self):
        offer = self.offer(pay_by=date(2026, 10, 24))
        decision = check_offer(make_invoice(), offer, TODAY, CONFIG)
        assert not decision.allowed
        assert "horizon" in decision.reason

    def test_pay_by_must_be_future(self):
        assert not check_offer(make_invoice(), self.offer(pay_by=TODAY), TODAY, CONFIG).allowed


class TestEscalation:
    def test_broken_promises_trigger(self):
        decision = should_escalate(make_facts(broken_promises=2), CONFIG)
        assert decision.allowed
        assert "broken promises" in decision.reason

    def test_exhausted_attempts_trigger(self):
        assert should_escalate(make_facts(attempts_total=6), CONFIG).allowed

    def test_below_thresholds_continue(self):
        assert not should_escalate(make_facts(broken_promises=1), CONFIG).allowed
