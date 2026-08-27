/** Invoices (table + explainable detail panel), the commitment integrity view, and the ledgers. */

import { ReactNode, useMemo, useState } from "react";
import {
  AuditEvent, BlockedCommitment, Commitment, CommitmentChain, Concession, Credibility, EventRef,
  Explain, Invoice, MatchedBy, PolicyCheck, Promise_, RailRow, api, inr, num, pct, useLoad, when,
} from "./api";
import { Empty, StateChip, Status, Tag } from "./ui";

// -- invoices --------------------------------------------------------------

export function InvoiceTable({ invoices, onOpen }: { invoices: Invoice[]; onOpen: (id: string) => void }) {
  const [filter, setFilter] = useState("all");
  const states = useMemo(() => Array.from(new Set(invoices.map((i) => i.state))).sort(), [invoices]);
  const shown = filter === "all" ? invoices : invoices.filter((i) => i.state === filter);
  return (
    <>
      <div className="toolbar">
        <label className="muted">
          State{" "}
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">all ({invoices.length})</option>
            {states.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")} ({invoices.filter((i) => i.state === s).length})
              </option>
            ))}
          </select>
        </label>
      </div>
      {shown.length === 0 ? <Empty>no invoices in this state</Empty> : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Invoice</th>
                <th>Debtor</th>
                <th>State</th>
                <th className="num">Amount</th>
                <th className="num">Recovered</th>
                <th className="num">Waived</th>
                <th className="num">Balance</th>
                <th>Due</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((invoice) => (
                <tr key={invoice.id} className="row" onClick={() => onOpen(invoice.id)}>
                  <td>{invoice.number}</td>
                  <td>{invoice.debtor_name ?? <span className="muted">{invoice.debtor_id}</span>}</td>
                  <td><StateChip state={invoice.state} /></td>
                  <td className="num">{inr(invoice.amount)}</td>
                  <td className="num">{inr(invoice.amount_paid)}</td>
                  <td className="num">{inr(invoice.amount_waived)}</td>
                  <td className="num">{inr(invoice.balance)}</td>
                  <td className="muted">{invoice.due_on}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// -- detail ----------------------------------------------------------------

function reasonTone(line: string): string {
  if (line.startsWith("+")) return "pos";
  if (line.startsWith("−") || line.startsWith("-")) return "neg";
  return "neutral";
}

function label(kind: string | null | undefined): string {
  return kind ? kind.replace(/_/g, " ") : "—";
}

/** "X of Y fulfilled" over resolved commitments; honest when nothing has resolved yet. */
function fulfilledLine(c: Credibility): string {
  const resolved = c.fulfilled + c.missed;
  if (c.commitments === 0) return "no commitment history";
  if (resolved === 0) return `${c.active} active, none resolved yet`;
  return `${c.fulfilled} of ${resolved} fulfilled`;
}

function CredibilityStrip({ c }: { c: Credibility }) {
  return (
    <div className="cred">
      <div className="cred-head">
        <span className="value">{num(c.credibility, 2)}</span>
        <span className="muted">credibility</span>
        <Tag tone={c.credibility >= 0.6 ? "ok" : c.credibility < 0.4 ? "bad" : "warn"}>{fulfilledLine(c)}</Tag>
        {c.average_delay_days !== null && (
          <span className="muted small">avg delay {num(c.average_delay_days, 1)} d</span>
        )}
        {c.missed > 0 && <span className="muted small">{c.missed} missed</span>}
        {c.partially_fulfilled > 0 && <span className="muted small">{c.partially_fulfilled} partial</span>}
        {c.amount_committed > 0 && (
          <span className="muted small">{inr(c.amount_received)} received of {inr(c.amount_committed)} committed</span>
        )}
      </div>
      {c.reasons.length > 0 && (
        <ul className="reasons">
          {c.reasons.map((r, i) => <li key={i} className={reasonTone(r)}>{r}</li>)}
        </ul>
      )}
    </div>
  );
}

function WhyThisAction({ explain }: { explain: Explain }) {
  const d = explain.latest_decision;
  return (
    <section className="why">
      <h2>Why this action?</h2>
      <div className="score">
        <span className="value">{explain.priority.score}</span>
        <span className="muted">/100 priority</span>
      </div>
      <ul className="reasons">
        {explain.priority.reasons.map((r, i) => <li key={i} className={reasonTone(r)}>{r}</li>)}
      </ul>

      <h4>Commitment credibility</h4>
      <CredibilityStrip c={explain.credibility} />

      {d === null ? <p className="muted">No intervention decided yet for this invoice.</p> : (
        <div className="decision">
          <div className="flow">
            <span className="muted">proposed</span> <b>{label(d.proposed)}</b>
            <span className="arrow">→</span>
            <span className="muted">final</span> <b>{label(d.final)}</b>
            {d.modified && <Tag tone="warn">modified by policy</Tag>}
            {d.confidence !== null && <span className="muted small">confidence {num(d.confidence, 2)}</span>}
            <span className="muted small">{when(d.at)}</span>
          </div>
          {d.rationale.length > 0 && (
            <>
              <h4>Brain rationale</h4>
              <ul>{d.rationale.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </>
          )}
          {d.policy_reasons.length > 0 && (
            <>
              <h4>Policy reasons</h4>
              <ul>{d.policy_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </>
          )}
          <h4>Gates</h4>
          {d.gates.length === 0 ? <p className="muted small">no gates evaluated</p> : (
            <ul className="gates">
              {d.gates.map((g, i) => (
                <li key={i} className={g.ok ? "ok" : "blocked"}>
                  <span className="mark">{g.ok ? "✓" : "✗"}</span>
                  <b>{g.gate}</b> <span className="muted">{g.reason}</span>
                </li>
              ))}
            </ul>
          )}
          {d.offer && (
            <>
              <h4>Offer</h4>
              <p className="small">
                {label(d.offer.type)}
                {d.offer.discount_bps > 0 && <> · {(d.offer.discount_bps / 100).toFixed(2)}% discount</>}
                {d.offer.installment_count > 1 && <> · {d.offer.installment_count} installments</>}
                {" "}· pay by {d.offer.pay_by}
              </p>
            </>
          )}
        </div>
      )}
      {explain.brain_failures > 0 && (
        <p className="muted small">{explain.brain_failures} brain failure(s) recorded on this invoice.</p>
      )}
    </section>
  );
}

// -- commitment integrity --------------------------------------------------

/** Audit reference: `#seq`, with kind and hash prefix on hover. */
function Ref({ event }: { event: EventRef | null | undefined }) {
  if (!event) return <span className="ref muted" title="no audit event linked">no audit ref</span>;
  return (
    <span className="ref" title={`${event.kind} · ${when(event.at)} · ${event.hash.slice(0, 12)}…`}>
      #{event.seq}
    </span>
  );
}

function Checklist({ checks }: { checks: PolicyCheck[] }) {
  if (checks.length === 0) return <p className="muted small">no checks recorded</p>;
  return (
    <ul className="gates">
      {checks.map((c, i) => (
        <li key={i} className={c.allowed ? "ok" : "blocked"}>
          <span className="mark">{c.allowed ? "✓" : "✗"}</span>
          <b>{c.gate}</b> <span className="muted">{c.reason}</span>
        </li>
      ))}
    </ul>
  );
}

function MatchTag({ matchedBy }: { matchedBy: MatchedBy | undefined }) {
  switch (matchedBy) {
    case "instrument": return <Tag tone="ok">exact · instrument</Tag>;
    case "instrument-late": return <Tag tone="warn">exact · instrument, late</Tag>;
    case "invoice": return <Tag>matched by invoice</Tag>;
    default: return <Tag tone="bad">unmatched</Tag>;
  }
}

function Step({ n, title, event, children }: { n: number; title: string; event?: EventRef | null; children: ReactNode }) {
  return (
    <li className="step">
      <div className="step-head">
        <span className="step-n">{n}</span>
        <span className="step-label">{title}</span>
        {event !== undefined && <Ref event={event} />}
      </div>
      <div className="evidence">{children}</div>
    </li>
  );
}

function RailPayment({ row }: { row: RailRow }) {
  if (row.payment_id) {
    return (
      <li>
        <b className="num-inline">{inr(row.amount ?? 0)}</b> · {row.method ?? "—"} · {when(row.observed_at)}{" "}
        <MatchTag matchedBy={row.matched_by} />
        <div className="muted small">{row.razorpay_payment_id} · event {row.razorpay_event_id}</div>
      </li>
    );
  }
  // No payment row survived the match, but the audit log recorded the fulfilment itself.
  return (
    <li>
      <b>{label(row.outcome)}</b>
      {typeof row.amount_received === "number" && <> · <span className="num-inline">{inr(row.amount_received)}</span> received</>}
      {" "}<MatchTag matchedBy={row.matched_by} /> <Ref event={row.event} />
    </li>
  );
}

function OutcomeTone(state: string): string {
  if (state === "fulfilled") return "ok";
  if (state === "missed" || state === "cancelled") return "bad";
  if (state === "partially_fulfilled" || state === "superseded") return "warn";
  return "";
}

export function CommitmentIntegrity({ chain }: { chain: CommitmentChain }) {
  const u = chain.understood;
  const ins = chain.instrument;
  const out = chain.outcome;
  return (
    <div className="commit">
      <div className="commit-head">
        <StateChip state={chain.state} />
        <b className="num-inline">{inr(chain.committed_amount)}</b>
        <span><span className="muted">due</span> {chain.due_on} <span className="muted small">(ends {when(chain.due_at)})</span></span>
        <span><span className="muted">source</span> {chain.source}</span>
        {chain.installment_index !== null && <span><span className="muted">installment</span> #{chain.installment_index}</span>}
        <span className="muted small">{chain.id}</span>
      </div>

      <ol className="provenance">
        <Step n={1} title="What was said" event={chain.said.event}>
          {chain.said.verbatim ? <div className="verbatim">“{chain.said.verbatim}”</div> : <p className="muted small">no verbatim — opened without a debtor message</p>}
          <div className="kv">
            {chain.said.promise_id ? (
              <span><span className="muted">promise</span> {chain.said.promise_id}{chain.said.promise_state && <> <StateChip state={chain.said.promise_state} /></>}</span>
            ) : <span className="muted">no promise record</span>}
            <span><span className="muted">at</span> {when(chain.said.at)}</span>
          </div>
        </Step>

        <Step n={2} title="What AI understood" event={u.event}>
          <div className="kv">
            <span><span className="muted">intent</span> <b>{label(u.intent)}</b></span>
            <span><span className="muted">amount</span> {u.amount !== null ? inr(u.amount) : "—"}</span>
            <span><span className="muted">on</span> {u.on ?? "—"}</span>
            <span><span className="muted">confidence</span> {num(u.confidence, 2)}</span>
            {u.partial && <Tag tone="warn">partial</Tag>}
            {u.brain && <span><span className="muted">brain</span> {u.brain}</span>}
          </div>
          {u.flags.length > 0 && (
            <div className="kv small">{u.flags.map((f) => <Tag key={f}>{f}</Tag>)}</div>
          )}
        </Step>

        <Step n={3} title="What policy allowed" event={chain.policy.event}>
          <Checklist checks={chain.policy.checks} />
          <p className="small decision-line">
            <span className="mark ok">✓</span> <b>accepted</b>
            {chain.policy.reason && <span className="muted"> — {chain.policy.reason}</span>}
          </p>
        </Step>

        <Step n={4} title="What instrument was created" event={ins.event}>
          {ins.type === null && !ins.id ? <p className="muted small">no payment instrument issued for this commitment</p> : (
            <div className="kv">
              <span><span className="muted">type</span> {label(ins.type)}</span>
              <span><span className="muted">amount</span> {inr(ins.amount)}</span>
              <span><span className="muted">expires</span> {when(ins.expires)}</span>
              {ins.url
                ? <a href={ins.url} target="_blank" rel="noreferrer">open payment link</a>
                : <span className="muted">no link</span>}
              {ins.id && <span className="muted small">{ins.id}</span>}
              {ins.reference_id && <span><span className="muted">reference</span> {ins.reference_id}</span>}
              {ins.sent
                ? <Tag tone="ok">sent to debtor{ins.confirmation ? <> · #{ins.confirmation.seq}</> : null}</Tag>
                : <Tag tone="warn">not yet sent</Tag>}
            </div>
          )}
          {ins.notes && <div className="muted small">{ins.notes}</div>}
        </Step>

        <Step n={5} title="What money arrived">
          {chain.rail.length === 0 ? <p className="muted small">nothing observed on the rails yet</p> : (
            <ul className="payments">
              {chain.rail.map((r, i) => <RailPayment key={r.payment_id ?? r.event?.seq ?? i} row={r} />)}
            </ul>
          )}
        </Step>

        <Step n={6} title="Final outcome" event={out.event ?? out.created_event}>
          <div className="kv">
            <StateChip state={out.state} />
            {out.promise_state && <span><span className="muted">promise</span> <StateChip state={out.promise_state} /></span>}
            <span><span className="muted">received</span> {inr(chain.amount_received)}</span>
            <span><span className="muted">remaining</span> {inr(chain.amount_remaining)}</span>
            {chain.days_late > 0 && <Tag tone={OutcomeTone("partially_fulfilled")}>{chain.days_late} d late</Tag>}
            {chain.fulfilled_at && <span className="muted small">fulfilled {when(chain.fulfilled_at)}</span>}
            {chain.missed_at && <span className="muted small">missed {when(chain.missed_at)}</span>}
          </div>
          {chain.cancel_reason && <div className="muted small">cancelled: {chain.cancel_reason}</div>}
          {out.event === null && out.created_event && (
            <div className="muted small">still open — created at #{out.created_event.seq}</div>
          )}
        </Step>
      </ol>
    </div>
  );
}

function BlockedCommitments({ rows }: { rows: BlockedCommitment[] }) {
  return (
    <ul className="blocked-list">
      {rows.map((b, i) => (
        <li key={b.event?.seq ?? i}>
          <div className="kv">
            <Tag tone="bad">blocked</Tag>
            <b className="num-inline">{b.amount !== null ? inr(b.amount) : "—"}</b>
            <span><span className="muted">for</span> {b.due_on ?? "—"}</span>
            <span className="muted small">{when(b.at)}</span>
            {b.event && <Ref event={b.event} />}
          </div>
          {b.reason && <div className="small">{b.reason}</div>}
          <Checklist checks={b.checks.filter((c) => !c.allowed)} />
          <div className="muted small">Promise recorded as evidence; commitment NOT created.</div>
        </li>
      ))}
    </ul>
  );
}

// -- promise / concession tables --------------------------------------------

function PromiseHistory({ promises }: { promises: Promise_[] }) {
  if (promises.length === 0) return <Empty>no promises recorded</Empty>;
  return (
    <div className="scroll">
      <table className="compact">
        <thead>
          <tr>
            <th>State</th>
            <th className="num">Amount</th>
            <th>Promised for</th>
            <th className="num">Confidence</th>
            <th>Their words</th>
          </tr>
        </thead>
        <tbody>
          {promises.map((p) => (
            <tr key={p.id}>
              <td><StateChip state={p.state} /></td>
              <td className="num">{inr(p.amount)}</td>
              <td>{p.promised_on}</td>
              <td className="num">{p.confidence.toFixed(2)}</td>
              <td className="muted">“{p.verbatim}”</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ConcessionTable({ concessions, showInvoice = false }: { concessions: Concession[]; showInvoice?: boolean }) {
  if (concessions.length === 0) return <Empty>no concessions offered</Empty>;
  return (
    <div className="scroll">
      <table className="compact">
        <thead>
          <tr>
            {showInvoice && <th>Invoice</th>}
            <th>Type</th>
            <th>State</th>
            <th className="num">Discount</th>
            <th className="num">Settlement</th>
            <th>Pay by</th>
            <th>Schedule</th>
            <th>Link</th>
          </tr>
        </thead>
        <tbody>
          {concessions.map((c) => (
            <tr key={c.id}>
              {showInvoice && <td>{c.invoice_id}</td>}
              <td>{c.type}</td>
              <td><StateChip state={c.state} /></td>
              <td className="num">{c.discount_bps > 0 ? `${(c.discount_bps / 100).toFixed(2)}%` : "—"}</td>
              <td className="num">{inr(c.settlement_amount)} <span className="muted small">of {inr(c.balance_at_offer)}</span></td>
              <td>{c.pay_by}</td>
              <td className="small">
                {c.installments.length === 0 ? <span className="muted">—</span> : (
                  <ul className="schedule">
                    {c.installments.map((i, k) => <li key={k}>{i.due_on} · {inr(i.amount)}</li>)}
                  </ul>
                )}
              </td>
              <td className="small">
                {c.payment_link_url
                  ? <a href={c.payment_link_url} target="_blank" rel="noreferrer">link</a>
                  : <span className="muted">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// -- audit timeline ---------------------------------------------------------

function payloadText(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function payloadNumber(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  return typeof value === "number" ? value : null;
}

const COMMITMENT_KINDS = new Set([
  "commitment_proposed", "commitment_approved", "commitment_blocked", "commitment_created",
  "payment_instrument_created", "commitment_partially_fulfilled", "commitment_fulfilled",
  "commitment_missed", "commitment_cancelled", "commitment_superseded",
]);

function CommitmentEvent({ event }: { event: AuditEvent }) {
  const p = event.payload;
  const committed = payloadNumber(p, "committed_amount") ?? payloadNumber(p, "amount");
  const received = payloadNumber(p, "amount_received");
  const outcome = payloadText(p, "outcome");
  const url = payloadText(p, "payment_url");
  const checks = Array.isArray(p.checks)
    ? (p.checks as unknown[]).filter((c): c is PolicyCheck => typeof c === "object" && c !== null && "allowed" in c)
    : [];
  const passed = checks.filter((c) => c.allowed).length;
  return (
    <div className="flow small">
      {committed !== null && <b className="num-inline">{inr(committed)}</b>}
      {payloadText(p, "due_on") && <span className="muted">due {payloadText(p, "due_on")}</span>}
      {received !== null && <span className="muted">· received {inr(received)}</span>}
      {outcome && <Tag tone={OutcomeTone(outcome)}>{label(outcome)}</Tag>}
      {checks.length > 0 && (
        <Tag tone={passed === checks.length ? "ok" : "bad"}>{passed}/{checks.length} checks passed</Tag>
      )}
      {url && <a href={url} target="_blank" rel="noreferrer">payment link</a>}
      {payloadText(p, "commitment_id") && <span className="muted">{payloadText(p, "commitment_id")}</span>}
    </div>
  );
}

function TimelineItem({ event }: { event: AuditEvent }) {
  const p = event.payload;
  const verbatim = payloadText(p, "verbatim");
  const text = payloadText(p, "text");
  const reason = payloadText(p, "reason");
  const proposed = payloadText(p, "proposed");
  const final = payloadText(p, "final");
  const intervention = payloadText(p, "intervention");
  const reasons = Array.isArray(p.reasons) ? (p.reasons as unknown[]).filter((r): r is string => typeof r === "string") : [];
  const commitment = COMMITMENT_KINDS.has(event.kind);
  return (
    <li>
      <span className={`kind ${event.kind}`}>{event.kind.replace(/_/g, " ")}</span>
      <span className="when">#{event.seq} · {when(event.at)} · {event.actor}</span>
      {event.kind === "intervention_decided" && (
        <div className="flow small">
          <b>{label(proposed)}</b> <span className="arrow">→</span> <b>{label(final)}</b>
          {p.modified === true && <Tag tone="warn">modified by policy</Tag>}
          {reasons.length > 0 && <span className="muted"> · {reasons.join("; ")}</span>}
        </div>
      )}
      {event.kind === "human_action" && (
        <div className="small">
          <b>{payloadText(p, "action") ?? "action"}</b> by {payloadText(p, "operator") ?? "operator"}
          {payloadText(p, "from_state") && <span className="muted"> · {payloadText(p, "from_state")} → {payloadText(p, "to_state")}</span>}
          {payloadText(p, "commitment_id") && <span className="muted"> · opened {payloadText(p, "commitment_id")}</span>}
          {payloadText(p, "notes") && <div className="muted">{payloadText(p, "notes")}</div>}
        </div>
      )}
      {intervention && event.kind === "message_sent" && (
        <div className="flow small">
          <Tag tone={intervention.startsWith("commitment_") ? "ok" : ""}>{label(intervention)}</Tag>
          {payloadText(p, "commitment_id") && <span className="muted">{payloadText(p, "commitment_id")}</span>}
        </div>
      )}
      {commitment && <CommitmentEvent event={event} />}
      {verbatim && <div className="verbatim">“{verbatim}”</div>}
      {text && <div className="verbatim">{text}</div>}
      {reason && <div className="muted">{reason}</div>}
      {!commitment && typeof p.amount === "number" && <div className="num-inline">{inr(p.amount)}</div>}
    </li>
  );
}

// -- detail panel -----------------------------------------------------------

export function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const detail = useLoad(() => api.invoice(id), [id]);
  return (
    <div className="detail" role="dialog" aria-label="invoice detail">
      <button className="close" onClick={onClose}>close</button>
      <Status load={detail}>
        {({ invoice, debtor, promises, concessions, payments, events, explain }) => {
          const chains = [...explain.commitments].sort((a, b) => b.created_at.localeCompare(a.created_at));
          return (
            <>
              <h1>{invoice.number}</h1>
              <p className="muted">
                {debtor.name} · {debtor.contact_name} · {debtor.preferred_channel} · {debtor.language}
              </p>
              <div className="facts">
                <StateChip state={invoice.state} />
                <span><span className="muted">amount</span> {inr(invoice.amount)}</span>
                <span><span className="muted">recovered</span> {inr(invoice.amount_paid)}</span>
                <span><span className="muted">waived</span> {inr(invoice.amount_waived)}</span>
                <span><span className="muted">balance</span> {inr(invoice.balance)}</span>
                <span><span className="muted">issued</span> {invoice.issued_on}</span>
                <span><span className="muted">due</span> {invoice.due_on}</span>
              </div>

              <WhyThisAction explain={explain} />

              {(explain.escalation || explain.dispute) && (
                <section>
                  <h2>Handed to a human</h2>
                  {explain.escalation && (
                    <p className="small">
                      <Tag tone="bad">escalated</Tag> {when(explain.escalation.at)}
                      {explain.escalation.reason && <> — {explain.escalation.reason}</>}
                    </p>
                  )}
                  {explain.dispute && (
                    <>
                      <p className="small">
                        <Tag tone="bad">disputed</Tag> {when(explain.dispute.at)}
                        {explain.dispute.reason && <> — {explain.dispute.reason}</>}
                      </p>
                      {explain.dispute.verbatim && <div className="verbatim">“{explain.dispute.verbatim}”</div>}
                    </>
                  )}
                </section>
              )}

              <section>
                <h2>
                  Commitments
                  {chains.length > 0 && <Tag>{chains.length}</Tag>}
                </h2>
                <p className="muted small">
                  Said → understood → allowed → instrument → rail → outcome. Each step points at the audit
                  event that backs it; a commitment only moves state when Razorpay's webhook says money arrived.
                </p>
                {chains.length === 0
                  ? <Empty>no commitment accepted on this invoice yet</Empty>
                  : chains.map((c) => <CommitmentIntegrity key={c.id} chain={c} />)}
              </section>

              {explain.blocked_commitments.length > 0 && (
                <section>
                  <h2>Blocked commitments <Tag tone="bad">{explain.blocked_commitments.length}</Tag></h2>
                  <BlockedCommitments rows={explain.blocked_commitments} />
                </section>
              )}

              <section>
                <h2>Promise history</h2>
                <PromiseHistory promises={promises} />
              </section>

              <section>
                <h2>Concessions</h2>
                <ConcessionTable concessions={concessions} />
              </section>

              <section>
                <h2>Payments observed on rails</h2>
                {payments.length === 0 ? <Empty>nothing observed yet</Empty> : (
                  <ul className="payments">
                    {payments.map((p) => (
                      <li key={p.id}>
                        <b className="num-inline">{inr(p.amount)}</b> · {p.method} · {when(p.observed_at)}{" "}
                        <MatchTag matchedBy={p.matched_by ?? null} />
                        {p.commitment_id && <Tag>{p.commitment_id}</Tag>}
                        <span className="muted small"> · {p.razorpay_payment_id}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section>
                <h2>Full audit timeline</h2>
                <ul className="timeline">
                  {events.map((event) => <TimelineItem key={event.seq} event={event} />)}
                </ul>
              </section>
            </>
          );
        }}
      </Status>
    </div>
  );
}

// -- ledger ---------------------------------------------------------------

export function PromiseTable({ promises }: { promises: Promise_[] }) {
  if (promises.length === 0) return <Empty>no promises recorded yet</Empty>;
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Invoice</th>
            <th>State</th>
            <th className="num">Amount</th>
            <th>Promised for</th>
            <th>Made</th>
            <th className="num">Confidence</th>
            <th>Their words</th>
          </tr>
        </thead>
        <tbody>
          {promises.map((promise) => (
            <tr key={promise.id}>
              <td>{promise.invoice_id}</td>
              <td><StateChip state={promise.state} /></td>
              <td className="num">{inr(promise.amount)}</td>
              <td>{promise.promised_on}</td>
              <td className="muted">{when(promise.made_at)}</td>
              <td className="num">{promise.confidence.toFixed(2)}</td>
              <td className="muted">“{promise.verbatim}”</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PromiseLedger() {
  const promises = useLoad(api.promises);
  const concessions = useLoad(api.concessions);
  const kept = promises.data?.filter((p) => p.state === "kept").length ?? 0;
  const broken = promises.data?.filter((p) => p.state === "broken").length ?? 0;
  const resolved = kept + broken;
  return (
    <>
      <section>
        <h2>
          Promises
          {promises.data && promises.data.length > 0 && (
            <Tag>{promises.data.length} · kept rate {pct(resolved ? kept / resolved : null, 0)}</Tag>
          )}
        </h2>
        <Status load={promises}>{(rows) => <PromiseTable promises={rows} />}</Status>
      </section>
      <section>
        <h2>Concessions</h2>
        <Status load={concessions}>{(rows) => <ConcessionTable concessions={rows} showInvoice />}</Status>
      </section>
    </>
  );
}

// -- commitments tab --------------------------------------------------------

const COMMITMENT_STATES = ["active", "partially_fulfilled", "fulfilled", "missed", "cancelled", "superseded"];

function excerpt(text: string, max = 90): string {
  const t = text.trim();
  return t.length <= max ? t : t.slice(0, max - 1).trimEnd() + "…";
}

export function CommitmentTable({ rows, onOpen }: { rows: Commitment[]; onOpen: (id: string) => void }) {
  if (rows.length === 0) return <Empty>no commitments in this state</Empty>;
  const sorted = [...rows].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Invoice</th>
            <th>Source</th>
            <th>State</th>
            <th className="num">Committed</th>
            <th className="num">Received</th>
            <th className="num">Remaining</th>
            <th>Due</th>
            <th className="num">Late</th>
            <th>Instrument</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((c) => (
            <tr key={c.id} className="row" onClick={() => onOpen(c.invoice_id)}>
              <td>{c.invoice_number ?? <span className="muted">{c.invoice_id}</span>}</td>
              <td>
                {c.source}
                {c.installment_index !== null && <span className="muted small"> #{c.installment_index}</span>}
              </td>
              <td><StateChip state={c.state} /></td>
              <td className="num">{inr(c.committed_amount)}</td>
              <td className="num">{inr(c.amount_received)}</td>
              <td className="num">{inr(c.amount_remaining ?? Math.max(0, c.committed_amount - c.amount_received))}</td>
              <td className="muted">{c.due_on}</td>
              <td className="num">{c.days_late > 0 ? <span className="neg">{c.days_late} d</span> : <span className="muted">—</span>}</td>
              <td className="small" onClick={(e) => e.stopPropagation()}>
                {c.payment_url
                  ? <a href={c.payment_url} target="_blank" rel="noreferrer">{c.instrument_sent ? "link · sent" : "link"}</a>
                  : <span className="muted">—</span>}
              </td>
              <td className="verbatim-cell" title={c.evidence}>{c.evidence ? `“${excerpt(c.evidence)}”` : <span className="muted">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CommitmentLedger({ onOpen }: { onOpen: (id: string) => void }) {
  const commitments = useLoad(api.commitments);
  const [filter, setFilter] = useState("all");
  const all = commitments.data ?? [];
  // Superseded rows were replaced by a newer arrangement; they are history, not outcomes.
  const counted = all.filter((c) => c.state !== "superseded");
  const fulfilled = counted.filter((c) => c.state === "fulfilled").length;
  const missed = counted.filter((c) => c.state === "missed").length;
  const resolved = fulfilled + missed;
  const shown = filter === "all" ? all : all.filter((c) => c.state === filter);
  return (
    <section>
      <h2>
        Commitments
        {all.length > 0 && (
          <>
            <Tag>{counted.length} created</Tag>
            <Tag tone="ok">{fulfilled} fulfilled</Tag>
            <Tag tone={missed > 0 ? "bad" : ""}>{missed} missed</Tag>
            <Tag>fulfilment rate {pct(resolved ? fulfilled / resolved : null, 0)}</Tag>
          </>
        )}
      </h2>
      <p className="muted small">
        What policy accepted from each promise: an exact amount, an exact deadline and a Razorpay Payment Link
        tagged with the commitment id. Click a row to open the invoice and its integrity chain.
      </p>
      <div className="toolbar">
        <label className="muted">
          State{" "}
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">all ({all.length})</option>
            {COMMITMENT_STATES.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")} ({all.filter((c) => c.state === s).length})
              </option>
            ))}
          </select>
        </label>
        <button onClick={commitments.reload}>refresh</button>
      </div>
      <Status load={commitments}>
        {(rows) => rows.length === 0
          ? <Empty>no commitments yet — a commitment is created only when policy accepts a promise</Empty>
          : <CommitmentTable rows={shown} onOpen={onOpen} />}
      </Status>
    </section>
  );
}
