import json
from datetime import date
from pathlib import Path

from urudhi.agent.brain import MockBrain
from urudhi.eval_replies import load_dataset, score_one, summarize

DATASET = Path(__file__).resolve().parents[1] / "data" / "reply_eval.jsonl"
ALLOWED = {"promise", "request_terms", "dispute", "claims_paid", "stop_contact",
           "accept_offer", "question", "vague"}


class TestDataset:
    def test_loads_and_is_well_formed(self):
        rows = load_dataset(DATASET)
        assert 75 <= len(rows) <= 120
        assert len({r["id"] for r in rows}) == len(rows)
        for r in rows:
            assert r["intent"] in ALLOWED
            assert r["language"] in {"en", "hinglish", "ta-en"}
            if r.get("promised_on"):
                assert date.fromisoformat(r["promised_on"]) >= date(2026, 8, 24)
            if r.get("promised_amount_paise") is not None:
                assert 0 < r["promised_amount_paise"] <= 10_000_000

    def test_required_examples_present(self):
        texts = {r["text"] for r in load_dataset(DATASET)}
        for required in ("Bhai next Monday pakka.", "Payment done, please check.", "STOP",
                         "Invoice amount itself is wrong.",
                         "Will transfer ₹50,000 in 3 days, rest next month."):
            assert required in texts


class TestScoring:
    def test_score_and_summarize_with_mock(self):
        rows = load_dataset(DATASET)[:20]
        results = [score_one(MockBrain(), r) for r in rows]
        summary = summarize(results, "mock", None)
        assert summary["items"] == 20
        assert 0.0 <= summary["intent_accuracy"] <= 1.0
        assert set(summary["per_language"]) <= {"en", "hinglish", "ta-en"}
        json.dumps(summary)  # serialisable

    def test_amount_and_date_only_scored_when_labelled(self):
        row = {"id": "x", "text": "Payment done, please check.", "language": "en",
               "intent": "claims_paid", "promised_amount_paise": None, "promised_on": None}
        result = score_one(MockBrain(), row)
        assert result["amount_ok"] is None and result["date_ok"] is None
        assert result["intent_ok"]
