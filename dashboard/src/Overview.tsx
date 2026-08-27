/** Overview: live summary tiles, recovery-over-time, and the simulated arm comparison. */

import { ReactNode } from "react";
import {
  ArmCommitments, ArmMetrics, ArmName, AttributionMethod, CommitmentSummary, Experiment, Loaded, Summary,
  api, inr, inrShort, inrSigned, num, pct, useLoad,
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

const money0 = (v: number | null | undefined) => (v === null || v === undefined ? "—" : inr(v));

function CommitmentTiles({ c }: { c: CommitmentSummary }) {
  const resolved = c.fulfilled + c.missed;
  return (
    <section>
      <h2>Commitments — accepted, instrumented, verified on rails <Tag>observed</Tag></h2>
      <div className="tiles">
        <Tile label="Commitments created" value={num(c.created)}
          sub={`${num(c.active)} active · ${num(c.partially_fulfilled)} partial · ${num(c.cancelled)} cancelled`} />
        <Tile label="Fulfilment rate" value={pct(c.fulfillment_rate)}
          sub={resolved > 0 ? `${num(c.fulfilled)} of ${num(resolved)} resolved · ${num(c.missed)} missed` : "nothing resolved yet"} />
        <Tile label="₹ recovered per commitment" value={money0(c.recovered_per_commitment_paise)}
          sub={c.conversion !== null ? `${pct(c.conversion, 0)} of commitments saw money` : "no commitments"} />
        <Tile label="₹ recovered per contact attempt" value={money0(c.recovered_per_attempt_paise)}
          sub={c.average_delay_days !== null ? `avg delay ${num(c.average_delay_days, 1)} d on fulfilled` : "no fulfilled commitments"} />
        <Tile label="Exact-matched on instrument" value={inr(c.exact_instrument_matched_paise)}
          sub={`${inr(c.amount_received_paise)} received of ${inr(c.amount_committed_paise)} committed`} />
      </div>
    </section>
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
type CommitmentRow = { label: string; value: (c: ArmCommitments) => string };
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
  { key: "recovered_per_contact_attempt_paise", label: "₹ recovered per contact attempt", format: money },
  { key: "offers_made", label: "Offers made", format: count },
  { key: "offers_accepted", label: "Offers accepted", format: count },
  { key: "days_to_recovery_median", label: "Days to recovery (median)", format: days },
  { key: "days_to_recovery_mean", label: "Days to recovery (mean)", format: days },
];

const COMMITMENT_ROWS: CommitmentRow[] = [
  { label: "Commitments created", value: (c) => num(c.created) },
  { label: "  from promises / concessions / installments / human",
    value: (c) => ["promise", "concession", "installment", "human"].map((k) => num(c.by_source[k] ?? 0)).join(" / ") },
  { label: "Instruments issued", value: (c) => num(c.instruments_issued) },
  { label: "Fulfilled (on time)", value: (c) => `${num(c.fulfilled)} (${num(c.fulfilled_on_time)})` },
  { label: "Partially fulfilled", value: (c) => num(c.partially_fulfilled) },
  { label: "Missed", value: (c) => num(c.missed) },
  { label: "Cancelled", value: (c) => num(c.cancelled) },
  { label: "Active at end", value: (c) => num(c.active_at_end) },
  { label: "Fulfilment rate", value: (c) => pct(c.fulfillment_rate) },
  { label: "Commitment → payment conversion", value: (c) => pct(c.commitment_to_payment_conversion) },
  { label: "Median days commitment → payment", value: (c) => days(c.median_days_commitment_to_payment) },
  { label: "Average delay on fulfilled", value: (c) => days(c.average_delay_days) },
  { label: "Amount committed", value: (c) => inr(c.amount_committed_paise) },
  { label: "Amount fulfilled", value: (c) => inr(c.amount_fulfilled_paise) },
  { label: "₹ recovered / commitment", value: (c) => money0(c.recovered_per_commitment_paise) },
  { label: "₹ recovered / contact attempt", value: (c) => money0(c.recovered_per_contact_attempt_paise) },
  { label: "Exact-matched payments (₹)", value: (c) => `${num(c.exact_matched_payments)} (${inr(c.exact_matched_paise)})` },
];

const METHODS: AttributionMethod[] = ["exact", "window", "unattributed"];
const METHOD_COLOR: Record<AttributionMethod, string> = {
  exact: COLORS.green, window: COLORS.blue, unattributed: COLORS.muted,
};

function CommitmentComparison({ x, arms }: { x: Experiment; arms: ArmName[] }) {
  const withData = arms.filter((a) => x.arms[a].commitments);
  if (withData.length === 0) {
    return (
      <p className="muted">
        This report predates the commitment engine — no per-arm commitment metrics were written. Re-run{" "}
        <code>python -m urudhi.sim --arms all</code> to measure them.
      </p>
    );
  }
  const bars: BarGroup[] = withData.map((a) => {
    const c = x.arms[a].commitments!;
    return {
      label: x.arms[a].label,
      bars: [
        { label: "₹ / commitment", value: c.recovered_per_commitment_paise ?? 0, color: COLORS.green,
          note: c.created === 0 ? "no commitments in this arm" : `${num(c.created)} created` },
        { label: "₹ / contact attempt", value: c.recovered_per_contact_attempt_paise ?? 0, color: COLORS.blue,
          note: pct(c.fulfillment_rate) === "—" ? "no fulfilment rate" : `fulfilment ${pct(c.fulfillment_rate, 0)}` },
      ],
    };
  });
  return (
    <>
      <p className="muted small">
        Only the Urudhi arm runs the commitment engine; the no-action and baseline arms never create
        commitments, so their zeros are real, not missing data.
      </p>
      <div className="scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>Metric</th>
              {withData.map((a) => <th key={a} className="num">{x.arms[a].label}</th>)}
            </tr>
          </thead>
          <tbody>
            {COMMITMENT_ROWS.map((row) => (
              <tr key={row.label}>
                <td>{row.label}</td>
                {withData.map((a) => {
                  const c = x.arms[a].commitments!;
                  return <td key={a} className="num">{c.created === 0 && row.label !== "Commitments created" ? <span className="muted">— (0 commitments)</span> : row.value(c)}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <HBars title="Recovered per commitment and per contact attempt, by arm" groups={bars} format={inrShort} />
    </>
  );
}

function AttributionByMethod({ x, arms }: { x: Experiment; arms: ("urudhi" | "baseline")[] }) {
  const withMethod = arms.filter((a) => x.attribution.arms[a]?.by_method);
  if (withMethod.length === 0) {
    return <p className="muted small">No attribution-by-method breakdown in this report (it predates exact instrument matching).</p>;
  }
  const groups: BarGroup[] = withMethod.map((a) => {
    const m = x.attribution.arms[a]!.by_method!;
    return {
      label: x.arms[a].label,
      bars: METHODS.map((k) => ({
        label: k, value: m[k].paise, color: METHOD_COLOR[k], note: `${num(m[k].payments)} payments`,
      })),
    };
  });
  return (
    <>
      <p className="muted small">
        <b>exact</b> = the payment came through the Razorpay link tagged with a commitment id;{" "}
        <b>window</b> = attributed to the last message within {x.attribution.window_days} days;{" "}
        <b>unattributed</b> = no intervention can claim it.
      </p>
      <HBars title="Attributed paise by matching method, per arm" groups={groups} format={inrShort} />
    </>
  );
}

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

      <h3>Commitment engine, by arm</h3>
      <CommitmentComparison x={x} arms={arms} />

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

      <h3>Attribution by matching method</h3>
      <AttributionByMethod x={x} arms={attributionArms} />

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
      <Status load={summary}>{(s) => <CommitmentTiles c={s.commitments} />}</Status>
      <RecoveryTimeline />
      <section>
        <Status load={experiment} notFound={runNote}>{(x) => <ArmComparison x={x} />}</Status>
      </section>
    </>
  );
}
