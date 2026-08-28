/** Shared primitives: cards, badges, drawer, load states, and the inline-SVG chart forms. */

import { ReactNode, useEffect, useId, useState } from "react";
import { ApiError, Loaded, RowSource, TOKEN_KEY, storageGet, when } from "./api";

// -- tones & badges ---------------------------------------------------------

export type Tone = "success" | "warn" | "danger" | "info" | "neutral";

const STATE_TONE: Record<string, Tone> = {
  // invoices
  paid: "success", partially_paid: "warn", outstanding: "warn", promised: "info",
  escalated: "danger", disputed: "danger", stop_contact: "neutral", closed: "neutral",
  // promises
  kept: "success", partially_kept: "warn", open: "info", broken: "danger",
  superseded: "neutral", withdrawn: "warn", declined: "danger",
  // commitments
  active: "info", partially_fulfilled: "warn", fulfilled: "success", missed: "danger", cancelled: "neutral",
  // concessions
  offered: "info", accepted: "success", settled: "success", expired: "warn",
};

export function toneFor(state: string): Tone {
  return STATE_TONE[state] ?? "neutral";
}

export function stateLabel(state: string | null | undefined): string {
  return state ? state.replace(/_/g, " ") : "—";
}

export function Pill({ tone = "neutral", children, title, upper = false, className = "" }: {
  tone?: Tone | "outline"; children: ReactNode; title?: string; upper?: boolean; className?: string;
}) {
  return <span className={`pill ${tone} ${upper ? "upper" : ""} ${className}`} title={title}>{children}</span>;
}

/** Any lifecycle state (invoice / promise / commitment / concession) as a labelled pill. */
export function StatusBadge({ state, title }: { state: string; title?: string }) {
  return <Pill tone={toneFor(state)} upper title={title}>{stateLabel(state)}</Pill>;
}

export type Mode = "razorpay_test" | "sandbox" | "simulation" | "observed" | "persona" | "measured" | "mixed";
const MODE_LABEL: Record<Mode, string> = {
  razorpay_test: "Razorpay Test Mode", sandbox: "Sandbox", simulation: "Simulation",
  observed: "Observed on rails", persona: "Persona model", measured: "Measured", mixed: "Mixed sources",
};
const MODE_TITLE: Record<Mode, string> = {
  razorpay_test: "A real Razorpay test-mode instrument; the URL is used exactly as Razorpay returned it",
  sandbox: "Simulation only — no Razorpay checkout exists",
  simulation: "Produced by the simulator, not by the live ledger",
  observed: "Counted only from payments the payment rails reported",
  persona: "Debtor behaviour comes from the persona model in the simulator",
  measured: "Measured on labelled replies",
  mixed: "Live test-mode records and simulation records merged; every row is labelled with its ledger",
};

const SOURCE_BADGE: Record<RowSource, { label: string; title: string }> = {
  live_test: { label: "Live Test", title: "From the live ledger: real Razorpay test-mode instruments, payments observed via signed webhook" },
  simulation: { label: "Simulation", title: "From the simulation ledger: persona-model debtors, sandbox rail, webhook-shaped events" },
};

/** Row-level provenance: which ledger a record came from. Emerald outline for live test, teal for simulation. */
export function SourceBadge({ source, title }: { source: RowSource | null | undefined; title?: string }) {
  if (!source) return <span className="source-badge unknown" title="The API did not label this row">Unlabelled</span>;
  const b = SOURCE_BADGE[source] ?? { label: source, title: "" };
  return <span className={`source-badge ${source}`} title={title ?? b.title}>{b.label}</span>;
}

/** Provenance badge: where a number or instrument came from. */
export function ModeBadge({ mode, label, title }: { mode: Mode; label?: string; title?: string }) {
  return <span className={`mode ${mode}`} title={title ?? MODE_TITLE[mode]}>{label ?? MODE_LABEL[mode]}</span>;
}

// -- layout ----------------------------------------------------------------

export function Card({ children, className = "", tight = false, flush = false }: {
  children: ReactNode; className?: string; tight?: boolean; flush?: boolean;
}) {
  return <section className={`card ${tight ? "tight" : ""} ${flush ? "flush" : ""} ${className}`}>{children}</section>;
}

export function SectionHeader({ title, badge, description, actions, level = 2 }: {
  title: ReactNode; badge?: ReactNode; description?: ReactNode; actions?: ReactNode; level?: 2 | 3;
}) {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <div className="section-header">
      <div>
        <div className="titles"><Heading>{title}</Heading>{badge}</div>
        {description && <p className="desc">{description}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function MetricCard({ label, value, exact, sub, badge, tone, size }: {
  label: ReactNode; value: ReactNode; exact?: string; sub?: ReactNode; badge?: ReactNode;
  tone?: "accent" | "danger" | "warn"; size?: "md";
}) {
  return (
    <div className={`metric ${tone ?? ""}`}>
      <div className="label"><span>{label}</span>{badge}</div>
      <div className={`value ${size ?? ""}`} title={exact}>{value}</div>
      {sub !== undefined && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Fact({ k, v, money = false, title }: { k: ReactNode; v: ReactNode; money?: boolean; title?: string }) {
  return (
    <div className="fact">
      <div className="k">{k}</div>
      <div className={`v ${money ? "money" : ""}`} title={title}>{v}</div>
    </div>
  );
}

export function TableWrap({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`table-wrap ${className}`}>{children}</div>;
}

/** Right-side panel. Escape closes; the backdrop click closes; focus lands on the close button. */
export function Drawer({ title, eyebrow, onClose, children, narrow = false, headExtra, labelledBy }: {
  title: ReactNode; eyebrow?: ReactNode; onClose: () => void; children: ReactNode; narrow?: boolean;
  headExtra?: ReactNode; labelledBy?: string;
}) {
  const id = useId();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className={`drawer ${narrow ? "narrow" : ""}`} role="dialog" aria-modal="true" aria-labelledby={labelledBy ?? id}>
        <div className="drawer-head">
          <div>
            {eyebrow && <div className="eyebrow">{eyebrow}</div>}
            <h2 id={labelledBy ?? id}>{title}</h2>
            {headExtra}
          </div>
          <button type="button" className="btn icon ghost" onClick={onClose} aria-label="Close panel" autoFocus>×</button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  );
}

export function DrawerSection({ title, badge, children }: { title: ReactNode; badge?: ReactNode; children: ReactNode }) {
  return (
    <section className="drawer-section">
      <h3>{title}{badge}</h3>
      {children}
    </section>
  );
}

// -- load states -----------------------------------------------------------

export function Skeleton({ rows = 5, label = "Loading" }: { rows?: number; label?: string }) {
  const widths = ["92%", "78%", "85%", "64%", "88%", "70%", "80%"];
  return (
    <div className="skeleton" role="status" aria-live="polite" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => <div key={i} className="line" style={{ width: widths[i % widths.length] }} />)}
      <span className="sr-only">{label}…</span>
    </div>
  );
}

export function EmptyState({ title, hint, command }: { title: ReactNode; hint?: ReactNode; command?: string }) {
  return (
    <div className="state-box">
      <b>{title}</b>
      {hint && <span>{hint}</span>}
      {command && <code>{command}</code>}
    </div>
  );
}

export function ErrorState({ error, onRetry, notFound }: { error: Error; onRetry?: () => void; notFound?: ReactNode }) {
  const retry = onRetry && <button type="button" className="btn sm" onClick={onRetry}>Retry</button>;
  if (error instanceof ApiError) {
    if (error.unauthorized) {
      return (
        <div className="state-box auth" role="alert">
          <b>Not connected</b>
          <span>
            {storageGet(TOKEN_KEY)
              ? "The API rejected the saved token. Paste the current URUDHI_API_TOKEN into the Connect field in the header."
              : "Paste URUDHI_API_TOKEN into the Connect field in the header — every /api request is bearer-token protected."}
          </span>
          {retry}
        </div>
      );
    }
    if (error.notFound && notFound) return <>{notFound}</>;
    return (
      <div className="state-box error" role="alert">
        <b>{error.status} — request failed</b>
        <span>{error.detail}</span>
        {retry}
      </div>
    );
  }
  return (
    <div className="state-box error" role="alert">
      <b>API unreachable</b>
      <span>{error.message}. Is the backend running on 127.0.0.1:8000?</span>
      {retry}
    </div>
  );
}

/** Render loading / error / data for one `useLoad` result. */
export function Status<T>({ load, notFound, rows = 5, children }: {
  load: Loaded<T>; notFound?: ReactNode; rows?: number; children: (data: T) => ReactNode;
}) {
  if (load.error) return <ErrorState error={load.error} onRetry={load.reload} notFound={notFound} />;
  if (load.data === null) return <Skeleton rows={rows} />;
  return <>{children(load.data)}</>;
}

// -- small pieces ----------------------------------------------------------

/** Audit reference: `#seq`, with kind and hash prefix on hover. */
export function Ref({ event, label }: { event: { seq: number; at: string; kind: string; hash: string } | null | undefined; label?: string }) {
  if (!event) return <span className="ref muted" title="no audit event linked">no audit ref</span>;
  return (
    <span className="ref" title={`${event.kind} · ${when(event.at)} · ${event.hash.slice(0, 12)}…`}>
      {label ? `${label} ` : ""}#{event.seq}
    </span>
  );
}

export function Checklist({ checks, onlyFailed = false }: {
  checks: { allowed: boolean; gate: string; reason: string }[]; onlyFailed?: boolean;
}) {
  const shown = onlyFailed ? checks.filter((c) => !c.allowed) : checks;
  if (shown.length === 0) return <p className="muted small">no checks recorded</p>;
  return (
    <ul className="checklist">
      {shown.map((c, i) => (
        <li key={`${c.gate}-${i}`} className={c.allowed ? "ok" : "blocked"}>
          <span className="mark" aria-label={c.allowed ? "passed" : "blocked"}>{c.allowed ? "✓" : "✗"}</span>
          <span className="gate">{c.gate.replace(/_/g, " ")}</span>
          <span className="why">{c.reason}</span>
        </li>
      ))}
    </ul>
  );
}

export function ConfidenceBar({ value }: { value: number }) {
  const width = Math.max(0, Math.min(1, value)) * 100;
  return (
    <span className="confbar" title={`confidence ${value.toFixed(2)}`}>
      <span className="track" aria-hidden="true"><span className="fill" style={{ width: `${width}%` }} /></span>
      <span className="num">{value.toFixed(2)}</span>
    </span>
  );
}

export type StepStatus = "done" | "pending" | "failed" | "warn";

/** One node of the commitment-integrity provenance visual. */
export function IntegrityStep({ n, title, status, statusText, event, children }: {
  n: number; title: string; status: StepStatus; statusText?: ReactNode;
  event?: { seq: number; at: string; kind: string; hash: string } | null; children: ReactNode;
}) {
  const glyph = status === "done" ? "✓" : status === "failed" ? "✗" : String(n);
  const word = status === "done" ? "evidence recorded" : status === "failed" ? "failed" : status === "warn" ? "partial" : "pending";
  return (
    <li className={`step ${status}`} aria-label={`${title}: ${word}`}>
      <span className="node" aria-hidden="true">{glyph}</span>
      <div>
        <div className="step-head">
          <span className="step-title">{title}</span>
          <span className="step-status">{statusText ?? word}</span>
          {event !== undefined && <Ref event={event} />}
        </div>
        <div className="step-body">{children}</div>
      </div>
    </li>
  );
}

export function ExternalLinkButton({ href, children, className = "primary", title }: {
  href: string; children: ReactNode; className?: string; title?: string;
}) {
  return (
    <a className={`btn ${className}`} href={href} target="_blank" rel="noopener noreferrer"
      title={title ?? href} aria-label={`${typeof children === "string" ? children : "Open link"} — ${href}`}>
      {children}
    </a>
  );
}

// -- charts ---------------------------------------------------------------

/** Chart series colours are CSS tokens; the arm names map 1:1 to `--chart-*`. */
export const COLORS = {
  no_action: "var(--chart-no-action)",
  baseline: "var(--chart-baseline)",
  urudhi: "var(--chart-urudhi)",
  accent: "var(--accent)",
  info: "var(--info)",
  warn: "var(--warn)",
  danger: "var(--danger)",
  muted: "var(--chart-no-action)",
};

export interface Series {
  label: string;
  color: string;
  values: number[];
}

function niceTicks(max: number): number[] {
  if (max <= 0) return [0];
  const raw = max / 4;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? magnitude;
  const ticks: number[] = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
  return ticks;
}

function shortDay(day: string): string {
  const d = new Date(day + "T00:00:00");
  return Number.isNaN(d.getTime()) ? day : d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

/** Cumulative-over-time line chart with a hover tooltip. Every series shares `labels` (x) and one y scale. */
export function LineChart({ labels, series, height = 240, format, title }: {
  labels: string[]; series: Series[]; height?: number; format: (v: number) => string; title: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 760;
  const pad = { l: 64, r: 16, t: 12, b: 28 };
  const n = labels.length;
  if (n === 0 || series.every((s) => s.values.length === 0)) {
    return <EmptyState title="No data points yet" />;
  }
  const max = Math.max(1, ...series.flatMap((s) => s.values));
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1] || max;
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const x = (i: number) => (n <= 1 ? pad.l + innerW / 2 : pad.l + (i * innerW) / (n - 1));
  const y = (v: number) => pad.t + innerH * (1 - v / top);
  const labelIdx = n <= 6
    ? labels.map((_, i) => i)
    : Array.from(new Set([0, Math.floor(n / 4), Math.floor(n / 2), Math.floor((3 * n) / 4), n - 1]));
  const slot = n > 1 ? innerW / (n - 1) : innerW;

  return (
    <figure className="chart">
      <div className="chart-wrap">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title} onMouseLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} className="grid" />
              <text x={pad.l - 8} y={y(t)} dy="0.35em" textAnchor="end" className="axis">{format(t)}</text>
            </g>
          ))}
          <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} className="axis-line" />
          {labelIdx.map((i) => (
            <text key={i} x={x(i)} y={height - 8} textAnchor="middle" className="axis">{shortDay(labels[i])}</text>
          ))}
          {series.map((s) => {
            const pts = s.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
            const last = s.values.length - 1;
            return (
              <g key={s.label}>
                {s.values.length === 1
                  ? <circle cx={x(0)} cy={y(s.values[0])} r={4} fill={s.color} />
                  : <polyline points={pts} fill="none" stroke={s.color} strokeWidth={2}
                      strokeLinejoin="round" strokeLinecap="round" />}
                {last >= 0 && <circle cx={x(last)} cy={y(s.values[last])} r={3.5} fill={s.color} />}
              </g>
            );
          })}
          {hover !== null && (
            <g>
              <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={y(0)} className="hover-line" />
              {series.map((s) => s.values[hover] !== undefined && (
                <circle key={s.label} cx={x(hover)} cy={y(s.values[hover])} r={4.5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
              ))}
            </g>
          )}
          {labels.map((_, i) => (
            <rect key={i} className="hit" x={x(i) - slot / 2} y={pad.t} width={slot} height={innerH}
              onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={-1} />
          ))}
        </svg>
        {hover !== null && (
          <div className="chart-tip" style={{ left: `${(x(hover) / width) * 100}%`, top: `${(pad.t / height) * 100}%` }}>
            <div className="day">{labels[hover]}</div>
            {series.map((s) => (
              <div key={s.label} className="row">
                <span><i className="legend-dot" style={{ background: s.color, display: "inline-block", width: 8, height: 8, borderRadius: 2, marginRight: 6 }} />{s.label}</span>
                <b>{s.values[hover] !== undefined ? format(s.values[hover]) : "—"}</b>
              </div>
            ))}
          </div>
        )}
      </div>
      <figcaption className="legend">
        {series.map((s) => (
          <span key={s.label}>
            <i style={{ background: s.color }} /> {s.label}
            {s.values.length > 0 && <b> {format(s.values[s.values.length - 1])}</b>}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

export interface Bar {
  label: string;
  value: number;
  color: string;
  note?: string;
}

export interface BarGroup {
  label: string;
  bars: Bar[];
}

/** Horizontal bars starting at 0; one row per group, one bar per entry. */
export function HBars({ groups, format, max, title, labelWidth = 150 }: {
  groups: BarGroup[]; format: (v: number) => string; max?: number; title: string; labelWidth?: number;
}) {
  if (groups.length === 0) return <EmptyState title="Nothing to chart" />;
  const width = 760;
  const barH = 14;
  const gap = 3;
  const groupGap = 10;
  const valueW = 140;
  const innerW = width - labelWidth - valueW - 12;
  const top = Math.max(1, max ?? Math.max(...groups.flatMap((g) => g.bars.map((b) => Math.abs(b.value)))));
  let cursor = 4;
  const rows = groups.map((g) => {
    const y0 = cursor;
    cursor += g.bars.length * (barH + gap) - gap + groupGap;
    return { g, y0 };
  });
  const height = cursor;
  const multi = groups.some((g) => g.bars.length > 1);

  return (
    <figure className="chart bars">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        {rows.map(({ g, y0 }) => (
          <g key={g.label}>
            <text x={labelWidth - 8} y={y0 + (g.bars.length * (barH + gap) - gap) / 2} dy="0.35em"
              textAnchor="end" className="axis label">{g.label}</text>
            {g.bars.map((b, i) => {
              const y = y0 + i * (barH + gap);
              const w = Math.max(0, (Math.abs(b.value) / top) * innerW);
              return (
                <g key={b.label}>
                  <rect x={labelWidth} y={y} width={innerW} height={barH} className="track" rx={3} />
                  <rect x={labelWidth} y={y} width={w} height={barH} fill={b.color} rx={3} />
                  <text x={labelWidth + innerW + 8} y={y + barH / 2} dy="0.35em" className="axis value">
                    {format(b.value)}{b.note ? ` · ${b.note}` : ""}
                  </text>
                </g>
              );
            })}
          </g>
        ))}
      </svg>
      {multi && (
        <figcaption className="legend">
          {groups[0].bars.map((b) => (
            <span key={b.label}><i style={{ background: b.color }} /> {b.label}</span>
          ))}
        </figcaption>
      )}
    </figure>
  );
}
