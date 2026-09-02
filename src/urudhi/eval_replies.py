"""Reply interpretation evaluation: the regex baseline vs the real LLM.

    python -m urudhi.eval_replies --brain mock
    python -m urudhi.eval_replies --brain claude [--workers 4] [--limit N]

Runs every labelled reply in ``data/reply_eval.jsonl`` through the chosen
brain under a fixed context (balance ₹1,00,000, today 2026-08-24) and scores:

* intent accuracy (overall and per intent / per language);
* promise detection precision / recall (promise ∪ accept_offer vs the rest);
* amount accuracy on items with a labelled amount;
* date accuracy on items with a labelled date;
* spurious extraction rate: an amount/date produced where none was labelled;
* fallback rate: interpretations that routed to human review because the
  model output was unusable.

Nothing is fabricated: every row in the results file is one measured call.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from urudhi.agent.brain import BRAIN_MODES, Brain, BrainConfigError, MessageContext, make_brain
from urudhi.observability import configure_logging

CONTEXT = MessageContext(
    debtor_name="Kumar Textiles", contact_name="Kumar", invoice_number="URU/2026/0001",
    balance=10_000_000, days_overdue=40, today=date(2026, 8, 24), language="en",
)
COMMITMENT = {"promise", "accept_offer"}


def load_dataset(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def score_one(brain: Brain, item: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    result = brain.interpret_reply(CONTEXT, item["text"])
    elapsed = time.perf_counter() - started
    predicted_on = result.promised_on.isoformat() if result.promised_on else None
    expected_amount = item.get("promised_amount_paise")
    expected_on = item.get("promised_on")
    row = {
        "id": item["id"], "text": item["text"], "language": item["language"],
        "expected_intent": item["intent"], "predicted_intent": result.intent.value,
        "intent_ok": result.intent.value == item["intent"],
        "expected_amount": expected_amount, "predicted_amount": result.promised_amount,
        "amount_ok": (result.promised_amount == expected_amount) if expected_amount is not None else None,
        "expected_on": expected_on, "predicted_on": predicted_on,
        "date_ok": (predicted_on == expected_on) if expected_on is not None else None,
        "spurious_amount": expected_amount is None and result.promised_amount is not None,
        "spurious_date": expected_on is None and predicted_on is not None,
        "confidence": result.confidence, "fallback": "fallback" in result.flags,
        "flags": result.flags, "seconds": round(elapsed, 2),
    }
    return row


def summarize(rows: list[dict[str, Any]], brain_name: str, model: str | None) -> dict[str, Any]:
    n = len(rows)
    per_intent: dict[str, Counter] = defaultdict(Counter)
    per_language: dict[str, Counter] = defaultdict(Counter)
    confusion: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        per_intent[r["expected_intent"]]["n"] += 1
        per_intent[r["expected_intent"]]["ok"] += r["intent_ok"]
        per_language[r["language"]]["n"] += 1
        per_language[r["language"]]["ok"] += r["intent_ok"]
        confusion[r["expected_intent"]][r["predicted_intent"]] += 1

    tp = sum(1 for r in rows if r["expected_intent"] in COMMITMENT and r["predicted_intent"] in COMMITMENT)
    fp = sum(1 for r in rows
             if r["expected_intent"] not in COMMITMENT and r["predicted_intent"] in COMMITMENT)
    fn = sum(1 for r in rows
             if r["expected_intent"] in COMMITMENT and r["predicted_intent"] not in COMMITMENT)
    amount_rows = [r for r in rows if r["amount_ok"] is not None]
    date_rows = [r for r in rows if r["date_ok"] is not None]
    return {
        "brain": brain_name, "model": model, "items": n,
        "intent_accuracy": round(sum(r["intent_ok"] for r in rows) / n, 4) if n else None,
        "per_intent": {k: {"n": v["n"], "accuracy": round(v["ok"] / v["n"], 3)}
                       for k, v in sorted(per_intent.items())},
        "per_language": {k: {"n": v["n"], "accuracy": round(v["ok"] / v["n"], 3)}
                         for k, v in sorted(per_language.items())},
        "promise_detection": {
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "tp": tp, "fp": fp, "fn": fn,
        },
        "amount_accuracy": {"n": len(amount_rows),
                            "accuracy": round(sum(r["amount_ok"] for r in amount_rows) / len(amount_rows), 4)
                            if amount_rows else None},
        "date_accuracy": {"n": len(date_rows),
                          "accuracy": round(sum(r["date_ok"] for r in date_rows) / len(date_rows), 4)
                          if date_rows else None},
        "spurious_amount_rate": round(sum(r["spurious_amount"] for r in rows) / n, 4) if n else None,
        "spurious_date_rate": round(sum(r["spurious_date"] for r in rows) / n, 4) if n else None,
        "fallback_rate": round(sum(r["fallback"] for r in rows) / n, 4) if n else None,
        "mean_seconds": round(sum(r["seconds"] for r in rows) / n, 2) if n else None,
        "confusion": {k: dict(v) for k, v in sorted(confusion.items())},
    }


def run(brain: Brain, rows: list[dict[str, Any]], workers: int) -> list[dict[str, Any]]:
    if workers <= 1:
        return [score_one(brain, item) for item in rows]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda item: score_one(brain, item), rows))


def print_summary(summary: dict[str, Any]) -> None:
    print(f"brain            : {summary['brain']}" + (f" ({summary['model']})" if summary["model"] else ""))
    print(f"items            : {summary['items']}")
    print(f"intent accuracy  : {summary['intent_accuracy']:.1%}")
    pd = summary["promise_detection"]
    print(f"promise P / R    : {pd['precision']:.2f} / {pd['recall']:.2f}  "
          f"(tp={pd['tp']} fp={pd['fp']} fn={pd['fn']})")
    aa, da = summary["amount_accuracy"], summary["date_accuracy"]
    print(f"amount accuracy  : {aa['accuracy']:.1%} of {aa['n']} labelled")
    print(f"date accuracy    : {da['accuracy']:.1%} of {da['n']} labelled")
    print(f"spurious amt/date: {summary['spurious_amount_rate']:.1%} / {summary['spurious_date_rate']:.1%}")
    print(f"fallback rate    : {summary['fallback_rate']:.1%}")
    print("per intent       : "
          + ", ".join(f"{k} {v['accuracy']:.0%}" for k, v in summary["per_intent"].items()))
    print("per language     : "
          + ", ".join(f"{k} {v['accuracy']:.0%}" for k, v in summary["per_language"].items()))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m urudhi.eval_replies")
    parser.add_argument("--brain", choices=BRAIN_MODES, default="mock")
    parser.add_argument("--dataset", type=Path, default=Path("data/reply_eval.jsonl"))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    load_dotenv(Path.cwd() / ".env")
    configure_logging("WARNING")
    try:
        brain = make_brain(args.brain)
    except BrainConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)

    rows = load_dataset(args.dataset)[: args.limit]
    results = run(brain, rows, args.workers)
    summary = summarize(results, args.brain, getattr(brain, "model", None))
    failures = [r for r in results if not r["intent_ok"] or r["amount_ok"] is False
                or r["date_ok"] is False or r["spurious_amount"] or r["spurious_date"]]
    out = args.out or Path(f"data/reply_eval_{args.brain}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "failures": failures, "rows": results},
                              indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_summary(summary)
    print(f"failures         : {len(failures)} (details in {out})")


if __name__ == "__main__":
    main()
