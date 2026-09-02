"""FastAPI surface: webhook receiver, inbound replies, human actions, read API.

Trust levels, from least to most:

* ``POST /webhooks/razorpay`` — trusts only Razorpay's HMAC signature. The
  app refuses to start without a webhook secret. Rail events are applied to
  the **live** ledger only.
* ``GET /health`` — unauthenticated liveness: brain mode, transport mode,
  rail mode, per-ledger chain status, counters. No ledger data.
* everything under ``/api`` and ``/inbound`` — bearer token
  (``URUDHI_API_TOKEN``). Debtor contact details are masked in every
  response; the raw values never leave the store through this app.

Two ledgers, one product view. The app serves a primary ledger (normally the
live test-mode ledger) and optionally a second, simulation ledger written by
the batch runner. They are never merged on disk: every row the API returns
carries ``source`` (``live_test`` / ``simulation``) taken from the ledger it
lives in — and, for commitments, from the origin persisted at creation — and
``?source=all|live_test|simulation`` selects which ledgers a read covers.
Writes (webhooks, replies, ticks, human actions) go to the ledger that owns
the record.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from urudhi import __version__
from urudhi.agent.explain import commitment_integrity, explain_invoice
from urudhi.agent.human import HumanRequest, apply_human_action, escalation_queue
from urudhi.agent.loop import RecoveryAgent, chaseable
from urudhi.agent.policy import PolicyConfig
from urudhi.audit.log import ChainError, EventKind, verify_chain
from urudhi.ledger.commitments import profile_for
from urudhi.ledger.models import CommitmentState, Debtor, InvoiceState, PaymentCommitment
from urudhi.ledger.transitions import InvalidTransition
from urudhi.observability import counters, get_logger, new_request_id
from urudhi.rails.razorpay_client import instrument_mode
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

Source = Literal["all", "live_test", "simulation"]


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


class Ledger:
    """One store plus the provenance label every row from it carries."""

    def __init__(self, store: Store, origin: str, path: str = "") -> None:
        self.store, self.origin, self.path = store, origin, path

    def chain(self) -> dict[str, Any]:
        events = list(self.store.audit_events())
        try:
            return {"verified": True, "events": verify_chain(events)}
        except ChainError as error:
            return {"verified": False, "error": str(error), "events": len(events)}

    def brain(self) -> str | None:
        """The brain that produced a simulation ledger (from RUN_STARTED), else None."""
        for e in self.store.events_of_kind(EventKind.RUN_STARTED):
            return str(e.payload.get("brain") or "")
        return None


DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://urudhi.vercel.app",
]

DEFAULT_CORS_HEADERS = [
    "Accept",
    "Accept-Language",
    "Authorization",
    "Content-Language",
    "Content-Type",
    "X-Razorpay-Signature",
    "X-Urudhi-Token",
]

DEFAULT_CORS_METHODS = ["GET", "POST", "OPTIONS"]


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
    simulation_store: Store | None = None,
    store_origin: str | None = None,
    store_path: str = "",
    simulation_path: str = "",
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

    primary = Ledger(store, store_origin or store.origin(), store_path)
    ledgers: list[Ledger] = [primary]
    if simulation_store is not None:
        ledgers.append(Ledger(simulation_store, "simulation", simulation_path))

    def selected(source: str) -> list[Ledger]:
        if source == "all":
            return ledgers
        if source not in ("live_test", "simulation"):
            raise HTTPException(status_code=400, detail="source must be all, live_test or simulation")
        return [lg for lg in ledgers if lg.origin == source]

    def owner(invoice_id: str) -> Ledger:
        for lg in ledgers:
            try:
                lg.store.get_invoice(invoice_id)
                return lg
            except KeyError:
                continue
        raise HTTPException(status_code=404, detail=f"invoices: no row with id {invoice_id!r}")

    def owner_of_commitment(commitment_id: str) -> tuple[Ledger, PaymentCommitment]:
        for lg in ledgers:
            try:
                return lg, lg.store.get_commitment(commitment_id)
            except KeyError:
                continue
        raise HTTPException(status_code=404,
                            detail="No matching commitment in current data source")

    app = FastAPI(title="Urudhi", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins if cors_origins is not None else DEFAULT_CORS_ORIGINS,
        allow_methods=DEFAULT_CORS_METHODS,
        allow_headers=DEFAULT_CORS_HEADERS,
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

    source_param = Query("all", description="all | live_test | simulation")

    # -- serialisers ---------------------------------------------------------

    def failed_instruments(lg: Ledger) -> set[str]:
        return {str(e.payload.get("commitment_id"))
                for e in lg.store.events_of_kind(EventKind.RAIL_FAILED) if e.payload.get("commitment_id")}

    def public_commitment(lg: Ledger, c: PaymentCommitment, numbers: dict[str, str],
                          debtors: dict[str, str], failed: set[str]) -> dict[str, Any]:
        mode = (c.instrument_mode.value if c.instrument_mode
                else instrument_mode(c.instrument_id, c.payment_url))
        return c.model_dump(mode="json") | {
            "amount_remaining": c.amount_remaining,
            "invoice_number": numbers.get(c.invoice_id),
            "debtor_name": debtors.get(c.debtor_id),
            "instrument_mode": mode,
            "instrument_failed": c.instrument_failed or (c.instrument_id is None and c.id in failed),
            # A record's source is the ledger it lives in; the instrument's mode is the rail's.
            "source": lg.origin,
            "rail_origin": c.origin.value if c.origin else None,
            "rail": mode if mode in ("razorpay_test", "sandbox") else None,
        }

    def public_invoice(lg: Ledger, invoice, debtors: dict[str, Debtor]) -> dict[str, Any]:
        return invoice.model_dump(mode="json") | {
            "balance": invoice.balance,
            "debtor_name": debtors[invoice.debtor_id].name if invoice.debtor_id in debtors else None,
            "source": lg.origin,
        }

    def tagged(lg: Ledger, rows: Iterable[BaseModel]) -> list[dict[str, Any]]:
        return [r.model_dump(mode="json") | {"source": lg.origin} for r in rows]

    # -- health -------------------------------------------------------------

    @app.get("/health")
    def health() -> dict[str, Any]:
        chains = [{"source": lg.origin, "db": lg.path, "invoices": len(lg.store.all_invoices()),
                   "audit_chain": lg.chain(), "brain": lg.brain()} for lg in ledgers]
        verified = all(c["audit_chain"]["verified"] for c in chains)
        return {
            "status": "ok" if verified else "degraded",
            "version": __version__,
            "brain": brain_name, "transport": transport_mode, "rails": rails_mode,
            "policy_timezone": policy.timezone,
            "invoices": sum(c["invoices"] for c in chains),
            "audit_chain": {"verified": verified,
                            "events": sum(c["audit_chain"].get("events", 0) for c in chains)},
            "ledgers": chains,
            "sources": [lg.origin for lg in ledgers],
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
        # Razorpay carries the delivery's idempotency key in a header, not in
        # the body (the body has no top-level "id"). Adopt it when absent.
        header_id = request.headers.get("x-razorpay-event-id", "").strip()
        if isinstance(event, dict) and not event.get("id") and header_id:
            event["id"] = header_id
        try:
            result = ingest_payment_event(primary.store, event, now=_now())
        except WebhookError as error:
            # Signed but not a payment event we handle: acknowledge so Razorpay
            # stops retrying; nothing to reconcile — but say why.
            counters.inc("webhook.ignored")
            log.warning("webhook.ignored", event=(event.get("event") if isinstance(event, dict) else None),
                        event_id=header_id or None, reason=str(error))
            return {"status": IngestStatus.IGNORED.value, "reason": str(error)}
        log.info("webhook.result", status=result.status.value, event=event.get("event"),
                 event_id=result.event_id, reason=result.reason or None,
                 payment_id=result.payment.id if result.payment else None)
        return {
            "status": result.status.value, "reason": result.reason,
            "payment_id": result.payment.id if result.payment else None,
            "event_id": result.event_id,
        }

    # -- inbound replies ----------------------------------------------------

    def _reply(invoice_id: str, text: str, now: datetime) -> dict[str, Any]:
        if agent is None:
            raise HTTPException(status_code=503, detail="no recovery agent configured")
        lg = owner(invoice_id)
        if lg is not primary:
            raise HTTPException(status_code=409, detail="replies act on the live ledger only; "
                                                        "this invoice belongs to the simulation")
        result = agent.handle_reply(invoice_id, text, now)
        counters.inc("inbound.replies")
        return result.model_dump(mode="json")

    @app.post("/inbound/email")
    def inbound_email(message: InboundEmail, _: str = Depends(require_token)) -> dict[str, Any]:
        number = reference_from_subject(message.subject)
        invoice = primary.store.find_invoice_by_number(number) if number else None
        if invoice is None:
            debtor = primary.store.find_debtor_by_email(message.sender.split("<")[-1].strip(" >"))
            if debtor is not None:
                open_ones = [i for i in primary.store.invoices_for_debtor(debtor.id)
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
        """One scheduler tick on the live ledger: expire commitments, then chase by priority."""
        if agent is None:
            raise HTTPException(status_code=503, detail="no recovery agent configured")
        now = _now(body.at)
        ticked = agent.daily_tick(now.date(), now)
        scores = []
        for invoice in chaseable(primary.store):
            attempts, _, _ = primary.store.attempt_facts(invoice.id, now.date().isoformat(),
                                                         invoice.human_released_at)
            scores.append(score_invoice(invoice, primary.store.promises_for(invoice.id), attempts,
                                        policy.max_attempts_per_invoice, now.date()))
        chased = [agent.chase(s.invoice_id, now).model_dump(mode="json")
                  for s in rank(scores)[: body.max_invoices]]
        counters.inc("runtime.ticks")
        return {"at": now.isoformat(), "expired": [t.model_dump(mode="json") for t in ticked],
                "chased": chased}

    # -- read API -----------------------------------------------------------

    @app.get("/api/invoices")
    def invoices(source: str = source_param, _: str = Depends(require_token)) -> list[dict[str, Any]]:
        out = []
        for lg in selected(source):
            debtors = {d.id: d for d in lg.store.all_debtors()}
            out.extend(public_invoice(lg, i, debtors) for i in lg.store.all_invoices())
        return out

    @app.get("/api/invoices/{invoice_id}")
    def invoice_detail(invoice_id: str, _: str = Depends(require_token)) -> dict[str, Any]:
        lg = owner(invoice_id)
        st = lg.store
        invoice = st.get_invoice(invoice_id)
        debtor = st.get_debtor(invoice.debtor_id)
        return {
            "source": lg.origin,
            "invoice": invoice.model_dump(mode="json") | {"balance": invoice.balance, "source": lg.origin},
            "debtor": public_debtor(debtor),
            "promises": tagged(lg, st.promises_for(invoice_id)),
            "commitments": [public_commitment(lg, c, {invoice.id: invoice.number},
                                              {debtor.id: debtor.name}, failed_instruments(lg))
                            for c in st.commitments_for(invoice_id)],
            "concessions": tagged(lg, st.concessions_for(invoice_id)),
            "payments": tagged(lg, st.payments_for(invoice_id)),
            "events": [e.model_dump(mode="json") for e in st.events_for(invoice_id)],
            "explain": explain_invoice(st, invoice_id, policy, datetime.now(UTC)),
        }

    @app.get("/api/invoices/{invoice_id}/explain")
    def invoice_explain(invoice_id: str, _: str = Depends(require_token)) -> dict[str, Any]:
        return explain_invoice(owner(invoice_id).store, invoice_id, policy, datetime.now(UTC))

    @app.post("/api/invoices/{invoice_id}/human")
    def human_action(invoice_id: str, body: HumanRequest,
                     _: str = Depends(require_token)) -> dict[str, Any]:
        lg = owner(invoice_id)
        try:
            return apply_human_action(lg.store, invoice_id, body, _now(),
                                      agent=agent if lg is primary else None) | {"source": lg.origin}
        except InvalidTransition as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/escalations")
    def escalations(source: str = source_param, _: str = Depends(require_token)) -> list[dict[str, Any]]:
        out = []
        for lg in selected(source):
            debtors = {d.id: d.name for d in lg.store.all_debtors()}
            invoices = {i.id: i for i in lg.store.all_invoices()}
            for row in escalation_queue(lg.store):
                inv = invoices.get(row["invoice_id"])
                out.append(row | {"source": lg.origin,
                                  "debtor_name": debtors.get(inv.debtor_id) if inv else None})
        return out

    @app.get("/api/promises")
    def promises(source: str = source_param, _: str = Depends(require_token)) -> list[dict[str, Any]]:
        out = []
        for lg in selected(source):
            numbers = {i.id: i.number for i in lg.store.all_invoices()}
            by_promise = {c.promise_id: c for c in lg.store.all_commitments() if c.promise_id}
            for p in lg.store.all_promises():
                c = by_promise.get(p.id)
                out.append(p.model_dump(mode="json") | {
                    "source": lg.origin, "invoice_number": numbers.get(p.invoice_id),
                    "commitment_id": c.id if c else None,
                    "commitment_state": c.state.value if c else None,
                    "commitment_received": c.amount_received if c else None,
                })
        return out

    @app.get("/api/commitments")
    def commitments(source: str = source_param, _: str = Depends(require_token)) -> list[dict[str, Any]]:
        out = []
        for lg in selected(source):
            numbers = {i.id: i.number for i in lg.store.all_invoices()}
            debtors = {d.id: d.name for d in lg.store.all_debtors()}
            failed = failed_instruments(lg)
            out.extend(public_commitment(lg, c, numbers, debtors, failed) for c in lg.store.all_commitments())
        return out

    @app.get("/api/commitments/{commitment_id}")
    def commitment_detail(commitment_id: str, _: str = Depends(require_token)) -> dict[str, Any]:
        """One commitment with its provenance chain, wherever it lives."""
        lg, c = owner_of_commitment(commitment_id)
        st = lg.store
        invoice = st.get_invoice(c.invoice_id)
        debtor = st.get_debtor(c.debtor_id)
        events = st.events_for(c.invoice_id)
        return {
            "source": lg.origin,
            "commitment": public_commitment(lg, c, {invoice.id: invoice.number}, {debtor.id: debtor.name},
                                            failed_instruments(lg)),
            "invoice": invoice.model_dump(mode="json") | {"balance": invoice.balance, "source": lg.origin},
            "debtor": public_debtor(debtor),
            "chain": commitment_integrity(st, c, events),
            "audit_chain": lg.chain(),
        }

    @app.get("/api/invoices/{invoice_id}/commitments")
    def invoice_commitments(invoice_id: str, _: str = Depends(require_token)) -> dict[str, Any]:
        """The provenance chain — said → understood → allowed → instrument → rail → outcome."""
        lg = owner(invoice_id)
        st = lg.store
        invoice = st.get_invoice(invoice_id)
        events = st.events_for(invoice_id)
        return {
            "invoice_id": invoice_id, "source": lg.origin,
            "credibility": profile_for(st.commitments_for_debtor(invoice.debtor_id),
                                       invoice.human_released_at).model_dump(),
            "commitments": [commitment_integrity(st, c, events) for c in st.commitments_for(invoice_id)],
            "blocked": [
                {"at": e.at.isoformat(), "amount": e.payload.get("amount"),
                 "due_on": e.payload.get("due_on"), "reason": e.payload.get("reason"),
                 "checks": e.payload.get("checks", [])}
                for e in events if e.kind is EventKind.COMMITMENT_BLOCKED
            ],
        }

    @app.get("/api/concessions")
    def concessions(source: str = source_param, _: str = Depends(require_token)) -> list[dict[str, Any]]:
        out = []
        for lg in selected(source):
            out.extend(tagged(lg, lg.store.all_concessions()))
        return out

    @app.get("/api/audit")
    def audit(offset: int = 0, limit: int = 200, source: str = source_param,
              _: str = Depends(require_token)) -> dict[str, Any]:
        chosen = selected(source)
        chains = {lg.origin: lg.chain() for lg in chosen}
        verified = all(c["verified"] for c in chains.values())
        events: list[dict[str, Any]] = []
        for lg in chosen:
            events.extend(e.model_dump(mode="json") | {"source": lg.origin} for e in lg.store.audit_events())
        return {
            "chain": {"verified": verified, "events": sum(c.get("events", 0) for c in chains.values()),
                      **({"error": "; ".join(c["error"] for c in chains.values() if not c["verified"])}
                         if not verified else {})},
            "chains": chains,
            "events": events[offset: offset + limit],
            "total": len(events),
        }

    def ledger_summary(lg: Ledger) -> dict[str, Any]:
        st = lg.store
        invoices = st.all_invoices()
        by_state = {state.value: 0 for state in InvoiceState}
        for invoice in invoices:
            by_state[invoice.state.value] += 1
        sent = st.events_of_kind(EventKind.MESSAGE_SENT)
        by_intervention: dict[str, int] = defaultdict(int)
        for e in sent:
            by_intervention[str(e.payload.get("intervention", "reminder"))] += 1
        commitments = st.all_commitments()
        profile = profile_for(commitments)
        recovered = sum(i.amount_paid for i in invoices)
        counted = [c for c in commitments if c.state is not CommitmentState.SUPERSEDED]
        with_money = sum(1 for c in counted if c.amount_received > 0)
        payments = st.all_payments()
        exact = sum(p.amount for p in payments if (p.matched_by or "").startswith("instrument"))
        nudges = [e for e in sent if not e.payload.get("responding")]
        real = sum(1 for c in counted if c.instrument_mode and c.instrument_mode.value == "razorpay_test")
        def _mode(c: PaymentCommitment) -> str | None:
            return (c.instrument_mode.value if c.instrument_mode
                    else instrument_mode(c.instrument_id, c.payment_url))
        sandbox = sum(1 for c in counted if _mode(c) == "sandbox")
        chain = lg.chain()
        return {
            "source": lg.origin,
            "invoices": len(invoices),
            "outstanding_paise": sum(i.amount for i in invoices),
            "recovered_paise": recovered,
            "waived_paise": sum(i.amount_waived for i in invoices),
            "by_state": by_state,
            "messages_sent": len(sent),
            "by_intervention": dict(by_intervention),
            "commitments": {
                "created": len(counted), "active": profile.active,
                "fulfilled": profile.fulfilled, "fulfilled_on_time": profile.fulfilled_on_time,
                "partially_fulfilled": profile.partially_fulfilled,
                "missed": profile.missed, "cancelled": profile.cancelled,
                "fulfillment_rate": profile.fulfillment_rate,
                "amount_committed_paise": profile.amount_committed,
                "amount_received_paise": profile.amount_received,
                "conversion": round(with_money / len(counted), 4) if counted else None,
                "average_delay_days": profile.average_delay_days,
                "recovered_per_commitment_paise": (recovered // len(counted)) if counted else None,
                "recovered_per_attempt_paise": (recovered // len(nudges)) if nudges else None,
                "messages_total": len(sent), "nudges": len(nudges),
                "exact_instrument_matched_paise": exact,
                "instruments_razorpay_test": real, "instruments_sandbox": sandbox,
            },
            "context": {
                "source": lg.origin,
                "brain": lg.brain() if lg.origin == "simulation" else brain_name,
                "rail": "sandbox" if lg.origin == "simulation" else rails_mode,
                "payments_observed": len(payments),
                "audit_events": chain.get("events", 0), "chain_verified": chain["verified"],
                "provenance": ("Simulation · persona model · webhook-shaped events"
                               if lg.origin == "simulation" else
                               "Razorpay Test Mode · observed via signed webhook"),
            },
        }

    @app.get("/api/summary")
    def summary(source: str = source_param, _: str = Depends(require_token)) -> dict[str, Any]:
        parts = [ledger_summary(lg) for lg in selected(source)]
        if not parts:
            raise HTTPException(status_code=404, detail=f"no ledger for source {source!r}")
        if len(parts) == 1:
            return parts[0] | {"brain": brain_name, "transport": transport_mode, "rails": rails_mode,
                               "sources": [p["source"] for p in parts], "by_source": parts}
        merged: dict[str, Any] = {
            "source": "all", "sources": [p["source"] for p in parts], "by_source": parts,
            "brain": brain_name, "transport": transport_mode, "rails": rails_mode,
            "invoices": sum(p["invoices"] for p in parts),
            "outstanding_paise": sum(p["outstanding_paise"] for p in parts),
            "recovered_paise": sum(p["recovered_paise"] for p in parts),
            "waived_paise": sum(p["waived_paise"] for p in parts),
            "messages_sent": sum(p["messages_sent"] for p in parts),
            "by_state": {k: sum(p["by_state"].get(k, 0) for p in parts) for k in parts[0]["by_state"]},
            "by_intervention": {},
        }
        for p in parts:
            for k, v in p["by_intervention"].items():
                merged["by_intervention"][k] = merged["by_intervention"].get(k, 0) + v
        c = {k: sum(p["commitments"][k] for p in parts) for k in (
            "created", "active", "fulfilled", "fulfilled_on_time", "partially_fulfilled", "missed",
            "cancelled", "amount_committed_paise", "amount_received_paise", "messages_total", "nudges",
            "exact_instrument_matched_paise", "instruments_razorpay_test", "instruments_sandbox")}
        resolved = c["fulfilled"] + c["missed"]
        c["fulfillment_rate"] = round(c["fulfilled"] / resolved, 4) if resolved else None
        c["conversion"] = None
        c["average_delay_days"] = None
        recovered_all = merged["recovered_paise"]
        c["recovered_per_commitment_paise"] = recovered_all // c["created"] if c["created"] else None
        c["recovered_per_attempt_paise"] = recovered_all // c["nudges"] if c["nudges"] else None
        merged["commitments"] = c
        merged["context"] = {
            "source": "all", "brain": brain_name, "rail": rails_mode,
            "payments_observed": sum(p["context"]["payments_observed"] for p in parts),
            "audit_events": sum(p["context"]["audit_events"] for p in parts),
            "chain_verified": all(p["context"]["chain_verified"] for p in parts),
            "provenance": "Mixed: live test-mode records and simulation records, labelled per row",
        }
        return merged

    @app.get("/api/timeline")
    def timeline(source: str = source_param, _: str = Depends(require_token)) -> dict[str, Any]:
        """Cumulative recovered paise and messages sent per calendar day, per source."""
        out: dict[str, Any] = {"series": [], "by_source": {}}
        for lg in selected(source):
            paid: dict[str, int] = defaultdict(int)
            for e in lg.store.events_of_kind(EventKind.PAYMENT_OBSERVED):
                paid[e.at.date().isoformat()] += int(e.payload.get("amount", 0))
            sent: dict[str, int] = defaultdict(int)
            for e in lg.store.events_of_kind(EventKind.MESSAGE_SENT):
                sent[e.at.date().isoformat()] += 1
            days = sorted(set(paid) | set(sent))
            running, series = 0, []
            for day in days:
                running += paid.get(day, 0)
                series.append({"day": day, "recovered_cumulative": running,
                               "recovered": paid.get(day, 0), "messages": sent.get(day, 0),
                               "source": lg.origin})
            out["by_source"][lg.origin] = series
        chosen = selected(source)
        out["series"] = out["by_source"][chosen[0].origin] if len(chosen) == 1 else \
            sorted((r for s in out["by_source"].values() for r in s), key=lambda r: r["day"])
        return out

    def _json_file(name: str) -> dict[str, Any] | None:
        path = data_dir / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/api/experiment")
    def experiment(_: str = Depends(require_token)) -> dict[str, Any]:
        data = _json_file("experiment.json")
        if data is None:
            raise HTTPException(status_code=404,
                                detail="no experiment report; run python -m urudhi.sim --arms all")
        return data | {"source": "simulation"}

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
