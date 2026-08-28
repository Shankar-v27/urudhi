/** Commitments: the hero table of policy-accepted promises turned into executable payment commitments, across both ledgers. */

import { useMemo, useState } from "react";
import { Commitment, DATA_SOURCES, SOURCE_LABEL, api, inr, num, pct, relativeDays, useLoad } from "./api";
import { useSource } from "./source";
import { EmptyState, Pill, SourceBadge, Status, StatusBadge, TableWrap, stateLabel } from "./ui";
import { InstrumentAction, factsFromCommitment } from "./components/InstrumentAction";

const STATES = ["active", "partially_fulfilled", "fulfilled", "missed", "cancelled", "superseded"];
const OPEN = new Set(["active", "partially_fulfilled"]);

/** The exact wording the API uses for a miss; the table uses it for an empty search too. */
export const NO_MATCH = "No matching commitment in current data source";

/** Search across invoice number / id, debtor, commitment id (= the instrument's reference id) and payment-link id. */
export function matchesQuery(c: Commitment, q: string): boolean {
  if (!q) return true;
  const hay = [
    c.invoice_number ?? "", c.invoice_id, c.debtor_name ?? "", c.debtor_id, c.id, c.instrument_id ?? "", c.evidence,
  ];
  return hay.some((h) => h.toLowerCase().includes(q));
}

function Received({ c }: { c: Commitment }) {
  if (c.amount_received > 0) return <>{inr(c.amount_received)}</>;
  if (OPEN.has(c.state)) return <span className="muted" title="Active commitment; the rails have reported nothing yet">Payment pending</span>;
  return <>{inr(0)}</>;
}

export function CommitmentTable({ rows, onOpen, now }: {
  rows: Commitment[]; onOpen: (id: string, invoiceId: string) => void; now?: number;
}) {
  if (rows.length === 0) return <EmptyState title={NO_MATCH} hint="Try another status, source or search term." />;
  const sorted = [...rows].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return (
    <TableWrap>
      <table>
        <thead>
          <tr>
            <th>Invoice</th>
            <th>Debtor</th>
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
              <tr key={`${c.source}:${c.id}`} className="clickable" onClick={() => onOpen(c.id, c.invoice_id)}>
                <td>
                  <b>{c.invoice_number ?? c.invoice_id}</b>
                  <span className="secondary mono">{c.id}</span>
                  {c.installment_index !== null && <span className="secondary">installment #{c.installment_index}</span>}
                </td>
                <td>{c.debtor_name ?? <span className="muted">{c.debtor_id}</span>}</td>
                <td><SourceBadge source={c.source} />{c.origin && <span className="secondary">{c.origin}</span>}</td>
                <td><StatusBadge state={c.state} /></td>
                <td className="num money">{inr(c.committed_amount)}</td>
                <td className="num"><Received c={c} /></td>
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
  const { source, setSource } = useSource();
  const commitments = useLoad(() => api.commitments(source), [source]);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const all = commitments.data ?? [];
  // Superseded rows were replaced by a newer arrangement; they are history, not outcomes.
  const counted = all.filter((c) => c.state !== "superseded");
  const fulfilled = counted.filter((c) => c.state === "fulfilled").length;
  const missed = counted.filter((c) => c.state === "missed").length;
  const resolved = fulfilled + missed;
  const liveCount = all.filter((c) => c.source === "live_test").length;
  const simCount = all.filter((c) => c.source === "simulation").length;
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return all.filter((c) => (filter === "all" || c.state === filter) && matchesQuery(c, q));
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
            {source === "all" && <Pill tone="outline" title="rows per ledger">{num(liveCount)} live test · {num(simCount)} simulation</Pill>}
          </>
        )}
      </div>
      <p className="page-desc">Policy-accepted promises converted into executable payment commitments.</p>
      <div className="toolbar">
        <input className="search" type="search" placeholder="Search invoice, debtor, commitment id or payment link id" value={query}
          onChange={(e) => setQuery(e.target.value)} aria-label="Search commitments" />
        <div className="segmented" role="group" aria-label="Filter by source">
          {DATA_SOURCES.map((s) => (
            <button key={s} type="button" aria-pressed={source === s} onClick={() => setSource(s)}>{SOURCE_LABEL[s]}</button>
          ))}
        </div>
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
          ? <EmptyState title="No commitments in this data source" hint="A commitment is created only when policy accepts a promise. Try another data source." />
          : <CommitmentTable rows={shown} onOpen={onOpen} />}
      </Status>
    </>
  );
}
