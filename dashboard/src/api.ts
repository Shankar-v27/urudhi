/** Typed client for Urudhi's read-only API. */

export interface Invoice {
  id: string;
  debtor_id: string;
  number: string;
  amount: number;
  amount_paid: number;
  balance: number;
  due_on: string;
  state: string;
}

export interface Promise_ {
  id: string;
  invoice_id: string;
  amount: number;
  promised_on: string;
  verbatim: string;
  confidence: number;
  state: string;
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

export interface Summary {
  invoices: number;
  outstanding_paise: number;
  recovered_paise: number;
  by_state: Record<string, number>;
}

export interface InvoiceDetail {
  invoice: Invoice;
  debtor: { name: string; contact_name: string; preferred_channel: string };
  promises: Promise_[];
  payments: { id: string; amount: number; method: string; observed_at: string }[];
  events: AuditEvent[];
}

export interface Audit {
  chain: { verified: boolean; events?: number; error?: string };
  total: number;
  events: AuditEvent[];
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

export const api = {
  summary: () => get<Summary>("/api/summary"),
  invoices: () => get<Invoice[]>("/api/invoices"),
  invoice: (id: string) => get<InvoiceDetail>(`/api/invoices/${id}`),
  promises: () => get<Promise_[]>("/api/promises"),
  audit: () => get<Audit>("/api/audit?limit=500"),
};

export function inr(paise: number): string {
  return "₹" + (paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 });
}
