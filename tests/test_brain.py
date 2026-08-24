from datetime import date, timedelta

import pytest

from urudhi.agent.brain import Intent, MessageContext, MockBrain

TODAY = date(2026, 8, 24)


@pytest.fixture
def context():
    return MessageContext(
        debtor_name="Kumar Textiles", contact_name="Kumar",
        invoice_number="URU/2026/001", balance=250_000,
        days_overdue=54, today=TODAY, language="ta",
    )


class TestMockInterpretation:
    def setup_method(self):
        self.brain = MockBrain()

    def test_explicit_promise_scores_high(self, context):
        result = self.brain.interpret_reply(
            context, "Sorry for the delay, will pay ₹2,500 in 4 days."
        )
        assert result.intent is Intent.PROMISE
        assert result.promised_amount == 250_000
        assert result.promised_on == TODAY + timedelta(days=4)
        assert result.confidence >= 0.85

    def test_vague_promise_scores_low_and_is_capped_at_balance(self, context):
        result = self.brain.interpret_reply(context, "Will settle everything soon, boss.")
        assert result.intent is Intent.PROMISE
        assert result.promised_amount == 250_000  # capped at balance
        assert result.confidence <= 0.6

    def test_stop_contact_is_certain(self, context):
        result = self.brain.interpret_reply(context, "Please stop messaging me.")
        assert result.intent is Intent.STOP_CONTACT
        assert result.confidence == 1.0

    def test_already_paid_is_dispute(self, context):
        result = self.brain.interpret_reply(context, "We already paid this last month!")
        assert result.intent is Intent.DISPUTE

    def test_terms_request(self, context):
        result = self.brain.interpret_reply(context, "Any discount if I clear it this week?")
        assert result.intent is Intent.REQUEST_TERMS

    def test_deflection_is_vague(self, context):
        result = self.brain.interpret_reply(context, "Things are tight right now.")
        assert result.intent is Intent.VAGUE
        assert result.confidence <= 0.3

    def test_verbatim_is_preserved_unedited(self, context):
        reply = "Will pay ₹1,000 in 2 days — vaakku."
        assert self.brain.interpret_reply(context, reply).verbatim == reply

    def test_deterministic_across_runs(self, context):
        reply = "will clear Rs 2,000 in 3 days"
        first = self.brain.interpret_reply(context, reply)
        again = MockBrain().interpret_reply(context, reply)
        assert first == again


class TestMockDrafting:
    def test_message_carries_facts_offer_link_and_optout(self, context):
        message = MockBrain().draft_message(context.model_copy(update={
            "approved_offer_text": "Clear it by Friday and a 3% early-payment "
                                   "discount applies.",
            "payment_url": "https://rzp.io/l/test123",
        }))
        assert "URU/2026/001" in message
        assert "₹2,500.00" in message
        assert "54 days" in message
        assert "3% early-payment" in message
        assert "https://rzp.io/l/test123" in message
        assert "STOP" in message

    def test_no_offer_without_policy_approval(self, context):
        message = MockBrain().draft_message(context)
        assert "discount" not in message.lower()
