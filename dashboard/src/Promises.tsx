/** Promise ledger: every promise as recorded, linked to the commitment policy made of it. */

import { useMemo, useState } from "react";
import { Commitment, Invoice, Loaded, Promise_, api, inr, num, pct, relativeDays, useLoad, when } from "./api";
import { ConfidenceBar, EmptyState, Pill, SectionHeader, Status, StatusBadge, TableWrap, stateLabel } from "./ui";
import { ConcessionTable } from "./Invoices";

const STATES = ["open", "kept", "partially_kept", "broken", "superseded", "withdrawn", "declined"];

function Outcome({ p, linked }: { p: Promise_; linked: Commitment | undefined }) {
  if (linked) {
    const remaining = linked.amount_remaining ?? Math.max(0, linked.committed_amount - linked.amount_received);
    return (
      <>
        <span className="num">{inr(linked.amount_received)} <span className="muted">of {inr(linked.committed_amount)}</span></span>
        <span className="secondary">
          {linked.state === "fulfilled" ? `fulfilled ${when(linked.fulfilled_at)}`
            : linked.state === "missed" ? `missed · ${inr(remaining)} unpaid`
            : linked.days_late > 0 ? `${linked.days_late} d late` : relativeDays(linked.due_on)}
        </span>
      </>
    );
  }
  if (p.resolved_at) return <span className="muted small">resolved {when(p.resolved_at)}</span>;
  return <span className="muted small">no commitment · {relativeDays(p.promised_on)}</span>;
}

export function PromiseLedgerPage({ invoices, onOpenCommitment }: {
  invoices: Loaded<Invoice[]>; onOpenCommitment: (id: string, invoiceId: string) => void;
}) {
  const promises = useLoad(api.promises);
  const commitments = useLoad(api.commitments);
  const concessions = useLoad(api.concessions);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");

  const byPromise = useMemo(() => {
    const m = new Map<string, Commitment>();
    for (const c of commitments.data ?? []) if (c.promise_id && !m.has(c.promise_id)) m.set(c.promise_id, c);
    return m;
  }, [commitments.data]);
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
        (!q || (numbers[p.invoice_id] ?? "").toLowerCase().includes(q) || p.invoice_id.toLowerCase().includes(q)
          || (debtors[p.debtor_id] ?? "").toLowerCase().includes(q) || p.verbatim.toLowerCase().includes(q)))
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
          <input className="search" type="search" placeholder="Search invoice, debtor or words" value={query}
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
          {(rows) => rows.length === 0 ? <EmptyState title="No promises recorded yet" /> : shown.length === 0
            ? <EmptyState title="No promises match" /> : (
            <TableWrap>
              <table>
                <thead>
                  <tr>
                    <th>Status</th>
                    <th className="num">Amount</th>
                    <th>Promised for</th>
                    <th>Made</th>
                    <th>Confidence</th>
                    <th>Debtor words</th>
                    <th>Linked commitment</th>
                    <th>Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((p) => {
                    const linked = byPromise.get(p.id);
                    return (
                      <tr key={p.id}>
                        <td><StatusBadge state={p.state} /></td>
                        <td className="num money">{inr(p.amount)}</td>
                        <td className="nowrap">{p.promised_on}<span className="secondary">{relativeDays(p.promised_on)}</span></td>
                        <td className="nowrap">
                          {when(p.made_at)}
                          <span className="secondary">{numbers[p.invoice_id] ?? p.invoice_id}{debtors[p.debtor_id] ? ` · ${debtors[p.debtor_id]}` : ""} · {p.channel}</span>
                        </td>
                        <td><ConfidenceBar value={p.confidence} /></td>
                        <td className="words">“{p.verbatim}”</td>
                        <td>
                          {linked ? (
                            <button type="button" className="btn sm" onClick={() => onOpenCommitment(linked.id, linked.invoice_id)}
                              aria-label={`Open commitment ${linked.id}`}>
                              <StatusBadge state={linked.state} /> <span className="mono">{linked.id}</span>
                            </button>
                          ) : commitments.data ? <span className="muted small">none</span> : <span className="muted small">…</span>}
                        </td>
                        <td><Outcome p={p} linked={linked} /></td>
                      </tr>
                    );
                  })}
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
