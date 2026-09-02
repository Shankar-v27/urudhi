"""Serve the Urudhi API.

    python -m urudhi.api [--db urudhi.sqlite3] [--sim-db data/run.sqlite3] [--brain mock|claude] [--port 8000]

``--db`` is the primary ledger (normally the live test-mode ledger the
webhook receiver writes to); ``--sim-db`` optionally adds the batch runner's
simulation ledger as a second, read-mostly source so the dashboard can show
Live Test, Simulation, or All — every row labelled with where it came from.
Configuration comes from the environment (a ``.env`` in the working
directory is loaded): RAZORPAY_WEBHOOK_SECRET and URUDHI_API_TOKEN are
required; ANTHROPIC_* are required for ``--brain claude``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from urudhi.agent.brain import BRAIN_MODES, BrainConfigError, make_brain
from urudhi.agent.loop import RecoveryAgent
from urudhi.agent.policy import PolicyConfig
from urudhi.api.app import create_app
from urudhi.config import format_presence_report
from urudhi.observability import configure_logging, get_logger
from urudhi.rails.razorpay_client import FakeRails, RazorpayRails
from urudhi.store import Store
from urudhi.transport.email import EmailOutbox

log = get_logger("urudhi.api.main")


def main() -> None:
    load_dotenv(Path.cwd() / ".env")
    parser = argparse.ArgumentParser(prog="python -m urudhi.api")
    parser.add_argument("--db", default="urudhi.sqlite3", help="primary (live test-mode) ledger")
    parser.add_argument("--sim-db", default=None, help="simulation ledger written by python -m urudhi.sim")
    parser.add_argument("--origin", choices=["auto", "live_test", "simulation"], default="auto",
                        help="provenance label for --db rows (auto: simulation if the batch runner wrote it)")
    parser.add_argument("--brain", choices=BRAIN_MODES, default="mock")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_logging(args.log_level)
    for line in format_presence_report().splitlines():
        log.info("config " + line)

    try:
        brain = make_brain(args.brain)
    except BrainConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)

    policy = PolicyConfig(timezone=os.environ.get("URUDHI_TZ", "Asia/Kolkata"))
    outbox = EmailOutbox.from_env()
    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    if key_id.startswith("rzp_test_") and os.environ.get("RAZORPAY_KEY_SECRET"):
        rails, rails_mode = RazorpayRails(key_id, os.environ["RAZORPAY_KEY_SECRET"]), "razorpay_test"
    else:
        rails, rails_mode = FakeRails(), "sandbox"

    store = Store(args.db)
    sim_store = Store(args.sim_db) if args.sim_db else None
    origin = None if args.origin == "auto" else args.origin
    if (origin or store.origin()) == "simulation":
        # A simulated ledger is never driven by the real rail: replies/ticks on it stay sandboxed.
        rails, rails_mode = FakeRails(), "sandbox"
    agent = RecoveryAgent(store, brain, outbox, policy, rails=rails)
    try:
        app = create_app(
            store, webhook_secret=os.environ.get("RAZORPAY_WEBHOOK_SECRET", ""),
            api_token=os.environ.get("URUDHI_API_TOKEN", ""), agent=agent, policy=policy,
            cors_origins=[o.strip() for o in os.environ.get("URUDHI_CORS_ORIGINS", "").split(",")
                          if o.strip()] or None,
            brain_name=agent.brain_name, transport_mode=f"email:{outbox.mode}",
            rails_mode=rails_mode, simulation_store=sim_store, store_origin=origin,
            store_path=args.db, simulation_path=args.sim_db or "",
        )
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)
    log.info("startup", db=args.db, sim_db=args.sim_db or "-", origin=origin or store.origin(),
             brain=agent.brain_name, transport=f"email:{outbox.mode}", rails=rails_mode, tz=policy.timezone)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
