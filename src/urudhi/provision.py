"""Provision real Razorpay test-mode Payment Links for selected commitments.

    python -m urudhi.provision --db data/live_demo.sqlite3 --commitment cmt_x [--commitment cmt_y]
    python -m urudhi.provision --db data/live_demo.sqlite3 --limit 3 [--dry-run]

Controlled and idempotent: the simulation batch is never turned into hundreds
of real links. A commitment is provisioned only if it exists, is live, and has
no instrument (or its earlier issue failed); a commitment that already holds a
Razorpay instrument is skipped, and one holding a *sandbox* instrument is
refused — a simulated record must not quietly become a live one. Each link is
created through the same code path the recovery loop uses
(:func:`urudhi.agent.instruments.issue_instrument`): amount = the approved
commitment amount, ``reference_id`` = commitment id, ``notes`` = {invoice_id,
commitment_id}, ``expire_by`` = deadline; the returned customer-facing URL is
stored verbatim and the creation is audited. A refusal is recorded on the
commitment as an instrument failure, never hidden.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from urudhi.agent.instruments import issue_instrument
from urudhi.audit.log import Actor
from urudhi.config import format_presence_report
from urudhi.ledger.models import InstrumentMode, PaymentCommitment
from urudhi.ledger.money import format_inr
from urudhi.observability import configure_logging
from urudhi.rails.razorpay_client import RailsClient, RazorpayRails
from urudhi.store import Store


@dataclass
class ProvisionResult:
    commitment_id: str
    outcome: str          # provisioned | skipped_has_instrument | refused_sandbox | skipped_not_live
    #                     | missing | failed | dry_run
    detail: str = ""
    instrument_id: str | None = None
    payment_url: str | None = None


def _eligible(c: PaymentCommitment, allow_sandbox_replace: bool) -> str | None:
    if not c.live:
        return f"skipped_not_live:{c.state}"
    if c.instrument_id and c.instrument_mode is InstrumentMode.RAZORPAY_TEST:
        return "skipped_has_instrument"
    if c.instrument_id and c.instrument_mode is InstrumentMode.SANDBOX and not allow_sandbox_replace:
        return "refused_sandbox"
    return None


def provision(store: Store, rails: RailsClient, commitment_ids: list[str] | None = None,
              limit: int | None = None, dry_run: bool = False, now: datetime | None = None,
              allow_sandbox_replace: bool = False) -> list[ProvisionResult]:
    now = now or datetime.now(UTC)
    if commitment_ids:
        targets: list[PaymentCommitment | None] = []
        for cid in commitment_ids:
            try:
                targets.append(store.get_commitment(cid))
            except KeyError:
                targets.append(None)
        pairs = list(zip(commitment_ids, targets, strict=True))
    else:
        live = [c for c in store.all_commitments() if c.live and _eligible(c, allow_sandbox_replace) is None]
        pairs = [(c.id, c) for c in live[: limit or len(live)]]

    results: list[ProvisionResult] = []
    for cid, c in pairs:
        if c is None:
            results.append(ProvisionResult(cid, "missing", "no such commitment in this ledger"))
            continue
        why = _eligible(c, allow_sandbox_replace)
        if why:
            results.append(ProvisionResult(cid, why.split(":")[0], why, c.instrument_id, c.payment_url))
            continue
        if dry_run:
            plan = f"would create {format_inr(c.committed_amount)} link due {c.due_on}"
            results.append(ProvisionResult(cid, "dry_run", plan))
            continue
        invoice = store.get_invoice(c.invoice_id)
        debtor = store.get_debtor(c.debtor_id)
        updated = issue_instrument(store, rails, invoice, debtor, c, now, actor=Actor.RAILS)
        store.put_commitment(updated)
        if updated.instrument_failed:
            results.append(ProvisionResult(cid, "failed", updated.instrument_failure))
        else:
            detail = f"{format_inr(c.committed_amount)} · mode {updated.instrument_mode}"
            results.append(ProvisionResult(cid, "provisioned", detail,
                                           updated.instrument_id, updated.payment_url))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m urudhi.provision")
    parser.add_argument("--db", required=True)
    parser.add_argument("--commitment", action="append", default=[], help="commitment id (repeatable)")
    parser.add_argument("--limit", type=int, default=None, help="provision up to N eligible commitments")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-sandbox-replace", action="store_true",
                        help="replace a sandbox instrument with a real one (turns a simulated record live)")
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env")
    configure_logging("WARNING")
    for line in format_presence_report().splitlines():
        print(f"  {line}")
    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not key_id.startswith("rzp_test_") or not key_secret:
        sys.exit("error: RAZORPAY_KEY_ID (rzp_test_…) and RAZORPAY_KEY_SECRET are required")
    if not args.commitment and args.limit is None:
        sys.exit("error: give --commitment ids or --limit N")
    store = Store(args.db)
    if store.origin() == "simulation" and not args.allow_sandbox_replace:
        sys.exit("error: this ledger was written by the simulator; refusing to attach real "
                 "instruments to simulated records (use --allow-sandbox-replace to override)")
    results = provision(store, RazorpayRails(key_id, key_secret), args.commitment or None,
                        args.limit, args.dry_run, allow_sandbox_replace=args.allow_sandbox_replace)
    for r in results:
        extra = f" → {r.instrument_id} {r.payment_url}" if r.instrument_id else ""
        print(f"{r.commitment_id:<40} {r.outcome:<24} {r.detail}{extra}")


if __name__ == "__main__":
    main()
