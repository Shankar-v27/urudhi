import random
from datetime import date

from urudhi.agent.intervention import InterventionKind
from urudhi.sim.batch import generate_batch
from urudhi.sim.personas import MIX, Archetype, Persona, Stimulus


class TestBatchGeneration:
    def test_deterministic_for_same_seed(self):
        a = generate_batch(count=50, seed=7)
        b = generate_batch(count=50, seed=7)
        assert [c.invoice.amount for c in a] == [c.invoice.amount for c in b]
        assert [c.debtor.name for c in a] == [c.debtor.name for c in b]
        assert [c.archetype for c in a] == [c.archetype for c in b]
        assert [c.persona().traits for c in a] == [c.persona().traits for c in b]

    def test_different_seeds_differ(self):
        a = generate_batch(count=50, seed=7)
        b = generate_batch(count=50, seed=8)
        assert [c.invoice.amount for c in a] != [c.invoice.amount for c in b]

    def test_mix_proportions_hold(self):
        cases = generate_batch(count=120)
        counts = {arch: 0 for arch, _ in MIX}
        for case in cases:
            counts[case.archetype] += 1
        assert counts[Archetype.PROMPT_PAYER] >= 36
        assert counts[Archetype.DISPUTER] == 9
        assert counts[Archetype.STOP_REQUESTER] == 6

    def test_every_invoice_is_overdue(self):
        today = date(2026, 8, 24)
        assert all(c.invoice.days_overdue(today) >= 8 for c in generate_batch())

    def test_unique_ids_and_numbers(self):
        cases = generate_batch(count=120)
        assert len({c.invoice.id for c in cases}) == 120
        assert len({c.debtor.id for c in cases}) == 120


def persona(archetype, seed=1, balance=5_000_000, language="en"):
    return Persona(archetype, balance, random.Random(seed), language=language)


class TestReactivePersonas:
    def test_disputer_disputes(self):
        r = persona(Archetype.DISPUTER).react(Stimulus(InterventionKind.REMINDER, 1), date(2026, 8, 24))
        assert r.text and ("wrong" in r.text.lower() or "galat" in r.text.lower() or "short" in r.text.lower())
        assert r.payments == []

    def test_stop_requester_asks_to_stop_once_patience_runs_out(self):
        p = persona(Archetype.STOP_REQUESTER)
        today = date(2026, 8, 24)
        texts = [p.react(Stimulus(InterventionKind.REMINDER, n), today).text for n in range(1, 5)]
        assert any(t and ("stop" in t.lower() or "unsubscribe" in t.lower()) for t in texts)

    def test_discount_moves_a_negotiator_more_than_a_reminder(self):
        today = date(2026, 8, 24)
        paid_plain = paid_discount = 0
        for seed in range(60):
            plain = persona(Archetype.NEGOTIATOR, seed).react(Stimulus(InterventionKind.REMINDER, 1), today)
            paid_plain += bool(plain.payments)
            offer = persona(Archetype.NEGOTIATOR, seed).react(
                Stimulus(InterventionKind.DISCOUNT_OFFER, 1, discount_bps=300, settlement_amount=4_850_000), today)
            paid_discount += bool(offer.payments)
        assert paid_discount > paid_plain

    def test_installments_move_the_cash_strapped(self):
        today = date(2026, 8, 24)
        full_plain = full_plan = 0
        for seed in range(60):
            plain = persona(Archetype.CASH_STRAPPED, seed).react(Stimulus(InterventionKind.REMINDER, 1), today)
            full_plain += sum(p.amount for p in plain.payments) >= 5_000_000
            plan = persona(Archetype.CASH_STRAPPED, seed).react(
                Stimulus(InterventionKind.INSTALLMENT_OFFER, 1, installments=3, first_installment=1_700_000,
                         installment_due_days=[7, 14, 21]), today)
            full_plan += sum(p.amount for p in plan.payments) >= 5_000_000
        assert full_plan > full_plain

    def test_fatigue_lowers_payment_probability(self):
        today = date(2026, 8, 24)
        early = late = 0
        for seed in range(80):
            early += bool(persona(Archetype.PROMPT_PAYER, seed).react(Stimulus(InterventionKind.REMINDER, 1), today).payments)
            late += bool(persona(Archetype.PROMPT_PAYER, seed).react(Stimulus(InterventionKind.REMINDER, 5), today).payments)
        assert late < early

    def test_promise_breaker_talks_more_than_pays(self):
        today = date(2026, 8, 24)
        promised = paid = 0
        for seed in range(60):
            r = persona(Archetype.PROMISE_BREAKER, seed).react(Stimulus(InterventionKind.REMINDER, 1), today)
            promised += bool(r.text and not r.kept)
            paid += bool(r.payments)
        assert promised > paid

    def test_language_shows_in_replies(self):
        today = date(2026, 8, 24)
        p = persona(Archetype.PROMPT_PAYER, 3, language="ta")
        r = p.react(Stimulus(InterventionKind.PAYMENT_LINK, 1, has_link=True), today)
        assert r.text is not None
