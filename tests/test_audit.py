from datetime import datetime

import pytest

from urudhi.audit.log import (
    GENESIS_HASH,
    Actor,
    ChainError,
    EventKind,
    make_event,
    verify_chain,
)


def build_chain(n: int):
    events = []
    prev = GENESIS_HASH
    for i in range(1, n + 1):
        event = make_event(
            seq=i,
            at=datetime(2026, 8, 24, 10, 0, i),
            actor=Actor.AGENT,
            kind=EventKind.MESSAGE_SENT,
            prev_hash=prev,
            invoice_id="inv_1",
            payload={"channel": "whatsapp", "n": i},
        )
        events.append(event)
        prev = event.hash
    return events


class TestChain:
    def test_valid_chain_verifies(self):
        assert verify_chain(build_chain(5)) == 5

    def test_empty_chain_is_valid(self):
        assert verify_chain([]) == 0

    def test_edited_payload_detected(self):
        events = build_chain(3)
        events[1] = events[1].model_copy(update={"payload": {"channel": "whatsapp", "n": 99}})
        with pytest.raises(ChainError, match="does not match its hash"):
            verify_chain(events)

    def test_dropped_event_detected(self):
        events = build_chain(3)
        del events[1]
        with pytest.raises(ChainError, match="expected seq 2"):
            verify_chain(events)

    def test_reordered_events_detected(self):
        events = build_chain(3)
        events[1], events[2] = events[2], events[1]
        with pytest.raises(ChainError):
            verify_chain(events)

    def test_reseal_after_edit_still_detected_by_linkage(self):
        # An attacker who edits an event AND recomputes its hash still breaks
        # the prev_hash link of the next event.
        events = build_chain(3)
        tampered = events[1].model_copy(update={"payload": {"n": 99}}).sealed()
        events[1] = tampered
        with pytest.raises(ChainError, match="prev_hash"):
            verify_chain(events)

    def test_hash_is_stable_for_unicode_payloads(self):
        event = make_event(
            seq=1,
            at=datetime(2026, 8, 24, 10, 0),
            actor=Actor.AGENT,
            kind=EventKind.MESSAGE_SENT,
            prev_hash=GENESIS_HASH,
            payload={"text": "₹1,000 by Friday — உறுதி"},
        )
        assert event.compute_hash() == event.hash
        assert verify_chain([event]) == 1
