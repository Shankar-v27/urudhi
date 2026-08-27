"""Commitment history → a structured, explainable credibility profile.

Pure functions over :class:`PaymentCommitment` rows. The profile is what
prioritisation, the brain's decision context, the dashboard and the
experiment report all read, so "4 of 5 commitments fulfilled" means the
same thing everywhere.
"""

from __future__ import annotations

import statistics
from datetime import datetime

from pydantic import BaseModel

from urudhi.ledger.models import CommitmentState, PaymentCommitment
from urudhi.ledger.money import Paise, format_inr


class CommitmentProfile(BaseModel):
    commitments: int = 0
    active: int = 0
    fulfilled: int = 0
    fulfilled_on_time: int = 0
    partially_fulfilled: int = 0
    missed: int = 0
    cancelled: int = 0
    fulfillment_rate: float | None = None     # fulfilled / (fulfilled + missed)
    average_delay_days: float | None = None   # mean days_late over fulfilled commitments
    average_committed: Paise | None = None
    amount_committed: Paise = 0               # sum over resolved + live commitments
    amount_received: Paise = 0                # rail money applied to commitments
    last_outcome: str | None = None
    credibility: float = 0.5                  # Laplace-smoothed fulfilment belief, 0..1
    reasons: list[str] = []

    def describe(self) -> str:
        if self.commitments == 0:
            return "no commitment history"
        return (f"{self.fulfilled} of {self.fulfilled + self.missed} resolved commitments "
                f"fulfilled" + (f", avg delay {self.average_delay_days:.1f} d"
                                if self.average_delay_days is not None else ""))


def profile_for(commitments: list[PaymentCommitment],
                since: datetime | None = None) -> CommitmentProfile:
    """Summarise a debtor's commitment record (optionally only since a human release)."""
    rows = [c for c in commitments if since is None or c.created_at >= since]
    rows = [c for c in rows if c.state is not CommitmentState.SUPERSEDED]
    fulfilled = [c for c in rows if c.state is CommitmentState.FULFILLED]
    missed = [c for c in rows if c.state is CommitmentState.MISSED]
    partial = [c for c in rows if c.state is CommitmentState.PARTIALLY_FULFILLED]
    active = [c for c in rows if c.state is CommitmentState.ACTIVE]
    cancelled = [c for c in rows if c.state is CommitmentState.CANCELLED]
    resolved = len(fulfilled) + len(missed)
    rate = round(len(fulfilled) / resolved, 4) if resolved else None
    delays = [c.days_late for c in fulfilled]
    on_time = sum(1 for c in fulfilled if c.days_late == 0)
    # Laplace-smoothed belief that the next commitment is kept: a fresh debtor
    # sits at 0.5, one kept commitment moves it to 0.67, one miss to 0.33.
    credibility = round((len(fulfilled) + 1) / (resolved + 2), 4)
    ordered = sorted(rows, key=lambda c: (c.resolved_at or c.created_at))
    last = ordered[-1].state.value if ordered else None
    reasons: list[str] = []
    if resolved:
        reasons.append(f"{len(fulfilled)} of {resolved} resolved commitments fulfilled")
        if delays and max(delays) > 0:
            reasons.append(f"average {statistics.mean(delays):.1f} day(s) late when fulfilled")
    if partial:
        reasons.append(f"{len(partial)} commitment(s) partially paid, deadline ahead")
    if active:
        reasons.append(f"{len(active)} commitment(s) running")
    if missed and missed[-1] is ordered[-1]:
        reasons.append("most recent commitment was missed")
    counted = fulfilled + missed + partial + active
    return CommitmentProfile(
        commitments=len(rows), active=len(active), fulfilled=len(fulfilled),
        fulfilled_on_time=on_time, partially_fulfilled=len(partial), missed=len(missed),
        cancelled=len(cancelled), fulfillment_rate=rate,
        average_delay_days=round(statistics.mean(delays), 2) if delays else None,
        average_committed=(sum(c.committed_amount for c in counted) // len(counted)
                           if counted else None),
        amount_committed=sum(c.committed_amount for c in counted),
        amount_received=sum(c.amount_received for c in rows),
        last_outcome=last, credibility=credibility, reasons=reasons,
    )


def describe_commitment(c: PaymentCommitment) -> str:
    via = f" via {c.instrument_type.value.replace('_', ' ')}" if c.instrument_type else ""
    received = f", {format_inr(c.amount_received)} received" if c.amount_received else ""
    return f"{format_inr(c.committed_amount)} by {c.due_on.isoformat()}{via}{received}"
