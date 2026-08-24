"""FastAPI surface: the webhook receiver and the dashboard's read API.

Two very different trust levels share this app:

* ``POST /webhooks/razorpay`` — the only write endpoint, and it only trusts
  Razorpay's HMAC signature. This is where live-mode money enters the ledger.
* ``GET /api/*`` — read-only projections of the ledger, promises, audit chain
  and metrics for the dashboard. Nothing here mutates state.
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from urudhi.audit.log import ChainError, verify_chain
from urudhi.ledger.models import InvoiceState
from urudhi.rails.webhooks import WebhookError, ingest_payment_event, verify_signature
from urudhi.store import Store


def create_app(store: Store, webhook_secret: str | None = None) -> FastAPI:
    app = FastAPI(title="Urudhi", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    secret = webhook_secret or os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

    @app.post("/webhooks/razorpay")
    async def razorpay_webhook(request: Request) -> dict[str, Any]:
        body = await request.body()
        signature = request.headers.get("x-razorpay-signature", "")
        try:
            verify_signature(body, signature, secret)
            payment = ingest_payment_event(store, json.loads(body))
        except WebhookError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if payment is None:
            return {"status": "replay_ignored"}
        return {"status": "recorded", "payment_id": payment.id}

    @app.get("/api/invoices")
    def invoices() -> list[dict[str, Any]]:
        return [
            invoice.model_dump(mode="json") | {"balance": invoice.balance}
            for invoice in store.all_invoices()
        ]

    @app.get("/api/invoices/{invoice_id}")
    def invoice_detail(invoice_id: str) -> dict[str, Any]:
        try:
            invoice = store.get_invoice(invoice_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "invoice": invoice.model_dump(mode="json") | {"balance": invoice.balance},
            "debtor": store.get_debtor(invoice.debtor_id).model_dump(mode="json"),
            "promises": [p.model_dump(mode="json") for p in store.promises_for(invoice_id)],
            "payments": [p.model_dump(mode="json") for p in store.payments_for(invoice_id)],
            "events": [
                e.model_dump(mode="json") for e in store.audit_events()
                if e.invoice_id == invoice_id
            ],
        }

    @app.get("/api/promises")
    def promises() -> list[dict[str, Any]]:
        return [p.model_dump(mode="json") for p in store.all_promises()]

    @app.get("/api/audit")
    def audit(offset: int = 0, limit: int = 200) -> dict[str, Any]:
        events = list(store.audit_events())
        try:
            verified = verify_chain(events)
            chain = {"verified": True, "events": verified}
        except ChainError as error:
            chain = {"verified": False, "error": str(error)}
        return {
            "chain": chain,
            "events": [e.model_dump(mode="json") for e in events[offset: offset + limit]],
            "total": len(events),
        }

    @app.get("/api/summary")
    def summary() -> dict[str, Any]:
        invoices = store.all_invoices()
        outstanding = sum(i.amount for i in invoices)
        by_state = {state.value: 0 for state in InvoiceState}
        for invoice in invoices:
            by_state[invoice.state.value] += 1
        return {
            "invoices": len(invoices),
            "outstanding_paise": outstanding,
            "recovered_paise": sum(i.amount_paid for i in invoices),
            "by_state": by_state,
        }

    return app
