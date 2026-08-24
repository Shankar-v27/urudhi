"""Urudhi — AI receivables recovery agent.

Core packages:
    agent    negotiation loop, LLM harness, policy gates
    ledger   invoice and promise-to-pay state machines
    scoring  chase prioritization
    rails    Razorpay integration (invoices, payment links, smart collect, webhooks)
    audit    append-only event log
    sim      synthetic debtor personas and batch runner
    api      FastAPI application
"""

__version__ = "0.1.0"
