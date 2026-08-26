/** Overview: live summary tiles, recovery-over-time, and the simulated arm comparison. */

import { ReactNode } from "react";
import {
  ArmMetrics, ArmName, Experiment, Loaded, Summary, api, inr, inrShort, inrSigned, num, pct, useLoad,
} from "./api";
import { BarGroup, COLORS, HBars, LineChart, Status, Tag, Tile } from "./ui";

const ARMS: ArmName[] = ["no_action", "baseline", "urudhi"];

function Tiles({ summary }: { summary: Summary }) {
  const rate = summary.outstanding_paise > 0 ? summary.recovered_paise / summary.outstanding_paise : 0;
  return (
    <div className="tiles">
      <Tile label="Invoices" value={num(summary.invoices)} />
      <Tile label="Outstanding" value={inr(summary.outstanding_paise)} />
      <Tile label="Recovered — observed on rails" value={inr(summary.recovered_paise)} />
      <Tile label="Waived (discount cost)" value={inr(summary.waived_paise)} />
      <Tile label="Recovery rate" value={pct(rate)} sub={`${num(summary.messages_sent)} messages sent`} />
      <Tile label="Brain · transport" value={<span className="mode">{summary.brain} · {summary.transport}</span>} />
    </div>
  );
}

function RecoveryTimeline() {
  const timeline = useLoad(api.timeline);
  return (
    <section>
      <h2>Cumulative recovered over time <Tag>observed</Tag></h2>
      <Status load={timeline}>
        {(t) => (
          <LineChart
            title="Cumulative recovered paise per day"
            labels={t.series.map((p) => p.day)}
            series={[{ label: "recovered (cumulative)", color: COLORS.green,
              values: t.series.map((p) => p.recovered_cumulative) }]}
            format={inrShort}
          />
        )}
      </Status>
    </section>
  );
}

type Row = { key: keyof ArmMetrics; label: string; format: (v: number | null) => string };
const money = (v: number | null) => (v === null ? "—" : inr(v));
const count = (v: number | null) => num(v);
const rate = (v: number | null) => pct(v);
const days = (v: number | null) => (v === null ? "—" : `${num(v, 1)} d`);

const METRIC_ROWS: Row[] = [
  { key: "invoices", label: "Invoices", format: count },
  { key: "amount_at_risk_paise", label: "Amount at risk", format: money },
  { key: "recovered_paise", label: "Recovered", format: money },
  { key: "recovery_rate", label: "Recovery rate", format: rate },
  { key: "discount_cost_paise", label: "Discount cost", format: money },
  { key: "net_recovered_paise", label: "Net recovered", format: money },
  { key: "invoices_paid", label: "Invoices paid", format: count },
  { key: "promises_made", label: "Promises made", format: count },
  { key: "promises_kept", label: "Promises kept", format: count },
  { key: "promises_broken", label: "Promises broken", format: count },
  { key: "promise_kept_rate", label: "Promise kept rate", format: rate },
  { key: "escalations", label: "Escalations", format: count },
  { key: "disputes", label: "Disputes", format: count },
  { key: "stop_contacts", label: "Stop-contact requests", format: count },
  { key: "contact_attempts", label: "Contact attempts", format: count },
  { key: "offers_made", label: "Offers made", format: count },
  { key: "offers_accepted", label: "Offers accepted", format: count },
  { key: "days_to_recovery_median", label: "Days to recovery (median)", format: days },
  { key: "days_to_recovery_mean", label: "Days to recovery (mean)", format: days },
];

function metric(arm: ArmMetrics, row: Row): string {
  const value = arm[row.key];
  return typeof value === "number" ? row.format(value) : row.format(null);
}

function bucketOrder(a: string, b: string): number {
  const na = parseFloat(a.replace(/[^\d.-]/g, ""));
  const nb = parseFloat(b.replace(/[^\d.-]/g, ""));
  if (Number.isNaN(na) || Number.isNaN(nb)) return a.localeCompare(b);
  return na - nb;
}

function ArmComparison({ x }: { x: Experiment }) {
  const arms = ARMS.filter((a) => x.arms[a]);
  const maxRisk = Math.max(...arms.map((a) => x.arms[a].amount_at_risk_paise), 1);
  const recoveredGroups: BarGroup[] = arms.map((a) => ({
    label: x.arms[a].label,
    bars: [
      { label: "recovered", value: x.arms[a].recovered_paise, color: COLORS.blue,
        note: pct(x.arms[a].recovery_rate) },
      { label: "net recovered", value: x.arms[a].net_recovered_paise, color: COLORS.green },
    ],
  }));

  const buckets = Array.from(new Set(
    Object.values(x.days_to_recovery).flatMap((d) => Object.keys(d.histogram)),
  )).sort(bucketOrder);
  const histogram: BarGroup[] = buckets.map((bucket) => ({
    label: `${bucket} days`,
    bars: arms.map((a) => ({
      label: x.arms[a].label, color: COLORS[a],
      value: x.days_to_recovery[a]?.histogram[bucket] ?? 0,
    })),
  }));

  const attributionArms = (["urudhi", "baseline"] as const).filter((a) => x.attribution.arms[a]);

  return (
    <section className="experiment">
      <h2>Urudhi vs baseline vs no action <Tag tone="sim">simulated</Tag></h2>
      <p className="muted">
        {x.generated_by} · seed {x.seed} · {x.days} days · {num(x.count)} synthetic invoices · brain: {x.brain}.
        Every number in this section comes from the simulator, not from the live ledger.
      </p>

      <div className="tiles">
        <Tile label="Urudhi vs baseline" value={inrSigned(x.uplift.urudhi_vs_baseline_paise)}
          sub={`${x.uplift.urudhi_vs_baseline_points >= 0 ? "+" : ""}${num(x.uplift.urudhi_vs_baseline_points, 1)} pts recovery rate`} />
        <Tile label="Urudhi vs no action" value={inrSigned(x.uplift.urudhi_vs_no_action_paise)}
          sub={`${x.uplift.urudhi_vs_no_action_points >= 0 ? "+" : ""}${num(x.uplift.urudhi_vs_no_action_points, 1)} pts recovery rate`} />
        <Tile label="Net of discounts vs baseline" value={inrSigned(x.uplift.net_urudhi_vs_baseline_paise)}
          sub="recovered minus discount cost" />
      </div>

      <h3>Recovered and net recovered, by arm</h3>
      <HBars title="Recovered and net recovered per arm" groups={recoveredGroups} max={maxRisk} format={inrShort} />
      <p className="muted small">Bars are scaled to the amount at risk ({inr(maxRisk)}); the note after each recovered bar is the recovery rate.</p>

      <h3>Cumulative recovered per day, by arm</h3>
      <LineChart
        title="Simulated cumulative recovered per day per arm"
        labels={x.timeline.days}
        series={arms.map((a) => ({ label: x.arms[a].label, color: COLORS[a], values: x.timeline[a] ?? [] }))}
        format={inrShort}
      />

      <h3>All arm metrics</h3>
      <div className="scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>Metric</th>
              {arms.map((a) => <th key={a} className="num">{x.arms[a].label}</th>)}
            </tr>
          </thead>
          <tbody>
            {METRIC_ROWS.map((row) => (
              <tr key={row.key}>
                <td>{row.label}</td>
                {arms.map((a) => <td key={a} className="num">{metric(x.arms[a], row)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Days to recovery</h3>
      <div className="tiles">
        {arms.map((a) => {
          const d = x.days_to_recovery[a];
          return (
            <Tile key={a} label={x.arms[a].label}
              value={d && d.median !== null ? `${num(d.median, 1)} d median` : "—"}
              sub={d && d.mean !== null ? `${num(d.mean, 1)} d mean` : "no recoveries"} />
          );
        })}
      </div>
      {histogram.length > 0 && (
        <HBars title="Days-to-recovery histogram by arm" groups={histogram} format={(v) => num(v)} />
      )}

      <h3>Attribution by intervention</h3>
      <p className="muted small">Rule: {x.attribution.rule} · window {x.attribution.window_days} days.</p>
      <div className="columns">
        {attributionArms.map((a) => {
          const att = x.attribution.arms[a]!;
          const items: BarGroup[] = Object.entries(att.by_intervention)
            .sort((p, q) => q[1].paise - p[1].paise)
            .map(([kind, b]) => ({
              label: kind.replace(/_/g, " "),
              bars: [{ label: kind, value: b.paise, color: COLORS[a], note: `${b.payments} payments` }],
            }));
          items.push({
            label: "unattributed",
            bars: [{ label: "unattributed", value: att.unattributed.paise, color: COLORS.muted,
              note: `${att.unattributed.payments} payments` }],
          });
          return (
            <div key={a}>
              <h4>{x.arms[a].label}</h4>
              <HBars title={`Attribution for ${x.arms[a].label}`} groups={items} format={inrShort} labelWidth={130} />
            </div>
          );
        })}
      </div>

      <h3>Policy sensitivity</h3>
      {x.sensitivity.length === 0 ? <p className="muted">no sensitivity sweep in this report</p> : (
        <div className="scroll">
          <table className="compact">
            <thead>
              <tr>
                <th>Parameter</th>
                <th className="num">Value</th>
                <th className="num">Recovered</th>
                <th className="num">Rate</th>
                <th className="num">Messages</th>
                <th className="num">Escalations</th>
                <th className="num">Discount cost</th>
                <th className="num">Stop contacts</th>
              </tr>
            </thead>
            <tbody>
              {x.sensitivity.map((s, i) => (
                <tr key={`${s.parameter}-${s.value}-${i}`}>
                  <td>{s.parameter}</td>
                  <td className="num">{num(s.value, Number.isInteger(s.value) ? 0 : 2)}</td>
                  <td className="num">{inr(s.recovered_paise)}</td>
                  <td className="num">{pct(s.recovery_rate)}</td>
                  <td className="num">{num(s.messages_sent)}</td>
                  <td className="num">{num(s.escalations)}</td>
                  <td className="num">{inr(s.discount_cost_paise)}</td>
                  <td className="num">{num(s.stop_contacts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="caveats">
        <h3>What these numbers are (and aren't)</h3>
        <ul>
          {x.caveats.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      </div>
    </section>
  );
}

export function Overview({ summary }: { summary: Loaded<Summary> }) {
  const experiment = useLoad(api.experiment);
  const runNote: ReactNode = (
    <>
      No experiment report yet. Run <code>python -m urudhi.sim --arms all</code> to produce
      <code>data/experiment.json</code>, then reload.
    </>
  );
  return (
    <>
      <Status load={summary}>{(s) => <Tiles summary={s} />}</Status>
      <RecoveryTimeline />
      <section>
        <Status load={experiment} notFound={runNote}>{(x) => <ArmComparison x={x} />}</Status>
      </section>
    </>
  );
}
