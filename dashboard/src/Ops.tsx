/** Operator views: the escalation queue with the human-resolution panel, and the reply-interpretation eval. */

import { useMemo, useState } from "react";
import {
  ApiError, Escalation, EvalFailure, EvalSummary, HumanRequest, Invoice, Loaded, OPERATOR_KEY, ReplyEval, Summary, api,
  inr, inrShort, num, pct, relativeDays, storageGet, storageSet, useLoad, when,
} from "./api";
import { useSource } from "./source";
import {
  BarGroup, COLORS, Card, Drawer, DrawerSection, EmptyState, Fact, HBars, MetricCard, ModeBadge, Pill, SectionHeader,
  SourceBadge, Status, StatusBadge, TableWrap, stateLabel,
} from "./ui";

// -- escalations --------------------------------------------------------------

type Outcome = { ok: boolean; text: string };
type ActBody = Omit<HumanRequest, "operator">;

/** Rupees typed by an operator → integer paise, or null when it is not a positive amount. */
function toPaise(rupees: string): number | null {
  const value = Number(rupees.replace(/,/g, ""));
  if (!Number.isFinite(value) || value <= 0) return null;
  const paise = Math.round(value * 100);
  return paise > 0 ? paise : null;
}

function credibilityTone(value: number): "success" | "warn" | "danger" {
  return value >= 0.6 ? "success" : value < 0.4 ? "danger" : "warn";
}

export function isBrokenPromise(row: Escalation): boolean {
  return /broken promise/i.test(row.reason ?? "");
}
export function isAttemptsExhausted(row: Escalation): boolean {
  return /attempts/i.test(row.reason ?? "");
}

function owner(row: Escalation): string | null {
  const ack = [...row.human_actions].reverse().find((h) => h.action === "acknowledge");
  return ack?.operator ?? (row.acknowledged ? "acknowledged" : null);
}

function ResolutionDrawer({ row, debtor, operator, busy, outcome, onAct, onClose }: {
  row: Escalation; debtor: string | null; operator: string; busy: boolean; outcome?: Outcome;
  onAct: (id: string, body: ActBody) => void; onClose: () => void;
}) {
  const [notes, setNotes] = useState("");
  const [rupees, setRupees] = useState("");
  const [dueOn, setDueOn] = useState("");
  const [confirmClose, setConfirmClose] = useState(false);
  const paise = toPaise(rupees);
  const hasNote = Boolean(notes.trim());
  const arrangeReady = Boolean(operator) && hasNote && paise !== null && /^\d{4}-\d{2}-\d{2}$/.test(dueOn);
  const arrangeHint = !operator ? "set an operator name first" : !hasNote ? "say what was agreed in the notes"
    : paise === null ? "amount must be a positive rupee value" : !dueOn ? "pick the deadline" : "";
  const last = row.last_commitment;
  const disabled = busy || !operator;
  return (
    <Drawer narrow eyebrow={<>Escalation · {row.number} · <SourceBadge source={row.source} /></>} onClose={onClose}
      title={<>{debtor ?? row.number}<StatusBadge state={row.state} /></>}>
      <DrawerSection title="Position">
        <div className="facts">
          <Fact k="Balance" v={inr(row.balance)} money />
          <Fact k="Paid so far" v={inr(row.amount_paid)} />
          <Fact k="Waiting since" v={when(row.since)} />
          <Fact k="Credibility" v={<Pill tone={credibilityTone(row.credibility)}>{num(row.credibility, 2)}</Pill>} />
          <Fact k="Recommended" v={<Pill tone="warn">{row.recommended_action}</Pill>} />
          <Fact k="Owner" v={owner(row) ?? <span className="muted">Unclaimed</span>} />
        </div>
        {row.reason && <p className="small" style={{ marginTop: 10 }}><b>Reason:</b> {row.reason}</p>}
        {row.verbatim && <blockquote className="quote" style={{ marginTop: 8 }}>“{row.verbatim}”</blockquote>}
      </DrawerSection>

      <DrawerSection title="Promise & commitment history">
        <div className="kv" style={{ marginBottom: 8 }}>
          <span><span className="k">fulfilled</span>{num(row.commitments_fulfilled)}</span>
          <span><span className="k">missed</span><span className={row.commitments_missed > 0 ? "neg" : ""}>{num(row.commitments_missed)}</span></span>
        </div>
        {last ? (
          <div className="payments"><div className="kv" style={{ padding: "8px 10px", border: "1px solid var(--border)", borderRadius: 8 }}>
            <StatusBadge state={last.state} />
            <b className="money">{inr(last.committed_amount)}</b>
            <span><span className="k">due</span>{last.due_on} <span className="muted">({relativeDays(last.due_on)})</span></span>
            <span><span className="k">received</span>{inr(last.amount_received)}</span>
            <span className="muted mono small">{last.id}</span>
          </div></div>
        ) : <p className="muted small">No commitment has been accepted on this invoice.</p>}
        {last?.evidence && <blockquote className="quote muted" style={{ marginTop: 8 }}>“{last.evidence}”</blockquote>}
        {row.human_actions.length > 0 && (
          <ul className="history" style={{ marginTop: 10 }}>
            {row.human_actions.map((h, i) => (
              <li key={i}>
                <b>{h.action}</b> · {h.operator} · {when(h.at)}
                {h.from_state !== h.to_state && <span className="muted"> · {h.from_state} → {h.to_state}</span>}
                {h.commitment_id && <span className="muted"> · opened {h.commitment_id}</span>}
                {h.notes && <div className="muted">{h.notes}</div>}
              </li>
            ))}
          </ul>
        )}
      </DrawerSection>

      <DrawerSection title="Resolve">
        {!operator && <div className="note warn" style={{ marginBottom: 10 }}>Set your operator name in the toolbar before acting; every action is written to the audit chain under that name.</div>}
        <div className="actions-grid">
          <label className="field">
            Notes (required for note, arrange, release and close)
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} maxLength={2000} placeholder="What you did or agreed" />
          </label>
          <div className="row">
            <button type="button" className="btn" disabled={disabled} onClick={() => onAct(row.invoice_id, { action: "acknowledge", notes })}>Acknowledge</button>
            <button type="button" className="btn" disabled={disabled || !hasNote} title={!hasNote ? "needs a note" : ""} onClick={() => onAct(row.invoice_id, { action: "note", notes })}>Add note</button>
            <button type="button" className="btn" disabled={disabled || !hasNote} title={!hasNote ? "needs a note" : ""} onClick={() => onAct(row.invoice_id, { action: "release", notes })}>Release to automation</button>
            {busy && <span className="muted small">working…</span>}
          </div>
          <div style={{ borderTop: "1px dashed var(--border)", paddingTop: 12 }}>
            <h4 style={{ marginBottom: 6 }}>Arrange</h4>
            <p className="muted small" style={{ marginBottom: 8 }}>Policy checks the arrangement, opens a commitment with a tagged payment link, and releases the invoice.</p>
            <div className="row">
              <label className="field">Amount (₹)
                <input inputMode="decimal" placeholder="e.g. 25000" value={rupees} onChange={(e) => setRupees(e.target.value)} aria-label="Arrangement amount in rupees" style={{ width: 150 }} />
              </label>
              <label className="field">Due
                <input type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} aria-label="Arrangement due date" />
              </label>
              <button type="button" className="btn primary" disabled={busy || !arrangeReady} title={arrangeHint}
                onClick={() => paise !== null && onAct(row.invoice_id, { action: "arrange", notes, amount: paise, due_on: dueOn })}>
                Approve arrangement{paise !== null ? ` · ${inr(paise)}` : ""}
              </button>
            </div>
          </div>
          <div style={{ borderTop: "1px dashed var(--border)", paddingTop: 12 }}>
            {!confirmClose ? (
              <button type="button" className="btn danger" disabled={disabled || !hasNote} title={!hasNote ? "needs a note" : ""} onClick={() => setConfirmClose(true)}>Close invoice…</button>
            ) : (
              <div className="confirm" role="alertdialog" aria-label="Confirm close">
                <b>Close {row.number} with {inr(row.balance)} still outstanding?</b>
                <span>This is final: the invoice leaves the queue and automation will not contact the debtor again. Your note is recorded with it.</span>
                <div className="row">
                  <button type="button" className="btn danger solid" disabled={busy} onClick={() => { setConfirmClose(false); onAct(row.invoice_id, { action: "close", notes }); }}>Confirm close</button>
                  <button type="button" className="btn" onClick={() => setConfirmClose(false)}>Keep open</button>
                </div>
              </div>
            )}
          </div>
          {outcome && <div className={`note ${outcome.ok ? "ok" : "bad"}`} role="status">{outcome.text}</div>}
        </div>
      </DrawerSection>
    </Drawer>
  );
}

export function Escalations({ invoices, summary, selectedId, onSelect }: {
  invoices: Loaded<Invoice[]>; summary: Loaded<Summary>; selectedId: string | null; onSelect: (id: string | null) => void;
}) {
  const { source } = useSource();
  const queue = useLoad(() => api.escalations(source), [source]);
  const [operator, setOperatorState] = useState(() => storageGet(OPERATOR_KEY));
  const [busy, setBusy] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const [kind, setKind] = useState<"all" | "disputed" | "broken" | "attempts">("all");

  // Rows carry `debtor_name`; the invoice list is only a fallback for older API builds.
  const debtors = useMemo(() => {
    const m: Record<string, string> = {};
    for (const i of invoices.data ?? []) if (i.debtor_name) m[i.id] = i.debtor_name;
    for (const r of queue.data ?? []) if (r.debtor_name) m[r.invoice_id] = r.debtor_name;
    return m;
  }, [invoices.data, queue.data]);

  const setOperator = (value: string) => {
    setOperatorState(value);
    storageSet(OPERATOR_KEY, value.trim());
  };

  const act = async (id: string, body: ActBody) => {
    setBusy(id);
    try {
      const result = await api.human(id, { ...body, operator: operator.trim() });
      const moved = result.from_state !== result.to_state ? ` (${result.from_state} → ${result.to_state})` : "";
      const opened = result.commitment_id ? ` · commitment ${result.commitment_id} opened` : "";
      setOutcomes((o) => ({ ...o, [id]: { ok: true, text: `${result.action} recorded by ${result.operator}${moved}${opened}` } }));
      queue.reload();
    } catch (failure) {
      const text = failure instanceof ApiError
        ? `${failure.status}${failure.status === 409 ? " — policy refused" : ""}: ${failure.detail}`
        : failure instanceof Error ? failure.message : String(failure);
      setOutcomes((o) => ({ ...o, [id]: { ok: false, text } }));
    } finally {
      setBusy(null);
    }
  };

  const rows = queue.data ?? [];
  const disputes = rows.filter((r) => r.state === "disputed").length;
  const broken = rows.filter(isBrokenPromise).length;
  const exhausted = rows.filter(isAttemptsExhausted).length;
  const total = rows.reduce((s, r) => s + r.balance, 0);
  const shown = rows.filter((r) => kind === "all" || (kind === "disputed" ? r.state === "disputed" : kind === "broken" ? isBrokenPromise(r) : isAttemptsExhausted(r)));
  const selected = selectedId ? rows.find((r) => r.invoice_id === selectedId) ?? null : null;

  return (
    <>
      <div className="page-title"><h1>Escalations</h1>{queue.data && <Pill tone="outline">{num(rows.length)} waiting on a human</Pill>}</div>
      <p className="page-desc">Invoices automation has handed over. Every action here is written to the audit chain under your operator name.</p>
      <div className="kpis" style={{ marginBottom: 16 }}>
        <MetricCard label="Balance in queue" size="md" value={queue.data ? inrShort(total) : "…"} exact={inr(total)} sub={`${num(rows.length)} invoices`} />
        <MetricCard label="Disputes" size="md" tone={disputes > 0 ? "danger" : undefined} value={queue.data ? num(disputes) : "…"} sub="debtor contests the invoice" />
        <MetricCard label="Broken-promise escalations" size="md" tone={broken > 0 ? "warn" : undefined} value={queue.data ? num(broken) : "…"} sub="commitments missed ≥ threshold" />
        <MetricCard label="Attempts exhausted" size="md" value={queue.data ? num(exhausted) : "…"} sub="contact budget spent, no recovery" />
        <MetricCard label="Stop-contact" size="md" value={summary.data ? num(summary.data.by_state.stop_contact ?? 0) : "…"} sub="not in this queue; contact is frozen" />
      </div>
      <div className="toolbar">
        <label>
          Operator
          <input value={operator} onChange={(e) => setOperator(e.target.value)} placeholder="your name" maxLength={80} aria-label="Operator name" />
        </label>
        <div className="segmented" role="group" aria-label="Filter escalations">
          {([["all", "All"], ["disputed", "Disputes"], ["broken", "Broken promises"], ["attempts", "Attempts exhausted"]] as const).map(([k, label]) => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>{label}</button>
          ))}
        </div>
        <span className="spacer" />
        <button type="button" className="btn sm" onClick={queue.reload}>Refresh</button>
      </div>
      <Status load={queue} rows={6}>
        {(all) => all.length === 0 ? <EmptyState title="No escalations in this data source" hint="Escalations, disputes and exhausted contact budgets land here." /> : shown.length === 0
          ? <EmptyState title="No escalations of this kind" /> : (
          <TableWrap>
            <table>
              <thead>
                <tr>
                  <th>Invoice</th>
                  <th>Debtor</th>
                  <th>Source</th>
                  <th className="num">Balance</th>
                  <th>Reason</th>
                  <th>Promise / commitment history</th>
                  <th>Owner</th>
                  <th>Recommended next action</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((row) => {
                  const last = row.last_commitment;
                  const who = owner(row);
                  return (
                    <tr key={row.invoice_id} className={`clickable ${selectedId === row.invoice_id ? "selected" : ""}`} onClick={() => onSelect(row.invoice_id)}>
                      <td><b>{row.number}</b><span className="secondary"><StatusBadge state={row.state} /></span></td>
                      <td>{row.debtor_name ?? debtors[row.invoice_id] ?? <span className="muted">{invoices.data ? row.invoice_id : "…"}</span>}<span className="secondary">since {when(row.since)}</span></td>
                      <td><SourceBadge source={row.source} /></td>
                      <td className="num money">{inr(row.balance)}</td>
                      <td className="wrap">{row.reason ?? <span className="muted">—</span>}{row.verbatim && <span className="secondary">“{row.verbatim}”</span>}</td>
                      <td className="wrap small">
                        <div className="kv">
                          <Pill tone={credibilityTone(row.credibility)}>credibility {num(row.credibility, 2)}</Pill>
                          {row.commitments_missed > 0 && <span className="neg">{row.commitments_missed} missed</span>}
                          {row.commitments_fulfilled > 0 && <span className="pos">{row.commitments_fulfilled} fulfilled</span>}
                        </div>
                        {last ? (
                          <div className="kv" style={{ marginTop: 4 }}>
                            <StatusBadge state={last.state} /><span className="money">{inr(last.committed_amount)}</span>
                            <span className="muted">due {last.due_on} · {inr(last.amount_received)} received</span>
                          </div>
                        ) : <div className="muted" style={{ marginTop: 4 }}>no commitment</div>}
                      </td>
                      <td>{who ? <Pill tone="success">{who}</Pill> : <Pill tone="neutral">Unclaimed</Pill>}</td>
                      <td><Pill tone="warn">{row.recommended_action}</Pill></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Status>
      {selected && (
        <ResolutionDrawer key={selected.invoice_id} row={selected} debtor={debtors[selected.invoice_id] ?? null} operator={operator.trim()}
          busy={busy === selected.invoice_id} outcome={outcomes[selected.invoice_id]} onAct={act} onClose={() => onSelect(null)} />
      )}
    </>
  );
}

// -- reply evaluation --------------------------------------------------------------

const BRAINS = ["claude", "mock"] as const;
type Brain = typeof BRAINS[number];
const BRAIN_LABEL: Record<Brain, string> = { claude: "Claude", mock: "Regex baseline" };
const BRAIN_COLOR: Record<Brain, string> = { claude: COLORS.urudhi, mock: COLORS.no_action };

type MetricRow = { label: string; value: (s: EvalSummary) => string };
const METRICS: MetricRow[] = [
  { label: "Intent accuracy", value: (s) => pct(s.intent_accuracy) },
  { label: "Promise precision", value: (s) => pct(s.promise_detection.precision) },
  { label: "Promise recall", value: (s) => pct(s.promise_detection.recall) },
  { label: "Promise tp / fp / fn", value: (s) => `${s.promise_detection.tp} / ${s.promise_detection.fp} / ${s.promise_detection.fn}` },
  { label: "Amount accuracy", value: (s) => `${pct(s.amount_accuracy.accuracy)} of ${s.amount_accuracy.n}` },
  { label: "Date accuracy", value: (s) => `${pct(s.date_accuracy.accuracy)} of ${s.date_accuracy.n}` },
  { label: "Spurious amount rate", value: (s) => pct(s.spurious_amount_rate) },
  { label: "Spurious date rate", value: (s) => pct(s.spurious_date_rate) },
  { label: "Fallback rate", value: (s) => pct(s.fallback_rate) },
  { label: "Mean seconds per reply", value: (s) => num(s.mean_seconds, 2) },
  { label: "Model", value: (s) => s.model ?? "—" },
];

/**
 * One line on why the LLM brain earns its place, from the measured intent accuracy of both brains.
 * Null unless both were evaluated with a known accuracy.
 */
export function whyAiLine(data: ReplyEval): string | null {
  const claude = data.claude?.intent_accuracy;
  const mock = data.mock?.intent_accuracy;
  if (claude === null || claude === undefined || mock === null || mock === undefined) return null;
  const pts = (claude - mock) * 100;
  const n = Math.min(data.claude!.items, data.mock!.items);
  const recall = data.claude!.promise_detection.recall;
  const mockRecall = data.mock!.promise_detection.recall;
  const recallPart = recall !== null && mockRecall !== null ? `; it finds ${pct(recall, 0)} of real promises against ${pct(mockRecall, 0)}` : "";
  if (pts <= 0) return `On ${num(n)} labelled replies the regex baseline reads intent correctly ${pct(mock)} of the time versus Claude's ${pct(claude)}${recallPart}.`;
  return `Claude reads debtor intent correctly ${pct(claude)} of the time versus ${pct(mock)} for the regex baseline — ${num(pts, 1)} points better on the same ${num(n)} labelled replies${recallPart}. Mixed-language, informal replies are where rules break and where every promise-to-pay starts.`;
}

function languageBars(data: ReplyEval, present: Brain[]): BarGroup[] {
  const langs = Array.from(new Set(present.flatMap((b) => Object.keys(data[b]!.per_language))));
  return langs.map((lang) => ({
    label: lang,
    bars: present.map((b) => {
      const v = data[b]!.per_language[lang];
      return { label: BRAIN_LABEL[b], value: v?.accuracy ?? 0, color: BRAIN_COLOR[b], note: v ? `n=${v.n}` : "n/a" };
    }),
  }));
}

function Confusion({ summary }: { summary: EvalSummary }) {
  const expected = Object.keys(summary.confusion);
  const predicted = Array.from(new Set([...expected, ...expected.flatMap((e) => Object.keys(summary.confusion[e]))]));
  const worst = expected.flatMap((e) => Object.entries(summary.confusion[e]).filter(([p]) => p !== e).map(([p, n]) => ({ e, p, n })))
    .sort((a, b) => b.n - a.n).slice(0, 3);
  return (
    <>
      {worst.length > 0 && (
        <p className="muted small" style={{ marginBottom: 8 }}>
          Most common confusions: {worst.map((w, i) => <span key={i}>{i > 0 && "; "}<b>{stateLabel(w.e)}</b> read as <b>{stateLabel(w.p)}</b> ×{w.n}</span>)}.
        </p>
      )}
      <TableWrap>
        <table className="compact confusion">
          <thead>
            <tr><th>expected ↓ / predicted →</th>{predicted.map((p) => <th key={p} className="num">{stateLabel(p)}</th>)}</tr>
          </thead>
          <tbody>
            {expected.map((e) => (
              <tr key={e}>
                <td>{stateLabel(e)}</td>
                {predicted.map((p) => {
                  const n = summary.confusion[e][p] ?? 0;
                  return <td key={p} className={`num ${n === 0 ? "zero" : e === p ? "hit" : "miss"}`}>{n}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </>
  );
}

function why(f: EvalFailure): string {
  return [
    !f.intent_ok && "intent", f.amount_ok === false && "amount", f.date_ok === false && "date",
    f.spurious_amount && "spurious amount", f.spurious_date && "spurious date", f.fallback && "fallback",
  ].filter(Boolean).join(", ");
}

function Examples({ data, present }: { data: ReplyEval; present: Brain[] }) {
  const [all, setAll] = useState(false);
  const byId = new Map<string, Partial<Record<Brain, EvalFailure>>>();
  for (const b of present) for (const f of data[b]!.failures) byId.set(f.id, { ...(byId.get(f.id) ?? {}), [b]: f });
  const rows = Array.from(byId.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  const shown = all ? rows : rows.slice(0, 12);
  if (rows.length === 0) return <EmptyState title="No failures recorded" />;
  return (
    <>
      <TableWrap>
        <table className="compact">
          <thead>
            <tr>
              <th>Debtor text</th>
              <th>Lang</th>
              <th>Expected</th>
              {present.map((b) => <th key={b}>{BRAIN_LABEL[b]} predicted</th>)}
            </tr>
          </thead>
          <tbody>
            {shown.map(([id, per]) => {
              const any = present.map((b) => per[b]).find(Boolean)!;
              return (
                <tr key={id}>
                  <td className="words">“{any.text}”<span className="secondary mono">{id}</span></td>
                  <td>{any.language}</td>
                  <td>
                    <b>{stateLabel(any.expected_intent)}</b>
                    {any.expected_amount !== null && <span className="secondary">amount {inr(any.expected_amount)}</span>}
                    {any.expected_on && <span className="secondary">date {any.expected_on}</span>}
                  </td>
                  {present.map((b) => {
                    const f = per[b];
                    if (!f) return <td key={b}><Pill tone="success">correct</Pill></td>;
                    return (
                      <td key={b}>
                        <span className={f.intent_ok ? "" : "neg"}>{stateLabel(f.predicted_intent)}</span>
                        {(f.expected_amount !== null || f.predicted_amount !== null) && (
                          <span className={`secondary ${f.amount_ok === false || f.spurious_amount ? "neg" : ""}`}>amount {f.predicted_amount !== null ? inr(f.predicted_amount) : "—"}</span>
                        )}
                        {(f.expected_on || f.predicted_on) && (
                          <span className={`secondary ${f.date_ok === false || f.spurious_date ? "neg" : ""}`}>date {f.predicted_on ?? "—"}</span>
                        )}
                        <span className="secondary">{why(f)} · confidence {num(f.confidence, 2)}</span>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableWrap>
      {rows.length > 12 && (
        <button type="button" className="btn sm" style={{ marginTop: 10 }} onClick={() => setAll((a) => !a)}>
          {all ? "Show fewer" : `Show all ${rows.length} examples`}
        </button>
      )}
    </>
  );
}

export function ReplyEvaluation() {
  const evaluation = useLoad(api.replyEval);
  const missing = (
    <Card>
      <EmptyState title="No reply evaluation yet"
        hint="Run the labelled-reply evaluation for each brain, then reload."
        command="python -m urudhi.eval_replies --brain mock && python -m urudhi.eval_replies --brain claude" />
    </Card>
  );
  return (
    <Status load={evaluation} notFound={missing} rows={6}>
      {(data: ReplyEval) => {
        const present = BRAINS.filter((b) => data[b]);
        const items = Math.max(...present.map((b) => data[b]!.items));
        const measured = <ModeBadge mode="measured" label={`Measured on ${num(items)} labelled replies`} />;
        const why = whyAiLine(data);
        return (
          <div className="stack">
            <div>
              <div className="page-title"><h1>Reply Evaluation</h1>{measured}</div>
              <p className="page-desc">
                Every labelled reply in <code>data/reply_eval.jsonl</code> run through each brain under a fixed context.
                {present.length === 1 && <> Only the <b>{BRAIN_LABEL[present[0]]}</b> brain has been evaluated so far.</>}
              </p>
              {why && <p className="note ok" role="note" style={{ marginBottom: 16 }}><b>Why AI is necessary:</b> {why}</p>}
              <div className="compare">
                {present.map((b) => (
                  <MetricCard key={b} label={`${BRAIN_LABEL[b]} · intent accuracy`} tone={b === "claude" ? "accent" : undefined}
                    value={pct(data[b]!.intent_accuracy)} badge={measured}
                    sub={<>{data[b]!.model ?? "deterministic rules"} · promise recall {pct(data[b]!.promise_detection.recall, 0)} · {num(data[b]!.items)} replies</>} />
                ))}
              </div>
            </div>

            <Card>
              <SectionHeader title="Metrics" badge={measured} />
              <TableWrap>
                <table className="compact stats">
                  <thead>
                    <tr><th>Metric</th>{present.map((b) => <th key={b} className={`num ${b === "claude" ? "urudhi" : ""}`}>{BRAIN_LABEL[b]}</th>)}</tr>
                  </thead>
                  <tbody>
                    {METRICS.map((m) => (
                      <tr key={m.label}>
                        <td>{m.label}</td>
                        {present.map((b) => <td key={b} className={`num ${b === "claude" ? "urudhi" : ""}`}>{m.value(data[b]!)}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </Card>

            <Card>
              <SectionHeader title="Accuracy by language" badge={measured} description="Intent accuracy on English, Hinglish and Tamil-English replies." />
              <HBars title="Intent accuracy by language and brain" groups={languageBars(data, present)} format={(v) => pct(v, 0)} max={1} labelWidth={110} />
            </Card>

            <div className="columns">
              {present.map((b) => (
                <Card key={b}>
                  <SectionHeader title={`${BRAIN_LABEL[b]} · confusion`} level={3} />
                  <Confusion summary={data[b]!} />
                </Card>
              ))}
            </div>

            <Card>
              <SectionHeader title="Representative examples" badge={<Pill tone="outline">failures only</Pill>}
                description="Replies at least one brain got wrong, with what was expected and what each brain predicted." />
              <Examples data={data} present={present} />
            </Card>
          </div>
        );
      }}
    </Status>
  );
}
