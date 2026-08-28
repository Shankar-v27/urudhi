/** Commitments: the hero table of policy-accepted promises turned into executable payment commitments. */

import { useMemo, useState } from "react";
import { Commitment, api, inr, num, pct, relativeDays, useLoad } from "./api";
import { EmptyState, Pill, Status, StatusBadge, TableWrap, stateLabel } from "./ui";
import { InstrumentAction, factsFromCommitment } from "./components/InstrumentAction";

const STATES = ["active", "partially_fulfilled", "fulfilled", "missed", "cancelled", "superseded"];

export function CommitmentTable({ rows, onOpen, now }: {
  rows: Commitment[]; onOpen: (id: string, invoiceId: string) => void; now?: number;
}) {
  if (rows.length === 0) return <EmptyState title="No commitments match" hint="Try another state or search term." />;
  const sorted = [...rows].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return (
    <TableWrap>
      <table>
        <thead>
          <tr>
            <th>Invoice</th>
            <th>Source</th>
            <th>Status</th>
            <th className="num">Committed</th>
            <th className="num">Received</th>
            <th className="num">Remaining</th>
            <th>Due</th>
            <th>Instrument</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((c) => {
            const remaining = c.amount_remaining ?? Math.max(0, c.committed_amount - c.amount_received);
            return (
              <tr key={c.id} className="clickable" onClick={() => onOpen(c.id, c.invoice_id)}>
                <td><b>{c.invoice_number ?? c.invoice_id}</b><span className="secondary mono">{c.id}</span></td>
                <td>{c.source}{c.installment_index !== null && <span className="secondary">installment #{c.installment_index}</span>}</td>
                <td><StatusBadge state={c.state} /></td>
                <td className="num money">{inr(c.committed_amount)}</td>
                <td className="num">{inr(c.amount_received)}</td>
                <td className="num">{remaining > 0 ? inr(remaining) : <span className="muted">—</span>}</td>
                <td className="nowrap">
                  {c.due_on}
                  <span className={`secondary ${c.days_late > 0 ? "neg" : ""}`}>
                    {c.days_late > 0 ? `${c.days_late} d late` : relativeDays(c.due_on)}
                  </span>
                </td>
                <td onClick={(e) => e.stopPropagation()}><InstrumentAction facts={factsFromCommitment(c)} now={now} compact /></td>
                <td className="words" title={c.evidence}>{c.evidence ? `“${c.evidence}”` : <span className="muted">—</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableWrap>
  );
}

export function CommitmentsPage({ onOpen }: { onOpen: (id: string, invoiceId: string) => void }) {
  const commitments = useLoad(api.commitments);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const all = commitments.data ?? [];
  // Superseded rows were replaced by a newer arrangement; they are history, not outcomes.
  const counted = all.filter((c) => c.state !== "superseded");
  const fulfilled = counted.filter((c) => c.state === "fulfilled").length;
  const missed = counted.filter((c) => c.state === "missed").length;
  const resolved = fulfilled + missed;
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return all.filter((c) =>
      (filter === "all" || c.state === filter) &&
      (!q || (c.invoice_number ?? "").toLowerCase().includes(q) || c.invoice_id.toLowerCase().includes(q)
        || c.id.toLowerCase().includes(q) || c.evidence.toLowerCase().includes(q)));
  }, [all, filter, query]);

  return (
    <>
      <div className="page-title">
        <h1>Commitments</h1>
        {commitments.data && all.length > 0 && (
          <>
            <Pill tone="outline">{num(counted.length)} created</Pill>
            <Pill tone="success">{num(fulfilled)} fulfilled</Pill>
            <Pill tone={missed > 0 ? "danger" : "neutral"}>{num(missed)} missed</Pill>
            <Pill tone="info" title="fulfilled ÷ (fulfilled + missed), superseded rows excluded">fulfilment rate {pct(resolved ? fulfilled / resolved : null, 0)}</Pill>
          </>
        )}
      </div>
      <p className="page-desc">Policy-accepted promises converted into executable payment commitments.</p>
      <div className="toolbar">
        <input className="search" type="search" placeholder="Search invoice, commitment id or evidence" value={query}
          onChange={(e) => setQuery(e.target.value)} aria-label="Search commitments" />
        <label>
          Status
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by status">
            <option value="all">All ({all.length})</option>
            {STATES.map((s) => <option key={s} value={s}>{stateLabel(s)} ({all.filter((c) => c.state === s).length})</option>)}
          </select>
        </label>
        <span className="spacer" />
        <button type="button" className="btn sm" onClick={commitments.reload}>Refresh</button>
      </div>
      <Status load={commitments} rows={8}>
        {(rows) => rows.length === 0
          ? <EmptyState title="No commitments yet" hint="A commitment is created only when policy accepts a promise." />
          : <CommitmentTable rows={shown} onOpen={onOpen} />}
      </Status>
    </>
  );
}
