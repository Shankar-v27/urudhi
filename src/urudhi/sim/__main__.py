"""Run the synthetic batch and publish the report.

    python -m urudhi.sim [--days 21] [--count 120] [--seed 2026] [--out data/report.json]

The report is written as JSON alongside a summary on stdout. Same seed, same
numbers — reviewers are invited to re-run it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from urudhi.sim.report import build_report
from urudhi.sim.runner import RunConfig, run_batch


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m urudhi.sim")
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--out", type=Path, default=Path("data/report.json"))
    args = parser.parse_args()

    result = run_batch(RunConfig(days=args.days, count=args.count, seed=args.seed))
    report = build_report(result)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    money = report["money"]
    promises = report["promises"]
    print(f"batch      : {args.count} invoices, {args.days} days, seed {args.seed}")
    print(f"outstanding: {money['outstanding_inr']}")
    print(f"recovered  : {money['recovered_inr']}  "
          f"({money['recovery_rate']:.1%} of outstanding)")
    print(f"promises   : {promises['made']} made — {promises['kept']} kept, "
          f"{promises['broken']} broken, {promises['partially_kept']} partially kept")
    print(f"exceptions : {len(report['exceptions'])} unresolved invoices "
          f"(full list in {args.out})")
    print(f"audit      : {report['audit']['events']} events, "
          f"chain verified: {report['audit']['chain_verified']}")


if __name__ == "__main__":
    main()
