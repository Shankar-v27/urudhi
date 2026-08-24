"""The agent's brain: language in, structure out.

The brain has exactly two jobs and no authority:

* **interpret** a debtor's reply into a typed :class:`ReplyInterpretation`
  (did they promise? dispute? ask us to stop?), quoting their verbatim words;
* **draft** the outgoing message for an action the policy layer has already
  approved.

It never decides to contact anyone, never sets an offer's terms, and nothing
it outputs reaches money or the ledger except through policy gates and state
transitions. Two implementations: :class:`ClaudeBrain` for live runs and
:class:`MockBrain`, a deterministic rule-based stand-in that makes the batch
runner reproducible and free to run (``ANTHROPIC_API_KEY`` unset).
"""

from __future__ import annotations

import enum
import json
import re
from datetime import date, timedelta
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from urudhi.ledger.money import Paise, format_inr


class Intent(enum.StrEnum):
    PROMISE = "promise"            # committed to an amount by a date
    REQUEST_TERMS = "request_terms"  # asked for discount / installments / time
    DISPUTE = "dispute"            # contests the invoice or claims it is paid
    STOP_CONTACT = "stop_contact"  # asked us to stop contacting them
    VAGUE = "vague"                # deflection with no usable commitment
    QUESTION = "question"          # asked something needing an answer


class ReplyInterpretation(BaseModel):
    """What the debtor's message actually said, in ledger-ready form."""

    intent: Intent
    verbatim: str                                 # the debtor's words, unedited
    promised_amount: Paise | None = None
    promised_on: date | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    summary: str = ""


class MessageContext(BaseModel):
    """Everything a brain may see when drafting or interpreting."""

    debtor_name: str
    contact_name: str
    invoice_number: str
    balance: Paise
    days_overdue: int
    today: date
    language: str = "en"
    approved_offer_text: str | None = None   # set only when policy approved an offer
    payment_url: str | None = None
    history_summary: str = ""                # prior attempts, promises kept/broken


class Brain(Protocol):
    def interpret_reply(self, context: MessageContext, reply: str) -> ReplyInterpretation: ...
    def draft_message(self, context: MessageContext) -> str: ...


# --------------------------------------------------------------------------
# Deterministic mock brain
# --------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"(?:₹|rs\.?\s*|inr\s*)([\d,]+)", re.IGNORECASE)
_IN_DAYS_RE = re.compile(r"\b(?:in|within|after)\s+(\d{1,2})\s+days?\b", re.IGNORECASE)
_STOP_RE = re.compile(r"\b(stop (?:contacting|messaging)|do not contact|unsubscribe)\b",
                      re.IGNORECASE)
_DISPUTE_RE = re.compile(r"\b(dispute|already paid|wrong invoice|never ordered|not ours)\b",
                         re.IGNORECASE)
_TERMS_RE = re.compile(r"\b(discount|installments?|emi|more time|extension|part payment)\b",
                       re.IGNORECASE)
_PROMISE_RE = re.compile(r"\b(will pay|will clear|will settle|transferring|kudukiren)\b",
                         re.IGNORECASE)


class MockBrain:
    """Keyword-rule interpreter and template drafter. Same shape as Claude,
    zero variance between runs — published batch numbers are re-runnable."""

    def interpret_reply(self, context: MessageContext, reply: str) -> ReplyInterpretation:
        if _STOP_RE.search(reply):
            return ReplyInterpretation(
                intent=Intent.STOP_CONTACT, verbatim=reply, confidence=1.0,
                summary="debtor asked us to stop contacting them",
            )
        if _DISPUTE_RE.search(reply):
            return ReplyInterpretation(
                intent=Intent.DISPUTE, verbatim=reply, confidence=0.9,
                summary="debtor contests the invoice",
            )
        if _PROMISE_RE.search(reply):
            amount_match = _AMOUNT_RE.search(reply)
            amount = (
                int(amount_match.group(1).replace(",", "")) * 100
                if amount_match else context.balance
            )
            days_match = _IN_DAYS_RE.search(reply)
            days = int(days_match.group(1)) if days_match else 7
            explicit = bool(amount_match and days_match)
            return ReplyInterpretation(
                intent=Intent.PROMISE, verbatim=reply,
                promised_amount=min(amount, context.balance),
                promised_on=context.today + timedelta(days=days),
                confidence=0.9 if explicit else 0.6,
                summary="debtor committed to pay"
                        + ("" if explicit else " (amount or date inferred)"),
            )
        if _TERMS_RE.search(reply):
            return ReplyInterpretation(
                intent=Intent.REQUEST_TERMS, verbatim=reply, confidence=0.8,
                summary="debtor asked for concessions",
            )
        if reply.rstrip().endswith("?"):
            return ReplyInterpretation(
                intent=Intent.QUESTION, verbatim=reply, confidence=0.7,
                summary="debtor asked a question",
            )
        return ReplyInterpretation(
            intent=Intent.VAGUE, verbatim=reply, confidence=0.3,
            summary="no usable commitment",
        )

    def draft_message(self, context: MessageContext) -> str:
        lines = [
            f"Namaste {context.contact_name}, this is Urudhi writing on behalf of "
            f"your supplier regarding invoice {context.invoice_number}.",
            f"The outstanding balance is {format_inr(context.balance)}, "
            f"now {context.days_overdue} days past due.",
        ]
        if context.approved_offer_text:
            lines.append(context.approved_offer_text)
        if context.payment_url:
            lines.append(f"You can pay securely here: {context.payment_url}")
        lines.append("If anything about this invoice looks wrong, reply here and "
                     "a person will look into it. Reply STOP to opt out.")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Claude brain
# --------------------------------------------------------------------------

_INTERPRET_SYSTEM = """\
You read a debtor's reply in an Indian B2B receivables conversation (English,
Hindi, Tamil, or code-switched) and return ONLY a JSON object:

{"intent": "promise|request_terms|dispute|stop_contact|vague|question",
 "promised_amount_paise": int|null, "promised_on": "YYYY-MM-DD"|null,
 "confidence": 0.0-1.0, "summary": "one line"}

Rules:
- confidence reflects how firm the commitment is. An explicit amount AND date
  is high (0.85+); "next week sometime" is low (0.4 or less).
- Never invent an amount or date the debtor did not state or clearly imply.
- Any request to stop contact is stop_contact, confidence 1.0. When unsure
  between dispute and vague, choose dispute — a human reviewing is the safe
  failure. Do not exceed the invoice balance."""

_DRAFT_SYSTEM = """\
You draft one short, courteous B2B payment reminder for an Indian business
context. Firm about the facts, respectful in tone, no threats, no legal
language. Include: invoice number, balance, days overdue; the offer text
verbatim if provided; the payment link if provided; one line inviting the
debtor to flag any error; and a STOP opt-out line. Write in the debtor's
language ("ta"=Tamil, "hi"=Hindi, else English). Return only the message."""


class ClaudeBrain:
    def __init__(self, api_key: str, model: str = "claude-sonnet-5") -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def interpret_reply(self, context: MessageContext, reply: str) -> ReplyInterpretation:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=400,
            system=_INTERPRET_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Invoice {context.invoice_number}, balance "
                    f"{context.balance} paise, today {context.today}.\n"
                    f"Debtor reply:\n{reply}"
                ),
            }],
        )
        raw = response.content[0].text.strip()
        try:
            data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            return ReplyInterpretation(
                intent=Intent(data["intent"]),
                verbatim=reply,
                promised_amount=data.get("promised_amount_paise"),
                promised_on=data.get("promised_on"),
                confidence=float(data.get("confidence", 0.0)),
                summary=str(data.get("summary", "")),
            )
        except (ValueError, KeyError, ValidationError):
            # An uninterpretable model response must fail safe: treat as a
            # dispute so a human reads the thread, and record why.
            return ReplyInterpretation(
                intent=Intent.DISPUTE, verbatim=reply, confidence=0.0,
                summary="model output unparseable; routed to human review",
            )

    def draft_message(self, context: MessageContext) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=500,
            system=_DRAFT_SYSTEM,
            messages=[{"role": "user", "content": context.model_dump_json()}],
        )
        return response.content[0].text.strip()
