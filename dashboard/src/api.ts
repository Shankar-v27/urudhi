/** Typed client for Urudhi's API. Everything under /api and /inbound needs a bearer token. */

import { useCallback, useEffect, useState } from "react";

export const TOKEN_KEY = "urudhi.token";
export const OPERATOR_KEY = "urudhi.operator";
export const SOURCE_KEY = "urudhi.source";

// -- data sources -----------------------------------------------------------
// The API serves two ledgers as one product view. `all` merges them; every row says which ledger it came from.

/** What the operator asked to see. */
export type DataSource = "all" | "live_test" | "simulation";
/** Which ledger a row was read from. */
export type RowSource = "live_test" | "simulation";

export const DATA_SOURCES: DataSource[] = ["all", "live_test", "simulation"];
export const SOURCE_LABEL: Record<DataSource, string> = { all: "All", live_test: "Live Test", simulation: "Simulation" };

export function isDataSource(value: unknown): value is DataSource {
  return typeof value === "string" && (DATA_SOURCES as string[]).includes(value);
}

function withSource(path: string, source: DataSource): string {
  return `${path}${path.includes("?") ? "&" : "?"}source=${source}`;
}

export function storageGet(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

export function storageSet(key: string, value: string): void {
  try {
    if (value) window.localStorage.setItem(key, value);
    else window.localStorage.removeItem(key);
  } catch {
    /* storage unavailable (private mode, sandbox) — token lives for this page only */
  }
}

// -- shapes ---------------------------------------------------------------

export interface Invoice {
  id: string;
  source?: RowSource;
  debtor_id: string;
  number: string;
  amount: number;
  amount_paid: number;
  amount_waived: number;
  balance: number;
  issued_on: string;
  due_on: string;
  state: string;
  debtor_name?: string | null;
  human_released_at?: string | null;
}

export interface Promise_ {
  id: string;
  invoice_id: string;
  debtor_id: string;
  amount: number;
  promised_on: string;
  made_at: string;
  channel: string;
  verbatim: string;
  confidence: number;
  state: string;
  resolved_at: string | null;
  source?: RowSource;
  invoice_number?: string | null;
  /** The commitment policy made of this promise, when one exists. */
  commitment_id?: string | null;
  commitment_state?: CommitmentState | null;
  commitment_received?: number | null;
}

export interface Installment {
  due_on: string;
  amount: number;
}

export interface Concession {
  id: string;
  source?: RowSource;
  invoice_id: string;
  debtor_id: string;
  type: string;
  state: string;
  discount_bps: number;
  balance_at_offer: number;
  settlement_amount: number;
  installments: Installment[];
  pay_by: string;
  offered_at: string;
  accepted_at: string | null;
  resolved_at: string | null;
  payment_link_url: string | null;
  rationale: string;
}

export interface Payment {
  id: string;
  invoice_id: string;
  amount: number;
  method: string;
  razorpay_payment_id: string;
  razorpay_event_id: string;
  observed_at: string;
  commitment_id?: string | null;
  matched_by?: MatchedBy;
}

// -- commitments ----------------------------------------------------------
// Promise = what the debtor said. Commitment = what deterministic policy accepted
// (exact amount, exact deadline, a Razorpay Payment Link tagged with the id).
// Payment = what the rails verified. The shapes below keep those three apart.

/** What opened the commitment (a promise, a concession, an installment of one, or a human arrangement). */
export type CommitmentOrigin = "promise" | "concession" | "installment" | "human";
/** @deprecated name kept for older imports; the row field is now `origin`, `source` is the ledger. */
export type CommitmentSource = CommitmentOrigin;
export type CommitmentState =
  | "active" | "partially_fulfilled" | "fulfilled" | "missed" | "cancelled" | "superseded";
export type MatchedBy = "instrument" | "invoice" | "instrument-late" | null;
/**
 * Who issued the payment instrument. `razorpay_test` = a real Razorpay test-mode link whose URL
 * must be used verbatim; `sandbox` = the offline fake rail (never a checkout, never openable);
 * null = nothing issued. Always decide by this field, never by inspecting the URL.
 */
export type InstrumentMode = "razorpay_test" | "sandbox" | null;

export interface Commitment {
  id: string;
  invoice_id: string;
  /** Present on /api/commitments; absent on the invoice detail rows. */
  invoice_number?: string | null;
  debtor_id: string;
  promise_id: string | null;
  concession_id: string | null;
  installment_index: number | null;
  /** Which ledger this row came from. */
  source: RowSource;
  /** What opened it; informational, may be null on older rows. */
  origin?: CommitmentOrigin | null;
  /** Informational only: where the rail said the instrument came from. */
  rail_origin?: string | null;
  /** Which rail issued the instrument (same vocabulary as `instrument_mode`). */
  rail?: InstrumentMode;
  debtor_name?: string | null;
  committed_amount: number;
  currency: string;
  due_on: string;
  due_at: string;
  state: CommitmentState;
  instrument_type: "payment_link" | null;
  instrument_id: string | null;
  /** The rail's own customer URL, verbatim. Only ever opened when `instrument_mode === "razorpay_test"`. */
  payment_url: string | null;
  /** Explicit and persisted by the API; absent only on builds that predate it (then never a link). */
  instrument_mode?: InstrumentMode;
  /** True when the rail refused to issue an instrument (a `rail_failed` audit event exists). */
  instrument_failed?: boolean | null;
  /** The rail's error text when `instrument_failed`; empty otherwise. */
  instrument_failure?: string | null;
  instrument_sent: boolean;
  reminder_sent: boolean;
  created_at: string;
  accepted_at: string | null;
  fulfilled_at: string | null;
  missed_at: string | null;
  resolved_at: string | null;
  amount_received: number;
  /** Present on /api/commitments; derive as committed − received elsewhere. */
  amount_remaining?: number;
  days_late: number;
  confidence: number;
  evidence: string;
  rationale: string;
  cancel_reason: string;
}

/** A pointer into the hash-chained audit log. */
export interface EventRef {
  seq: number;
  at: string;
  kind: string;
  hash: string;
}

export interface PolicyCheck {
  allowed: boolean;
  gate: string;
  reason: string;
}

export interface Credibility {
  commitments: number;
  active: number;
  fulfilled: number;
  fulfilled_on_time: number;
  partially_fulfilled: number;
  missed: number;
  cancelled: number;
  fulfillment_rate: number | null;
  average_delay_days: number | null;
  average_committed: number | null;
  amount_committed: number;
  amount_received: number;
  last_outcome: string | null;
  credibility: number;
  reasons: string[];
  summary?: string;
}

/**
 * One row of what the rails reported for a commitment. Normally a verified payment;
 * when no payment row was matched the backend falls back to the fulfilment audit event.
 */
export interface RailRow {
  payment_id?: string;
  razorpay_payment_id?: string;
  razorpay_event_id?: string;
  amount?: number;
  method?: string;
  observed_at?: string;
  matched_by: MatchedBy;
  event?: EventRef | null;
  outcome?: string | null;
  amount_received?: number | null;
}

/** The provenance chain: said → understood → allowed → instrument → rail → outcome. */
export interface CommitmentChain {
  id: string;
  state: CommitmentState;
  /** What opened the commitment (promise / concession / installment / human). */
  source: CommitmentOrigin;
  invoice_id: string;
  installment_index: number | null;
  committed_amount: number;
  amount_received: number;
  amount_remaining: number;
  due_on: string;
  due_at: string;
  created_at: string;
  fulfilled_at: string | null;
  missed_at: string | null;
  days_late: number;
  confidence: number;
  cancel_reason: string | null;
  said: {
    verbatim: string; promise_id: string | null; promise_state: string | null; at: string;
    event: EventRef | null;
  };
  understood: {
    intent: string | null; amount: number | null; on: string | null; confidence: number;
    flags: string[]; brain: string | null; partial: boolean; event: EventRef | null;
  };
  policy: { allowed: true; reason: string; checks: PolicyCheck[]; event: EventRef | null };
  instrument: {
    type: string | null; id: string | null; url: string | null; amount: number; expires: string;
    /** Razorpay `notes` object (invoice_id, commitment_id) as issued; a string on very old rows. */
    notes: Record<string, string> | string | null;
    reference_id: string | null; sent: boolean;
    mode?: InstrumentMode; origin?: string | null; failed?: boolean; failure_reason?: string | null;
    event: EventRef | null; confirmation: EventRef | null;
  };
  rail: RailRow[];
  outcome: {
    state: CommitmentState; promise_state: string | null;
    event: EventRef | null; created_event: EventRef | null;
  };
  timeline: (EventRef & { detail: string })[];
}

/** A promise that was recorded as evidence but refused as a commitment. */
export interface BlockedCommitment {
  at: string;
  amount: number | null;
  due_on: string | null;
  promise_id?: string | null;
  reason: string | null;
  checks: PolicyCheck[];
  event?: EventRef | null;
}

export interface InvoiceCommitments {
  invoice_id: string;
  credibility: Credibility;
  commitments: CommitmentChain[];
  blocked: BlockedCommitment[];
}

export interface Debtor {
  id: string; name: string; contact_name: string; phone: string; email: string;
  preferred_channel: string; language: string;
}

export interface ChainStatus { verified: boolean; events?: number; error?: string }

/** `GET /api/commitments/{id}` — one commitment with its invoice, debtor and full provenance chain, from either ledger. */
export interface CommitmentDetail {
  source: RowSource;
  commitment: Commitment;
  invoice: Invoice;
  debtor: Debtor;
  chain: CommitmentChain;
  audit_chain: ChainStatus;
}

export interface CommitmentSummary {
  created: number;
  active: number;
  fulfilled: number;
  fulfilled_on_time: number;
  partially_fulfilled: number;
  missed: number;
  cancelled: number;
  fulfillment_rate: number | null;
  amount_committed_paise: number;
  amount_received_paise: number;
  conversion: number | null;
  average_delay_days: number | null;
  recovered_per_commitment_paise: number | null;
  recovered_per_attempt_paise: number | null;
  /** Total messages and the subset that were nudges (asks for money); absent on older builds. */
  messages_total?: number;
  nudges?: number;
  exact_instrument_matched_paise: number;
  /** How many instruments each rail issued. */
  instruments_razorpay_test?: number;
  instruments_sandbox?: number;
}

/** What produced the numbers in a summary: which brain, which rail, how payments were observed. */
export interface SummaryContext {
  source: DataSource;
  brain: string;
  rail: string;
  payments_observed: number;
  audit_events: number;
  chain_verified: boolean;
  /** Human-readable provenance, e.g. "Razorpay Test Mode · observed via signed webhook". */
  provenance: string;
}

export interface AuditEvent {
  seq: number;
  at: string;
  actor: string;
  kind: string;
  invoice_id: string | null;
  payload: Record<string, unknown>;
  hash: string;
}

/** One ledger's slice of the summary (also the whole summary when a single source is selected). */
export interface SourceSummary {
  source: RowSource;
  invoices: number;
  outstanding_paise: number;
  recovered_paise: number;
  waived_paise: number;
  by_state: Record<string, number>;
  messages_sent: number;
  by_intervention: Record<string, number>;
  commitments: CommitmentSummary;
  context: SummaryContext;
}

export interface Summary {
  source?: DataSource;
  sources?: RowSource[];
  /** Present when `source=all`: the per-ledger summaries the totals were merged from. */
  by_source?: SourceSummary[];
  invoices: number;
  outstanding_paise: number;
  recovered_paise: number;
  waived_paise: number;
  by_state: Record<string, number>;
  messages_sent: number;
  by_intervention: Record<string, number>;
  /** The running process's brain / transport / rails — not necessarily what produced a simulation ledger. */
  brain: string;
  transport: string;
  rails?: string;
  commitments: CommitmentSummary;
  context?: SummaryContext;
}

export interface TimelinePoint {
  day: string;
  recovered_cumulative: number;
  recovered: number;
  messages: number;
  source?: RowSource;
}

export interface Timeline {
  series: TimelinePoint[];
  by_source?: Partial<Record<RowSource, TimelinePoint[]>>;
}

/** One ledger as reported by /health. `brain` is the brain that produced a simulation ledger (e.g. "mock"), else null. */
export interface Ledger {
  source: RowSource;
  db: string;
  invoices: number;
  audit_chain: ChainStatus;
  brain: string | null;
}

export interface Health {
  status: string;
  version: string;
  /** The brain the running process uses for live traffic. */
  brain: string;
  transport: string;
  /** "razorpay_test" | "sandbox" (older builds: "razorpay-test" | "fake"). */
  rails: string;
  policy_timezone: string;
  invoices: number;
  audit_chain: ChainStatus;
  sources?: RowSource[];
  ledgers?: Ledger[];
  counters: Record<string, number>;
}

export interface Gate {
  ok: boolean;
  gate: string;
  reason: string;
}

export interface Offer {
  type: string;
  invoice_id: string;
  discount_bps: number;
  installment_count: number;
  pay_by: string;
}

export interface LatestDecision {
  at: string;
  proposed: string | null;
  final: string | null;
  modified: boolean | null;
  rationale: string[];
  confidence: number | null;
  policy_reasons: string[];
  gates: Gate[];
  offer: Offer | null;
}

export interface Explain {
  invoice_id: string;
  priority: { score: number; components: Record<string, number>; reasons: string[] };
  credibility: Credibility;
  latest_decision: LatestDecision | null;
  decision_history: { at: string; proposed: string | null; final: string | null; modified: boolean | null }[];
  promises: {
    id: string; amount: number; promised_on: string; made_at: string; state: string;
    confidence: number; verbatim: string;
  }[];
  concessions: {
    id: string; type: string; state: string; discount_bps: number; settlement_amount: number;
    balance_at_offer: number; pay_by: string; installments: Installment[];
    payment_link_url: string | null; rationale: string;
  }[];
  commitments: CommitmentChain[];
  blocked_commitments: BlockedCommitment[];
  payments: {
    id: string; amount: number; method: string; observed_at: string; event_id: string;
    commitment_id: string | null; matched_by: MatchedBy;
  }[];
  amount_waived: number;
  escalation: { at: string; reason: string | null } | null;
  dispute: { at: string; reason: string | null; verbatim: string | null } | null;
  brain_failures: number;
  interventions: {
    at: string; kind: string | null; responding: boolean; payment_url: string | null; brain: string | null;
    commitment_id: string | null;
  }[];
}

export interface InvoiceDetail {
  source?: RowSource;
  invoice: Invoice;
  debtor: Debtor;
  promises: Promise_[];
  commitments: Commitment[];
  concessions: Concession[];
  payments: Payment[];
  events: AuditEvent[];
  explain: Explain;
}

export interface Audit {
  chain: ChainStatus;
  chains?: Partial<Record<RowSource, ChainStatus>>;
  total: number;
  events: AuditEvent[];
}

export type HumanAction = "acknowledge" | "note" | "arrange" | "release" | "close";

export interface HumanRequest {
  action: HumanAction;
  operator: string;
  notes: string;
  /** "arrange" only: paise, integer > 0. */
  amount?: number;
  /** "arrange" only: YYYY-MM-DD. */
  due_on?: string;
}

export interface HumanResult {
  action: string;
  operator: string;
  notes: string;
  from_state: string;
  to_state: string;
  /** Set when an "arrange" action opened a commitment. */
  commitment_id?: string | null;
}

export interface Escalation {
  invoice_id: string;
  number: string;
  source?: RowSource;
  debtor_name?: string | null;
  state: string;
  balance: number;
  amount_paid: number;
  since: string | null;
  reason: string | null;
  verbatim: string | null;
  acknowledged: boolean;
  human_actions: (HumanResult & { at: string })[];
  last_commitment: {
    id: string; committed_amount: number; amount_received: number; due_on: string;
    state: CommitmentState; evidence: string;
  } | null;
  commitments_missed: number;
  commitments_fulfilled: number;
  credibility: number;
  recommended_action: string;
}

export interface ArmMetrics {
  label: string;
  invoices: number;
  amount_at_risk_paise: number;
  recovered_paise: number;
  recovery_rate: number;
  discount_cost_paise: number;
  net_recovered_paise: number;
  invoices_paid: number;
  promises_made: number;
  promises_kept: number;
  promises_broken: number;
  promise_kept_rate: number | null;
  escalations: number;
  disputes: number;
  stop_contacts: number;
  contact_attempts: number;
  offers_made: number;
  offers_accepted: number;
  days_to_recovery_median: number | null;
  days_to_recovery_mean: number | null;
  /** Absent on reports produced before the commitment engine existed. */
  recovered_per_contact_attempt_paise?: number | null;
  commitments?: ArmCommitments;
}

export interface ArmCommitments {
  created: number;
  by_source: Record<string, number>;
  accepted: number;
  fulfilled: number;
  fulfilled_on_time: number;
  partially_fulfilled: number;
  missed: number;
  cancelled: number;
  active_at_end: number;
  fulfillment_rate: number | null;
  amount_committed_paise: number;
  amount_fulfilled_paise: number;
  commitment_to_payment_conversion: number | null;
  median_days_commitment_to_payment: number | null;
  average_delay_days: number | null;
  recovered_per_commitment_paise: number | null;
  recovered_per_contact_attempt_paise: number | null;
  instruments_issued: number;
  exact_matched_payments: number;
  exact_matched_paise: number;
}

export type ArmName = "no_action" | "baseline" | "urudhi";

export interface AttributionBucket {
  payments: number;
  paise: number;
}

export type AttributionMethod = "exact" | "window" | "unattributed";

export interface ArmAttribution {
  by_intervention: Record<string, AttributionBucket>;
  unattributed: AttributionBucket;
  /** Absent on reports produced before the commitment engine existed. */
  by_method?: Record<AttributionMethod, AttributionBucket>;
}

export interface SensitivityRow {
  parameter: string;
  value: number;
  recovered_paise: number;
  recovery_rate: number;
  messages_sent: number;
  escalations: number;
  discount_cost_paise: number;
  stop_contacts: number;
}

export interface Experiment {
  /** The experiment is always a simulation artefact. */
  source?: "simulation";
  generated_by: string;
  seed: number;
  days: number;
  count: number;
  brain: string;
  policy: Record<string, unknown>;
  arms: Record<ArmName, ArmMetrics>;
  uplift: {
    urudhi_vs_baseline_paise: number;
    urudhi_vs_baseline_points: number;
    urudhi_vs_no_action_paise: number;
    urudhi_vs_no_action_points: number;
    net_urudhi_vs_baseline_paise: number;
  };
  attribution: {
    window_days: number;
    rule: string;
    arms: Partial<Record<ArmName, ArmAttribution>>;
  };
  timeline: { days: string[] } & Record<ArmName, number[]>;
  days_to_recovery: Record<string, {
    median: number | null; mean: number | null; histogram: Record<string, number>;
  }>;
  sensitivity: SensitivityRow[];
  caveats: string[];
}

export interface EvalFailure {
  id: string;
  text: string;
  language: string;
  expected_intent: string;
  predicted_intent: string;
  intent_ok: boolean;
  expected_amount: number | null;
  predicted_amount: number | null;
  amount_ok: boolean | null;
  expected_on: string | null;
  predicted_on: string | null;
  date_ok: boolean | null;
  spurious_amount: boolean;
  spurious_date: boolean;
  confidence: number;
  fallback: boolean;
  flags: string[];
  seconds: number;
}

export interface EvalSummary {
  brain: string;
  model: string | null;
  items: number;
  intent_accuracy: number | null;
  per_intent: Record<string, { n: number; accuracy: number }>;
  per_language: Record<string, { n: number; accuracy: number }>;
  promise_detection: {
    precision: number | null; recall: number | null; tp: number; fp: number; fn: number;
  };
  amount_accuracy: { n: number; accuracy: number | null };
  date_accuracy: { n: number; accuracy: number | null };
  spurious_amount_rate: number | null;
  spurious_date_rate: number | null;
  fallback_rate: number | null;
  mean_seconds: number | null;
  confusion: Record<string, Record<string, number>>;
  failures: EvalFailure[];
}

export type ReplyEval = Partial<Record<"mock" | "claude", EvalSummary>>;

// -- transport ------------------------------------------------------------

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly path: string;

  constructor(status: number, detail: string, path: string) {
    super(`${path}: ${status} ${detail}`);
    this.status = status;
    this.detail = detail;
    this.path = path;
  }

  get unauthorized(): boolean {
    return this.status === 401;
  }

  get notFound(): boolean {
    return this.status === 404;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = storageGet(TOKEN_KEY);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let detail = response.statusText || "request failed";
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "detail" in body) {
        const d = (body as { detail: unknown }).detail;
        detail = typeof d === "string" ? d : JSON.stringify(d);
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail, path);
  }
  return response.json() as Promise<T>;
}

const get = <T,>(path: string) => request<T>(path);
const post = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

/** Every list endpoint takes the selected data source; detail endpoints look across both ledgers. */
export const api = {
  health: () => get<Health>("/health"),
  summary: (source: DataSource = "all") => get<Summary>(withSource("/api/summary", source)),
  timeline: (source: DataSource = "all") => get<Timeline>(withSource("/api/timeline", source)),
  invoices: (source: DataSource = "all") => get<Invoice[]>(withSource("/api/invoices", source)),
  invoice: (id: string) => get<InvoiceDetail>(`/api/invoices/${encodeURIComponent(id)}`),
  explain: (id: string) => get<Explain>(`/api/invoices/${encodeURIComponent(id)}/explain`),
  human: (id: string, body: HumanRequest) =>
    post<HumanResult>(`/api/invoices/${encodeURIComponent(id)}/human`, body),
  escalations: (source: DataSource = "all") => get<Escalation[]>(withSource("/api/escalations", source)),
  promises: (source: DataSource = "all") => get<Promise_[]>(withSource("/api/promises", source)),
  commitments: (source: DataSource = "all") => get<Commitment[]>(withSource("/api/commitments", source)),
  /** One commitment from whichever ledger holds it; 404 "No matching commitment in current data source" otherwise. */
  commitment: (id: string) => get<CommitmentDetail>(`/api/commitments/${encodeURIComponent(id)}`),
  invoiceCommitments: (id: string) =>
    get<InvoiceCommitments>(`/api/invoices/${encodeURIComponent(id)}/commitments`),
  concessions: (source: DataSource = "all") => get<Concession[]>(withSource("/api/concessions", source)),
  audit: (source: DataSource = "all") => get<Audit>(withSource("/api/audit?limit=500", source)),
  experiment: () => get<Experiment>("/api/experiment"),
  replyEval: () => get<ReplyEval>("/api/reply-eval"),
};

// -- loading hook ---------------------------------------------------------

export interface Loaded<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

/** Run a loader on mount (and whenever `deps` change); exposes data / error / reload. */
export function useLoad<T>(loader: () => Promise<T>, deps: unknown[] = []): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    loader()
      .then((value) => {
        if (!live) return;
        setData(value);
        setError(null);
      })
      .catch((failure: unknown) => {
        if (!live) return;
        setError(failure instanceof Error ? failure : new Error(String(failure)));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
    // The loader identity is deliberately not a dependency; callers pass `deps`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

// -- formatting -----------------------------------------------------------

export function inr(paise: number): string {
  return "₹" + (paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 });
}

/** Compact rupees for chart axes: ₹1.2L, ₹3.4Cr, ₹850k. */
export function inrShort(paise: number): string {
  const sign = paise < 0 ? "−" : "";
  const rupees = Math.abs(paise) / 100;
  if (rupees >= 1e7) return `${sign}₹${(rupees / 1e7).toFixed(2)}Cr`;
  if (rupees >= 1e5) return `${sign}₹${(rupees / 1e5).toFixed(1)}L`;
  if (rupees >= 1e3) return `${sign}₹${(rupees / 1e3).toFixed(0)}k`;
  return `${sign}₹${rupees.toFixed(0)}`;
}

/** Signed rupees for uplift callouts. */
export function inrSigned(paise: number): string {
  return (paise > 0 ? "+" : paise < 0 ? "−" : "") + inr(Math.abs(paise));
}

/** A 0–1 fraction as a percentage; null-safe. */
export function pct(fraction: number | null | undefined, digits = 1): string {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return "—";
  return (fraction * 100).toFixed(digits) + "%";
}

export function num(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString("en-IN");
}

/** A timestamp rendered in Indian Standard Time, the policy timezone, e.g. "28 Aug 2026, 11:59 pm IST". */
export function whenIST(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", hour12: true,
  }) + " IST";
}

/** Whole days from today (local calendar) to a YYYY-MM-DD date; negative when the date has passed. */
export function daysUntil(ymd: string | null | undefined, today: Date = new Date()): number | null {
  if (!ymd || !/^\d{4}-\d{2}-\d{2}$/.test(ymd)) return null;
  const [y, m, d] = ymd.split("-").map(Number);
  const target = Date.UTC(y, m - 1, d);
  const base = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.round((target - base) / 86_400_000);
}

/** "in 3 days", "today", "5 days ago" — for secondary date text next to a due date. */
export function relativeDays(ymd: string | null | undefined, today?: Date): string {
  const n = daysUntil(ymd, today);
  if (n === null) return "";
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n === -1) return "yesterday";
  return n > 0 ? `in ${n} days` : `${-n} days ago`;
}
