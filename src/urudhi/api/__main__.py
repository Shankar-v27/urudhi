"""Serve the Urudhi API.

    python -m urudhi.api [--db urudhi.sqlite3] [--brain mock|claude] [--port 8000]

Point ``--db`` at a batch run's database (``python -m urudhi.sim --db …``) to
browse it in the dashboard, or at a fresh file for a live-mode receiver.
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
    parser = argparse.ArgumentParser(prog="python -m urudhi.api")
    parser.add_argument("--db", default="urudhi.sqlite3")
    parser.add_argument("--brain", choices=BRAIN_MODES, default="mock")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    load_dotenv(Path.cwd() / ".env")
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
        rails, rails_mode = RazorpayRails(key_id, os.environ["RAZORPAY_KEY_SECRET"]), "razorpay-test"
    else:
        rails, rails_mode = FakeRails(), "fake"

    store = Store(args.db)
    agent = RecoveryAgent(store, brain, outbox, policy, rails=rails)
    try:
        app = create_app(
            store, webhook_secret=os.environ.get("RAZORPAY_WEBHOOK_SECRET", ""),
            api_token=os.environ.get("URUDHI_API_TOKEN", ""), agent=agent, policy=policy,
            cors_origins=[o for o in os.environ.get("URUDHI_CORS_ORIGINS", "").split(",") if o] or None,
            brain_name=agent.brain_name, transport_mode=f"email:{outbox.mode}",
            rails_mode=rails_mode,
        )
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)
    log.info("startup", db=args.db, brain=agent.brain_name, transport=f"email:{outbox.mode}",
             rails=rails_mode, tz=policy.timezone)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
