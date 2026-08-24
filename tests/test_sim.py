from urudhi.sim.batch import generate_batch
from urudhi.sim.personas import MIX, Archetype


class TestBatchGeneration:
    def test_deterministic_for_same_seed(self):
        a = generate_batch(count=50, seed=7)
        b = generate_batch(count=50, seed=7)
        assert [c.invoice.amount for c in a] == [c.invoice.amount for c in b]
        assert [c.debtor.name for c in a] == [c.debtor.name for c in b]
        assert [c.persona.archetype for c in a] == [c.persona.archetype for c in b]

    def test_different_seeds_differ(self):
        a = generate_batch(count=50, seed=7)
        b = generate_batch(count=50, seed=8)
        assert [c.invoice.amount for c in a] != [c.invoice.amount for c in b]

    def test_mix_proportions_hold(self):
        cases = generate_batch(count=120)
        counts = {arch: 0 for arch, _ in MIX}
        for case in cases:
            counts[case.persona.archetype] += 1
        assert counts[Archetype.PROMPT_PAYER] >= 36  # 30% + fill remainder
        assert counts[Archetype.DISPUTER] == 9       # 8% of 120
        assert counts[Archetype.STOP_REQUESTER] == 3

    def test_every_invoice_is_overdue(self):
        from datetime import date
        today = date(2026, 8, 24)
        assert all(c.invoice.days_overdue(today) >= 8 for c in generate_batch())

    def test_unique_ids_and_numbers(self):
        cases = generate_batch(count=120)
        assert len({c.invoice.id for c in cases}) == 120
        assert len({c.debtor.id for c in cases}) == 120


class TestPersonaScripts:
    def test_prompt_payer_pays_full(self):
        case = next(c for c in generate_batch() if c.persona.archetype is Archetype.PROMPT_PAYER)
        reply = case.persona.reply(contacted=1)
        assert reply.pays_paise == case.invoice.amount
        assert "will pay" in reply.text.lower()

    def test_promise_breaker_pays_nothing_early(self):
        case = next(
            c for c in generate_batch() if c.persona.archetype is Archetype.PROMISE_BREAKER
        )
        assert case.persona.reply(contacted=1).pays_paise == 0
        assert case.persona.reply(contacted=3).pays_paise == case.invoice.amount

    def test_ghost_stays_silent(self):
        case = next(c for c in generate_batch() if c.persona.archetype is Archetype.GHOST)
        assert case.persona.reply(contacted=1).text is None

    def test_negotiator_asks_then_commits(self):
        case = next(c for c in generate_batch() if c.persona.archetype is Archetype.NEGOTIATOR)
        first = case.persona.reply(contacted=1)
        assert "discount" in first.text.lower()
        assert first.pays_paise == 0
        second = case.persona.reply(contacted=2)
        assert second.pays_paise == case.invoice.amount
