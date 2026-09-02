"""Run the synthetic batch and publish the reports.

    python -m urudhi.sim --brain mock                     # Urudhi arm only, deterministic
    python -m urudhi.sim --brain mock --arms all          # + no-action and baseline arms,
                                                          #   uplift, attribution, sensitivity
    python -m urudhi.sim --brain claude --count 20 --days 14 --arms all

Writes ``data/report.json`` (the Urudhi arm), ``data/experiment.json`` (all
arms) and, with ``--db``, the Urudhi arm's ledger + audit chain for the API.
Same seed, same numbers under the mock brain — reviewers are invited to re-run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from urudhi.agent.brain import BRAIN_MODES, BrainConfigError, MockBrain, make_brain
from urudhi.agent.policy import PolicyConfig
from urudhi.observability import configure_logging
from urudhi.sim.report import (
    build_experiment,
    build_report,
    sensitivity_grid,
    sensitivity_row,
    summarize_for_stdout,
)
from urudhi.sim.runner import Arm, RunConfig, run_batch


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m urudhi.sim")
    parser.add_argument("--brain", choices=BRAIN_MODES, default="mock")
    parser.add_argument("--arms", choices=["urudhi", "all", "baseline", "no_action"], default="urudhi")
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--out", type=Path, default=Path("data/report.json"))
    parser.add_argument("--experiment-out", type=Path, default=Path("data/experiment.json"))
    parser.add_argument("--db", default=":memory:",
                        help="persist the Urudhi arm's ledger and audit chain to this "
                             "SQLite file (serve it with python -m urudhi.api)")
    parser.add_argument("--no-sensitivity", action="store_true")
    parser.add_argument("--workers", type=int, default=1,
                        help="parallel chases per day for LLM-bound runs (mock stays deterministic at 1)")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()

    load_dotenv(Path.cwd() / ".env")
    configure_logging(args.log_level)
    try:
        brain = make_brain(args.brain)
    except BrainConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)

    if args.db != ":memory:" and Path(args.db).exists():
        Path(args.db).unlink()  # a run is a fresh world; stale state would lie
        for suffix in ("-wal", "-shm"):
            Path(args.db + suffix).unlink(missing_ok=True)

    arms = {"urudhi": [Arm.URUDHI], "all": [Arm.NO_ACTION, Arm.BASELINE, Arm.URUDHI],
            "baseline": [Arm.BASELINE], "no_action": [Arm.NO_ACTION]}[args.arms]
    results = {}
    for arm in arms:
        started = time.perf_counter()
        config = RunConfig(days=args.days, count=args.count, seed=args.seed, arm=arm,
                           workers=args.workers if arm is Arm.URUDHI else 1)
        results[arm] = run_batch(config, brain=brain if arm is Arm.URUDHI else MockBrain(),
                                 db_path=args.db if arm is Arm.URUDHI else ":memory:")
        print(f"arm {arm.value:<10} done in {time.perf_counter() - started:5.1f}s "
              f"(brain: {results[arm].brain_name})", file=sys.stderr)

    if Arm.URUDHI in results:
        report = build_report(results[Arm.URUDHI])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        money, m = report["money"], report["metrics"]
        print(f"batch      : {args.count} invoices, {args.days} days, seed {args.seed}, "
              f"brain {report['run']['brain']}")
        print(f"outstanding: {money['outstanding_inr']}")
        print(f"recovered  : {money['recovered_inr']}  ({money['recovery_rate']:.1%} of outstanding, "
              f"observed via webhooks; simulation)")
        print(f"waived     : {money['waived_paise'] / 100:,.0f} INR discount cost; "
              f"net {money['net_recovered_paise'] / 100:,.0f}")
        print(f"promises   : {m['promises_made']} made — {m['promises_kept']} kept, "
              f"{m['promises_broken']} broken, {m['promises_partially_kept']} partially kept")
        print(f"offers     : {m['offers_made']} made, {m['offers_accepted']} accepted, "
              f"{m['offers_settled']} settled, {m['offers_expired']} expired")
        print(f"exceptions : {len(report['exceptions'])} unresolved invoices (full list in {args.out})")
        print(f"audit      : {report['audit']['events']} events, "
              f"chain verified: {report['audit']['chain_verified']}")

    if args.arms == "all":
        sensitivity = []
        if not args.no_sensitivity:
            for parameter, value in sensitivity_grid():
                policy = PolicyConfig(**{parameter: value})
                config = RunConfig(days=args.days, count=args.count, seed=args.seed, arm=Arm.URUDHI)
                sensitivity.append(sensitivity_row(
                    parameter, value, run_batch(config, policy=policy, brain=MockBrain())))
            print(f"sensitivity: {len(sensitivity)} mock-brain runs", file=sys.stderr)
        experiment = build_experiment(
            results, sensitivity,
            generated_by=f"python -m urudhi.sim --brain {args.brain} --arms all "
                         f"--days {args.days} --count {args.count} --seed {args.seed}",
        )
        args.experiment_out.parent.mkdir(parents=True, exist_ok=True)
        args.experiment_out.write_text(
            json.dumps(experiment, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print()
        print(summarize_for_stdout(experiment))
        print(f"experiment : {args.experiment_out}")


if __name__ == "__main__":
    main()
