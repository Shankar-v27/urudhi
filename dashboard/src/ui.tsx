/** Shared primitives: chips, status/error notes, and the two inline-SVG chart forms. */

import { ReactNode } from "react";
import { ApiError, Loaded, TOKEN_KEY, storageGet } from "./api";

export function StateChip({ state }: { state: string }) {
  return <span className={`state ${state}`}>{state.replace(/_/g, " ")}</span>;
}

export function Tag({ children, tone = "" }: { children: ReactNode; tone?: string }) {
  return <span className={`tag ${tone}`}>{children}</span>;
}

export function ErrorNote({ error, notFound }: { error: Error; notFound?: ReactNode }) {
  if (error instanceof ApiError) {
    if (error.unauthorized) {
      return (
        <div className="note bad">
          <strong>401 Unauthorized.</strong>{" "}
          {storageGet(TOKEN_KEY)
            ? "The API rejected the saved token. Paste the current URUDHI_API_TOKEN in the header and press connect."
            : "No API token set. Paste URUDHI_API_TOKEN in the header and press connect."}
        </div>
      );
    }
    if (error.notFound && notFound) return <div className="note">{notFound}</div>;
    return (
      <div className="note bad">
        <strong>{error.status}</strong> {error.detail}
      </div>
    );
  }
  return (
    <div className="note bad">
      {error.message} — is the API running on 127.0.0.1:8000?
    </div>
  );
}

/** Render loading / error / data for one `useLoad` result. */
export function Status<T>({
  load, notFound, children,
}: { load: Loaded<T>; notFound?: ReactNode; children: (data: T) => ReactNode }) {
  if (load.error) return <ErrorNote error={load.error} notFound={notFound} />;
  if (load.data === null) return <p className="muted">loading…</p>;
  return <>{children(load.data)}</>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="muted empty">{children}</p>;
}

export function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub !== undefined && <div className="sub muted">{sub}</div>}
    </div>
  );
}

// -- charts ---------------------------------------------------------------

export const COLORS = {
  no_action: "var(--muted)",
  baseline: "var(--amber)",
  urudhi: "var(--blue)",
  green: "var(--green)",
  blue: "var(--blue)",
  amber: "var(--amber)",
  red: "var(--red)",
  muted: "var(--muted)",
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

/** Cumulative-over-time line chart. Every series shares `labels` (x) and one y scale. */
export function LineChart({
  labels, series, height = 220, format, title,
}: { labels: string[]; series: Series[]; height?: number; format: (v: number) => string; title: string }) {
  const width = 760;
  const pad = { l: 72, r: 20, t: 14, b: 30 };
  const n = labels.length;
  if (n === 0 || series.every((s) => s.values.length === 0)) {
    return <Empty>no data points yet</Empty>;
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
    : [0, Math.floor(n / 4), Math.floor(n / 2), Math.floor((3 * n) / 4), n - 1];

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={pad.l - 8} y={y(t)} dy="0.35em" textAnchor="end" className="axis">{format(t)}</text>
          </g>
        ))}
        <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} className="axis-line" />
        {labelIdx.map((i) => (
          <text key={i} x={x(i)} y={height - 8} textAnchor="middle" className="axis">{labels[i]}</text>
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
      </svg>
      <figcaption className="legend">
        {series.map((s) => (
          <span key={s.label}>
            <i style={{ background: s.color }} /> {s.label}
            {s.values.length > 0 && <b className="num"> {format(s.values[s.values.length - 1])}</b>}
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

/** Horizontal bars; one row per group, one bar per entry. Also used as a plain bar list. */
export function HBars({
  groups, format, max, title, labelWidth = 150,
}: { groups: BarGroup[]; format: (v: number) => string; max?: number; title: string; labelWidth?: number }) {
  if (groups.length === 0) return <Empty>nothing to chart</Empty>;
  const width = 760;
  const barH = 14;
  const gap = 3;
  const groupGap = 10;
  const valueW = 110;
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
                  <rect x={labelWidth} y={y} width={innerW} height={barH} className="track" />
                  <rect x={labelWidth} y={y} width={w} height={barH} fill={b.color} rx={2} />
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
