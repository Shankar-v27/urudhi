"""FastAPI surface: webhook receiver, inbound replies, human actions, read API.

Trust levels, from least to most:

* ``POST /webhooks/razorpay`` — trusts only Razorpay's HMAC signature. The
  app refuses to start without a webhook secret.
* ``GET /health`` — unauthenticated liveness: brain mode, transport mode,
  counters, chain status. No ledger data.
* everything under ``/api`` and ``/inbound`` — bearer token
  (``URUDHI_API_TOKEN``). Debtor contact details are masked in every
  response; the raw values never leave the store through this app.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from urudhi import __version__
from urudhi.agent.explain import explain_invoice
from urudhi.agent.human import HumanRequest, apply_human_action, escalation_queue
from urudhi.agent.loop import RecoveryAgent, chaseable
from urudhi.agent.policy import PolicyConfig
from urudhi.audit.log import ChainError, EventKind, verify_chain
from urudhi.ledger.models import Debtor, InvoiceState
from urudhi.ledger.transitions import InvalidTransition
from urudhi.observability import counters, get_logger, new_request_id
from urudhi.rails.webhooks import (
    IngestStatus,
    WebhookError,
    ingest_payment_event,
    verify_signature,
)
from urudhi.scoring.priority import rank, score_invoice
from urudhi.store import Store
from urudhi.transport.email import reference_from_subject

log = get_logger("urudhi.api")


def mask_phone(phone: str) -> str:
    digits = phone.strip()
    return digits[:3] + "•" * max(0, len(digits) - 5) + digits[-2:] if len(digits) > 5 else "•••"


def mask_email(email: str) -> str:
    if "@" not in email:
        return "•••"
    local, domain = email.split("@", 1)
    return f"{local[:1]}•••@{domain}"


def public_debtor(debtor: Debtor) -> dict[str, Any]:
    return {
        "id": debtor.id, "name": debtor.name, "contact_name": debtor.contact_name,
        "phone": mask_phone(debtor.phone), "email": mask_email(debtor.email),
        "preferred_channel": debtor.preferred_channel.value, "language": debtor.language,
    }


class InboundEmail(BaseModel):
    sender: str = Field(alias="from")
    subject: str = ""
    text: str = Field(min_length=1, max_length=5000)

    model_config = {"populate_by_name": True}


class InboundReply(BaseModel):
    invoice_id: str
    text: str = Field(min_length=1, max_length=5000)
    channel: str = "whatsapp"


class TickRequest(BaseModel):
    max_invoices: int = Field(default=10, ge=1, le=500)
    at: datetime | None = None


def create_app(
    store: Store,
    *,
    webhook_secret: str,
    api_token: str,
    agent: RecoveryAgent | None = None,
    policy: PolicyConfig | None = None,
    cors_origins: list[str] | None = None,
    brain_name: str = "none",
    transport_mode: str = "none",
    rails_mode: str = "none",
    data_dir: str | Path = "data",
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    if not webhook_secret or not webhook_secret.strip():
        raise RuntimeError(
            "RAZORPAY_WEBHOOK_SECRET is empty; refusing to start a receiver that would "
            "accept unsigned payments"
        )
    if not api_token or len(api_token.strip()) < 8:
        raise RuntimeError("URUDHI_API_TOKEN must be set (≥ 8 chars) to serve the ledger API")
    policy = policy or (agent.config if agent else PolicyConfig())
    data_dir = Path(data_dir)

    app = FastAPI(title="Urudhi", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type",
                                                      "X-Urudhi-Token", "X-Razorpay-Signature"],
    )

    @app.middleware("http")
    async def request_log(request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id") or new_request_id()
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        log.info("http", rid=rid, method=request.method, path=request.url.path,
                 status=response.status_code, ms=round((time.perf_counter() - started) * 1000))
        counters.inc("http.requests")
        return response

    def require_token(request: Request) -> str:
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else request.headers.get(
            "x-urudhi-token", "")
        if not token or token != api_token:
            counters.inc("http.unauthorized")
            raise HTTPException(status_code=401, detail="missing or invalid API token")
        return "operator"

    def _now(explicit: datetime | None = None) -> datetime:
        if explicit is None:
            return clock() if clock is not None else datetime.now(UTC)
        return explicit if explicit.tzinfo else explicit.replace(tzinfo=UTC)

    # -- health -------------------------------------------------------------

    @app.get("/health")
    def health() -> dict[str, Any]:
        events = list(store.audit_events())
        try:
            chain = {"verified": True, "events": verify_chain(events)}
        except ChainError as error:
            chain = {"verified": False, "error": str(error)}
        return {
            "status": "ok" if chain["verified"] else "degraded",
            "version": __version__,
            "brain": brain_name, "transport": transport_mode, "rails": rails_mode,
            "policy_timezone": policy.timezone,
            "invoices": len(store.all_invoices()),
            "audit_chain": chain,
            "counters": counters.snapshot(),
        }

    # -- rails --------------------------------------------------------------

    @app.post("/webhooks/razorpay")
    async def razorpay_webhook(request: Request) -> dict[str, Any]:
        body = await request.body()
        signature = request.headers.get("x-razorpay-signature", "")
        try:
            verify_signature(body, signature, webhook_secret)
        except WebhookError as error:
            counters.inc("webhook.bad_signature")
            raise HTTPException(status_code=400, detail=str(error)) from error
        try:
            event = json.loads(body)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="body is not JSON") from error
        try:
            result = ingest_payment_event(store, event, now=_now())
        except WebhookError as error:
            # Signed but not a payment event we handle: acknowledge so Razorpay
            # stops retrying; nothing to reconcile.
            counters.inc("webhook.ignored")
            return {"status": IngestStatus.IGNORED.value, "reason": str(error)}
        return {
            "status": result.status.value, "reason": result.reason,
            "payment_id": result.payment.id if result.payment else None,
            "event_id": result.event_id,
        }

    # -- inbound replies ----------------------------------------------------

    def _reply(invoice_id: str, text: str, now: datetime) -> dict[str, Any]:
        if agent is None:
            raise HTTPException(status_code=503, detail="no recovery agent configured")
        try:
            store.get_invoice(invoice_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        result = agent.handle_reply(invoice_id, text, now)
        counters.inc("inbound.replies")
        return result.model_dump(mode="json")

    @app.post("/inbound/email")
    def inbound_email(message: InboundEmail, _: str = Depends(require_token)) -> dict[str, Any]:
        number = reference_from_subject(message.subject)
        invoice = store.find_invoice_by_number(number) if number else None
        if invoice is None:
            debtor = store.find_debtor_by_email(message.sender.split("<")[-1].strip(" >"))
            if debtor is not None:
                open_ones = [i for i in store.invoices_for_debtor(debtor.id)
                             if i.state is not InvoiceState.PAID]
                invoice = open_ones[0] if open_ones else None
        if invoice is None:
            counters.inc("inbound.unmatched")
            raise HTTPException(status_code=404, detail="could not match this email to an invoice")
        return _reply(invoice.id, message.text, _now()) | {"matched_invoice": invoice.id}

    @app.post("/inbound/reply")
    def inbound_reply(message: InboundReply, _: str = Depends(require_token)) -> dict[str, Any]:
        return _reply(message.invoice_id, message.text, _now())

    # -- runtime ------------------------------------------------------------

    @app.post("/api/run/tick")
    def run_tick(body: TickRequest, _: str = Depends(require_token)) -> dict[str, Any]:
        """One scheduler tick: expire commitments, then chase by priority."""
        if agent is None:
            raise HTTPException(status_code=503, detail="no recovery agent configured")
        now = _now(body.at)
        ticked = agent.daily_tick(now.date(), now)
        scores = []
        for invoice in chaseable(store):
            attempts, _, _ = store.attempt_facts(invoice.id, now.date().isoformat(),
                                                 invoice.human_released_at)
            scores.append(score_invoice(invoice, store.promises_for(invoice.id), attempts,
                                        policy.max_attempts_per_invoice, now.date()))
        chased = [agent.chase(s.invoice_id, now).model_dump(mode="json")
                  for s in rank(scores)[: body.max_invoices]]
        counters.inc("runtime.ticks")
        return {"at": now.isoformat(), "expired": [t.model_dump(mode="json") for t in ticked],
                "chased": chased}

    # -- read API -----------------------------------------------------------

    @app.get("/api/invoices")
    def invoices(_: str = Depends(require_token)) -> list[dict[str, Any]]:
        debtors = {d.id: d for d in store.all_debtors()}
        return [
            invoice.model_dump(mode="json") | {
                "balance": invoice.balance,
                "debtor_name": debtors[invoice.debtor_id].name if invoice.debtor_id in debtors else None,
            }
            for invoice in store.all_invoices()
        ]

    @app.get("/api/invoices/{invoice_id}")
    def invoice_detail(invoice_id: str, _: str = Depends(require_token)) -> dict[str, Any]:
        try:
            invoice = store.get_invoice(invoice_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "invoice": invoice.model_dump(mode="json") | {"balance": invoice.balance},
            "debtor": public_debtor(store.get_debtor(invoice.debtor_id)),
            "promises": [p.model_dump(mode="json") for p in store.promises_for(invoice_id)],
            "concessions": [c.model_dump(mode="json") for c in store.concessions_for(invoice_id)],
            "payments": [p.model_dump(mode="json") for p in store.payments_for(invoice_id)],
            "events": [e.model_dump(mode="json") for e in store.events_for(invoice_id)],
            "explain": explain_invoice(store, invoice_id, policy, datetime.now(UTC)),
        }

    @app.get("/api/invoices/{invoice_id}/explain")
    def invoice_explain(invoice_id: str, _: str = Depends(require_token)) -> dict[str, Any]:
        try:
            return explain_invoice(store, invoice_id, policy, datetime.now(UTC))
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/invoices/{invoice_id}/human")
    def human_action(invoice_id: str, body: HumanRequest,
                     _: str = Depends(require_token)) -> dict[str, Any]:
        try:
            return apply_human_action(store, invoice_id, body, datetime.now(UTC))
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except InvalidTransition as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/escalations")
    def escalations(_: str = Depends(require_token)) -> list[dict[str, Any]]:
        return escalation_queue(store)

    @app.get("/api/promises")
    def promises(_: str = Depends(require_token)) -> list[dict[str, Any]]:
        return [p.model_dump(mode="json") for p in store.all_promises()]

    @app.get("/api/concessions")
    def concessions(_: str = Depends(require_token)) -> list[dict[str, Any]]:
        return [c.model_dump(mode="json") for c in store.all_concessions()]

    @app.get("/api/audit")
    def audit(offset: int = 0, limit: int = 200,
              _: str = Depends(require_token)) -> dict[str, Any]:
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
    def summary(_: str = Depends(require_token)) -> dict[str, Any]:
        invoices = store.all_invoices()
        by_state = {state.value: 0 for state in InvoiceState}
        for invoice in invoices:
            by_state[invoice.state.value] += 1
        sent = store.events_of_kind(EventKind.MESSAGE_SENT)
        by_intervention: dict[str, int] = defaultdict(int)
        for e in sent:
            by_intervention[str(e.payload.get("intervention", "reminder"))] += 1
        return {
            "invoices": len(invoices),
            "outstanding_paise": sum(i.amount for i in invoices),
            "recovered_paise": sum(i.amount_paid for i in invoices),
            "waived_paise": sum(i.amount_waived for i in invoices),
            "by_state": by_state,
            "messages_sent": len(sent),
            "by_intervention": dict(by_intervention),
            "brain": brain_name, "transport": transport_mode,
        }

    @app.get("/api/timeline")
    def timeline(_: str = Depends(require_token)) -> dict[str, Any]:
        """Cumulative recovered paise and messages sent per calendar day."""
        paid: dict[str, int] = defaultdict(int)
        for e in store.events_of_kind(EventKind.PAYMENT_OBSERVED):
            paid[e.at.date().isoformat()] += int(e.payload.get("amount", 0))
        sent: dict[str, int] = defaultdict(int)
        for e in store.events_of_kind(EventKind.MESSAGE_SENT):
            sent[e.at.date().isoformat()] += 1
        days = sorted(set(paid) | set(sent))
        running = 0
        series = []
        for day in days:
            running += paid.get(day, 0)
            series.append({"day": day, "recovered_cumulative": running,
                           "recovered": paid.get(day, 0), "messages": sent.get(day, 0)})
        return {"series": series}

    def _json_file(name: str) -> dict[str, Any] | None:
        path = data_dir / name
        if not path.exists():
            return None
        return json.loads(path.read_text())

    @app.get("/api/experiment")
    def experiment(_: str = Depends(require_token)) -> dict[str, Any]:
        data = _json_file("experiment.json")
        if data is None:
            raise HTTPException(status_code=404,
                                detail="no experiment report; run python -m urudhi.sim --arms all")
        return data

    @app.get("/api/reply-eval")
    def reply_eval(_: str = Depends(require_token)) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in ("mock", "claude"):
            data = _json_file(f"reply_eval_{name}.json")
            if data is not None:
                out[name] = data["summary"] | {"failures": data.get("failures", [])[:40]}
        if not out:
            raise HTTPException(status_code=404,
                                detail="no reply evaluation; run python -m urudhi.eval_replies")
        return out

    return app
