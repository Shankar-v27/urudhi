"""Policy is the final authority over what the brain proposes."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from urudhi.agent.intervention import DecisionContext, InterventionKind, InterventionRecommendation
from urudhi.agent.policy import ContactFacts, OfferType, PolicyConfig, decide_intervention
from urudhi.ledger.models import Channel, Invoice

IST = ZoneInfo("Asia/Kolkata")
TODAY = date(2026, 8, 24)
NOW = datetime(2026, 8, 24, 11, 0, tzinfo=IST)
CONFIG = PolicyConfig()


def invoice(**o) -> Invoice:
    base = dict(id="inv_1", debtor_id="deb_1", number="URU/2026/001", amount=1_000_000,
                issued_on=date(2026, 6, 1), due_on=date(2026, 7, 1))
    return Invoice(**{**base, **o})


def context(**o) -> DecisionContext:
    base = dict(invoice_number="URU/2026/001", balance=1_000_000, original_amount=1_000_000,
                days_overdue=54, today=TODAY, invoice_state="outstanding", attempts_total=1,
                attempts_allowed=6, max_discount_bps=500, max_installments=3,
                min_installment=100_000, max_horizon_days=30, payment_links_available=True)
    return DecisionContext(**{**base, **o})


def facts(**o) -> ContactFacts:
    base = dict(now=NOW, channel=Channel.EMAIL, attempts_total=1, attempts_today=0,
                broken_promises=0, days_since_last_contact=3)
    return ContactFacts(**{**base, **o})


def proposal(action, **o) -> InterventionRecommendation:
    return InterventionRecommendation(action=action, rationale=["test"], confidence=0.8, **o)


class TestDecide:
    def test_reminder_passes_through(self):
        d = decide_intervention(invoice(), context(), proposal(InterventionKind.REMINDER), facts(), CONFIG)
        assert d.final is InterventionKind.REMINDER and not d.modified
        assert any(g.gate == "contact" and g.allowed for g in d.gates)

    def test_open_promise_forces_waiting(self):
        ctx = context(open_promise_on=date(2026, 8, 27), open_promise_amount=1_000_000)
        d = decide_intervention(invoice(), ctx, proposal(InterventionKind.DISCOUNT_OFFER,
                                                         proposed_discount_bps=300), facts(), CONFIG)
        assert d.final is InterventionKind.WAIT_FOR_PROMISE and d.modified

    def test_over_cap_discount_degrades_to_reminder_not_smaller_discount(self):
        d = decide_intervention(invoice(), context(),
                                proposal(InterventionKind.DISCOUNT_OFFER, proposed_discount_bps=900),
                                facts(), CONFIG)
        assert d.final is InterventionKind.REMINDER and d.modified and d.offer is None
        assert any("exceeds delegated cap" in g.reason for g in d.gates if not g.allowed)

    def test_discount_within_cap_carries_an_offer(self):
        d = decide_intervention(invoice(), context(),
                                proposal(InterventionKind.DISCOUNT_OFFER, proposed_discount_bps=300,
                                         proposed_pay_by=date(2026, 8, 30)), facts(), CONFIG)
        assert d.final is InterventionKind.DISCOUNT_OFFER and d.offer is not None
        assert d.offer.type is OfferType.DISCOUNT and d.offer.discount_bps == 300

    def test_installments_below_floor_degrade(self):
        small = invoice(amount=240_000)
        d = decide_intervention(small, context(balance=240_000),
                                proposal(InterventionKind.INSTALLMENT_OFFER, proposed_installments=3),
                                facts(), CONFIG)
        assert d.final is InterventionKind.REMINDER and d.modified

    def test_installments_within_authority(self):
        d = decide_intervention(invoice(), context(),
                                proposal(InterventionKind.INSTALLMENT_OFFER, proposed_installments=3),
                                facts(), CONFIG)
        assert d.final is InterventionKind.INSTALLMENT_OFFER and d.offer.installment_count == 3

    def test_escalation_is_not_the_models_to_give(self):
        d = decide_intervention(invoice(), context(), proposal(InterventionKind.ESCALATE_HUMAN),
                                facts(broken_promises=0), CONFIG)
        assert d.final is InterventionKind.REMINDER and d.modified

    def test_escalation_honoured_after_a_broken_promise_with_confidence(self):
        d = decide_intervention(invoice(), context(promises_broken=1),
                                proposal(InterventionKind.ESCALATE_HUMAN),
                                facts(broken_promises=1), CONFIG)
        assert d.final is InterventionKind.ESCALATE_HUMAN

    def test_payment_link_without_rails_degrades(self):
        d = decide_intervention(invoice(), context(payment_links_available=False),
                                proposal(InterventionKind.PAYMENT_LINK), facts(), CONFIG)
        assert d.final is InterventionKind.REMINDER and d.modified

    def test_contact_blocked_means_no_action(self):
        night = facts(now=datetime(2026, 8, 24, 22, 0, tzinfo=IST))
        d = decide_intervention(invoice(), context(), proposal(InterventionKind.REMINDER), night, CONFIG)
        assert d.final is InterventionKind.NO_ACTION and d.modified

    def test_no_action_is_always_fine(self):
        d = decide_intervention(invoice(), context(), proposal(InterventionKind.NO_ACTION), facts(), CONFIG)
        assert d.final is InterventionKind.NO_ACTION and not d.modified
