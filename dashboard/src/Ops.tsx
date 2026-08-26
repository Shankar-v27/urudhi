/** Operator views: the escalation queue with human actions, and the reply-interpretation eval. */

import { useState } from "react";
import {
  ApiError, Escalation, EvalSummary, HumanAction, OPERATOR_KEY, ReplyEval, api, inr, num, pct,
  storageGet, storageSet, useLoad, when,
} from "./api";
import { BarGroup, COLORS, Empty, HBars, StateChip, Status, Tag } from "./ui";

// -- escalations ----------------------------------------------------------

const ACTIONS: { action: HumanAction; label: string; needsNote: boolean }[] = [
  { action: "acknowledge", label: "Acknowledge", needsNote: false },
  { action: "note", label: "Add note", needsNote: true },
  { action: "release", label: "Release to automation", needsNote: true },
  { action: "close", label: "Close", needsNote: true },
];

type Outcome = { ok: boolean; text: string };

function EscalationRow({
  row, operator, busy, outcome, onAct,
}: {
  row: Escalation; operator: string; busy: boolean; outcome?: Outcome;
  onAct: (id: string, action: HumanAction, notes: string) => void;
}) {
  const [notes, setNotes] = useState("");
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="row" onClick={() => setOpen((o) => !o)}>
        <td>{row.number}</td>
        <td><StateChip state={row.state} /></td>
        <td className="num">{inr(row.balance)}</td>
        <td className="muted">{when(row.since)}</td>
        <td>{row.reason ?? <span className="muted">—</span>}</td>
        <td>{row.acknowledged ? <Tag tone="ok">acknowledged</Tag> : <Tag>unclaimed</Tag>}</td>
      </tr>
      <tr className="expand">
        <td colSpan={6}>
          {row.verbatim && <div className="verbatim">“{row.verbatim}”</div>}
          {open && (
            <div className="actions">
              {row.human_actions.length > 0 && (
                <ul className="history small">
                  {row.human_actions.map((h, i) => (
                    <li key={i}>
                      <b>{h.action}</b> · {h.operator} · {when(h.at)}
                      {h.notes && <span className="muted"> — {h.notes}</span>}
                    </li>
                  ))}
                </ul>
              )}
              <textarea
                placeholder="notes (required for note / release / close)"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={2}
                maxLength={2000}
              />
              <div className="buttons">
                {ACTIONS.map((a) => (
                  <button
                    key={a.action}
                    disabled={busy || !operator || (a.needsNote && !notes.trim())}
                    title={!operator ? "set an operator name first" : a.needsNote && !notes.trim() ? "needs a note" : ""}
                    className={a.action === "close" ? "danger" : ""}
                    onClick={() => onAct(row.invoice_id, a.action, notes)}
                  >
                    {a.label}
                  </button>
                ))}
                {busy && <span className="muted small">working…</span>}
              </div>
              {outcome && <div className={`note ${outcome.ok ? "ok" : "bad"}`}>{outcome.text}</div>}
            </div>
          )}
        </td>
      </tr>
    </>
  );
}

export function Escalations() {
  const queue = useLoad(api.escalations);
  const [operator, setOperatorState] = useState(() => storageGet(OPERATOR_KEY));
  const [busy, setBusy] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});

  const setOperator = (value: string) => {
    setOperatorState(value);
    storageSet(OPERATOR_KEY, value.trim());
  };

  const act = async (id: string, action: HumanAction, notes: string) => {
    setBusy(id);
    try {
      const result = await api.human(id, { action, operator: operator.trim(), notes });
      const moved = result.from_state !== result.to_state ? ` (${result.from_state} → ${result.to_state})` : "";
      setOutcomes((o) => ({ ...o, [id]: { ok: true, text: `${result.action} recorded by ${result.operator}${moved}` } }));
      queue.reload();
    } catch (failure) {
      const text = failure instanceof ApiError
        ? `${failure.status}${failure.status === 409 ? " — not allowed" : ""}: ${failure.detail}`
        : String(failure);
      setOutcomes((o) => ({ ...o, [id]: { ok: false, text } }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section>
      <div className="toolbar">
        <label className="muted">
          Operator{" "}
          <input value={operator} onChange={(e) => setOperator(e.target.value)} placeholder="your name" maxLength={80} />
        </label>
        <button onClick={queue.reload}>refresh</button>
        <span className="muted small">Click a row for actions. Release and close require a note; the audit chain records every action.</span>
      </div>
      <Status load={queue}>
        {(rows) => rows.length === 0 ? <Empty>nothing is waiting on a human</Empty> : (
          <div className="scroll">
            <table className="queue">
              <thead>
                <tr>
                  <th>Invoice</th>
                  <th>State</th>
                  <th className="num">Balance</th>
                  <th>Since</th>
                  <th>Reason</th>
                  <th>Owner</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <EscalationRow key={row.invoice_id} row={row} operator={operator.trim()}
                    busy={busy === row.invoice_id} outcome={outcomes[row.invoice_id]} onAct={act} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Status>
    </section>
  );
}

// -- reply evaluation -----------------------------------------------------

const BRAINS = ["mock", "claude"] as const;

type MetricRow = { label: string; value: (s: EvalSummary) => string };
const METRICS: MetricRow[] = [
  { label: "Items", value: (s) => num(s.items) },
  { label: "Model", value: (s) => s.model ?? "—" },
  { label: "Intent accuracy", value: (s) => pct(s.intent_accuracy) },
  { label: "Promise precision", value: (s) => pct(s.promise_detection.precision) },
  { label: "Promise recall", value: (s) => pct(s.promise_detection.recall) },
  { label: "Promise tp / fp / fn", value: (s) => `${s.promise_detection.tp} / ${s.promise_detection.fp} / ${s.promise_detection.fn}` },
  { label: "Amount accuracy", value: (s) => `${pct(s.amount_accuracy.accuracy)} of ${s.amount_accuracy.n}` },
  { label: "Date accuracy", value: (s) => `${pct(s.date_accuracy.accuracy)} of ${s.date_accuracy.n}` },
  { label: "Spurious amount rate", value: (s) => pct(s.spurious_amount_rate) },
  { label: "Spurious date rate", value: (s) => pct(s.spurious_date_rate) },
  { label: "Fallback rate", value: (s) => pct(s.fallback_rate) },
  { label: "Mean seconds", value: (s) => num(s.mean_seconds, 2) },
];

function accuracyBars(rows: Record<string, { n: number; accuracy: number }>, color: string): BarGroup[] {
  return Object.entries(rows).map(([key, v]) => ({
    label: key.replace(/_/g, " "),
    bars: [{ label: key, value: v.accuracy, color, note: `n=${v.n}` }],
  }));
}

function Failures({ summary }: { summary: EvalSummary }) {
  if (summary.failures.length === 0) return <Empty>no failures</Empty>;
  return (
    <div className="scroll">
      <table className="compact">
        <thead>
          <tr>
            <th>Text</th>
            <th>Expected → predicted</th>
            <th>Lang</th>
            <th>Why</th>
          </tr>
        </thead>
        <tbody>
          {summary.failures.map((f) => {
            const why = [
              !f.intent_ok && "intent",
              f.amount_ok === false && "amount",
              f.date_ok === false && "date",
              f.spurious_amount && "spurious amount",
              f.spurious_date && "spurious date",
              f.fallback && "fallback",
            ].filter(Boolean).join(", ");
            return (
              <tr key={f.id}>
                <td className="verbatim-cell">“{f.text}”</td>
                <td>
                  <b>{f.expected_intent}</b> <span className="arrow">→</span>{" "}
                  <span className={f.intent_ok ? "" : "neg"}>{f.predicted_intent}</span>
                  {(f.expected_amount !== null || f.predicted_amount !== null) && (
                    <div className="muted small">
                      amount {f.expected_amount !== null ? inr(f.expected_amount) : "—"} → {f.predicted_amount !== null ? inr(f.predicted_amount) : "—"}
                    </div>
                  )}
                  {(f.expected_on || f.predicted_on) && (
                    <div className="muted small">date {f.expected_on ?? "—"} → {f.predicted_on ?? "—"}</div>
                  )}
                </td>
                <td>{f.language}</td>
                <td className="muted small">{why}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ReplyEvaluation() {
  const evaluation = useLoad(api.replyEval);
  const runNote = (
    <>
      No reply evaluation yet. Run <code>python -m urudhi.eval_replies --brain mock</code> and/or{" "}
      <code>python -m urudhi.eval_replies --brain claude</code>, then reload.
    </>
  );
  return (
    <Status load={evaluation} notFound={runNote}>
      {(data: ReplyEval) => {
        const present = BRAINS.filter((b) => data[b]);
        const colorFor = (b: string) => (b === "claude" ? COLORS.blue : COLORS.amber);
        return (
          <>
            <section>
              <h2>Reply interpretation — mock vs claude <Tag>measured</Tag></h2>
              <p className="muted small">
                Every labelled reply in <code>data/reply_eval.jsonl</code> run through each brain under a fixed
                context. {present.length === 1 && <>Only the <b>{present[0]}</b> brain has been evaluated so far.</>}
              </p>
              <div className="scroll">
                <table className="compact">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      {present.map((b) => <th key={b} className="num">{b}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {METRICS.map((m) => (
                      <tr key={m.label}>
                        <td>{m.label}</td>
                        {present.map((b) => <td key={b} className="num">{m.value(data[b]!)}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="columns">
              {present.map((b) => (
                <div key={b}>
                  <h3>{b} — accuracy by intent</h3>
                  <HBars title={`${b} accuracy by intent`} groups={accuracyBars(data[b]!.per_intent, colorFor(b))}
                    format={(v) => pct(v, 0)} max={1} labelWidth={130} />
                  <h3>{b} — accuracy by language</h3>
                  <HBars title={`${b} accuracy by language`} groups={accuracyBars(data[b]!.per_language, colorFor(b))}
                    format={(v) => pct(v, 0)} max={1} labelWidth={130} />
                </div>
              ))}
            </section>

            {present.map((b) => (
              <section key={b}>
                <h2>{b} — failures <Tag>{data[b]!.failures.length}</Tag></h2>
                <Failures summary={data[b]!} />
              </section>
            ))}
          </>
        );
      }}
    </Status>
  );
}
