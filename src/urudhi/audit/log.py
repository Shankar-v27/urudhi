"""Tamper-evident audit log.

Every action Urudhi takes — every message sent, promise recorded, policy gate
allowed or blocked, payment observed, escalation — is an :class:`AuditEvent`.
Events form a hash chain: each event stores the SHA-256 of the previous event's
hash plus its own canonical content. Verifying the chain proves the trail is
complete and unedited; a recovery number that can't survive that check doesn't
deserve to be called measured.

This module is pure: hashing and verification only. Persistence enforces
append-only storage separately.
"""

from __future__ import annotations

import enum
import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

GENESIS_HASH = "0" * 64


class Actor(enum.StrEnum):
    AGENT = "agent"      # the LLM-driven negotiator
    POLICY = "policy"    # deterministic authority gates
    RAILS = "rails"      # Razorpay webhook-driven events
    SYSTEM = "system"    # scheduler, batch runner
    HUMAN = "human"      # operator actions after escalation


class EventKind(enum.StrEnum):
    MESSAGE_SENT = "message_sent"
    MESSAGE_RECEIVED = "message_received"
    PROMISE_RECORDED = "promise_recorded"
    PROMISE_RESOLVED = "promise_resolved"
    PAYMENT_OBSERVED = "payment_observed"
    GATE_ALLOWED = "gate_allowed"
    GATE_BLOCKED = "gate_blocked"
    OFFER_MADE = "offer_made"
    INVOICE_STATE_CHANGED = "invoice_state_changed"
    ESCALATED = "escalated"
    STOP_CONTACT_HONORED = "stop_contact_honored"
    DISPUTE_RECORDED = "dispute_recorded"
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"


class AuditEvent(BaseModel):
    """One link in the chain. ``hash`` covers all fields except itself."""

    seq: int                      # 1-based position in the chain
    at: datetime
    actor: Actor
    kind: EventKind
    invoice_id: str | None = None
    debtor_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str
    hash: str = ""

    def canonical(self) -> str:
        """Deterministic JSON of everything the hash must cover."""
        body = self.model_dump(mode="json", exclude={"hash"})
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def compute_hash(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()

    def sealed(self) -> "AuditEvent":
        return self.model_copy(update={"hash": self.compute_hash()})


def make_event(
    *,
    seq: int,
    at: datetime,
    actor: Actor,
    kind: EventKind,
    prev_hash: str,
    invoice_id: str | None = None,
    debtor_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Build and seal the next event in a chain."""
    return AuditEvent(
        seq=seq,
        at=at,
        actor=actor,
        kind=kind,
        invoice_id=invoice_id,
        debtor_id=debtor_id,
        payload=payload or {},
        prev_hash=prev_hash,
    ).sealed()


class ChainError(Exception):
    """The audit chain is broken: an event was altered, dropped, or reordered."""


def verify_chain(events: Iterable[AuditEvent]) -> int:
    """Verify hash linkage and sequence; return the number of events checked."""
    prev_hash = GENESIS_HASH
    expected_seq = 1
    count = 0
    for event in events:
        if event.seq != expected_seq:
            raise ChainError(f"expected seq {expected_seq}, found {event.seq}")
        if event.prev_hash != prev_hash:
            raise ChainError(f"event {event.seq}: prev_hash does not link to previous event")
        if event.compute_hash() != event.hash:
            raise ChainError(f"event {event.seq}: content does not match its hash")
        prev_hash = event.hash
        expected_seq += 1
        count += 1
    return count
