/**
 * Commitment drawer: one commitment's full provenance, read from `GET /api/commitments/{id}` — which looks across
 * both ledgers and returns the row, its invoice and debtor, the chain (said → understood → allowed → instrument →
 * rail → outcome) and the audit-chain status of the ledger it lives in.
 */

import { ReactNode } from "react";
import {
  ChainStatus, CommitmentChain, CommitmentDetail, EventRef, MatchedBy, RailRow, api, inr, num, relativeDays, useLoad,
  when, whenIST,
} from "../api";
import {
  Checklist, Drawer, DrawerSection, EmptyState, Fact, IntegrityStep, ModeBadge, Pill, Ref, SourceBadge, Status,
  StatusBadge, StepStatus, stateLabel,
} from "../ui";
import { InstrumentAction, InstrumentFacts, deriveInstrument, factsFromChain } from "./InstrumentAction";

export function MatchTag({ matchedBy }: { matchedBy: MatchedBy | undefined }) {
  switch (matchedBy) {
    case "instrument": return <Pill tone="success" title="The payment arrived through the instrument tagged with this commitment id">exact · instrument</Pill>;
    case "instrument-late": return <Pill tone="warn" title="Arrived through the tagged instrument, after the deadline">exact · instrument, late</Pill>;
    case "invoice": return <Pill tone="neutral" title="Matched to the invoice, not to a specific instrument">matched by invoice</Pill>;
    default: return <Pill tone="danger">unmatched</Pill>;
  }
}

function RailPayment({ row }: { row: RailRow }) {
  if (row.payment_id) {
    return (
      <li>
        <div className="kv">
          <b className="money">{inr(row.amount ?? 0)}</b>
          <span>{row.method ?? "—"}</span>
          <span className="muted">{when(row.observed_at)}</span>
          <MatchTag matchedBy={row.matched_by} />
        </div>
        <div className="muted small mono">razorpay payment {row.razorpay_payment_id} · event {row.razorpay_event_id}</div>
      </li>
    );
  }
  return (
    <li>
      <div className="kv">
        <b>{stateLabel(row.outcome)}</b>
        {typeof row.amount_received === "number" && <span><span className="k">received</span>{inr(row.amount_received)}</span>}
        <MatchTag matchedBy={row.matched_by} />
        <Ref event={row.event} />
      </div>
    </li>
  );
}

/** Instrument facts for the chain, letting the row's explicit, persisted flags win over the chain's. */
function facts(d: CommitmentDetail): InstrumentFacts {
  const f = factsFromChain(d.chain);
  const row = d.commitment;
  f.instrument_mode = row.instrument_mode ?? f.instrument_mode;
  f.instrument_failed = (row.instrument_failed ?? false) || Boolean(f.instrument_failed);
  f.failure_reason = row.instrument_failure || f.failure_reason || null;
  return f;
}

function note(notes: Record<string, string> | string | null, key: string): string | null {
  if (!notes || typeof notes === "string") return null;
  return notes[key] ?? null;
}

function notesList(notes: Record<string, string> | string | null): ReactNode {
  if (!notes) return <span className="muted">—</span>;
  if (typeof notes === "string") return <span className="mono">{notes}</span>;
  const entries = Object.entries(notes);
  if (entries.length === 0) return <span className="muted">—</span>;
  return <span className="mono">{entries.map(([k, v]) => `${k}=${v}`).join(" · ")}</span>;
}

export function stepStatuses(chain: CommitmentChain, f: InstrumentFacts): Record<"said" | "understood" | "policy" | "instrument" | "money" | "outcome", StepStatus> {
  const terminalBad = chain.state === "missed" || chain.state === "cancelled";
  return {
    said: chain.said.verbatim || chain.said.event ? "done" : "pending",
    understood: chain.understood.event || chain.understood.intent ? "done" : "pending",
    policy: chain.policy.event || chain.policy.checks.length > 0 ? "done" : "pending",
    instrument: f.instrument_failed ? "failed" : chain.instrument.id || chain.instrument.url ? "done" : "pending",
    money: chain.rail.length > 0 ? "done" : terminalBad ? "failed" : "pending",
    outcome: chain.state === "fulfilled" ? "done" : terminalBad ? "failed"
      : chain.state === "partially_fulfilled" ? "warn" : "pending",
  };
}

/** The six-step provenance visual. */
export function CommitmentIntegrity({ chain, f, now }: { chain: CommitmentChain; f: InstrumentFacts; now?: number }) {
  const s = stepStatuses(chain, f);
  const u = chain.understood;
  const ins = chain.instrument;
  const out = chain.outcome;
  return (
    <ol className="integrity" aria-label="Commitment integrity">
      <IntegrityStep n={1} title="What the debtor said" status={s.said} event={chain.said.event}
        statusText={chain.said.at ? when(chain.said.at) : undefined}>
        {chain.said.verbatim
          ? <blockquote className="quote">“{chain.said.verbatim}”</blockquote>
          : <p className="quote muted">No debtor message — this commitment was opened without one.</p>}
        <div className="kv small">
          {chain.said.promise_id
            ? <span><span className="k">promise</span><span className="mono">{chain.said.promise_id}</span></span>
            : <span className="muted">no promise record</span>}
          {chain.said.promise_state && <StatusBadge state={chain.said.promise_state} />}
        </div>
      </IntegrityStep>

      <IntegrityStep n={2} title="What AI understood" status={s.understood} event={u.event}
        statusText={u.brain ? <>brain <b>{u.brain}</b></> : undefined}>
        <div className="facts">
          <Fact k="Intent" v={<b>{stateLabel(u.intent)}</b>} />
          <Fact k="Amount" v={u.amount !== null ? inr(u.amount) : "—"} money={u.amount !== null} />
          <Fact k="Pay by" v={u.on ?? "—"} />
          <Fact k="Confidence" v={num(u.confidence, 2)} />
        </div>
        <div className="kv small">
          {u.partial && <Pill tone="warn" title="The debtor offered part of the balance">partial</Pill>}
          {u.flags.map((flag) => <Pill key={flag} tone="outline">{flag}</Pill>)}
        </div>
      </IntegrityStep>

      <IntegrityStep n={3} title="What policy accepted" status={s.policy} event={chain.policy.event}>
        <Checklist checks={chain.policy.checks} />
        <div className="decision-line">
          <span className="checklist"><span className="mark pos">✓</span></span>
          <b>Accepted</b>
          {chain.policy.reason && <span className="muted">— {chain.policy.reason}</span>}
        </div>
      </IntegrityStep>

      <IntegrityStep n={4} title="What payment instrument was issued" status={s.instrument} event={ins.event}
        statusText={f.instrument_mode ? <ModeBadge mode={f.instrument_mode} /> : undefined}>
        {s.instrument === "failed" ? (
          <>
            <InstrumentAction facts={f} now={now} />
            <p className="muted small">Nothing was issued: the payment rail refused the request. The commitment still stands and is judged by the calendar.</p>
          </>
        ) : !ins.id && !ins.url ? (
          <p className="muted small">No payment instrument issued for this commitment.</p>
        ) : (
          <>
            <div className="facts">
              <Fact k="Type" v={stateLabel(ins.type)} />
              <Fact k="Payment Link ID" v={<span className="mono">{ins.id ?? "—"}</span>} />
              <Fact k="reference_id" v={<span className="mono">{ins.reference_id ?? "—"}</span>} />
              <Fact k="Amount" v={inr(ins.amount)} money />
              <Fact k="Expires" v={whenIST(ins.expires)} />
              <Fact k="Notes" v={notesList(ins.notes)} />
            </div>
            <div className="kv">
              <InstrumentAction facts={f} now={now} />
              {ins.sent
                ? <Pill tone="success" title="Confirmation message went to the debtor">sent to debtor{ins.confirmation ? ` · #${ins.confirmation.seq}` : ""}</Pill>
                : <Pill tone="warn">not yet sent</Pill>}
            </div>
          </>
        )}
      </IntegrityStep>

      <IntegrityStep n={5} title="What money arrived" status={s.money}
        statusText={chain.rail.length > 0 ? `${chain.rail.length} rail event${chain.rail.length === 1 ? "" : "s"}` : s.money === "failed" ? "nothing before the deadline" : "nothing observed yet"}>
        {chain.rail.length === 0
          ? <p className="muted small">Only a Razorpay webhook can move this step; the ledger never assumes payment.</p>
          : <ul className="payments">{chain.rail.map((r, i) => <RailPayment key={r.payment_id ?? r.event?.seq ?? i} row={r} />)}</ul>}
      </IntegrityStep>

      <IntegrityStep n={6} title="Final outcome" status={s.outcome} event={out.event ?? out.created_event}>
        <div className="facts">
          <Fact k="Status" v={<StatusBadge state={out.state} />} />
          <Fact k="Received" v={inr(chain.amount_received)} money />
          <Fact k="Remaining" v={inr(chain.amount_remaining)} money />
          <Fact k="Days late" v={chain.days_late > 0 ? <span className="neg">{chain.days_late} d</span> : "0"} />
          {chain.fulfilled_at && <Fact k="Fulfilled at" v={whenIST(chain.fulfilled_at)} />}
          {chain.missed_at && <Fact k="Missed at" v={whenIST(chain.missed_at)} />}
          {out.promise_state && <Fact k="Promise" v={<StatusBadge state={out.promise_state} />} />}
        </div>
        {chain.cancel_reason && <p className="small">Cancelled: {chain.cancel_reason}</p>}
        {out.event === null && out.created_event && (
          <p className="muted small">Still open — created at #{out.created_event.seq}; due {relativeDays(chain.due_on)}.</p>
        )}
      </IntegrityStep>
    </ol>
  );
}

function AuditRefs({ chain, chainStatus }: { chain: CommitmentChain; chainStatus: ChainStatus | null }) {
  const refs: { label: string; event: EventRef | null | undefined }[] = [
    { label: "Debtor message", event: chain.said.event },
    { label: "Interpretation", event: chain.understood.event },
    { label: "Policy approval", event: chain.policy.event },
    { label: "Instrument issued", event: chain.instrument.event },
    { label: "Confirmation sent", event: chain.instrument.confirmation },
    { label: "Commitment created", event: chain.outcome.created_event },
    { label: "Outcome", event: chain.outcome.event },
  ];
  return (
    <>
      <div className="kv small" style={{ marginBottom: 10 }}>
        {chainStatus === null ? <span className="muted">chain status unavailable</span>
          : chainStatus.verified
            ? <Pill tone="success">Audit chain verified{typeof chainStatus.events === "number" ? ` · ${num(chainStatus.events)} events` : ""}</Pill>
            : <Pill tone="danger">Audit chain broken{chainStatus.error ? ` — ${chainStatus.error}` : ""}</Pill>}
      </div>
      <ul className="checklist">
        {refs.map((r) => (
          <li key={r.label} className={r.event ? "ok" : ""}>
            <span className="mark" aria-hidden="true">{r.event ? "•" : "·"}</span>
            <span className="gate">{r.label}</span>
            <span className="why">
              <Ref event={r.event} />{r.event && <> {r.event.kind.replace(/_/g, " ")} · {when(r.event.at)}</>}
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

/** Razorpay's view of the instrument next to Urudhi's view of the commitment, field by field. */
function Mapping({ d }: { d: CommitmentDetail }) {
  const ins = d.chain.instrument;
  const c = d.commitment;
  const pair = (k: string, v: ReactNode, text = false) => (
    <div className="kvp"><span className="k">{k}</span><span className={`v ${text ? "text" : ""}`}>{v}</span></div>
  );
  const dash = <span className="muted">—</span>;
  return (
    <div className="mapping" role="table" aria-label="Razorpay to Urudhi mapping">
      <div className="col" role="rowgroup">
        <h4>Razorpay</h4>
        {pair("Payment Link", ins.id ?? dash)}
        {pair("Amount", inr(ins.amount))}
        {pair("Reference ID", ins.reference_id ?? dash)}
        {pair("notes.commitment_id", note(ins.notes, "commitment_id") ?? dash)}
        {pair("notes.invoice_id", note(ins.notes, "invoice_id") ?? dash)}
      </div>
      <div className="link" aria-hidden="true">⇄</div>
      <div className="col" role="rowgroup">
        <h4>Urudhi</h4>
        {pair("Commitment", c.id)}
        {pair("Committed", inr(c.committed_amount))}
        {pair("Invoice", <>{d.invoice.number} <span className="muted">{d.invoice.id}</span></>, true)}
        {pair("Debtor", d.debtor.name, true)}
        {pair("Ledger", <SourceBadge source={d.source} />, true)}
      </div>
    </div>
  );
}

function urlStatus(f: InstrumentFacts, now?: number): { label: string; tone: "success" | "warn" | "neutral" | "info" | "danger" } {
  const v = deriveInstrument(f, now);
  switch (v.kind) {
    case "live": return { label: "live", tone: "success" };
    case "expired": return { label: "expired", tone: "warn" };
    case "paid": return { label: "paid", tone: "success" };
    case "missed": return { label: v.expiredUrl ? "expired" : "none", tone: "danger" };
    case "sandbox": return { label: "sandbox — no checkout", tone: "info" };
    case "failed": return { label: "none — rail refused", tone: "danger" };
    case "not_issued": return { label: "none", tone: "neutral" };
    default: return { label: v.kind, tone: "neutral" };
  }
}

/** The drawer body given loaded data; exported so tests can render it without the network. */
export function CommitmentDrawerBody({ detail, now, onOpenInvoice }: {
  detail: CommitmentDetail; now?: number; onOpenInvoice?: (invoiceId: string) => void;
}) {
  const d = detail;
  const chain = d.chain;
  const c = d.commitment;
  const u = chain.understood;
  const ins = chain.instrument;
  const f = facts(d);
  const rail = f.instrument_mode ?? c.rail ?? null;
  const status = urlStatus(f, now);
  const pending = (chain.state === "active" || chain.state === "partially_fulfilled") && chain.amount_received === 0;
  const fulfilment = chain.committed_amount > 0 ? chain.amount_received / chain.committed_amount : null;
  return (
    <>
      <div className="provenance-strip" aria-label="Provenance">
        <SourceBadge source={d.source} />
        {rail ? <ModeBadge mode={rail} /> : <Pill tone="neutral">no rail</Pill>}
        <span>brain <b>{u.brain ?? "—"}</b></span>
        <span>debtor <b>{d.debtor.name}</b> · {d.debtor.phone} · {d.debtor.email}</span>
      </div>

      <DrawerSection title="Promise" badge={chain.said.promise_state ? <StatusBadge state={chain.said.promise_state} /> : undefined}>
        {chain.said.verbatim
          ? <blockquote className="quote">“{chain.said.verbatim}”</blockquote>
          : <p className="quote muted">No debtor message — this commitment was opened without one.</p>}
        <div className="kv small" style={{ marginTop: 8 }}>
          <span><span className="k">promise</span><span className="mono">{chain.said.promise_id ?? "—"}</span></span>
          <span><span className="k">said</span>{when(chain.said.at)}</span>
          <Ref event={chain.said.event} />
        </div>
      </DrawerSection>

      <DrawerSection title="AI interpretation" badge={<Pill tone="outline">brain {u.brain ?? "—"}</Pill>}>
        <div className="facts">
          <Fact k="Intent" v={<b>{stateLabel(u.intent)}</b>} />
          <Fact k="Amount" v={u.amount !== null ? inr(u.amount) : "—"} money={u.amount !== null} />
          <Fact k="Date" v={u.on ?? "—"} />
          <Fact k="Confidence" v={num(u.confidence, 2)} />
          <Fact k="Brain" v={u.brain ?? "—"} />
          <Fact k="Audit" v={<Ref event={u.event} />} />
        </div>
        {(u.partial || u.flags.length > 0) && (
          <div className="kv small" style={{ marginTop: 8 }}>
            {u.partial && <Pill tone="warn" title="The debtor offered part of the balance">partial</Pill>}
            {u.flags.map((flag) => <Pill key={flag} tone="outline">{flag}</Pill>)}
          </div>
        )}
      </DrawerSection>

      <DrawerSection title="Policy" badge={<Pill tone="success">{chain.policy.checks.filter((k) => k.allowed).length}/{chain.policy.checks.length} checks passed</Pill>}>
        <Checklist checks={chain.policy.checks} />
        <div className="decision-line">
          <span className="checklist"><span className="mark pos">✓</span></span>
          <b>Accepted</b>
          {chain.policy.reason && <span className="muted">— {chain.policy.reason}</span>}
          <Ref event={chain.policy.event} />
        </div>
      </DrawerSection>

      <DrawerSection title="Commitment" badge={<StatusBadge state={chain.state} />}>
        <div className="facts">
          <Fact k="Commitment ID" v={<span className="mono">{c.id}</span>} />
          <Fact k="Amount" v={inr(chain.committed_amount)} money />
          <Fact k="Deadline" v={<>{whenIST(chain.due_at)}<span className="secondary muted small" style={{ display: "block" }}>{relativeDays(chain.due_on)}</span></>} />
          <Fact k="Status" v={<StatusBadge state={chain.state} />} />
          <Fact k="Source" v={<SourceBadge source={d.source} />} />
          <Fact k="Opened by" v={<>{chain.source}{chain.installment_index !== null ? ` · installment #${chain.installment_index}` : ""}</>} />
          <Fact k="Invoice" v={onOpenInvoice
            ? <button type="button" className="btn sm" onClick={() => onOpenInvoice(chain.invoice_id)}>View invoice {d.invoice.number}</button>
            : d.invoice.number} />
          <Fact k="Debtor" v={d.debtor.name} />
          <Fact k="Created" v={whenIST(chain.created_at)} />
        </div>
        {chain.cancel_reason && <p className="small muted" style={{ marginTop: 8 }}>{chain.cancel_reason}</p>}
      </DrawerSection>

      <DrawerSection title="Payment instrument" badge={rail ? <ModeBadge mode={rail} /> : undefined}>
        <div className="facts">
          <Fact k="Source" v={<SourceBadge source={d.source} />} />
          <Fact k="Rail" v={rail === "razorpay_test" ? "Razorpay Test Mode" : rail === "sandbox" ? "Sandbox" : "—"} />
          <Fact k="Type" v={stateLabel(ins.type)} />
          <Fact k="Payment Link ID" v={<span className="mono">{ins.id ?? "—"}</span>} />
          <Fact k="Reference ID" v={<span className="mono">{ins.reference_id ?? c.id}</span>} title="Razorpay reference_id = the commitment id" />
          <Fact k="notes.invoice_id" v={<span className="mono">{note(ins.notes, "invoice_id") ?? "—"}</span>} />
          <Fact k="notes.commitment_id" v={<span className="mono">{note(ins.notes, "commitment_id") ?? "—"}</span>} />
          <Fact k="URL status" v={<Pill tone={status.tone}>{status.label}</Pill>} />
          <Fact k="Mode" v={<span className="mono">{f.instrument_mode ?? "—"}</span>} />
          <Fact k="Payment status" v={pending ? <Pill tone="warn">Payment pending</Pill> : <StatusBadge state={chain.state} />} />
        </div>
        <div className="kv" style={{ marginTop: 12 }}>
          <InstrumentAction facts={f} now={now} />
          {(ins.id || ins.url) && (ins.sent
            ? <Pill tone="success" title="Confirmation message went to the debtor">sent to debtor{ins.confirmation ? ` · #${ins.confirmation.seq}` : ""}</Pill>
            : <Pill tone="warn">not yet sent</Pill>)}
        </div>
        {f.instrument_failed && f.failure_reason && <p className="neg small" style={{ marginTop: 8 }}>{f.failure_reason}</p>}
      </DrawerSection>

      <DrawerSection title="Razorpay ↔ Urudhi mapping">
        <Mapping d={d} />
      </DrawerSection>

      <DrawerSection title="Outcome" badge={<StatusBadge state={chain.outcome.state} />}>
        <div className="facts">
          <Fact k="Received" v={inr(chain.amount_received)} money />
          <Fact k="Remaining" v={inr(chain.amount_remaining)} money />
          <Fact k="Fulfilment" v={fulfilment === null ? "—" : `${(fulfilment * 100).toFixed(0)}%`} />
          <Fact k="Days late" v={chain.days_late > 0 ? <span className="neg">{chain.days_late} d</span> : "0"} />
          {chain.outcome.promise_state && <Fact k="Promise state" v={<StatusBadge state={chain.outcome.promise_state} />} />}
          {chain.fulfilled_at && <Fact k="Fulfilled at" v={whenIST(chain.fulfilled_at)} />}
          {chain.missed_at && <Fact k="Missed at" v={whenIST(chain.missed_at)} />}
        </div>
        {pending && <p className="muted small" style={{ marginTop: 8 }}>Payment pending — the commitment is open and the rails have reported nothing yet.</p>}
        {chain.rail.length > 0 && <ul className="payments" style={{ marginTop: 10 }}>{chain.rail.map((r, i) => <RailPayment key={r.payment_id ?? r.event?.seq ?? i} row={r} />)}</ul>}
      </DrawerSection>

      <DrawerSection title="Commitment integrity" badge={<span className="muted small">said → understood → accepted → instrument → money → outcome</span>}>
        <CommitmentIntegrity chain={chain} f={f} now={now} />
      </DrawerSection>

      <DrawerSection title="Integrity" badge={d.audit_chain.verified
        ? <Pill tone="success">verified · {num(d.audit_chain.events ?? 0)} events</Pill>
        : <Pill tone="danger">chain broken</Pill>}>
        <AuditRefs chain={chain} chainStatus={d.audit_chain} />
      </DrawerSection>
    </>
  );
}

/** Opens by commitment id from either ledger; the Drawer stays mounted throughout so focus and scroll are kept. */
export function CommitmentDrawer({ id, onClose, onOpenInvoice }: {
  id: string; onClose: () => void; onOpenInvoice: (invoiceId: string) => void;
}) {
  const detail = useLoad(() => api.commitment(id), [id]);
  const d = detail.data;
  return (
    <Drawer
      eyebrow={<>Commitment · <span className="mono">{id}</span>{d && <> · <SourceBadge source={d.source} /></>}</>}
      title={d ? <><span className="money">{inr(d.chain.committed_amount)}</span><StatusBadge state={d.chain.state} /></> : "Commitment"}
      onClose={onClose}
    >
      <Status load={detail} rows={8}
        notFound={<EmptyState title="No matching commitment in current data source" hint={<>No commitment with id <code>{id}</code> in either ledger.</>} />}>
        {(data) => <CommitmentDrawerBody detail={data} onOpenInvoice={onOpenInvoice} />}
      </Status>
    </Drawer>
  );
}
