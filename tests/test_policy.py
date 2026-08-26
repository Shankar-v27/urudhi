from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

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

IST = ZoneInfo("Asia/Kolkata")
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
        now=datetime(2026, 8, 24, 11, 0, tzinfo=IST), channel=Channel.WHATSAPP,
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

    @pytest.mark.parametrize("state", [InvoiceState.DISPUTED, InvoiceState.ESCALATED,
                                       InvoiceState.CLOSED])
    def test_human_owned_states_block(self, state):
        assert not check_contact(make_invoice(state=state), make_facts(), CONFIG).allowed

    @pytest.mark.parametrize("hour", [9, 19, 22])
    def test_outside_contact_hours_blocks(self, hour):
        facts = make_facts(now=datetime(2026, 8, 24, hour, 0, tzinfo=IST))
        decision = check_contact(make_invoice(), facts, CONFIG)
        assert not decision.allowed
        assert "contact hours" in decision.reason

    def test_daily_limit(self):
        assert not check_contact(make_invoice(), make_facts(attempts_today=1), CONFIG).allowed

    def test_total_attempt_limit(self):
        decision = check_contact(make_invoice(), make_facts(attempts_total=6), CONFIG)
        assert not decision.allowed
        assert "escalate" in decision.reason

    def test_spacing_between_contacts(self):
        blocked = check_contact(make_invoice(), make_facts(days_since_last_contact=1), CONFIG)
        assert not blocked.allowed and "spacing" in blocked.reason
        assert check_contact(make_invoice(), make_facts(days_since_last_contact=2), CONFIG).allowed

    def test_disallowed_channel(self):
        assert not check_contact(make_invoice(), make_facts(channel=Channel.VOICE), CONFIG).allowed


class TestTimezone:
    """Contact hours are judged in the policy zone, never in the caller's."""

    def test_utc_morning_is_ist_late_morning_and_allowed(self):
        facts = make_facts(now=datetime(2026, 8, 24, 5, 0, tzinfo=UTC))  # 10:30 IST
        assert check_contact(make_invoice(), facts, CONFIG).allowed

    def test_utc_afternoon_is_ist_evening_and_blocked(self):
        facts = make_facts(now=datetime(2026, 8, 24, 14, 0, tzinfo=UTC))  # 19:30 IST
        decision = check_contact(make_invoice(), facts, CONFIG)
        assert not decision.allowed
        assert "19:30" in decision.reason

    def test_utc_dawn_is_ist_before_opening_and_blocked(self):
        facts = make_facts(now=datetime(2026, 8, 24, 3, 30, tzinfo=UTC))  # 09:00 IST
        assert not check_contact(make_invoice(), facts, CONFIG).allowed

    def test_naive_datetime_is_refused_not_guessed(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            check_contact(make_invoice(), make_facts(now=datetime(2026, 8, 24, 11, 0)), CONFIG)

    def test_policy_zone_is_configurable(self):
        singapore = PolicyConfig(timezone="Asia/Singapore")
        facts = make_facts(now=datetime(2026, 8, 24, 11, 0, tzinfo=UTC))  # 19:00 SGT
        assert not check_contact(make_invoice(), facts, singapore).allowed
        assert check_contact(make_invoice(), facts, CONFIG).allowed         # 16:30 IST


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

    def test_no_discount_on_fresh_debt(self):
        fresh = make_invoice(due_on=date(2026, 8, 20))
        decision = check_offer(fresh, self.offer(), TODAY, CONFIG)
        assert not decision.allowed
        assert "days overdue" in decision.reason

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

    def test_settlement_amount_and_schedule(self):
        assert self.offer().settlement_amount(1_000_000) == 970_000
        plan = self.offer(type=OfferType.INSTALLMENTS, discount_bps=0, installment_count=3,
                          pay_by=date(2026, 9, 23))
        schedule = plan.schedule(1_000_000, TODAY)
        assert sum(i.amount for i in schedule) == 1_000_000
        assert [i.due_on for i in schedule] == sorted(i.due_on for i in schedule)
        assert schedule[-1].due_on == date(2026, 9, 23)


class TestEscalation:
    def test_broken_promises_trigger(self):
        decision = should_escalate(make_facts(broken_promises=2), CONFIG)
        assert decision.allowed
        assert "broken promises" in decision.reason

    def test_exhausted_attempts_trigger(self):
        assert should_escalate(make_facts(attempts_total=6), CONFIG).allowed

    def test_below_thresholds_continue(self):
        assert not should_escalate(make_facts(broken_promises=1), CONFIG).allowed
