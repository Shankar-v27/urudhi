from datetime import date, datetime

from urudhi.ledger.models import Channel, Invoice, PromiseState, PromiseToPay
from urudhi.scoring.priority import rank, score_invoice

TODAY = date(2026, 8, 24)


def make_invoice(**overrides) -> Invoice:
    defaults = dict(
        id="inv_1", debtor_id="deb_1", number="URU/2026/001",
        amount=1_000_000, issued_on=date(2026, 5, 1), due_on=date(2026, 7, 1),
    )
    return Invoice(**{**defaults, **overrides})


def make_promise(state: PromiseState, **overrides) -> PromiseToPay:
    defaults = dict(
        id="ptp_1", invoice_id="inv_1", debtor_id="deb_1", amount=500_000,
        promised_on=date(2026, 8, 20), made_at=datetime(2026, 8, 15, 11, 0),
        channel=Channel.WHATSAPP, verbatim="paying by the 20th", confidence=0.8,
        state=state,
    )
    return PromiseToPay(**{**defaults, **overrides})


def score(**kwargs):
    defaults = dict(
        invoice=make_invoice(), promises=[], attempts=0, max_attempts=6, today=TODAY,
    )
    return score_invoice(**{**defaults, **defaults | kwargs})


class TestComponents:
    def test_score_ships_with_breakdown(self):
        result = score()
        assert set(result.components) == {"value", "urgency", "credibility", "fatigue"}
        assert "score" in result.explain()

    def test_bigger_balance_scores_higher(self):
        small = score(invoice=make_invoice(amount=50_000))
        big = score(invoice=make_invoice(amount=50_000_000))
        assert big.score > small.score

    def test_more_overdue_scores_higher(self):
        fresh = score(invoice=make_invoice(due_on=date(2026, 8, 20)))
        stale = score(invoice=make_invoice(due_on=date(2026, 2, 1)))
        assert stale.score > fresh.score

    def test_open_promise_suppresses_chasing(self):
        chasing = score()
        waiting = score(promises=[make_promise(PromiseState.OPEN)])
        assert waiting.components["credibility"] < 0.1
        assert waiting.score < chasing.score

    def test_broken_promises_raise_urgency(self):
        clean = score()
        burned = score(promises=[
            make_promise(PromiseState.BROKEN),
            make_promise(PromiseState.BROKEN, id="ptp_2"),
        ])
        assert burned.components["credibility"] > clean.components["credibility"]
        assert burned.score > clean.score

    def test_attempt_fatigue_lowers_priority(self):
        fresh = score(attempts=0)
        tired = score(attempts=5)
        assert tired.score < fresh.score

    def test_paid_off_invoice_scores_zero_value(self):
        settled = score(invoice=make_invoice(amount_paid=1_000_000))
        assert settled.components["value"] == 0.0


class TestRank:
    def test_orders_by_score_then_id(self):
        a = score(invoice=make_invoice(id="inv_a", amount=50_000_000))
        b = score(invoice=make_invoice(id="inv_b"))
        c = score(invoice=make_invoice(id="inv_c"))  # identical to b
        ordered = rank([c, b, a])
        assert [s.invoice_id for s in ordered] == ["inv_a", "inv_b", "inv_c"]
