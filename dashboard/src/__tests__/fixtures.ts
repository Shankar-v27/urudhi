/**
 * Fixtures copied from real API responses (shapes and values as served by `urudhi.api`):
 * a live Razorpay test-mode commitment from the :8000 ledger and a sandbox commitment from the
 * mock batch. Test data only — the application never carries any of this.
 */
import type { Commitment, Escalation, Health, Invoice, InvoiceDetail, Summary } from "../api";

export const LIVE_URL = "https://rzp.io/rzp/fLnAb1SP";

export const liveCommitment: Commitment = {
  id: "cmt_inv_live_20260827170223_1", invoice_id: "inv_live_20260827170223", debtor_id: "deb_live_20260827170223",
  promise_id: "ptp_inv_live_20260827170223_1", concession_id: null, installment_index: null, source: "promise",
  committed_amount: 5000000, currency: "INR", due_on: "2026-08-28", due_at: "2026-08-28T23:59:59+05:30", state: "active",
  instrument_type: "payment_link", instrument_id: "plink_TUmLQ82CcnfqwP", payment_url: LIVE_URL,
  instrument_sent: true, reminder_sent: false, created_at: "2026-08-27T11:32:41.579168Z",
  accepted_at: "2026-08-27T11:32:41.579168Z", fulfilled_at: null, missed_at: null, resolved_at: null,
  amount_received: 0, days_late: 0, confidence: 0.88, evidence: "Cash konjam tight ah iruku. Friday 50000 kudukuren.",
  rationale: "₹50,000.00 by 2026-08-28 within delegated authority", cancel_reason: "", amount_remaining: 5000000,
  invoice_number: "URU/2026/L170223", instrument_mode: "razorpay_test", instrument_failed: false,
};

export const sandboxCommitment: Commitment = {
  id: "cmt_inv_003_1", invoice_id: "inv_003", debtor_id: "deb_003", promise_id: "ptp_inv_003_1", concession_id: null,
  installment_index: null, source: "promise", committed_amount: 15772100, currency: "INR", due_on: "2026-08-25",
  due_at: "2026-08-25T23:59:59+05:30", state: "fulfilled", instrument_type: "payment_link", instrument_id: "plink_fake_0076",
  payment_url: "https://rzp.io/l/fake0076", instrument_sent: true, reminder_sent: false,
  created_at: "2026-08-24T13:00:00+05:30", accepted_at: "2026-08-24T13:00:00+05:30", fulfilled_at: "2026-08-25T09:00:00+05:30",
  missed_at: null, resolved_at: "2026-08-25T09:00:00+05:30", amount_received: 15772100, days_late: 0, confidence: 0.9,
  evidence: "Will transfer ₹157,721 by Tuesday. Rest next month.", rationale: "₹1,57,721.00 by 2026-08-25 within delegated authority",
  cancel_reason: "", amount_remaining: 0, invoice_number: "URU/2026/0003", instrument_mode: "sandbox", instrument_failed: false,
};

export const liveInvoice: Invoice = {
  id: "inv_live_20260827170223", debtor_id: "deb_live_20260827170223", number: "URU/2026/L170223", amount: 5000000,
  issued_on: "2026-06-28", due_on: "2026-07-28", state: "promised", amount_paid: 0, amount_waived: 0,
  human_released_at: null, balance: 5000000, debtor_name: "Kumar Textiles",
};

export const health: Health = {
  status: "ok", version: "0.2.0", brain: "claude", transport: "email:sandbox", rails: "razorpay-test",
  policy_timezone: "Asia/Kolkata", invoices: 1, audit_chain: { verified: true, events: 13 }, counters: {},
};

export const summary: Summary = {
  invoices: 1, outstanding_paise: 5000000, recovered_paise: 0, waived_paise: 0,
  by_state: { outstanding: 0, promised: 1, partially_paid: 0, paid: 0, disputed: 0, escalated: 0, stop_contact: 0, closed: 0 },
  messages_sent: 2, by_intervention: { payment_link: 1, commitment_confirmation: 1 }, brain: "claude", transport: "email:sandbox",
  commitments: {
    created: 1, active: 1, fulfilled: 0, fulfilled_on_time: 0, partially_fulfilled: 0, missed: 0, cancelled: 0,
    fulfillment_rate: null, amount_committed_paise: 5000000, amount_received_paise: 0, conversion: 0, average_delay_days: null,
    recovered_per_commitment_paise: 0, recovered_per_attempt_paise: 0, messages_total: 2, nudges: 1, exact_instrument_matched_paise: 0,
  },
};

export const liveDetail: InvoiceDetail = {
  invoice: liveInvoice,
  debtor: {
    id: "deb_live_20260827170223", name: "Kumar Textiles", contact_name: "Kumar", phone: "+91••••••••01",
    email: "v•••@razorpay.com", preferred_channel: "email", language: "ta",
  },
  promises: [{
    id: "ptp_inv_live_20260827170223_1", invoice_id: "inv_live_20260827170223", debtor_id: "deb_live_20260827170223",
    amount: 5000000, promised_on: "2026-08-28", made_at: "2026-08-27T11:32:41.579168Z", channel: "email",
    verbatim: "Cash konjam tight ah iruku. Friday 50000 kudukuren.", confidence: 0.88, state: "open", resolved_at: null,
  }],
  commitments: [liveCommitment],
  concessions: [],
  payments: [],
  events: [
    { seq: 10, at: "2026-08-27T11:32:41.579168+00:00", actor: "rails", kind: "payment_instrument_created", invoice_id: "inv_live_20260827170223",
      payload: { commitment_id: "cmt_inv_live_20260827170223_1", instrument_type: "payment_link", instrument_id: "plink_TUmLQ82CcnfqwP", payment_url: LIVE_URL, amount: 5000000 },
      hash: "ddd0ffe79928dd01aaaa" },
  ],
  explain: {
    invoice_id: "inv_live_20260827170223",
    priority: { score: 30, components: { value: 0, urgency: 0.4778, credibility: 0.35, fatigue: 0.6667 },
      reasons: ["− balance ₹0.00 (value 0.00)", "+ 31 days overdue (urgency 0.48)", "· no commitment history (credibility 0.50)", "+ 2 attempt(s) used (fatigue 0.67)"] },
    credibility: { commitments: 1, active: 1, fulfilled: 0, fulfilled_on_time: 0, partially_fulfilled: 0, missed: 0, cancelled: 0,
      fulfillment_rate: null, average_delay_days: null, average_committed: 5000000, amount_committed: 5000000, amount_received: 0,
      last_outcome: null, credibility: 0.5, reasons: ["· 1 active, none resolved yet"] },
    latest_decision: { at: "2026-08-27T11:32:23.367156+00:00", proposed: "payment_link", final: "payment_link", modified: false,
      rationale: ["make paying one tap"], confidence: 0.6, policy_reasons: [],
      gates: [{ ok: true, gate: "contact", reason: "within contact hours, spacing and attempt limits" }, { ok: true, gate: "offer", reason: "no concession proposed" }],
      offer: null },
    decision_history: [],
    promises: [],
    concessions: [],
    commitments: [{
      id: "cmt_inv_live_20260827170223_1", state: "active", source: "promise", invoice_id: "inv_live_20260827170223", installment_index: null,
      committed_amount: 5000000, amount_received: 0, amount_remaining: 5000000, due_on: "2026-08-28", due_at: "2026-08-28T23:59:59+05:30",
      created_at: "2026-08-27T11:32:41.579168Z", fulfilled_at: null, missed_at: null, days_late: 0, confidence: 0.88, cancel_reason: null,
      said: { verbatim: "Cash konjam tight ah iruku. Friday 50000 kudukuren.", promise_id: "ptp_inv_live_20260827170223_1", promise_state: "open",
        at: "2026-08-27T11:32:41.579168Z", event: { seq: 6, at: "2026-08-27T11:32:41.579168+00:00", kind: "message_received", hash: "a861ee7baf8406b2" } },
      understood: { intent: "promise", amount: 5000000, on: "2026-08-28", confidence: 0.88, flags: [], brain: "claude", partial: true,
        event: { seq: 8, at: "2026-08-27T11:32:41.579168+00:00", kind: "commitment_proposed", hash: "53416b14a8a42986" } },
      policy: { allowed: true, reason: "₹50,000.00 by 2026-08-28 within delegated authority",
        checks: [
          { allowed: true, gate: "invoice_active", reason: "invoice is promised" },
          { allowed: true, gate: "amount_within_balance", reason: "₹50,000.00 ≤ balance ₹50,000.00" },
          { allowed: true, gate: "deadline_within_horizon", reason: "1 day(s) out, within the 30-day horizon" },
        ],
        event: { seq: 9, at: "2026-08-27T11:32:41.579168+00:00", kind: "commitment_approved", hash: "df007391558f2442" } },
      instrument: { type: "payment_link", id: "plink_TUmLQ82CcnfqwP", url: LIVE_URL, amount: 5000000, expires: "2026-08-28T23:59:59+05:30",
        notes: { commitment_id: "cmt_inv_live_20260827170223_1", invoice_id: "inv_live_20260827170223" }, reference_id: "cmt_inv_live_20260827170223_1",
        sent: true, mode: "razorpay_test", failed: false, failure_reason: null,
        event: { seq: 10, at: "2026-08-27T11:32:41.579168+00:00", kind: "payment_instrument_created", hash: "ddd0ffe79928dd01" },
        confirmation: { seq: 13, at: "2026-08-27T11:32:41.579168+00:00", kind: "message_sent", hash: "88f3111f6b434f2f" } },
      rail: [],
      outcome: { state: "active", promise_state: "open", event: null,
        created_event: { seq: 11, at: "2026-08-27T11:32:41.579168+00:00", kind: "commitment_created", hash: "5a112ba6934dc40d" } },
      timeline: [],
    }],
    blocked_commitments: [],
    payments: [],
    amount_waived: 0,
    escalation: null,
    dispute: null,
    brain_failures: 0,
    interventions: [],
  },
};

export const escalation: Escalation = {
  invoice_id: "inv_101", number: "URU/2026/0101", state: "disputed", balance: 67740000, amount_paid: 0,
  since: "2026-08-24T13:00:00+05:30", reason: "debtor contests the invoice", verbatim: "Yeh invoice galat hai, hume yeh order mila hi nahi.",
  acknowledged: false, human_actions: [], last_commitment: null, commitments_missed: 0, commitments_fulfilled: 0, credibility: 0.5,
  recommended_action: "human review",
};

/** A `fetch` stub that routes by path. Unrouted paths get 404 with a JSON detail like the API. */
export function mockFetch(routes: Record<string, unknown | ((init?: RequestInit) => unknown)>) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const key = Object.keys(routes).find((k) => k === path || (k.endsWith("*") && path.startsWith(k.slice(0, -1))));
    if (!key) {
      return new Response(JSON.stringify({ detail: `no route for ${path}` }), { status: 404, headers: { "Content-Type": "application/json" } });
    }
    const value = routes[key];
    const body = typeof value === "function" ? (value as (init?: RequestInit) => unknown)(init) : value;
    if (body instanceof Response) return body;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}
