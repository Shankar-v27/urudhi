/**
 * Commitment drawer: the provenance chain for one commitment, read from `/api/invoices/{id}`
 * (`explain.commitments[]` has every step; `commitments[]` carries the instrument mode flags).
 */

import { ReactNode } from "react";
import {
  Commitment, CommitmentChain, EventRef, Health, InvoiceDetail, MatchedBy, RailRow, api, inr, num,
  relativeDays, useLoad, when, whenIST,
} from "../api";
import {
  Checklist, Drawer, DrawerSection, EmptyState, Fact, IntegrityStep, ModeBadge, Pill, Ref, Skeleton, Status,
  StatusBadge, StepStatus, stateLabel,
} from "../ui";
import { InstrumentAction, InstrumentFacts, factsFromChain } from "./InstrumentAction";

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
        <div className="muted small mono">{row.razorpay_payment_id} · event {row.razorpay_event_id}</div>
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

/** Instrument facts for the chain, preferring the row flags (they carry `instrument_failed`). */
function facts(chain: CommitmentChain, row: Commitment | undefined): InstrumentFacts {
  const f = factsFromChain(chain);
  if (row) {
    f.instrument_mode = row.instrument_mode ?? f.instrument_mode;
    f.instrument_failed = (row.instrument_failed ?? false) || Boolean(f.instrument_failed);
  }
  return f;
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

export function CommitmentIntegrity({ chain, row, now }: { chain: CommitmentChain; row?: Commitment; now?: number }) {
  const f = facts(chain, row);
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
        statusText={ins.mode ? <ModeBadge mode={ins.mode} /> : undefined}>
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

function AuditRefs({ chain, chainStatus }: { chain: CommitmentChain; chainStatus: Health["audit_chain"] | null }) {
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

/** The drawer body given loaded data; exported so tests can render it without the network. */
export function CommitmentDrawerBody({ detail, id, chainStatus, now, onOpenInvoice }: {
  detail: InvoiceDetail; id: string; chainStatus: Health["audit_chain"] | null; now?: number;
  onOpenInvoice?: (invoiceId: string) => void;
}) {
  const chain = detail.explain.commitments.find((c) => c.id === id);
  const row = detail.commitments.find((c) => c.id === id);
  if (!chain) {
    return <EmptyState title="Commitment not found" hint={<>No commitment <code>{id}</code> on invoice {detail.invoice.number}.</>} />;
  }
  const f = facts(chain, row);
  return (
    <>
      <DrawerSection title="Commitment" badge={<StatusBadge state={chain.state} />}>
        <div className="facts">
          <Fact k="Amount" v={inr(chain.committed_amount)} money />
          <Fact k="Deadline" v={<>{whenIST(chain.due_at)}<span className="secondary muted small" style={{ display: "block" }}>{relativeDays(chain.due_on)}</span></>} />
          <Fact k="Source" v={<>{chain.source}{chain.installment_index !== null ? ` · installment #${chain.installment_index}` : ""}</>} />
          <Fact k="Invoice" v={onOpenInvoice
            ? <button type="button" className="btn sm" onClick={() => onOpenInvoice(chain.invoice_id)}>View invoice {detail.invoice.number}</button>
            : detail.invoice.number} />
          <Fact k="Debtor" v={detail.debtor.name} />
          <Fact k="Created" v={whenIST(chain.created_at)} />
        </div>
        <div className="kv" style={{ marginTop: 12 }}>
          <InstrumentAction facts={f} now={now} />
        </div>
      </DrawerSection>

      <DrawerSection title="Commitment integrity" badge={<span className="muted small">said → understood → accepted → instrument → money → outcome</span>}>
        <CommitmentIntegrity chain={chain} row={row} now={now} />
      </DrawerSection>

      <DrawerSection title="Integrity — audit references">
        <AuditRefs chain={chain} chainStatus={chainStatus} />
      </DrawerSection>
    </>
  );
}

/**
 * Opens by commitment id. When the invoice id is not known (deep link) it is resolved from /api/commitments
 * first; the Drawer itself stays mounted throughout so focus and scroll position are not reset.
 */
export function CommitmentDrawer({ id, invoiceId, onClose, onOpenInvoice }: {
  id: string; invoiceId?: string | null; onClose: () => void; onOpenInvoice: (invoiceId: string) => void;
}) {
  const rows = useLoad(() => (invoiceId ? Promise.resolve<Commitment[] | null>(null) : api.commitments()), [invoiceId]);
  const resolved = invoiceId ?? rows.data?.find((c) => c.id === id)?.invoice_id ?? null;
  // Until the invoice id is known the loader never settles, which keeps `detail` in its loading state.
  const detail = useLoad(() => (resolved ? api.invoice(resolved) : new Promise<InvoiceDetail>(() => {})), [resolved]);
  const health = useLoad(api.health);
  const chain = detail.data?.explain.commitments.find((c) => c.id === id) ?? null;
  const unresolved = !resolved && rows.data !== null && !rows.error;
  return (
    <Drawer
      eyebrow={<>Commitment · <span className="mono">{id}</span></>}
      title={chain ? <><span className="money">{inr(chain.committed_amount)}</span><StatusBadge state={chain.state} /></> : "Commitment"}
      onClose={onClose}
    >
      {rows.error ? <Status load={rows}>{() => null}</Status>
        : unresolved ? <EmptyState title="Commitment not found" hint={<>No commitment with id <code>{id}</code>.</>} />
        : !resolved ? <Skeleton rows={8} />
        : (
          <Status load={detail} rows={8}>
            {(d) => <CommitmentDrawerBody detail={d} id={id} chainStatus={health.data?.audit_chain ?? null} onOpenInvoice={onOpenInvoice} />}
          </Status>
        )}
    </Drawer>
  );
}
