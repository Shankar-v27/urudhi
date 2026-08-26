"""Persistence guarantees the recovery numbers rest on."""

import sqlite3
import threading
from datetime import UTC, datetime

import pytest

from urudhi.audit.log import Actor, EventKind, verify_chain
from urudhi.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "t.sqlite3") as s:
        yield s


class TestAuditConcurrency:
    def test_concurrent_appends_never_fork_the_chain(self, store):
        errors = []

        def worker(n):
            try:
                for i in range(25):
                    store.append_event(at=datetime(2026, 8, 24, 10, n, i, tzinfo=UTC),
                                       actor=Actor.SYSTEM, kind=EventKind.MESSAGE_SENT,
                                       payload={"w": n, "i": i})
            except Exception as error:  # pragma: no cover - the assertion below reports it
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        events = list(store.audit_events())
        assert len(events) == 200
        assert [e.seq for e in events] == list(range(1, 201))
        assert verify_chain(events) == 200

    def test_update_and_delete_are_blocked_by_triggers(self, store):
        store.append_event(at=datetime(2026, 8, 24, tzinfo=UTC), actor=Actor.AGENT,
                           kind=EventKind.MESSAGE_SENT, payload={})
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._conn.execute("UPDATE audit_events SET data = '{}' WHERE seq = 1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._conn.execute("DELETE FROM audit_events WHERE seq = 1")

    def test_transaction_rolls_back_on_error(self, store):
        with pytest.raises(RuntimeError), store.transaction():
            store.append_event(at=datetime(2026, 8, 24, tzinfo=UTC), actor=Actor.AGENT,
                               kind=EventKind.MESSAGE_SENT, payload={})
            raise RuntimeError("boom")
        assert store.audit_count() == 0


class TestOutboundClaims:
    def test_claim_is_idempotent(self, store):
        now = datetime(2026, 8, 24, 11, 0, tzinfo=UTC)
        assert store.claim_outbound("inv_1:2026-08-24:1", "inv_1", "2026-08-24", now, "email", {})
        assert not store.claim_outbound("inv_1:2026-08-24:1", "inv_1", "2026-08-24", now, "email", {})
        total, today, last = store.attempt_facts("inv_1", "2026-08-24")
        assert (total, today, last) == (1, 1, now)

    def test_claims_count_even_when_never_marked_sent(self, store):
        now = datetime(2026, 8, 24, 11, 0, tzinfo=UTC)
        store.claim_outbound("k1", "inv_1", "2026-08-24", now, "email", {})
        store.mark_outbound("k1", "failed")
        assert store.attempt_facts("inv_1", "2026-08-24")[0] == 1

    def test_since_filter_for_human_release(self, store):
        early = datetime(2026, 8, 20, 11, 0, tzinfo=UTC)
        late = datetime(2026, 8, 24, 11, 0, tzinfo=UTC)
        store.claim_outbound("k1", "inv_1", "2026-08-20", early, "email", {})
        store.claim_outbound("k2", "inv_1", "2026-08-24", late, "email", {})
        assert store.attempt_facts("inv_1", "2026-08-24")[0] == 2
        assert store.attempt_facts("inv_1", "2026-08-24", since=datetime(2026, 8, 22, tzinfo=UTC))[0] == 1


class TestWebhookLedger:
    def test_rulings_are_remembered_once(self, store):
        now = datetime(2026, 8, 24, tzinfo=UTC)
        assert store.record_webhook_event("evt_1", "unmatched", now, {"x": 1})
        assert not store.record_webhook_event("evt_1", "recorded", now, {})
        assert store.webhook_event_status("evt_1") == "unmatched"
        assert store.webhook_event_status("evt_2") is None
