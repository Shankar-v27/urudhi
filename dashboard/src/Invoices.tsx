/** Invoices: the table, and the invoice drawer with "Why this action?", commitments, ledgers and audit trail. */

import { ReactNode, useMemo, useState } from "react";
import {
  AuditEvent, BlockedCommitment, Commitment, Concession, Credibility, Explain, InstrumentMode, Invoice, Loaded,
  PolicyCheck, Promise_, api, daysUntil, inr, num, relativeDays, useLoad, when, whenIST,
} from "./api";
import {
  Checklist, ConfidenceBar, Drawer, DrawerSection, EmptyState, Fact, ModeBadge, Pill, Ref, SourceBadge, Status, StatusBadge,
  TableWrap, stateLabel,
} from "./ui";
import { InstrumentAction, PayloadLink, factsFromCommitment } from "./components/InstrumentAction";
import { MatchTag } from "./components/CommitmentDrawer";

// -- invoices table --------------------------------------------------------

type SortKey = "balance" | "overdue" | "due" | "number";

function overdueDays(invoice: Invoice, today?: Date): number {
  const d = daysUntil(invoice.due_on, today);
  return d === null ? 0 : -d;
}

export function InvoicesPage({ invoices, onOpen }: { invoices: Loaded<Invoice[]>; onOpen: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState<SortKey>("balance");
  const rows = invoices.data ?? [];
  const states = useMemo(() => Array.from(new Set(rows.map((i) => i.state))).sort(), [rows]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = rows.filter((i) =>
      (filter === "all" || i.state === filter) &&
      (!q || i.number.toLowerCase().includes(q) || (i.debtor_name ?? "").toLowerCase().includes(q) || i.debtor_id.toLowerCase().includes(q)));
    const by: Record<SortKey, (a: Invoice, b: Invoice) => number> = {
      balance: (a, b) => b.balance - a.balance,
      overdue: (a, b) => overdueDays(b) - overdueDays(a),
      due: (a, b) => a.due_on.localeCompare(b.due_on),
      number: (a, b) => a.number.localeCompare(b.number),
    };
    return [...filtered].sort(by[sort]);
  }, [rows, query, filter, sort]);

  return (
    <>
      <div className="page-title"><h1>Invoices</h1>{invoices.data && <Pill tone="outline">{num(rows.length)} invoices</Pill>}</div>
      <p className="page-desc">Every receivable the agent is working, with what has been recovered, waived and what still stands. Open a row for the reasoning behind its current action.</p>
      <div className="toolbar">
        <input className="search" type="search" placeholder="Search invoice number or debtor" value={query}
          onChange={(e) => setQuery(e.target.value)} aria-label="Search invoices" />
        <label>
          State
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by state">
            <option value="all">All ({rows.length})</option>
            {states.map((s) => <option key={s} value={s}>{stateLabel(s)} ({rows.filter((i) => i.state === s).length})</option>)}
          </select>
        </label>
        <label>
          Sort
          <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort invoices">
            <option value="balance">Balance (highest first)</option>
            <option value="overdue">Overdue age (oldest first)</option>
            <option value="due">Due date</option>
            <option value="number">Invoice number</option>
          </select>
        </label>
        <span className="spacer" />
        <button type="button" className="btn sm" onClick={invoices.reload}>Refresh</button>
      </div>
      <Status load={invoices} rows={8}>
        {(all) => all.length === 0 ? <EmptyState title="No invoices in this data source" /> : shown.length === 0 ? <EmptyState title="No invoices match" hint="Try a different state or search term." /> : (
          <TableWrap>
            <table>
              <thead>
                <tr>
                  <th>Invoice</th>
                  <th>Debtor</th>
                  <th>Source</th>
                  <th>State</th>
                  <th className="num">Outstanding</th>
                  <th className="num">Recovered</th>
                  <th className="num">Waived</th>
                  <th className="num">Balance</th>
                  <th>Due</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((invoice) => {
                  const overdue = overdueDays(invoice);
                  return (
                    <tr key={`${invoice.source ?? ""}:${invoice.id}`} className="clickable" onClick={() => onOpen(invoice.id)}>
                      <td><b>{invoice.number}</b><span className="secondary mono">{invoice.id}</span></td>
                      <td>{invoice.debtor_name ?? <span className="muted">{invoice.debtor_id}</span>}</td>
                      <td><SourceBadge source={invoice.source} /></td>
                      <td><StatusBadge state={invoice.state} /></td>
                      <td className="num">{inr(invoice.amount)}</td>
                      <td className="num">{inr(invoice.amount_paid)}</td>
                      <td className="num">{invoice.amount_waived > 0 ? inr(invoice.amount_waived) : <span className="muted">—</span>}</td>
                      <td className="num money">{inr(invoice.balance)}</td>
                      <td className="nowrap">
                        {invoice.due_on}
                        <span className={`secondary ${overdue > 0 && invoice.balance > 0 ? "neg" : ""}`}>
                          {invoice.balance > 0 ? (overdue > 0 ? `${overdue} days overdue` : relativeDays(invoice.due_on)) : "settled"}
                        </span>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <button type="button" className="btn sm" onClick={() => onOpen(invoice.id)} aria-label={`View invoice ${invoice.number}`}>View</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Status>
    </>
  );
}

// -- why this action ---------------------------------------------------------

function reasonTone(line: string): string {
  if (line.startsWith("+")) return "pos";
  if (line.startsWith("−") || line.startsWith("-")) return "neg";
  return "neutral";
}

function fulfilledLine(c: Credibility): string {
  const resolved = c.fulfilled + c.missed;
  if (c.commitments === 0) return "no commitment history";
  if (resolved === 0) return `${c.active} active, none resolved yet`;
  return `${c.fulfilled} of ${resolved} fulfilled`;
}

function credibilityTone(value: number): "success" | "warn" | "danger" {
  return value >= 0.6 ? "success" : value < 0.4 ? "danger" : "warn";
}

export function CredibilityStrip({ c }: { c: Credibility }) {
  return (
    <div className="stack-sm">
      <div className="score">
        <span className="value">{num(c.credibility, 2)}</span>
        <span className="muted">credibility</span>
        <Pill tone={credibilityTone(c.credibility)}>{fulfilledLine(c)}</Pill>
      </div>
      <div className="kv small muted">
        {c.average_delay_days !== null && <span>avg delay {num(c.average_delay_days, 1)} d</span>}
        {c.missed > 0 && <span>{c.missed} missed</span>}
        {c.partially_fulfilled > 0 && <span>{c.partially_fulfilled} partial</span>}
        {c.amount_committed > 0 && <span>{inr(c.amount_received)} received of {inr(c.amount_committed)} committed</span>}
      </div>
      {c.reasons.length > 0 && (
        <ul className="reasons">{c.reasons.map((r, i) => <li key={i} className={reasonTone(r)}>{r}</li>)}</ul>
      )}
    </div>
  );
}

export function WhyThisAction({ explain }: { explain: Explain }) {
  const d = explain.latest_decision;
  return (
    <DrawerSection title="Why this action?">
      <div className="score">
        <span className="value">{explain.priority.score}</span>
        <span className="muted">/ 100 priority</span>
      </div>
      <ul className="reasons" style={{ margin: "8px 0 14px" }}>
        {explain.priority.reasons.map((r, i) => <li key={i} className={reasonTone(r)}>{r}</li>)}
      </ul>

      <h4 style={{ marginBottom: 8 }}>Commitment credibility</h4>
      <CredibilityStrip c={explain.credibility} />

      {d === null ? <p className="muted small" style={{ marginTop: 12 }}>No intervention decided yet for this invoice.</p> : (
        <div className="stack-sm" style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
          <div className="flow">
            <span className="muted">proposed</span> <b>{stateLabel(d.proposed)}</b>
            <span className="arrow">→</span>
            <span className="muted">final</span> <b>{stateLabel(d.final)}</b>
            {d.modified && <Pill tone="warn">modified by policy</Pill>}
            {d.confidence !== null && <span className="muted small">confidence {num(d.confidence, 2)}</span>}
            <span className="muted small">{when(d.at)}</span>
          </div>
          {d.rationale.length > 0 && (
            <div><h4>Brain rationale</h4><ul className="bullets">{d.rationale.map((r, i) => <li key={i}>{r}</li>)}</ul></div>
          )}
          {d.policy_reasons.length > 0 && (
            <div><h4>Policy reasons</h4><ul className="bullets">{d.policy_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul></div>
          )}
          <div>
            <h4>Gates</h4>
            {d.gates.length === 0 ? <p className="muted small">no gates evaluated</p>
              : <Checklist checks={d.gates.map((g) => ({ allowed: g.ok, gate: g.gate, reason: g.reason }))} />}
          </div>
          {d.offer && (
            <div>
              <h4>Offer</h4>
              <p className="small">
                {stateLabel(d.offer.type)}
                {d.offer.discount_bps > 0 && <> · {(d.offer.discount_bps / 100).toFixed(2)}% discount</>}
                {d.offer.installment_count > 1 && <> · {d.offer.installment_count} installments</>}
                {" "}· pay by {d.offer.pay_by}
              </p>
            </div>
          )}
        </div>
      )}
      {explain.brain_failures > 0 && (
        <p className="muted small" style={{ marginTop: 8 }}>{explain.brain_failures} brain failure(s) recorded on this invoice.</p>
      )}
    </DrawerSection>
  );
}

// -- ledgers inside the drawer -------------------------------------------------

function BlockedCommitments({ rows }: { rows: BlockedCommitment[] }) {
  return (
    <ul className="payments">
      {rows.map((b, i) => (
        <li key={b.event?.seq ?? i}>
          <div className="kv">
            <Pill tone="danger">blocked</Pill>
            <b className="money">{b.amount !== null ? inr(b.amount) : "—"}</b>
            <span><span className="k">for</span>{b.due_on ?? "—"}</span>
            <span className="muted small">{when(b.at)}</span>
            {b.event && <Ref event={b.event} />}
          </div>
          {b.reason && <div className="small" style={{ margin: "6px 0" }}>{b.reason}</div>}
          <Checklist checks={b.checks} onlyFailed />
          <div className="muted small" style={{ marginTop: 6 }}>Promise recorded as evidence; commitment NOT created.</div>
        </li>
      ))}
    </ul>
  );
}

function PromiseHistory({ promises }: { promises: Promise_[] }) {
  if (promises.length === 0) return <EmptyState title="No promises recorded" />;
  return (
    <TableWrap>
      <table className="compact">
        <thead>
          <tr><th>Status</th><th className="num">Amount</th><th>Promised for</th><th>Confidence</th><th>Debtor words</th></tr>
        </thead>
        <tbody>
          {promises.map((p) => (
            <tr key={p.id}>
              <td><StatusBadge state={p.state} /></td>
              <td className="num money">{inr(p.amount)}</td>
              <td className="nowrap">{p.promised_on}</td>
              <td><ConfidenceBar value={p.confidence} /></td>
              <td className="words">“{p.verbatim}”</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

/** Concession link column: resolved through the commitment it opened; a bare URL is never an anchor. */
function ConcessionLink({ c, commitments }: { c: Concession; commitments: Commitment[] }) {
  const linked = commitments.find((k) => k.concession_id === c.id);
  if (linked) return <InstrumentAction facts={factsFromCommitment(linked)} compact />;
  if (c.payment_link_url) return <PayloadLink url={c.payment_link_url} mode={undefined} />;
  return <span className="muted">—</span>;
}

export function ConcessionTable({ concessions, commitments, showInvoice = false, invoiceNumbers }: {
  concessions: Concession[]; commitments: Commitment[]; showInvoice?: boolean; invoiceNumbers?: Record<string, string>;
}) {
  if (concessions.length === 0) return <EmptyState title="No concessions offered" />;
  return (
    <TableWrap>
      <table className="compact">
        <thead>
          <tr>
            {showInvoice && <th>Invoice</th>}
            <th>Type</th>
            <th>Status</th>
            <th className="num">Discount</th>
            <th className="num">Settlement</th>
            <th>Pay by</th>
            <th>Schedule</th>
            <th>Instrument</th>
            <th>Rationale</th>
          </tr>
        </thead>
        <tbody>
          {concessions.map((c) => (
            <tr key={c.id}>
              {showInvoice && <td>{invoiceNumbers?.[c.invoice_id] ?? c.invoice_id}</td>}
              <td>{stateLabel(c.type)}</td>
              <td><StatusBadge state={c.state} /></td>
              <td className="num">{c.discount_bps > 0 ? `${(c.discount_bps / 100).toFixed(2)}%` : "—"}</td>
              <td className="num"><span className="money">{inr(c.settlement_amount)}</span><span className="secondary">of {inr(c.balance_at_offer)}</span></td>
              <td className="nowrap">{c.pay_by}</td>
              <td className="small">
                {c.installments.length === 0 ? <span className="muted">—</span> : (
                  <ul style={{ listStyle: "none", padding: 0, margin: 0 }} className="num">
                    {c.installments.map((i, k) => <li key={k}>{i.due_on} · {inr(i.amount)}</li>)}
                  </ul>
                )}
              </td>
              <td><ConcessionLink c={c} commitments={commitments} /></td>
              <td className="wrap muted small">{c.rationale}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

// -- audit timeline ------------------------------------------------------------

function text(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function number(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  return typeof value === "number" ? value : null;
}

const GOOD = new Set(["payment_observed", "promise_kept", "commitment_created", "commitment_fulfilled", "payment_instrument_created", "commitment_approved", "gate_allowed"]);
const BAD = new Set(["gate_blocked", "escalated", "dispute_recorded", "brain_failed", "commitment_blocked", "commitment_missed", "commitment_cancelled", "rail_failed", "promise_broken"]);
const WARN = new Set(["human_action", "commitment_partially_fulfilled", "commitment_superseded", "stop_contact"]);
const COMMITMENT_KINDS = new Set([
  "commitment_proposed", "commitment_approved", "commitment_blocked", "commitment_created",
  "payment_instrument_created", "commitment_partially_fulfilled", "commitment_fulfilled",
  "commitment_missed", "commitment_cancelled", "commitment_superseded",
]);

function toneOf(kind: string): string {
  return GOOD.has(kind) ? "good" : BAD.has(kind) ? "bad" : WARN.has(kind) ? "warn" : "";
}

function CommitmentEvent({ event, modes, commitments }: { event: AuditEvent; modes: Record<string, InstrumentMode | undefined>; commitments: Commitment[] }) {
  const p = event.payload;
  const committed = number(p, "committed_amount") ?? number(p, "amount");
  const received = number(p, "amount_received");
  const outcome = text(p, "outcome");
  const url = text(p, "payment_url");
  const cid = text(p, "commitment_id");
  const checks = Array.isArray(p.checks)
    ? (p.checks as unknown[]).filter((c): c is PolicyCheck => typeof c === "object" && c !== null && "allowed" in c)
    : [];
  const passed = checks.filter((c) => c.allowed).length;
  const linked = cid ? commitments.find((c) => c.id === cid) : undefined;
  return (
    <div className="flow small">
      {committed !== null && <b className="money">{inr(committed)}</b>}
      {text(p, "due_on") && <span className="muted">due {text(p, "due_on")}</span>}
      {received !== null && <span className="muted">· received {inr(received)}</span>}
      {outcome && <StatusBadge state={outcome} />}
      {checks.length > 0 && <Pill tone={passed === checks.length ? "success" : "danger"}>{passed}/{checks.length} checks passed</Pill>}
      {url && <PayloadLink url={url} mode={cid ? modes[cid] : undefined} commitmentFacts={linked ? factsFromCommitment(linked) : undefined} />}
      {cid && <span className="muted mono">{cid}</span>}
    </div>
  );
}

function TimelineItem({ event, modes, commitments }: { event: AuditEvent; modes: Record<string, InstrumentMode | undefined>; commitments: Commitment[] }) {
  const p = event.payload;
  const verbatim = text(p, "verbatim");
  const message = text(p, "text");
  const reason = text(p, "reason");
  const intervention = text(p, "intervention");
  const reasons = Array.isArray(p.reasons) ? (p.reasons as unknown[]).filter((r): r is string => typeof r === "string") : [];
  const commitment = COMMITMENT_KINDS.has(event.kind);
  return (
    <li className={toneOf(event.kind)}>
      <span className="kind">{stateLabel(event.kind)}</span>
      <span className="when">#{event.seq} · {when(event.at)} · {event.actor}</span>
      <div className="body">
        {event.kind === "intervention_decided" && (
          <div className="flow small">
            <b>{stateLabel(text(p, "proposed"))}</b> <span className="arrow">→</span> <b>{stateLabel(text(p, "final"))}</b>
            {p.modified === true && <Pill tone="warn">modified by policy</Pill>}
            {reasons.length > 0 && <span className="muted">· {reasons.join("; ")}</span>}
          </div>
        )}
        {event.kind === "human_action" && (
          <div className="small">
            <b>{text(p, "action") ?? "action"}</b> by {text(p, "operator") ?? "operator"}
            {text(p, "from_state") && <span className="muted"> · {text(p, "from_state")} → {text(p, "to_state")}</span>}
            {text(p, "commitment_id") && <span className="muted"> · opened {text(p, "commitment_id")}</span>}
            {text(p, "notes") && <div className="muted">{text(p, "notes")}</div>}
          </div>
        )}
        {intervention && event.kind === "message_sent" && (
          <div className="flow small">
            <Pill tone={intervention.startsWith("commitment_") ? "success" : "neutral"}>{stateLabel(intervention)}</Pill>
            {text(p, "commitment_id") && <span className="muted mono">{text(p, "commitment_id")}</span>}
          </div>
        )}
        {event.kind === "rail_failed" && text(p, "error") && <div className="neg small">{text(p, "error")}</div>}
        {commitment && <CommitmentEvent event={event} modes={modes} commitments={commitments} />}
        {verbatim && <blockquote className="quote">“{verbatim}”</blockquote>}
        {message && <div className="msg">{message}</div>}
        {reason && <div className="muted small">{reason}</div>}
        {!commitment && typeof p.amount === "number" && <div className="money">{inr(p.amount)}</div>}
      </div>
    </li>
  );
}

// -- invoice drawer --------------------------------------------------------------

function InvoiceCommitments({ rows, onOpenCommitment }: { rows: Commitment[]; onOpenCommitment: (id: string, invoiceId: string) => void }) {
  if (rows.length === 0) return <EmptyState title="No commitment accepted on this invoice yet" hint="A commitment is created only when policy accepts a promise." />;
  const sorted = [...rows].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return (
    <TableWrap>
      <table className="compact">
        <thead>
          <tr><th>Status</th><th className="num">Committed</th><th className="num">Received</th><th>Due</th><th>Instrument</th><th>Evidence</th><th /></tr>
        </thead>
        <tbody>
          {sorted.map((c) => (
            <tr key={c.id} className="clickable" onClick={() => onOpenCommitment(c.id, c.invoice_id)}>
              <td><StatusBadge state={c.state} /></td>
              <td className="num money">{inr(c.committed_amount)}</td>
              <td className="num">{inr(c.amount_received)}</td>
              <td className="nowrap">{c.due_on}<span className="secondary">{relativeDays(c.due_on)}</span></td>
              <td onClick={(e) => e.stopPropagation()}><InstrumentAction facts={factsFromCommitment(c)} compact /></td>
              <td className="words small">{c.evidence ? `“${c.evidence}”` : <span className="muted">—</span>}</td>
              <td onClick={(e) => e.stopPropagation()}>
                <button type="button" className="btn sm" onClick={() => onOpenCommitment(c.id, c.invoice_id)} aria-label={`Open commitment ${c.id}`}>Open</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

export function InvoiceDrawer({ id, onClose, onOpenCommitment }: {
  id: string; onClose: () => void; onOpenCommitment: (id: string, invoiceId: string) => void;
}) {
  const detail = useLoad(() => api.invoice(id), [id]);
  const inv = detail.data?.invoice;
  const headExtra: ReactNode = detail.data && (
    <p className="muted small" style={{ marginTop: 4 }}>
      {detail.data.debtor.name} · {detail.data.debtor.contact_name} · {detail.data.debtor.preferred_channel} · {detail.data.debtor.language}
    </p>
  );
  return (
    <Drawer eyebrow={<>Invoice{detail.data && <> · <SourceBadge source={detail.data.source ?? detail.data.invoice.source} /></>}</>}
      title={inv ? <>{inv.number}<StatusBadge state={inv.state} /></> : id} onClose={onClose} headExtra={headExtra}>
      <Status load={detail} rows={10}>
        {({ invoice, debtor, promises, concessions, commitments, payments, events, explain }) => {
          const modes: Record<string, InstrumentMode | undefined> = {};
          for (const c of commitments) modes[c.id] = c.instrument_mode;
          return (
            <>
              <DrawerSection title="Position">
                <div className="facts">
                  <Fact k="Balance" v={inr(invoice.balance)} money />
                  <Fact k="Invoice amount" v={inr(invoice.amount)} />
                  <Fact k="Recovered" v={inr(invoice.amount_paid)} />
                  <Fact k="Waived" v={inr(invoice.amount_waived)} />
                  <Fact k="Issued" v={invoice.issued_on} />
                  <Fact k="Due" v={<>{invoice.due_on}<span className="secondary" style={{ display: "block" }}>{relativeDays(invoice.due_on)}</span></>} />
                  <Fact k="Contact" v={<>{debtor.phone}<span className="secondary" style={{ display: "block" }}>{debtor.email}</span></>} />
                  {invoice.human_released_at && <Fact k="Released by human" v={whenIST(invoice.human_released_at)} />}
                </div>
              </DrawerSection>

              <WhyThisAction explain={explain} />

              {(explain.escalation || explain.dispute) && (
                <DrawerSection title="Handed to a human">
                  {explain.escalation && (
                    <p className="small"><Pill tone="danger">escalated</Pill> {when(explain.escalation.at)}{explain.escalation.reason && <> — {explain.escalation.reason}</>}</p>
                  )}
                  {explain.dispute && (
                    <>
                      <p className="small" style={{ marginTop: 6 }}><Pill tone="danger">disputed</Pill> {when(explain.dispute.at)}{explain.dispute.reason && <> — {explain.dispute.reason}</>}</p>
                      {explain.dispute.verbatim && <blockquote className="quote" style={{ marginTop: 8 }}>“{explain.dispute.verbatim}”</blockquote>}
                    </>
                  )}
                </DrawerSection>
              )}

              <DrawerSection title="Commitments" badge={commitments.length > 0 ? <Pill tone="outline">{commitments.length}</Pill> : undefined}>
                <p className="muted small" style={{ marginBottom: 10 }}>
                  What policy accepted from each promise. A commitment only moves state when the payment rails report money; open one for its full provenance chain.
                </p>
                <InvoiceCommitments rows={commitments} onOpenCommitment={onOpenCommitment} />
              </DrawerSection>

              {explain.blocked_commitments.length > 0 && (
                <DrawerSection title="Blocked commitments" badge={<Pill tone="danger">{explain.blocked_commitments.length}</Pill>}>
                  <BlockedCommitments rows={explain.blocked_commitments} />
                </DrawerSection>
              )}

              <DrawerSection title="Promise history"><PromiseHistory promises={promises} /></DrawerSection>

              <DrawerSection title="Concessions"><ConcessionTable concessions={concessions} commitments={commitments} /></DrawerSection>

              <DrawerSection title="Payments observed on rails" badge={<ModeBadge mode="observed" />}>
                {payments.length === 0 ? <EmptyState title="Nothing observed yet" /> : (
                  <ul className="payments">
                    {payments.map((p) => (
                      <li key={p.id}>
                        <div className="kv">
                          <b className="money">{inr(p.amount)}</b>
                          <span>{p.method}</span>
                          <span className="muted">{when(p.observed_at)}</span>
                          <MatchTag matchedBy={p.matched_by ?? null} />
                          {p.commitment_id && <span className="mono muted">{p.commitment_id}</span>}
                        </div>
                        <div className="muted small mono">{p.razorpay_payment_id} · event {p.razorpay_event_id}</div>
                      </li>
                    ))}
                  </ul>
                )}
              </DrawerSection>

              <DrawerSection title="Audit trail" badge={<Pill tone="outline">{events.length} events</Pill>}>
                <ul className="timeline">
                  {events.map((event) => <TimelineItem key={event.seq} event={event} modes={modes} commitments={commitments} />)}
                </ul>
              </DrawerSection>
            </>
          );
        }}
      </Status>
    </Drawer>
  );
}
