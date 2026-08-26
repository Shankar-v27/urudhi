/** Invoices (table + explainable detail panel) and the promise / concession ledger. */

import { useMemo, useState } from "react";
import {
  AuditEvent, Concession, Explain, Invoice, Promise_, api, inr, num, pct, useLoad, when,
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

function payloadText(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function TimelineItem({ event }: { event: AuditEvent }) {
  const p = event.payload;
  const verbatim = payloadText(p, "verbatim");
  const text = payloadText(p, "text");
  const reason = payloadText(p, "reason");
  const proposed = payloadText(p, "proposed");
  const final = payloadText(p, "final");
  const reasons = Array.isArray(p.reasons) ? (p.reasons as unknown[]).filter((r): r is string => typeof r === "string") : [];
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
          {payloadText(p, "notes") && <div className="muted">{payloadText(p, "notes")}</div>}
        </div>
      )}
      {verbatim && <div className="verbatim">“{verbatim}”</div>}
      {text && <div className="verbatim">{text}</div>}
      {reason && <div className="muted">{reason}</div>}
      {typeof p.amount === "number" && <div className="num-inline">{inr(p.amount)}</div>}
    </li>
  );
}

export function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const detail = useLoad(() => api.invoice(id), [id]);
  return (
    <div className="detail" role="dialog" aria-label="invoice detail">
      <button className="close" onClick={onClose}>close</button>
      <Status load={detail}>
        {({ invoice, debtor, promises, concessions, payments, events, explain }) => (
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
                      <b className="num-inline">{inr(p.amount)}</b> · {p.method} · {when(p.observed_at)}
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
        )}
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
