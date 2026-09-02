import hashlib
import hmac
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from urudhi.agent.brain import MockBrain
from urudhi.agent.loop import RecoveryAgent
from urudhi.api.app import create_app, mask_email, mask_phone
from urudhi.ledger.models import Debtor, Invoice, InvoiceState
from urudhi.rails.razorpay_client import FakeRails
from urudhi.store import Store

SECRET = "whsec_test"
TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Outbox:
    def __init__(self):
        self.sent = []

    def send(self, debtor, channel, text, *, subject, reference):
        self.sent.append((debtor.email, subject, text))
        return "m1"


@pytest.fixture
def world():
    store = Store(":memory:")
    store.put_debtor(Debtor(
        id="deb_1", name="Kumar Textiles", contact_name="Kumar",
        phone="+919800000001", email="kumar@example.in",
    ))
    store.put_invoice(Invoice(
        id="inv_1", debtor_id="deb_1", number="URU/2026/0001",
        amount=100_000, issued_on=date(2026, 6, 1), due_on=date(2026, 7, 1),
    ))
    outbox = Outbox()
    agent = RecoveryAgent(store, MockBrain(), outbox, rails=FakeRails())
    app = create_app(store, webhook_secret=SECRET, api_token=TOKEN, agent=agent,
                     brain_name="mock", transport_mode="email:sandbox", rails_mode="fake",
                     data_dir="/nonexistent")
    return store, outbox, TestClient(app)


@pytest.fixture
def client(world):
    return world[2]


def signed(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"x-razorpay-signature": sig}


def payment_event(event_id="evt_1", invoice_id="inv_1", amount=100_000):
    return {
        "id": event_id,
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": f"pay_rzp_{event_id}", "amount": amount, "currency": "INR", "method": "upi",
            "notes": {"invoice_id": invoice_id},
        }}},
    }


class TestStartup:
    def test_refuses_empty_webhook_secret(self):
        with pytest.raises(RuntimeError, match="RAZORPAY_WEBHOOK_SECRET"):
            create_app(Store(":memory:"), webhook_secret="", api_token=TOKEN)

    def test_refuses_missing_api_token(self):
        with pytest.raises(RuntimeError, match="URUDHI_API_TOKEN"):
            create_app(Store(":memory:"), webhook_secret=SECRET, api_token="")


class TestWebhook:
    def test_signed_payment_is_recorded(self, client):
        raw, headers = signed(payment_event())
        response = client.post("/webhooks/razorpay", content=raw, headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "recorded"
        assert client.get("/api/summary", headers=AUTH).json()["recovered_paise"] == 100_000

    def test_unsigned_request_rejected(self, client):
        response = client.post(
            "/webhooks/razorpay", content=b"{}",
            headers={"x-razorpay-signature": "bad"},
        )
        assert response.status_code == 400

    def test_replay_is_acknowledged_but_ignored(self, client):
        raw, headers = signed(payment_event())
        client.post("/webhooks/razorpay", content=raw, headers=headers)
        again = client.post("/webhooks/razorpay", content=raw, headers=headers)
        assert again.status_code == 200 and again.json()["status"] == "replay_ignored"

    def test_unknown_invoice_is_acknowledged_and_audited(self, client):
        raw, headers = signed(payment_event(invoice_id="inv_404"))
        response = client.post("/webhooks/razorpay", content=raw, headers=headers)
        assert response.status_code == 200 and response.json()["status"] == "unmatched"
        events = client.get("/api/audit", headers=AUTH).json()["events"]
        assert events[-1]["kind"] == "payment_unmatched"

    def test_late_payment_after_settled_does_not_500(self, client):
        raw, headers = signed(payment_event())
        client.post("/webhooks/razorpay", content=raw, headers=headers)
        raw2, headers2 = signed(payment_event(event_id="evt_2", amount=5))
        response = client.post("/webhooks/razorpay", content=raw2, headers=headers2)
        assert response.status_code == 200 and response.json()["status"] == "rejected"

    def test_unhandled_event_is_acknowledged(self, client):
        event = payment_event() | {"event": "refund.processed"}
        raw, headers = signed(event)
        response = client.post("/webhooks/razorpay", content=raw, headers=headers)
        assert response.status_code == 200 and response.json()["status"] == "ignored"


class TestAuthAndPii:
    def test_api_requires_token(self, client):
        assert client.get("/api/invoices").status_code == 401
        assert client.get("/api/invoices", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert client.get("/api/invoices", headers=AUTH).status_code == 200

    def test_health_is_open_and_has_no_ledger_data(self, client):
        health = client.get("/health").json()
        assert health["status"] == "ok" and health["brain"] == "mock"
        assert health["audit_chain"]["verified"] is True
        assert "counters" in health and "invoices" in health
        assert "kumar" not in json.dumps(health).lower()

    def test_detail_masks_contact_details(self, client):
        detail = client.get("/api/invoices/inv_1", headers=AUTH).json()
        assert detail["debtor"]["phone"] != "+919800000001"
        assert detail["debtor"]["email"] != "kumar@example.in"
        assert "example.in" in detail["debtor"]["email"]
        assert "+919800000001" not in json.dumps(detail)

    def test_masking_helpers(self):
        assert mask_phone("+919800000001") == "+91••••••••01"
        assert mask_email("kumar@example.in") == "k•••@example.in"


class TestReadApi:
    def test_invoice_detail_joins_everything(self, client):
        raw, headers = signed(payment_event())
        client.post("/webhooks/razorpay", content=raw, headers=headers)
        detail = client.get("/api/invoices/inv_1", headers=AUTH).json()
        assert detail["invoice"]["state"] == "paid"
        assert detail["debtor"]["name"] == "Kumar Textiles"
        assert len(detail["payments"]) == 1
        assert len(detail["events"]) == 1
        assert detail["explain"]["priority"]["score"] >= 0

    def test_missing_invoice_404s(self, client):
        assert client.get("/api/invoices/nope", headers=AUTH).status_code == 404

    def test_audit_endpoint_reports_verified_chain(self, client):
        raw, headers = signed(payment_event())
        client.post("/webhooks/razorpay", content=raw, headers=headers)
        audit = client.get("/api/audit", headers=AUTH).json()
        assert audit["chain"] == {"verified": True, "events": 1}

    def test_experiment_and_eval_404_when_absent(self, client):
        assert client.get("/api/experiment", headers=AUTH).status_code == 404
        assert client.get("/api/reply-eval", headers=AUTH).status_code == 404


class TestRuntimeAndInbound:
    def test_tick_chases_and_explains(self, world):
        store, outbox, client = world
        response = client.post("/api/run/tick", json={"max_invoices": 5, "at": "2026-08-24T11:00:00+05:30"},
                               headers=AUTH)
        assert response.status_code == 200
        chased = response.json()["chased"]
        assert chased and chased[0]["action"] == "message_sent"
        assert len(outbox.sent) == 1
        explain = client.get("/api/invoices/inv_1/explain", headers=AUTH).json()
        assert explain["latest_decision"]["final"] == "payment_link"
        assert any(g["gate"] == "contact" and g["ok"] for g in explain["latest_decision"]["gates"])
        assert explain["priority"]["reasons"]

    def test_inbound_reply_flows_through_the_brain(self, world):
        store, _, client = world
        response = client.post("/inbound/reply", json={"invoice_id": "inv_1", "text": "will pay ₹1,000 in 2 days"},
                               headers=AUTH)
        assert response.status_code == 200 and response.json()["action"] == "commitment_created"
        assert store.get_invoice("inv_1").state is InvoiceState.PROMISED

    def test_inbound_email_is_matched_by_subject_reference(self, world):
        store, _, client = world
        response = client.post("/inbound/email", json={
            "from": "Kumar <kumar@example.in>",
            "subject": "Re: Invoice URU/2026/0001 — payment reminder [URU/2026/0001]",
            "text": "Please stop messaging me.",
        }, headers=AUTH)
        assert response.status_code == 200
        assert response.json()["matched_invoice"] == "inv_1"
        assert store.get_invoice("inv_1").state is InvoiceState.STOP_CONTACT

    def test_inbound_email_falls_back_to_sender(self, world):
        store, _, client = world
        response = client.post("/inbound/email", json={
            "from": "kumar@example.in", "subject": "hello", "text": "Invoice amount itself is wrong.",
        }, headers=AUTH)
        assert response.json()["matched_invoice"] == "inv_1"
        assert store.get_invoice("inv_1").state is InvoiceState.DISPUTED

    def test_unmatched_inbound_email_404s(self, client):
        response = client.post("/inbound/email", json={"from": "x@y.z", "subject": "?", "text": "hi"},
                               headers=AUTH)
        assert response.status_code == 404


class TestHumanWorkflow:
    def test_queue_and_actions(self, world):
        store, _, client = world
        client.post("/inbound/reply", json={"invoice_id": "inv_1", "text": "Invoice amount itself is wrong."},
                    headers=AUTH)
        queue = client.get("/api/escalations", headers=AUTH).json()
        assert len(queue) == 1 and queue[0]["state"] == "disputed" and not queue[0]["acknowledged"]

        ack = client.post("/api/invoices/inv_1/human",
                          json={"action": "acknowledge", "operator": "asha"}, headers=AUTH)
        assert ack.status_code == 200
        assert client.get("/api/escalations", headers=AUTH).json()[0]["acknowledged"] is True

        bad = client.post("/api/invoices/inv_1/human",
                          json={"action": "release", "operator": "asha", "notes": ""}, headers=AUTH)
        assert bad.status_code == 409

        release = client.post("/api/invoices/inv_1/human",
                              json={"action": "release", "operator": "asha",
                                    "notes": "rate corrected; debtor agreed"}, headers=AUTH)
        assert release.status_code == 200 and release.json()["to_state"] == "outstanding"
        assert client.get("/api/escalations", headers=AUTH).json() == []
        events = client.get("/api/invoices/inv_1", headers=AUTH).json()["events"]
        assert events[-1]["actor"] == "human" and events[-1]["payload"]["action"] == "release"

    def test_close_needs_a_reason_and_is_terminal(self, world):
        store, _, client = world
        response = client.post("/api/invoices/inv_1/human",
                               json={"action": "close", "operator": "asha", "notes": "written off Q3"},
                               headers=AUTH)
        assert response.status_code == 200 and response.json()["to_state"] == "closed"
        assert client.post("/api/invoices/inv_1/human",
                           json={"action": "acknowledge", "operator": "asha"}, headers=AUTH).status_code == 409


class TestRazorpayShapedDelivery:
    """Real Razorpay bodies have no top-level "id"; the event id is a header."""

    def _razorpay_body(self, kind="payment_link.paid"):
        return {
            "entity": "event", "account_id": "acc_test", "event": kind,
            "contains": ["payment", "payment_link"],
            "payload": {
                "payment": {"entity": {"id": "pay_real1", "amount": 100_000, "currency": "INR",
                                       "status": "captured", "method": "upi",
                                       "notes": {"invoice_id": "inv_1"}}},
                "payment_link": {"entity": {"id": "plink_real1", "reference_id": "inv_1",
                                            "notes": {"invoice_id": "inv_1"}}},
            },
            "created_at": 1787823953,
        }

    def test_event_id_from_header_is_adopted_and_deduplicated(self, client):
        raw, headers = signed(self._razorpay_body())
        headers["x-razorpay-event-id"] = "evt_hdr_1"
        first = client.post("/webhooks/razorpay", content=raw, headers=headers).json()
        assert first["status"] == "recorded" and first["event_id"] == "evt_hdr_1"
        again = client.post("/webhooks/razorpay", content=raw, headers=headers).json()
        assert again["status"] == "replay_ignored"
        assert client.get("/api/summary", headers=AUTH).json()["recovered_paise"] == 100_000

    def test_without_any_event_id_it_is_still_refused(self, client):
        raw, headers = signed(self._razorpay_body())
        assert client.post("/webhooks/razorpay", content=raw, headers=headers).json()["status"] == "ignored"
        assert client.get("/api/summary", headers=AUTH).json()["recovered_paise"] == 0


class TestInstrumentMode:
    def test_sandbox_instruments_are_labelled_and_never_look_like_razorpay(self, world):
        store, _, client = world
        client.post("/inbound/reply", json={"invoice_id": "inv_1", "text": "will pay ₹1,000 in 2 days"},
                    headers=AUTH)
        [row] = client.get("/api/commitments", headers=AUTH).json()
        assert row["instrument_mode"] == "sandbox" and row["instrument_failed"] is False
        assert row["payment_url"].startswith("https://sandbox.urudhi.invalid/")
        detail = client.get("/api/invoices/inv_1", headers=AUTH).json()
        assert detail["commitments"][0]["instrument_mode"] == "sandbox"
        assert detail["explain"]["commitments"][0]["instrument"]["mode"] == "sandbox"

    def test_razorpay_ids_are_test_mode_and_fake_prefix_is_sandbox(self):
        from urudhi.rails.razorpay_client import instrument_mode
        assert instrument_mode("plink_TUmLQ82CcnfqwP", "https://rzp.io/rzp/fLnAb1SP") == "razorpay_test"
        assert instrument_mode("plink_fake_0004", "https://rzp.io/l/fake0004") == "sandbox"  # legacy rows
        assert instrument_mode(None, None) is None


class TestTwoLedgers:
    """The live ledger and the simulation ledger are served as one product view, labelled per row."""

    def _sim_store(self):
        from urudhi.sim.runner import Arm, RunConfig, run_batch
        return run_batch(RunConfig(days=5, count=12, seed=7, arm=Arm.URUDHI)).store

    def _two_ledger_client(self, world):
        store, outbox, _ = world
        agent = RecoveryAgent(store, MockBrain(), outbox, rails=FakeRails())
        app = create_app(store, webhook_secret=SECRET, api_token=TOKEN, agent=agent,
                         simulation_store=self._sim_store(), brain_name="mock", rails_mode="sandbox")
        return store, TestClient(app)

    def test_source_filter_and_labels(self, world):
        store, c = self._two_ledger_client(world)
        c.post("/inbound/reply", json={"invoice_id": "inv_1", "text": "will pay ₹1,000 in 2 days"}, headers=AUTH)
        live = c.get("/api/commitments?source=live_test", headers=AUTH).json()
        simu = c.get("/api/commitments?source=simulation", headers=AUTH).json()
        both = c.get("/api/commitments", headers=AUTH).json()
        assert {r["source"] for r in live} == {"live_test"} and {r["source"] for r in simu} == {"simulation"}
        assert len(both) == len(live) + len(simu) and len(simu) > 0
        assert all(r["instrument_mode"] == "sandbox" for r in simu)
        health = c.get("/health").json()
        assert [lg["source"] for lg in health["ledgers"]] == ["live_test", "simulation"]
        assert health["ledgers"][1]["brain"] == "mock"
        summary = c.get("/api/summary?source=simulation", headers=AUTH).json()
        assert summary["context"]["rail"] == "sandbox" and "Simulation" in summary["context"]["provenance"]
        assert c.get("/api/summary?source=live_test", headers=AUTH).json()["context"]["source"] == "live_test"
        merged = c.get("/api/summary", headers=AUTH).json()
        assert merged["source"] == "all" and merged["invoices"] == 1 + summary["invoices"]
        assert c.get("/api/commitments?source=bogus", headers=AUTH).status_code == 400

    def test_detail_and_writes_route_to_the_owning_ledger(self, world):
        store, c = self._two_ledger_client(world)
        sim_invoice = c.get("/api/invoices?source=simulation", headers=AUTH).json()[0]["id"]
        detail = c.get(f"/api/invoices/{sim_invoice}", headers=AUTH).json()
        assert detail["source"] == "simulation" and detail["invoice"]["source"] == "simulation"
        # replies never act on simulated records through the live agent
        assert c.post("/inbound/reply", json={"invoice_id": sim_invoice, "text": "will pay tomorrow"},
                      headers=AUTH).status_code == 409
        cid = c.get("/api/commitments?source=simulation", headers=AUTH).json()[0]["id"]
        one = c.get(f"/api/commitments/{cid}", headers=AUTH).json()
        assert one["source"] == "simulation" and one["chain"]["id"] == cid and one["audit_chain"]["verified"]
        assert c.get("/api/commitments/cmt_none", headers=AUTH).json()["detail"] == \
            "No matching commitment in current data source"


class TestCORS:
    def test_options_preflight_vercel_origin(self, client):
        resp = client.options(
            "/api/summary?source=all",
            headers={
                "Origin": "https://urudhi.vercel.app",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "https://urudhi.vercel.app"
        assert "GET" in resp.headers.get("access-control-allow-methods", "")
        allowed_headers = resp.headers.get("access-control-allow-headers", "").lower()
        assert "authorization" in allowed_headers
        assert "content-type" in allowed_headers

    def test_options_preflight_localhost_origins(self, client):
        for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
            resp = client.options(
                "/api/summary?source=all",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            )
            assert resp.status_code == 200
            assert resp.headers.get("access-control-allow-origin") == origin

    def test_options_preflight_disallowed_origin(self, client):
        resp = client.options(
            "/api/summary?source=all",
            headers={
                "Origin": "https://unauthorized.site",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert resp.status_code == 400 or resp.headers.get("access-control-allow-origin") is None

    def test_authenticated_get_with_cors(self, client):
        # Unauthenticated request from allowed origin still returns 401 but includes CORS headers
        unauth = client.get(
            "/api/summary?source=all",
            headers={"Origin": "https://urudhi.vercel.app"},
        )
        assert unauth.status_code == 401
        assert unauth.headers.get("access-control-allow-origin") == "https://urudhi.vercel.app"

        # Authenticated request from allowed origin succeeds and includes CORS headers
        auth_resp = client.get(
            "/api/summary?source=all",
            headers={"Origin": "https://urudhi.vercel.app", **AUTH},
        )
        assert auth_resp.status_code == 200
        assert auth_resp.headers.get("access-control-allow-origin") == "https://urudhi.vercel.app"
        assert "invoices" in auth_resp.json()

    def test_health_and_api_health_endpoints_cors(self, client):
        for path in ("/health", "/api/health"):
            # Preflight
            pre = client.options(
                path,
                headers={
                    "Origin": "https://urudhi.vercel.app",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            )
            assert pre.status_code == 200
            assert pre.headers.get("access-control-allow-origin") == "https://urudhi.vercel.app"

            # Direct unauthenticated GET with Origin
            get_resp = client.get(path, headers={"Origin": "https://urudhi.vercel.app"})
            assert get_resp.status_code == 200
            assert get_resp.headers.get("access-control-allow-origin") == "https://urudhi.vercel.app"
            assert get_resp.json()["status"] == "ok"

    def test_ensure_sim_db_seeds_when_empty(self, tmp_path):
        from urudhi.api.__main__ import ensure_sim_db
        sim_path = tmp_path / "sim.sqlite3"
        ensure_sim_db(str(sim_path))
        assert sim_path.exists()
        s = Store(str(sim_path))
        assert len(s.all_invoices()) == 120
        assert len(s.all_commitments()) == 153
        # Calling again is idempotent
        ensure_sim_db(str(sim_path))
        assert len(s.all_invoices()) == 120



