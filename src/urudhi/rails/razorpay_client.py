"""Thin Razorpay test-mode client.

Only the operations Urudhi actually uses, behind a small protocol so tests and
the simulator can substitute a fake without patching. Amounts are paise
throughout — Razorpay's own convention. Every rail-side object is tagged
``notes.invoice_id`` (and ``notes.commitment_id`` when it executes a
commitment) so the webhook path can resolve it exactly. ``reference_id`` —
which Razorpay requires to be unique per link — carries the commitment id
when there is one, else a per-invoice sequence.
"""

from __future__ import annotations

from typing import Any, Protocol

from urudhi.observability import counters, get_logger

log = get_logger("urudhi.rails")

SUPPORTED_CURRENCY = "INR"


class RailsClient(Protocol):
    """The surface Urudhi needs from a payment rail."""

    def create_payment_link(self, *, amount: int, description: str, invoice_id: str,
                            customer_name: str, customer_email: str,
                            customer_contact: str, expire_by: int | None = None,
                            commitment_id: str | None = None,
                            reference_id: str | None = None) -> dict:
        """Create a payment link for an exact amount (balance, settlement, or a commitment)."""
        ...

    def create_virtual_account(self, *, invoice_id: str, description: str) -> dict:
        """Create a Smart Collect virtual account for NEFT/RTGS/IMPS collection."""
        ...


class RazorpayRails:
    """Live test-mode implementation over the official SDK."""

    def __init__(self, key_id: str, key_secret: str) -> None:
        import razorpay

        if not key_id.startswith("rzp_test_"):
            raise ValueError(
                "Urudhi only runs against Razorpay test mode; "
                f"refusing key id {key_id[:12]}…"
            )
        self._client = razorpay.Client(auth=(key_id, key_secret))

    def create_payment_link(self, *, amount: int, description: str, invoice_id: str,
                            customer_name: str, customer_email: str,
                            customer_contact: str, expire_by: int | None = None,
                            commitment_id: str | None = None,
                            reference_id: str | None = None) -> dict:
        body: dict[str, Any] = {
            "amount": amount,
            "currency": SUPPORTED_CURRENCY,
            "description": description,
            "reference_id": link_reference(invoice_id, commitment_id, reference_id),
            "notes": link_notes(invoice_id, commitment_id),
            "customer": {
                "name": customer_name,
                "email": customer_email,
                "contact": customer_contact,
            },
            "notify": {"sms": False, "email": False},
        }
        if expire_by:
            body["expire_by"] = expire_by
        counters.inc("rails.payment_link.created")
        return self._client.payment_link.create(body)

    def create_virtual_account(self, *, invoice_id: str, description: str) -> dict:
        counters.inc("rails.virtual_account.created")
        return self._client.virtual_account.create({
            "receivers": {"types": ["bank_account"]},
            "description": description,
            "notes": {"invoice_id": invoice_id},
        })


class FakeRails:
    """Offline rail for the simulator and tests: links are deterministic URLs."""

    def __init__(self) -> None:
        self.links: list[dict[str, Any]] = []
        self.virtual_accounts: list[dict[str, Any]] = []

    def create_payment_link(self, *, amount: int, description: str, invoice_id: str,
                            customer_name: str, customer_email: str,
                            customer_contact: str, expire_by: int | None = None,
                            commitment_id: str | None = None,
                            reference_id: str | None = None) -> dict:
        n = len(self.links) + 1
        link = {
            "id": f"plink_fake_{n:04d}", "amount": amount, "currency": SUPPORTED_CURRENCY,
            "reference_id": link_reference(invoice_id, commitment_id, reference_id),
            "notes": link_notes(invoice_id, commitment_id),
            "short_url": f"https://rzp.io/l/fake{n:04d}", "status": "created",
            "expire_by": expire_by,
        }
        self.links.append(link)
        return link

    def create_virtual_account(self, *, invoice_id: str, description: str) -> dict:
        n = len(self.virtual_accounts) + 1
        va = {
            "id": f"va_fake_{n:04d}", "notes": {"invoice_id": invoice_id},
            "receivers": [{"account_number": f"2323230{n:08d}", "ifsc": "RATN0VAAPIS"}],
        }
        self.virtual_accounts.append(va)
        return va


def link_notes(invoice_id: str, commitment_id: str | None) -> dict[str, str]:
    notes = {"invoice_id": invoice_id}
    if commitment_id:
        notes["commitment_id"] = commitment_id
    return notes


def link_reference(invoice_id: str, commitment_id: str | None, explicit: str | None) -> str:
    """Razorpay wants ``reference_id`` unique per link and ≤ 40 chars."""
    return (explicit or commitment_id or invoice_id)[:40]


def payment_amount(entity: dict[str, Any]) -> int:
    """Paise amount from a payment entity; defensive about missing/odd fields."""
    amount = entity.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise ValueError(f"payment entity carries no usable amount: {amount!r}")
    currency = entity.get("currency", SUPPORTED_CURRENCY)
    if currency != SUPPORTED_CURRENCY:
        raise ValueError(f"unsupported currency {currency!r}; ledger is {SUPPORTED_CURRENCY}")
    return amount
