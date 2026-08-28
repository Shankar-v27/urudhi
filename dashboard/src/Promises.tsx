/** Promise ledger: every promise as recorded, linked to the commitment policy made of it, across both ledgers. */

import { useMemo, useState } from "react";
import { Invoice, Loaded, Promise_, api, inr, num, pct, relativeDays, useLoad, when } from "./api";
import { useSource } from "./source";
import { ConfidenceBar, EmptyState, Pill, SectionHeader, SourceBadge, Status, StatusBadge, TableWrap, stateLabel } from "./ui";
import { ConcessionTable } from "./Invoices";

/** Every promise state the ledger can hold, each with its own pill. */
const STATES = ["open", "kept", "partially_kept", "broken", "superseded", "withdrawn", "declined"];

/** What the rails reported against the linked commitment, next to what was promised. */
function PaymentOutcome({ p }: { p: Promise_ }) {
  if (!p.commitment_id) {
    if (p.resolved_at) return <span className="muted small">no commitment · resolved {when(p.resolved_at)}</span>;
    return <span className="muted small">no commitment · {relativeDays(p.promised_on)}</span>;
  }
  const received = p.commitment_received ?? 0;
  const state = p.commitment_state;
  const note = state === "fulfilled" ? "paid in full"
    : state === "missed" ? `missed · ${inr(Math.max(0, p.amount - received))} unpaid`
    : state === "partially_fulfilled" ? `${inr(Math.max(0, p.amount - received))} still open`
    : state === "active" && received === 0 ? "payment pending"
    : state === "superseded" ? "replaced by a newer arrangement"
    : state === "cancelled" ? "cancelled"
    : relativeDays(p.promised_on);
  return (
    <>
      <span className="num">{inr(received)} <span className="muted">of {inr(p.amount)}</span></span>
      <span className={`secondary ${state === "missed" ? "neg" : ""}`}>{note}</span>
    </>
  );
}

export function PromiseLedgerPage({ invoices, onOpenCommitment }: {
  invoices: Loaded<Invoice[]>; onOpenCommitment: (id: string, invoiceId: string) => void;
}) {
  const { source } = useSource();
  const promises = useLoad(() => api.promises(source), [source]);
  const commitments = useLoad(() => api.commitments(source), [source]);
  const concessions = useLoad(() => api.concessions(source), [source]);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");

  const numbers = useMemo(() => {
    const m: Record<string, string> = {};
    for (const i of invoices.data ?? []) m[i.id] = i.number;
    return m;
  }, [invoices.data]);
  const debtors = useMemo(() => {
    const m: Record<string, string> = {};
    for (const i of invoices.data ?? []) if (i.debtor_name) m[i.debtor_id] = i.debtor_name;
    return m;
  }, [invoices.data]);

  const all = promises.data ?? [];
  const kept = all.filter((p) => p.state === "kept").length;
  const broken = all.filter((p) => p.state === "broken").length;
  const open = all.filter((p) => p.state === "open");
  const resolved = kept + broken;
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return [...all]
      .filter((p) => (filter === "all" || p.state === filter) &&
        (!q || (p.invoice_number ?? numbers[p.invoice_id] ?? "").toLowerCase().includes(q) || p.invoice_id.toLowerCase().includes(q)
          || (debtors[p.debtor_id] ?? "").toLowerCase().includes(q) || p.verbatim.toLowerCase().includes(q)
          || (p.commitment_id ?? "").toLowerCase().includes(q)))
      .sort((a, b) => b.made_at.localeCompare(a.made_at));
  }, [all, filter, query, numbers, debtors]);

  return (
    <div className="stack">
      <div>
        <div className="page-title">
          <h1>Promise Ledger</h1>
          {promises.data && all.length > 0 && (
            <>
              <Pill tone="outline">{num(all.length)} promises</Pill>
              <Pill tone="success">kept rate {pct(resolved ? kept / resolved : null, 0)}</Pill>
              <Pill tone="info">{num(open.length)} open · {inr(open.reduce((s, p) => s + p.amount, 0))}</Pill>
            </>
          )}
        </div>
        <p className="page-desc">What each debtor said, in their words, and what became of it. A promise is evidence; only the linked commitment moves money.</p>
        <div className="toolbar">
          <input className="search" type="search" placeholder="Search invoice, debtor, commitment or words" value={query}
            onChange={(e) => setQuery(e.target.value)} aria-label="Search promises" />
          <label>
            Status
            <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by status">
              <option value="all">All ({all.length})</option>
              {STATES.map((s) => <option key={s} value={s}>{stateLabel(s)} ({all.filter((p) => p.state === s).length})</option>)}
            </select>
          </label>
          <span className="spacer" />
          <button type="button" className="btn sm" onClick={() => { promises.reload(); commitments.reload(); }}>Refresh</button>
        </div>
        <Status load={promises} rows={8}>
          {(rows) => rows.length === 0 ? <EmptyState title="No promises in this data source" /> : shown.length === 0
            ? <EmptyState title="No promises match" /> : (
            <TableWrap>
              <table>
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Invoice</th>
                    <th className="num">Amount</th>
                    <th>Promised for</th>
                    <th>Made</th>
                    <th>Confidence</th>
                    <th>Debtor words</th>
                    <th>Linked commitment</th>
                    <th>Payment outcome</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((p) => (
                    <tr key={`${p.source ?? ""}:${p.id}`}>
                      <td><StatusBadge state={p.state} /></td>
                      <td>
                        <b>{p.invoice_number ?? numbers[p.invoice_id] ?? p.invoice_id}</b>
                        <span className="secondary">{debtors[p.debtor_id] ?? p.debtor_id}</span>
                      </td>
                      <td className="num money">{inr(p.amount)}</td>
                      <td className="nowrap">{p.promised_on}<span className="secondary">{relativeDays(p.promised_on)}</span></td>
                      <td className="nowrap">{when(p.made_at)}<span className="secondary">{p.channel}</span></td>
                      <td><ConfidenceBar value={p.confidence} /></td>
                      <td className="words">“{p.verbatim}”</td>
                      <td>
                        {p.commitment_id ? (
                          <button type="button" className="btn sm" onClick={() => onOpenCommitment(p.commitment_id!, p.invoice_id)}
                            aria-label={`Open commitment ${p.commitment_id}`}>
                            {p.commitment_state && <StatusBadge state={p.commitment_state} />} <span className="mono">{p.commitment_id}</span>
                          </button>
                        ) : <span className="muted small">none</span>}
                      </td>
                      <td><PaymentOutcome p={p} /></td>
                      <td><SourceBadge source={p.source} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Status>
      </div>

      <div>
        <SectionHeader title="Concessions" description="Discounts and installment plans offered under delegated authority; a settled concession is what produced any waived amount." />
        <Status load={concessions} rows={4}>
          {(rows) => <ConcessionTable concessions={rows} commitments={commitments.data ?? []} showInvoice invoiceNumbers={numbers} />}
        </Status>
      </div>
    </div>
  );
}
