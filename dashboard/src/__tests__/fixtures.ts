/**
 * Fixtures copied from real API responses (shapes and values as served by `urudhi.api` with two ledgers):
 * a live Razorpay test-mode commitment from the live_test ledger and a sandbox commitment from the simulation
 * ledger, plus /health, /api/summary and /api/commitments/{id}. Test data only — the application never carries any of this.
 */
import type {
  Commitment, CommitmentChain, CommitmentDetail, Escalation, Experiment, Health, Invoice, InvoiceDetail, Promise_,
  ReplyEval, Summary,
} from "../api";

export const LIVE_URL = "https://rzp.io/rzp/fLnAb1SP";
export const SANDBOX_URL = "https://sandbox.urudhi.invalid/pay/plink_fake_0076";

export const liveCommitment: Commitment = {
  id: "cmt_inv_live_20260827170223_1", invoice_id: "inv_live_20260827170223", debtor_id: "deb_live_20260827170223",
  promise_id: "ptp_inv_live_20260827170223_1", concession_id: null, installment_index: null, source: "live_test", origin: null,
  committed_amount: 5000000, currency: "INR", due_on: "2026-08-28", due_at: "2026-08-28T23:59:59+05:30", state: "active",
  instrument_type: "payment_link", instrument_id: "plink_TUmLQ82CcnfqwP", payment_url: LIVE_URL,
  instrument_mode: "razorpay_test", instrument_failed: false, instrument_failure: "",
  instrument_sent: true, reminder_sent: false, created_at: "2026-08-27T11:32:41.579168Z",
  accepted_at: "2026-08-27T11:32:41.579168Z", fulfilled_at: null, missed_at: null, resolved_at: null,
  amount_received: 0, days_late: 0, confidence: 0.88, evidence: "Cash konjam tight ah iruku. Friday 50000 kudukuren.",
  rationale: "₹50,000.00 by 2026-08-28 within delegated authority", cancel_reason: "", amount_remaining: 5000000,
  invoice_number: "URU/2026/L170223", debtor_name: "Kumar Textiles", rail_origin: null, rail: "razorpay_test",
};

export const sandboxCommitment: Commitment = {
  id: "cmt_inv_003_1", invoice_id: "inv_003", debtor_id: "deb_003", promise_id: "ptp_inv_003_1", concession_id: null,
  installment_index: null, source: "simulation", origin: null, committed_amount: 15772100, currency: "INR", due_on: "2026-08-25",
  due_at: "2026-08-25T23:59:59+05:30", state: "fulfilled", instrument_type: "payment_link", instrument_id: "plink_fake_0076",
  payment_url: SANDBOX_URL, instrument_mode: "sandbox", instrument_failed: false, instrument_failure: "",
  instrument_sent: true, reminder_sent: false,
  created_at: "2026-08-24T13:00:00+05:30", accepted_at: "2026-08-24T13:00:00+05:30", fulfilled_at: "2026-08-25T09:00:00+05:30",
  missed_at: null, resolved_at: "2026-08-25T09:00:00+05:30", amount_received: 15772100, days_late: 0, confidence: 0.9,
  evidence: "Will transfer ₹157,721 by Tuesday. Rest next month.", rationale: "₹1,57,721.00 by 2026-08-25 within delegated authority",
  cancel_reason: "", amount_remaining: 0, invoice_number: "URU/2026/0003", debtor_name: "Annapoorna Foods", rail_origin: null, rail: "sandbox",
};

/** The rail refused to issue: no id, no URL, explicit failure text (as served for a live commitment). */
export const failedCommitment: Commitment = {
  ...liveCommitment, id: "cmt_inv_live_coimbatore_20260828224824_1", invoice_id: "inv_live_coimbatore_20260828224824",
  invoice_number: "URU/2026/L224824", debtor_name: "Coimbatore Mills", promise_id: "ptp_inv_live_coimbatore_20260828224824_1",
  committed_amount: 120000000, amount_remaining: 120000000, instrument_id: null, payment_url: null, instrument_mode: null,
  instrument_failed: true, instrument_failure: "BadRequestError: amount exceeds maximum amount allowed.", instrument_sent: false, rail: null,
  evidence: "Will pay 12 lakh by month end.",
};

export const liveInvoice: Invoice = {
  id: "inv_live_20260827170223", debtor_id: "deb_live_20260827170223", number: "URU/2026/L170223", amount: 5000000,
  issued_on: "2026-06-28", due_on: "2026-07-28", state: "promised", amount_paid: 0, amount_waived: 0,
  human_released_at: null, balance: 5000000, debtor_name: "Kumar Textiles", source: "live_test",
};

export const simInvoice: Invoice = {
  id: "inv_003", debtor_id: "deb_003", number: "URU/2026/0003", amount: 31544200, issued_on: "2026-06-10", due_on: "2026-07-10",
  state: "partially_paid", amount_paid: 15772100, amount_waived: 0, human_released_at: null, balance: 15772100,
  debtor_name: "Annapoorna Foods", source: "simulation",
};

export const health: Health = {
  status: "ok", version: "0.2.0", brain: "claude", transport: "email:sandbox", rails: "razorpay_test",
  policy_timezone: "Asia/Kolkata", invoices: 127, audit_chain: { verified: true, events: 3711 },
  ledgers: [
    { source: "live_test", db: "data/live_demo.sqlite3", invoices: 7, audit_chain: { verified: true, events: 63 }, brain: null },
    { source: "simulation", db: "data/run.sqlite3", invoices: 120, audit_chain: { verified: true, events: 3648 }, brain: "mock" },
  ],
  sources: ["live_test", "simulation"], counters: { "http.requests": 1 },
};

const liveCommitmentSummary = {
  created: 1, active: 1, fulfilled: 0, fulfilled_on_time: 0, partially_fulfilled: 0, missed: 0, cancelled: 0,
  fulfillment_rate: null, amount_committed_paise: 5000000, amount_received_paise: 0, conversion: 0, average_delay_days: null,
  recovered_per_commitment_paise: 0, recovered_per_attempt_paise: 0, messages_total: 2, nudges: 1, exact_instrument_matched_paise: 0,
  instruments_razorpay_test: 1, instruments_sandbox: 0,
};
const simCommitmentSummary = {
  created: 153, active: 11, fulfilled: 99, fulfilled_on_time: 99, partially_fulfilled: 2, missed: 41, cancelled: 0,
  fulfillment_rate: 0.7071, amount_committed_paise: 1435215806, amount_received_paise: 1047221964, conversion: 0.6993,
  average_delay_days: 0, recovered_per_commitment_paise: 8515558, recovered_per_attempt_paise: 4110033, messages_total: 486,
  nudges: 317, exact_instrument_matched_paise: 1061789614, instruments_razorpay_test: 0, instruments_sandbox: 153,
};

export const summaryLive: Summary = {
  source: "live_test", sources: ["live_test"], invoices: 1, outstanding_paise: 5000000, recovered_paise: 0, waived_paise: 0,
  by_state: { outstanding: 0, promised: 1, partially_paid: 0, paid: 0, disputed: 0, escalated: 0, stop_contact: 0, closed: 0 },
  messages_sent: 2, by_intervention: { payment_link: 1, commitment_confirmation: 1 }, brain: "claude", transport: "email:sandbox", rails: "razorpay_test",
  commitments: liveCommitmentSummary,
  context: { source: "live_test", brain: "claude", rail: "razorpay_test", payments_observed: 0, audit_events: 63, chain_verified: true, provenance: "Razorpay Test Mode · observed via signed webhook" },
};

export const summarySim: Summary = {
  source: "simulation", sources: ["simulation"], invoices: 120, outstanding_paise: 1906630000, recovered_paise: 1302880514, waived_paise: 63000,
  by_state: { outstanding: 3, promised: 1, partially_paid: 6, paid: 70, disputed: 9, escalated: 24, stop_contact: 7, closed: 0 },
  messages_sent: 486, by_intervention: { payment_link: 218, commitment_confirmation: 149 }, brain: "claude", transport: "email:sandbox", rails: "razorpay_test",
  commitments: simCommitmentSummary,
  context: { source: "simulation", brain: "mock", rail: "sandbox", payments_observed: 122, audit_events: 3648, chain_verified: true, provenance: "Simulation · persona model · webhook-shaped events" },
};

export const summaryAll: Summary = {
  source: "all", sources: ["live_test", "simulation"],
  by_source: [
    { source: "live_test", invoices: 1, outstanding_paise: 5000000, recovered_paise: 0, waived_paise: 0, by_state: summaryLive.by_state,
      messages_sent: 2, by_intervention: summaryLive.by_intervention, commitments: liveCommitmentSummary, context: summaryLive.context! },
    { source: "simulation", invoices: 120, outstanding_paise: 1906630000, recovered_paise: 1302880514, waived_paise: 63000, by_state: summarySim.by_state,
      messages_sent: 486, by_intervention: summarySim.by_intervention, commitments: simCommitmentSummary, context: summarySim.context! },
  ],
  invoices: 121, outstanding_paise: 1911630000, recovered_paise: 1302880514, waived_paise: 63000,
  by_state: { outstanding: 3, promised: 2, partially_paid: 6, paid: 70, disputed: 9, escalated: 24, stop_contact: 7, closed: 0 },
  messages_sent: 488, by_intervention: { payment_link: 219, commitment_confirmation: 150 }, brain: "claude", transport: "email:sandbox", rails: "razorpay_test",
  commitments: { ...simCommitmentSummary, created: 154, active: 12, amount_committed_paise: 1440215806, messages_total: 488, nudges: 318, instruments_razorpay_test: 1 },
  context: { source: "all", brain: "claude", rail: "razorpay_test", payments_observed: 122, audit_events: 3711, chain_verified: true, provenance: "Mixed: live test-mode records and simulation records, labelled per row" },
};

/** Backwards-compatible alias used by older tests. */
export const summary = summaryLive;

export const liveChain: CommitmentChain = {
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
      { allowed: true, gate: "not_stop_contact", reason: "debtor has not asked us to stop" },
      { allowed: true, gate: "amount_within_balance", reason: "₹50,000.00 ≤ balance ₹50,000.00" },
      { allowed: true, gate: "deadline_within_horizon", reason: "1 day(s) out, within the 30-day horizon" },
    ],
    event: { seq: 9, at: "2026-08-27T11:32:41.579168+00:00", kind: "commitment_approved", hash: "df007391558f2442" } },
  instrument: { type: "payment_link", id: "plink_TUmLQ82CcnfqwP", url: LIVE_URL, amount: 5000000, expires: "2026-08-28T23:59:59+05:30",
    notes: { commitment_id: "cmt_inv_live_20260827170223_1", invoice_id: "inv_live_20260827170223" }, reference_id: "cmt_inv_live_20260827170223_1",
    sent: true, mode: "razorpay_test", origin: null, failed: false, failure_reason: null,
    event: { seq: 10, at: "2026-08-27T11:32:41.579168+00:00", kind: "payment_instrument_created", hash: "ddd0ffe79928dd01" },
    confirmation: { seq: 13, at: "2026-08-27T11:32:41.579168+00:00", kind: "message_sent", hash: "88f3111f6b434f2f" } },
  rail: [],
  outcome: { state: "active", promise_state: "open", event: null,
    created_event: { seq: 11, at: "2026-08-27T11:32:41.579168+00:00", kind: "commitment_created", hash: "5a112ba6934dc40d" } },
  timeline: [],
};

export const liveDebtor = {
  id: "deb_live_20260827170223", name: "Kumar Textiles", contact_name: "Kumar", phone: "+91••••••••01",
  email: "v•••@razorpay.com", preferred_channel: "email", language: "ta",
};

/** `GET /api/commitments/cmt_inv_live_20260827170223_1`. */
export const liveCommitmentDetail: CommitmentDetail = {
  source: "live_test", commitment: liveCommitment, invoice: liveInvoice, debtor: liveDebtor, chain: liveChain,
  audit_chain: { verified: true, events: 63 },
};

/** `GET /api/commitments/cmt_inv_003_1` (simulation ledger, fulfilled through the sandbox rail). */
export const sandboxCommitmentDetail: CommitmentDetail = {
  source: "simulation", commitment: sandboxCommitment, invoice: simInvoice,
  debtor: { id: "deb_003", name: "Annapoorna Foods", contact_name: "Vijay", phone: "+91••••••••97", email: "a•••@annapoornafoods.example.in", preferred_channel: "email", language: "en" },
  chain: {
    ...liveChain, id: "cmt_inv_003_1", state: "fulfilled", invoice_id: "inv_003", committed_amount: 15772100, amount_received: 15772100, amount_remaining: 0,
    due_on: "2026-08-25", due_at: "2026-08-25T23:59:59+05:30", created_at: "2026-08-24T13:00:00+05:30", fulfilled_at: "2026-08-25T09:00:00+05:30", confidence: 0.9,
    said: { verbatim: "Will transfer ₹157,721 by Tuesday. Rest next month.", promise_id: "ptp_inv_003_1", promise_state: "kept", at: "2026-08-24T13:00:00+05:30",
      event: { seq: 470, at: "2026-08-24T13:00:00+05:30", kind: "message_received", hash: "0c1b2a3d4e5f6071" } },
    understood: { intent: "promise", amount: 15772100, on: "2026-08-25", confidence: 0.9, flags: [], brain: "mock", partial: true,
      event: { seq: 471, at: "2026-08-24T13:00:00+05:30", kind: "commitment_proposed", hash: "445350bf781dceb5" } },
    instrument: { type: "payment_link", id: "plink_fake_0076", url: SANDBOX_URL, amount: 15772100, expires: "2026-08-25T23:59:59+05:30",
      notes: { invoice_id: "inv_003", commitment_id: "cmt_inv_003_1" }, reference_id: "cmt_inv_003_1", sent: true, mode: "sandbox", origin: null, failed: false, failure_reason: null,
      event: { seq: 473, at: "2026-08-24T13:00:00+05:30", kind: "payment_instrument_created", hash: "7e6130ebc20c85fe" },
      confirmation: { seq: 476, at: "2026-08-24T13:00:00+05:30", kind: "message_sent", hash: "2b9b72865157e1e1" } },
    rail: [{ payment_id: "pay_evt_sim_00024", razorpay_payment_id: "pay_sim_00024", razorpay_event_id: "evt_sim_00024", amount: 15772100, method: "upi", observed_at: "2026-08-25T09:00:00+05:30", matched_by: "instrument" }],
    outcome: { state: "fulfilled", promise_state: "kept",
      event: { seq: 662, at: "2026-08-25T09:00:00+05:30", kind: "commitment_fulfilled", hash: "ddd90fee82e16a48" },
      created_event: { seq: 474, at: "2026-08-24T13:00:00+05:30", kind: "commitment_created", hash: "354d42261201e611" } },
  },
  audit_chain: { verified: true, events: 3648 },
};

export const livePromise: Promise_ = {
  id: "ptp_inv_live_20260827170223_1", invoice_id: "inv_live_20260827170223", debtor_id: "deb_live_20260827170223",
  amount: 5000000, promised_on: "2026-08-28", made_at: "2026-08-27T11:32:41.579168Z", channel: "email",
  verbatim: "Cash konjam tight ah iruku. Friday 50000 kudukuren.", confidence: 0.88, state: "open", resolved_at: null,
  source: "live_test", invoice_number: "URU/2026/L170223", commitment_id: "cmt_inv_live_20260827170223_1", commitment_state: "active", commitment_received: 0,
};

export const simPromise: Promise_ = {
  id: "ptp_inv_003_1", invoice_id: "inv_003", debtor_id: "deb_003", amount: 15772100, promised_on: "2026-08-25", made_at: "2026-08-24T13:00:00+05:30",
  channel: "email", verbatim: "Will transfer ₹157,721 by Tuesday. Rest next month.", confidence: 0.9, state: "kept", resolved_at: "2026-08-25T09:00:00+05:30",
  source: "simulation", invoice_number: "URU/2026/0003", commitment_id: "cmt_inv_003_1", commitment_state: "fulfilled", commitment_received: 15772100,
};

export const liveDetail: InvoiceDetail = {
  source: "live_test",
  invoice: liveInvoice,
  debtor: liveDebtor,
  promises: [livePromise],
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
    commitments: [liveChain],
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
  recommended_action: "human review", source: "simulation", debtor_name: "Nagercoil Spices Unit 4",
};

const arm = (label: string, recovered: number, rate: number, attempts: number, perContact: number | null) => ({
  label, invoices: 120, amount_at_risk_paise: 1906630000, recovered_paise: recovered, recovery_rate: rate, discount_cost_paise: 0,
  net_recovered_paise: recovered, invoices_paid: Math.round(rate * 120), promises_made: 0, promises_kept: 0, promises_broken: 0,
  promise_kept_rate: null, escalations: 0, disputes: 0, stop_contacts: 0, contact_attempts: attempts, offers_made: 0, offers_accepted: 0,
  days_to_recovery_median: null, days_to_recovery_mean: null, recovered_per_contact_attempt_paise: perContact,
});

/** A trimmed `/api/experiment` (arm figures as served on 2026-08-28). */
export const experiment: Experiment = {
  source: "simulation", generated_by: "urudhi.sim", seed: 7, days: 14, count: 120, brain: "mock", policy: {},
  arms: {
    no_action: arm("No action", 434190000, 0.2277, 0, null),
    baseline: arm("Fixed-cadence baseline", 1229586400, 0.6449, 413, 2977206),
    urudhi: arm("Urudhi", 1302880514, 0.6833, 317, 4110033),
  },
  uplift: { urudhi_vs_baseline_paise: 73294114, urudhi_vs_baseline_points: 3.8, urudhi_vs_no_action_paise: 868690514, urudhi_vs_no_action_points: 45.6, net_urudhi_vs_baseline_paise: 73294114 },
  attribution: { window_days: 3, rule: "exact then window", arms: {} },
  timeline: { days: ["2026-08-24", "2026-08-25"], no_action: [0, 1], baseline: [0, 2], urudhi: [0, 3] },
  days_to_recovery: {},
  sensitivity: [],
  caveats: ["Persona model, not real debtors."],
};

const evalSummary = (brain: string, model: string | null, accuracy: number, recall: number) => ({
  brain, model, items: 90, intent_accuracy: accuracy, per_intent: {}, per_language: { en: { n: 30, accuracy } },
  promise_detection: { precision: 0.9, recall, tp: 27, fp: 3, fn: 3 }, amount_accuracy: { n: 30, accuracy: 0.9 }, date_accuracy: { n: 30, accuracy: 0.9 },
  spurious_amount_rate: 0, spurious_date_rate: 0, fallback_rate: 0, mean_seconds: 1, confusion: {}, failures: [],
});

export const replyEval: ReplyEval = { mock: evalSummary("mock", null, 0.5889, 0.6), claude: evalSummary("claude", "claude-sonnet-4-5", 0.8667, 0.9) };

/**
 * A `fetch` stub that routes by path (query string ignored for matching; handed to route functions as `url`).
 * Unrouted paths get 404 with a JSON detail like the API.
 */
export function mockFetch(routes: Record<string, unknown | ((init: RequestInit | undefined, url: string) => unknown)>) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const path = url.replace(/^https?:\/\/[^/]+/, "").replace(/\?.*$/, "");
    const key = Object.keys(routes).find((k) => k === path || (k.endsWith("*") && path.startsWith(k.slice(0, -1))));
    if (!key) {
      return new Response(JSON.stringify({ detail: `no route for ${path}` }), { status: 404, headers: { "Content-Type": "application/json" } });
    }
    const value = routes[key];
    const body = typeof value === "function" ? (value as (init: RequestInit | undefined, url: string) => unknown)(init, url) : value;
    if (body instanceof Response) return body;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

/** The `source` query of a request URL, as the API would read it. */
export function sourceOf(url: string): string {
  return new URL(url, "http://localhost").searchParams.get("source") ?? "all";
}

/** Filter rows by their `source` the way the API does for `?source=`. */
export function bySource<T extends { source?: string }>(rows: T[], url: string): T[] {
  const s = sourceOf(url);
  return s === "all" ? rows : rows.filter((r) => r.source === s);
}

/** `/api/summary?source=…` as the API answers it. */
export function summaryFor(url: string): Summary {
  const s = sourceOf(url);
  return s === "live_test" ? summaryLive : s === "simulation" ? summarySim : summaryAll;
}

export const notFound = (detail: string) =>
  new Response(JSON.stringify({ detail }), { status: 404, headers: { "Content-Type": "application/json" } });
