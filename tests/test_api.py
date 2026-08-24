import hashlib
import hmac
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from urudhi.api.app import create_app
from urudhi.ledger.models import Debtor, Invoice
from urudhi.store import Store

SECRET = "whsec_test"


@pytest.fixture
def client():
    store = Store(":memory:")
    store.put_debtor(Debtor(
        id="deb_1", name="Kumar Textiles", contact_name="Kumar",
        phone="+919800000001", email="kumar@example.in",
    ))
    store.put_invoice(Invoice(
        id="inv_1", debtor_id="deb_1", number="URU/2026/001",
        amount=100_000, issued_on=date(2026, 6, 1), due_on=date(2026, 7, 1),
    ))
    return TestClient(create_app(store, webhook_secret=SECRET))


def signed(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"x-razorpay-signature": sig}


def payment_event(event_id="evt_1"):
    return {
        "id": event_id,
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": f"pay_rzp_{event_id}", "amount": 100_000, "method": "upi",
            "notes": {"invoice_id": "inv_1"},
        }}},
    }


class TestWebhook:
    def test_signed_payment_is_recorded(self, client):
        raw, headers = signed(payment_event())
        response = client.post("/webhooks/razorpay", content=raw, headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "recorded"
        assert client.get("/api/summary").json()["recovered_paise"] == 100_000

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
        assert again.json()["status"] == "replay_ignored"


class TestReadApi:
    def test_invoice_detail_joins_everything(self, client):
        raw, headers = signed(payment_event())
        client.post("/webhooks/razorpay", content=raw, headers=headers)
        detail = client.get("/api/invoices/inv_1").json()
        assert detail["invoice"]["state"] == "paid"
        assert detail["debtor"]["name"] == "Kumar Textiles"
        assert len(detail["payments"]) == 1
        assert len(detail["events"]) == 1

    def test_missing_invoice_404s(self, client):
        assert client.get("/api/invoices/nope").status_code == 404

    def test_audit_endpoint_reports_verified_chain(self, client):
        raw, headers = signed(payment_event())
        client.post("/webhooks/razorpay", content=raw, headers=headers)
        audit = client.get("/api/audit").json()
        assert audit["chain"] == {"verified": True, "events": 1}
