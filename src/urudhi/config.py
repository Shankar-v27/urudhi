"""Startup configuration report — presence only, never values.

Every entry point prints this once so an operator can see at a glance what
the process is wired to (Razorpay test keys, webhook secret, the LLM
endpoint) without a single secret reaching a terminal, a log or a screenshot.
Non-secret settings (base URL, model, timezone) are shown verbatim; secrets
are reduced to ``configured`` / ``missing``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

SECRET_VARS = ("RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "ANTHROPIC_API_KEY",
               "URUDHI_API_TOKEN", "SMTP_PASSWORD")


def _present(value: str | None) -> str:
    return "configured" if value and value.strip() else "missing"


def presence_report(environ: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    env = environ if environ is not None else os.environ
    key_id = env.get("RAZORPAY_KEY_ID", "").strip()
    key_mode = ("test mode" if key_id.startswith("rzp_test_")
                else "NOT a test key — refused" if key_id else "missing")
    return [
        ("Razorpay Key ID", f"{_present(key_id)} ({key_mode})" if key_id else "missing"),
        ("Razorpay Key Secret", _present(env.get("RAZORPAY_KEY_SECRET"))),
        ("Razorpay Webhook Secret", _present(env.get("RAZORPAY_WEBHOOK_SECRET"))),
        ("Claude API", _present(env.get("ANTHROPIC_API_KEY"))),
        ("Claude Base URL", env.get("ANTHROPIC_BASE_URL", "").strip() or "(SDK default)"),
        ("Claude Model", env.get("ANTHROPIC_MODEL", "").strip() or "(unset)"),
        ("Urudhi API token", _present(env.get("URUDHI_API_TOKEN"))),
        ("Policy timezone", env.get("URUDHI_TZ", "").strip() or "Asia/Kolkata"),
    ]


def format_presence_report(environ: Mapping[str, str] | None = None) -> str:
    rows = presence_report(environ)
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label:<{width}} : {value}" for label, value in rows)
