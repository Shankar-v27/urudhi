"""Thin Razorpay test-mode client.

Only the operations Urudhi actually uses, behind a small protocol so tests and
the simulator can substitute a fake without patching. Amounts are paise
throughout — Razorpay's own convention.
"""

from __future__ import annotations

from typing import Any, Protocol

import razorpay


class RailsClient(Protocol):
    """The surface Urudhi needs from a payment rail."""

    def create_invoice(self, *, invoice_number: str, amount: int, customer_name: str,
                       customer_email: str, customer_contact: str, description: str) -> dict:
        """Create a rail-side invoice; returns the provider's representation."""
        ...

    def create_payment_link(self, *, amount: int, description: str, reference_id: str,
                            customer_name: str, customer_email: str,
                            customer_contact: str) -> dict:
        """Create a payment link for a negotiated amount (discounted / installment)."""
        ...

    def create_virtual_account(self, *, reference_id: str, description: str) -> dict:
        """Create a Smart Collect virtual account for NEFT/RTGS/IMPS collection."""
        ...


class RazorpayRails:
    """Live test-mode implementation over the official SDK."""

    def __init__(self, key_id: str, key_secret: str) -> None:
        if not key_id.startswith("rzp_test_"):
            raise ValueError(
                "Urudhi only runs against Razorpay test mode; "
                f"refusing key id {key_id[:12]}…"
            )
        self._client = razorpay.Client(auth=(key_id, key_secret))

    def create_invoice(self, *, invoice_number: str, amount: int, customer_name: str,
                       customer_email: str, customer_contact: str, description: str) -> dict:
        return self._client.invoice.create({
            "type": "invoice",
            "description": description,
            "currency": "INR",
            "customer": {
                "name": customer_name,
                "email": customer_email,
                "contact": customer_contact,
            },
            "line_items": [{
                "name": f"Invoice {invoice_number}",
                "amount": amount,
                "currency": "INR",
                "quantity": 1,
            }],
        })

    def create_payment_link(self, *, amount: int, description: str, reference_id: str,
                            customer_name: str, customer_email: str,
                            customer_contact: str) -> dict:
        return self._client.payment_link.create({
            "amount": amount,
            "currency": "INR",
            "description": description,
            "reference_id": reference_id,
            "notes": {"invoice_id": reference_id},
            "customer": {
                "name": customer_name,
                "email": customer_email,
                "contact": customer_contact,
            },
            "notify": {"sms": False, "email": False},
        })

    def create_virtual_account(self, *, reference_id: str, description: str) -> dict:
        return self._client.virtual_account.create({
            "receivers": {"types": ["bank_account"]},
            "description": description,
            "notes": {"reference_id": reference_id},
        })


def payment_amount(entity: dict[str, Any]) -> int:
    """Paise amount from a payment entity; defensive about missing fields."""
    amount = entity.get("amount")
    if not isinstance(amount, int) or amount <= 0:
        raise ValueError(f"payment entity carries no usable amount: {amount!r}")
    return amount
