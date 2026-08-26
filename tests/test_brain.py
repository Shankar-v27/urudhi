"""Both brains: the deterministic mock, and Claude through a fake client."""

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from urudhi.agent.brain import (
    BrainConfigError,
    BrainUnavailable,
    ClaudeBrain,
    Intent,
    MessageContext,
    MockBrain,
    make_brain,
    sanitize_interpretation,
)
from urudhi.agent.intervention import DecisionContext, InterventionKind

TODAY = date(2026, 8, 24)  # a Monday


@pytest.fixture
def context():
    return MessageContext(
        debtor_name="Kumar Textiles", contact_name="Kumar",
        invoice_number="URU/2026/001", balance=250_000,
        days_overdue=54, today=TODAY, language="ta",
    )


def decision_context(**o) -> DecisionContext:
    base = dict(invoice_number="URU/2026/001", balance=250_000, original_amount=250_000,
                days_overdue=54, today=TODAY, invoice_state="outstanding", attempts_total=0,
                attempts_allowed=6, max_discount_bps=500, max_installments=3,
                min_installment=100_000, max_horizon_days=30, payment_links_available=True)
    return DecisionContext(**{**base, **o})


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

    def test_vague_promise_scores_low(self, context):
        result = self.brain.interpret_reply(context, "Will settle everything soon, boss.")
        assert result.intent is Intent.PROMISE
        assert result.promised_amount in (None, 250_000)
        assert result.confidence <= 0.6

    def test_will_transfer_partial_is_a_promise(self, context):
        result = self.brain.interpret_reply(context, "Will transfer ₹1,000 in 3 days, rest next month.")
        assert result.intent is Intent.PROMISE
        assert result.promised_amount == 100_000
        assert result.promised_on == TODAY + timedelta(days=3)

    def test_weekday_and_k_amounts(self, context):
        result = self.brain.interpret_reply(context, "Will transfer 2k by Friday.")
        assert result.promised_amount == 200_000
        assert result.promised_on == date(2026, 8, 28)

    def test_stop_contact_is_certain(self, context):
        for text in ("Please stop messaging me.", "STOP", "Unsubscribe"):
            result = self.brain.interpret_reply(context, text)
            assert result.intent is Intent.STOP_CONTACT, text
            assert result.confidence == 1.0

    def test_already_paid_is_claims_paid(self, context):
        result = self.brain.interpret_reply(context, "We already paid this last month!")
        assert result.intent is Intent.CLAIMS_PAID
        assert self.brain.interpret_reply(context, "Payment done, please check.").intent is Intent.CLAIMS_PAID

    def test_dispute(self, context):
        assert self.brain.interpret_reply(context, "Invoice amount itself is wrong.").intent is Intent.DISPUTE

    def test_terms_request(self, context):
        result = self.brain.interpret_reply(context, "Any discount if I clear it this week?")
        assert result.intent is Intent.REQUEST_TERMS

    def test_accept_offer(self, context):
        result = self.brain.interpret_reply(context, "Ok deal. Will pay ₹2,425 by Friday.")
        assert result.intent is Intent.ACCEPT_OFFER
        assert result.promised_on == date(2026, 8, 28)

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

    def test_amount_never_exceeds_balance(self, context):
        result = self.brain.interpret_reply(context, "will pay ₹9,99,999 in 2 days")
        assert result.promised_amount == 250_000
        assert any("clamped" in f for f in result.flags)


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


class TestMockRecommendation:
    def test_waits_on_open_promise(self):
        rec = MockBrain().recommend_intervention(decision_context(open_promise_on=TODAY))
        assert rec.action is InterventionKind.WAIT_FOR_PROMISE

    def test_terms_request_gets_a_concession_proposal(self):
        rec = MockBrain().recommend_intervention(decision_context(last_intent="request_terms"))
        assert rec.action in (InterventionKind.DISCOUNT_OFFER, InterventionKind.INSTALLMENT_OFFER)
        assert rec.proposed_discount_bps is None or rec.proposed_discount_bps <= 500

    def test_two_broken_promises_propose_escalation(self):
        rec = MockBrain().recommend_intervention(decision_context(promises_broken=2))
        assert rec.action is InterventionKind.ESCALATE_HUMAN


# --------------------------------------------------------------------------
# Claude brain through a fake client: no network, every failure mode
# --------------------------------------------------------------------------

class FakeClient:
    """Mimics ``anthropic.Anthropic().messages.create``; returns scripted outputs."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(content=[SimpleNamespace(text=out)], model=kwargs["model"])


def claude(*outputs) -> tuple[ClaudeBrain, FakeClient]:
    client = FakeClient(*outputs)
    return ClaudeBrain(client=client, model="claude-test"), client


def interp(**fields) -> str:
    base = {"intent": "promise", "promised_amount_paise": None, "promised_on": None,
            "confidence": 0.9, "summary": "ok"}
    return json.dumps(base | fields)


class TestClaudeInterpretation:
    def test_normal_promise(self, context):
        brain, client = claude(interp(promised_amount_paise=100_000, promised_on="2026-08-27"))
        result = brain.interpret_reply(context, "Will pay ₹1,000 in 3 days")
        assert result.intent is Intent.PROMISE
        assert result.promised_amount == 100_000 and result.promised_on == date(2026, 8, 27)
        assert client.calls[0]["model"] == "claude-test"
        assert "Debtor reply" in client.calls[0]["messages"][0]["content"]

    def test_code_switched_reply_flows_through(self, context):
        text = "Cash konjam tight ah iruku. Friday 50k kudukuren, balance next month."
        brain, _ = claude("```json\n" + interp(promised_amount_paise=250_000,
                                                promised_on="2026-08-28", confidence=0.86) + "\n```")
        result = brain.interpret_reply(context, text)
        assert result.intent is Intent.PROMISE and result.verbatim == text
        assert result.promised_on == date(2026, 8, 28)

    def test_stop_contact(self, context):
        brain, _ = claude(interp(intent="stop_contact", confidence=1.0))
        assert brain.interpret_reply(context, "STOP").intent is Intent.STOP_CONTACT

    def test_dispute(self, context):
        brain, _ = claude(interp(intent="dispute", confidence=0.9))
        assert brain.interpret_reply(context, "Invoice amount itself is wrong.").intent is Intent.DISPUTE

    def test_malformed_json_fails_safe_to_human_review(self, context):
        brain, _ = claude("Sure! The debtor seems to promise something.")
        result = brain.interpret_reply(context, "hmm")
        assert result.intent is Intent.DISPUTE and result.confidence == 0.0
        assert "fallback" in result.flags

    def test_unknown_intent_fails_safe(self, context):
        brain, _ = claude(interp(intent="pay_now_or_else"))
        assert brain.interpret_reply(context, "x").intent is Intent.DISPUTE

    def test_missing_fields_tolerated(self, context):
        brain, _ = claude('{"intent": "vague"}')
        result = brain.interpret_reply(context, "will see")
        assert result.intent is Intent.VAGUE and result.confidence == 0.0

    def test_negative_amount_dropped(self, context):
        brain, _ = claude(interp(promised_amount_paise=-5))
        result = brain.interpret_reply(context, "x")
        assert result.promised_amount is None and any("non-positive" in f for f in result.flags)

    def test_excessive_amount_clamped(self, context):
        brain, _ = claude(interp(promised_amount_paise=99_999_999))
        assert brain.interpret_reply(context, "x").promised_amount == 250_000

    def test_past_date_dropped_and_confidence_capped(self, context):
        brain, _ = claude(interp(promised_on="2026-01-01", confidence=0.95))
        result = brain.interpret_reply(context, "x")
        assert result.promised_on is None and result.confidence <= 0.4

    def test_invalid_date_string_dropped(self, context):
        brain, _ = claude(interp(promised_on="next Friday-ish"))
        assert brain.interpret_reply(context, "x").promised_on is None

    def test_amount_on_non_commitment_intent_ignored(self, context):
        brain, _ = claude(interp(intent="question", promised_amount_paise=100))
        assert brain.interpret_reply(context, "?").promised_amount is None

    def test_api_error_raises_unavailable(self, context):
        brain, _ = claude(TimeoutError("read timed out"))
        with pytest.raises(BrainUnavailable):
            brain.interpret_reply(context, "x")

    def test_empty_output_raises_unavailable(self, context):
        brain, _ = claude("")
        with pytest.raises(BrainUnavailable):
            brain.interpret_reply(context, "x")


class TestClaudeRecommendation:
    def test_parses_proposal(self):
        brain, client = claude(json.dumps({
            "action": "discount_offer", "rationale": ["debtor asked for terms"],
            "proposed_discount_bps": 300, "proposed_pay_by": "2026-08-30", "confidence": 0.8,
        }))
        rec = brain.recommend_intervention(decision_context(last_intent="request_terms"))
        assert rec.action is InterventionKind.DISCOUNT_OFFER and rec.proposed_discount_bps == 300
        assert rec.proposed_pay_by == date(2026, 8, 30)
        assert "max_discount_bps" in client.calls[0]["messages"][0]["content"]

    def test_unsupported_action_defaults_to_reminder(self):
        brain, _ = claude('{"action": "send_legal_notice", "confidence": 0.9}')
        rec = brain.recommend_intervention(decision_context())
        assert rec.action is InterventionKind.REMINDER and rec.confidence == 0.0

    def test_garbage_defaults_to_reminder(self):
        brain, _ = claude("I think we should call them.")
        assert brain.recommend_intervention(decision_context()).action is InterventionKind.REMINDER

    def test_api_failure_raises(self):
        brain, _ = claude(ConnectionError("boom"))
        with pytest.raises(BrainUnavailable):
            brain.recommend_intervention(decision_context())


class TestClaudeDrafting:
    def test_draft_appends_stop_line_if_missing(self, context):
        brain, _ = claude("Namaste Kumar, invoice URU/2026/001 of ₹2,500 is 54 days overdue.")
        assert "STOP" in brain.draft_message(context)


class TestConfiguration:
    def test_from_env_requires_key_and_model(self):
        with pytest.raises(BrainConfigError, match="ANTHROPIC_API_KEY"):
            ClaudeBrain.from_env({})
        with pytest.raises(BrainConfigError, match="ANTHROPIC_MODEL"):
            ClaudeBrain.from_env({"ANTHROPIC_API_KEY": "sk-test"})

    def test_make_brain_never_falls_back(self):
        with pytest.raises(BrainConfigError):
            make_brain("claude", {})
        assert isinstance(make_brain("mock", {}), MockBrain)
        with pytest.raises(BrainConfigError):
            make_brain("gpt", {})

    def test_base_url_and_model_reach_the_sdk(self, monkeypatch):
        captured = {}

        class FakeAnthropic:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.messages = SimpleNamespace(create=lambda **k: None)

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
        brain = ClaudeBrain.from_env({
            "ANTHROPIC_API_KEY": "sk-test-abcdef", "ANTHROPIC_BASE_URL": "https://relay.example",
            "ANTHROPIC_MODEL": "claude-x",
        })
        assert captured["base_url"] == "https://relay.example"
        assert captured["api_key"] == "sk-test-abcdef"
        assert brain.model == "claude-x"


class TestSanitize:
    def test_horizon(self):
        result = sanitize_interpretation(Intent.PROMISE, "x", 100, TODAY + timedelta(days=400),
                                         0.9, "", balance=1000, today=TODAY)
        assert result.promised_on is None and result.confidence <= 0.4

    def test_bool_amount_is_not_money(self):
        result = sanitize_interpretation(Intent.PROMISE, "x", True, None, 0.9, "", balance=1000, today=TODAY)
        assert result.promised_amount is None
