/** Overview: live KPIs, the three-arm comparison and recovery trend from the experiment, and revenue at risk. */

import { useMemo } from "react";
import {
  ArmName, Experiment, Invoice, Loaded, Summary, api, daysUntil, inr, inrShort, inrSigned, num, pct, useLoad,
} from "./api";
import {
  BarGroup, COLORS, Card, EmptyState, HBars, LineChart, MetricCard, ModeBadge, Pill, SectionHeader, Skeleton, Status,
  stateLabel,
} from "./ui";

const ARMS: ArmName[] = ["no_action", "baseline", "urudhi"];
const ARM_LABEL: Record<ArmName, string> = { no_action: "No action", baseline: "Fixed-cadence baseline", urudhi: "Urudhi" };

const SIM_COMMAND = "python -m urudhi.sim --arms all";

function money0(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : inr(v);
}

// -- KPI row -----------------------------------------------------------------

function Kpis({ summary, experiment }: { summary: Summary; experiment: Loaded<Experiment> }) {
  const rate = summary.outstanding_paise > 0 ? summary.recovered_paise / summary.outstanding_paise : null;
  const net = summary.recovered_paise - summary.waived_paise;
  const x = experiment.data;
  const c = summary.commitments;
  const nudges = c.nudges ?? null;
  const perNudge = c.recovered_per_attempt_paise ?? x?.arms.urudhi?.recovered_per_contact_attempt_paise ?? null;
  const perNudgeSimulated = c.recovered_per_attempt_paise === null || c.recovered_per_attempt_paise === undefined;
  return (
    <div className="kpis">
      <MetricCard label="Amount at Risk" value={inrShort(summary.outstanding_paise)} exact={inr(summary.outstanding_paise)}
        sub={`${num(summary.invoices)} invoices`} />
      <MetricCard label="Recovered" badge={<ModeBadge mode="observed" />} tone="accent"
        value={inrShort(summary.recovered_paise)} exact={inr(summary.recovered_paise)}
        sub="Observed on payment rails" />
      <MetricCard label="Recovery Rate" value={pct(rate)} exact={rate === null ? undefined : `${inr(summary.recovered_paise)} of ${inr(summary.outstanding_paise)}`}
        sub={`${num(summary.messages_sent)} messages sent`} />
      <MetricCard label="Net Recovered" value={inrShort(net)} exact={inr(net)}
        sub={`after ${inr(summary.waived_paise)} waived`} />
      <MetricCard label="Recovery Uplift vs baseline" badge={<ModeBadge mode="simulation" />}
        value={x ? inrShort(x.uplift.urudhi_vs_baseline_paise) : "—"}
        exact={x ? inrSigned(x.uplift.urudhi_vs_baseline_paise) : undefined}
        sub={x ? `${x.uplift.urudhi_vs_baseline_points >= 0 ? "+" : ""}${num(x.uplift.urudhi_vs_baseline_points, 1)} pts recovery rate` : experiment.error ? "no experiment report" : "loading experiment…"} />
      <MetricCard label="Contact Efficiency" badge={perNudgeSimulated && perNudge !== null ? <ModeBadge mode="simulation" /> : undefined}
        value={perNudge === null ? "—" : inrShort(perNudge)} exact={perNudge === null ? undefined : inr(perNudge)}
        sub={perNudge === null ? "no nudges yet" : `₹ recovered per nudge${nudges !== null ? ` · ${num(nudges)} nudges` : ""}`} />
    </div>
  );
}

// -- three-arm comparison ------------------------------------------------------

function ArmComparison({ x }: { x: Experiment }) {
  const arms = ARMS.filter((a) => x.arms[a]);
  const maxRisk = Math.max(1, ...arms.map((a) => x.arms[a].amount_at_risk_paise));
  const recovered: BarGroup[] = [{
    label: "Recovered",
    bars: arms.map((a) => ({ label: ARM_LABEL[a], value: x.arms[a].recovered_paise, color: COLORS[a], note: pct(x.arms[a].recovery_rate) })),
  }];
  const perContact: BarGroup[] = [{
    label: "₹ per contact",
    bars: arms.map((a) => ({
      label: ARM_LABEL[a], color: COLORS[a],
      value: x.arms[a].recovered_per_contact_attempt_paise ?? (x.arms[a].contact_attempts > 0 ? Math.round(x.arms[a].recovered_paise / x.arms[a].contact_attempts) : 0),
      note: `${num(x.arms[a].contact_attempts)} nudges`,
    })),
  }];
  const perContactValue = (a: ArmName) =>
    x.arms[a].recovered_per_contact_attempt_paise ?? (x.arms[a].contact_attempts > 0 ? Math.round(x.arms[a].recovered_paise / x.arms[a].contact_attempts) : null);
  const rows: { label: string; value: (a: ArmName) => string; sub?: (a: ArmName) => string }[] = [
    { label: "Recovery rate", value: (a) => pct(x.arms[a].recovery_rate), sub: (a) => `${num(x.arms[a].invoices_paid)} of ${num(x.arms[a].invoices)} paid` },
    { label: "Recovered", value: (a) => inr(x.arms[a].recovered_paise), sub: (a) => `net ${inr(x.arms[a].net_recovered_paise)}` },
    { label: "Messages (nudges)", value: (a) => num(x.arms[a].contact_attempts) },
    { label: "₹ per contact", value: (a) => money0(perContactValue(a)) },
    { label: "Days to recovery (median)", value: (a) => x.arms[a].days_to_recovery_median === null ? "—" : `${num(x.arms[a].days_to_recovery_median, 1)} d`,
      sub: (a) => x.arms[a].days_to_recovery_mean === null ? "no recoveries" : `mean ${num(x.arms[a].days_to_recovery_mean, 1)} d` },
    { label: "Promises kept", value: (a) => `${num(x.arms[a].promises_kept)} / ${num(x.arms[a].promises_made)}`, sub: (a) => pct(x.arms[a].promise_kept_rate) },
    { label: "Escalations · disputes · stop-contact", value: (a) => `${num(x.arms[a].escalations)} · ${num(x.arms[a].disputes)} · ${num(x.arms[a].stop_contacts)}` },
  ];
  return (
    <Card>
      <SectionHeader title="No action · Fixed-cadence baseline · Urudhi"
        badge={<><ModeBadge mode="simulation" /><ModeBadge mode="persona" /></>}
        description={<>{x.generated_by} · seed {x.seed} · {x.days} days · {num(x.count)} synthetic invoices · brain {x.brain}. Three arms start from byte-identical portfolios; the difference is strategy under the persona model, not evidence about real debtors.</>} />
      <div className="arm-grid" role="table" aria-label="Arm comparison">
        <div className="head" role="columnheader">Metric</div>
        {arms.map((a) => <div key={a} className={`head v ${a}`} role="columnheader">{ARM_LABEL[a]}</div>)}
        {rows.map((r) => (
          <div key={r.label} style={{ display: "contents" }} role="row">
            <div role="rowheader">{r.label}</div>
            {arms.map((a) => (
              <div key={a} className={`v ${a}`} role="cell">
                {r.value(a)}
                {r.sub && <span className="sub">{r.sub(a)}</span>}
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="columns" style={{ marginTop: 16 }}>
        <div>
          <h4 style={{ marginBottom: 6 }}>Recovered · scaled to amount at risk {inrShort(maxRisk)}</h4>
          <HBars title="Recovered per arm" groups={recovered} max={maxRisk} format={inrShort} labelWidth={110} />
        </div>
        <div>
          <h4 style={{ marginBottom: 6 }}>₹ recovered per contact</h4>
          <HBars title="Rupees recovered per contact per arm" groups={perContact} format={inrShort} labelWidth={110} />
        </div>
      </div>
      <div className="kpis" style={{ marginTop: 16 }}>
        <MetricCard label="Urudhi vs baseline" size="md" badge={<ModeBadge mode="simulation" />} value={inrSigned(x.uplift.urudhi_vs_baseline_paise)}
          sub={`${x.uplift.urudhi_vs_baseline_points >= 0 ? "+" : ""}${num(x.uplift.urudhi_vs_baseline_points, 1)} pts recovery rate`} />
        <MetricCard label="Urudhi vs no action" size="md" badge={<ModeBadge mode="simulation" />} value={inrSigned(x.uplift.urudhi_vs_no_action_paise)}
          sub={`${x.uplift.urudhi_vs_no_action_points >= 0 ? "+" : ""}${num(x.uplift.urudhi_vs_no_action_points, 1)} pts recovery rate`} />
        <MetricCard label="Net of discounts vs baseline" size="md" badge={<ModeBadge mode="simulation" />} value={inrSigned(x.uplift.net_urudhi_vs_baseline_paise)}
          sub="recovered minus discount cost" />
      </div>
    </Card>
  );
}

function Trend({ x }: { x: Experiment }) {
  const arms = ARMS.filter((a) => x.arms[a] && x.timeline[a]);
  return (
    <Card>
      <SectionHeader title="Cumulative recovery" badge={<ModeBadge mode="simulation" />}
        description="Recovered paise per day, cumulative, for each arm of the experiment. Hover for the exact figures." />
      <LineChart title="Simulated cumulative recovered per day per arm" labels={x.timeline.days}
        series={arms.map((a) => ({ label: ARM_LABEL[a], color: COLORS[a], values: x.timeline[a] }))} format={inrShort} />
    </Card>
  );
}

function ObservedTrend() {
  const timeline = useLoad(api.timeline);
  return (
    <Card>
      <SectionHeader title="Recovery on the rails" badge={<ModeBadge mode="observed" />}
        description="Cumulative paise reported by the payment rails on the live ledger." />
      <Status load={timeline} rows={4}>
        {(t) => (
          <LineChart title="Cumulative recovered per day, observed" labels={t.series.map((p) => p.day)}
            series={[{ label: "Recovered (cumulative)", color: COLORS.urudhi, values: t.series.map((p) => p.recovered_cumulative) }]}
            format={inrShort} />
        )}
      </Status>
    </Card>
  );
}

// -- revenue at risk -----------------------------------------------------------

type Bucket = { label: string; tone: "" | "warn" | "danger" | "neutral"; count: number; paise: number };

function ageBuckets(invoices: Invoice[], today?: Date): Bucket[] {
  const buckets: Bucket[] = [
    { label: "Not yet due", tone: "neutral", count: 0, paise: 0 },
    { label: "1–30 days", tone: "", count: 0, paise: 0 },
    { label: "31–60 days", tone: "warn", count: 0, paise: 0 },
    { label: "61–90 days", tone: "warn", count: 0, paise: 0 },
    { label: "90+ days", tone: "danger", count: 0, paise: 0 },
  ];
  for (const i of invoices) {
    if (i.balance <= 0) continue;
    const d = daysUntil(i.due_on, today);
    const overdue = d === null ? 0 : -d;
    const idx = overdue <= 0 ? 0 : overdue <= 30 ? 1 : overdue <= 60 ? 2 : overdue <= 90 ? 3 : 4;
    buckets[idx].count += 1;
    buckets[idx].paise += i.balance;
  }
  return buckets;
}

function BucketList({ items, format }: { items: { label: string; tone: string; value: number; sub?: string }[]; format: (v: number) => string }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="buckets">
      {items.map((i) => (
        <li key={i.label} className={i.tone}>
          <span>{i.label}</span>
          <span className="bar" aria-hidden="true"><i style={{ width: `${(i.value / max) * 100}%` }} /></span>
          <span className="v">{format(i.value)}{i.sub && <small>{i.sub}</small>}</span>
        </li>
      ))}
    </ul>
  );
}

function RevenueAtRisk({ summary, invoices }: { summary: Summary; invoices: Loaded<Invoice[]> }) {
  const promises = useLoad(api.promises);
  const rows = invoices.data ?? [];
  const byState = useMemo(() => {
    const m = new Map<string, { count: number; paise: number }>();
    for (const i of rows) {
      const e = m.get(i.state) ?? { count: 0, paise: 0 };
      e.count += 1; e.paise += i.balance;
      m.set(i.state, e);
    }
    return m;
  }, [rows]);
  const buckets = useMemo(() => ageBuckets(rows), [rows]);
  const openPromised = (promises.data ?? []).filter((p) => p.state === "open").reduce((s, p) => s + p.amount, 0);
  const openCount = (promises.data ?? []).filter((p) => p.state === "open").length;
  const escalatedBalance = rows.filter((i) => i.state === "escalated" || i.state === "disputed").reduce((s, i) => s + i.balance, 0);
  const escalatedCount = rows.filter((i) => i.state === "escalated" || i.state === "disputed").length;
  const outstandingBalance = rows.reduce((s, i) => s + i.balance, 0);
  const toneOf = (state: string): string =>
    state === "escalated" || state === "disputed" ? "danger" : state === "paid" || state === "closed" ? "neutral"
      : state === "stop_contact" ? "neutral" : state === "promised" ? "" : "warn";
  const stateOrder = Object.keys(summary.by_state);
  const sorted = (keys: string[]) => [...keys].sort((a, b) => stateOrder.indexOf(a) - stateOrder.indexOf(b));

  return (
    <Card>
      <SectionHeader title="Revenue at risk" badge={<ModeBadge mode="observed" label="Live ledger" title="Computed from the live invoices, promises and summary endpoints" />}
        description="Where the outstanding balance sits right now: by invoice state, by promise, and by how long it has been overdue." />
      <div className="kpis" style={{ marginBottom: 16 }}>
        <MetricCard label="Open balance" size="md" value={inrShort(outstandingBalance)} exact={inr(outstandingBalance)}
          sub={`${num(rows.filter((i) => i.balance > 0).length)} invoices with a balance`} />
        <MetricCard label="Promised (open promises)" size="md" value={promises.data ? inrShort(openPromised) : "…"} exact={inr(openPromised)}
          sub={promises.data ? `${num(openCount)} open promise${openCount === 1 ? "" : "s"}` : promises.error ? "promises unavailable" : "loading"} />
        <MetricCard label="Escalated / disputed balance" size="md" tone={escalatedBalance > 0 ? "danger" : undefined}
          value={inrShort(escalatedBalance)} exact={inr(escalatedBalance)} sub={`${num(escalatedCount)} invoices waiting on a human`} />
        <MetricCard label="Stop-contact" size="md" value={num(summary.by_state.stop_contact ?? 0)} sub="debtors who asked us to stop" />
      </div>
      {invoices.data === null && !invoices.error && <Skeleton rows={4} />}
      {invoices.error && <Status load={invoices}>{() => null}</Status>}
      {invoices.data && (
        <div className="risk-grid">
          <div>
            <h4 style={{ marginBottom: 8 }}>Invoices by state</h4>
            <BucketList format={(v) => num(v)} items={sorted(Object.keys(summary.by_state)).map((s) => ({
              label: stateLabel(s), tone: toneOf(s), value: summary.by_state[s],
            }))} />
          </div>
          <div>
            <h4 style={{ marginBottom: 8 }}>Outstanding balance by state</h4>
            <BucketList format={inrShort} items={sorted(Array.from(byState.keys())).filter((s) => (byState.get(s)?.paise ?? 0) > 0).map((s) => ({
              label: stateLabel(s), tone: toneOf(s), value: byState.get(s)!.paise, sub: `${num(byState.get(s)!.count)} invoices`,
            }))} />
          </div>
          <div>
            <h4 style={{ marginBottom: 8 }}>Overdue age of open balance</h4>
            <BucketList format={inrShort} items={buckets.map((b) => ({ label: b.label, tone: b.tone, value: b.paise, sub: `${num(b.count)} invoices` }))} />
          </div>
        </div>
      )}
    </Card>
  );
}

// -- page ------------------------------------------------------------------------

export function Overview({ summary, invoices }: { summary: Loaded<Summary>; invoices: Loaded<Invoice[]> }) {
  const experiment = useLoad(api.experiment);
  const missing = (
    <Card>
      <EmptyState title="No experiment report yet"
        hint={<>Run the three-arm simulation to produce <code>data/experiment.json</code>, then reload.</>}
        command={SIM_COMMAND} />
    </Card>
  );
  return (
    <div className="stack">
      <div>
        <div className="page-title"><h1>Overview</h1>{summary.data && <Pill tone="outline">brain {summary.data.brain} · {summary.data.transport}</Pill>}</div>
        <p className="page-desc">Recovered means money the payment rails reported. Uplift and efficiency comparisons come from the simulated experiment and are labelled as such.</p>
        <Status load={summary} rows={3}>{(s) => <Kpis summary={s} experiment={experiment} />}</Status>
      </div>
      <Status load={experiment} notFound={missing} rows={6}>{(x) => <ArmComparison x={x} />}</Status>
      <Status load={experiment} notFound={null} rows={4}>{(x) => <Trend x={x} />}</Status>
      <ObservedTrend />
      <Status load={summary} rows={3}>{(s) => <RevenueAtRisk summary={s} invoices={invoices} />}</Status>
      {experiment.data && experiment.data.caveats.length > 0 && (
        <details className="caveats">
          <summary>What the simulated numbers are (and aren't)</summary>
          <ul>{experiment.data.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </details>
      )}
    </div>
  );
}
