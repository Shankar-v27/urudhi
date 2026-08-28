/**
 * The one place that decides how a payment instrument is shown.
 *
 * A commitment's instrument is rendered from backend facts only — never from the shape of the URL.
 * `instrument_mode` says who issued it (`razorpay_test` = a real Razorpay test-mode link, used verbatim;
 * `sandbox` = the offline fake rail, which has no checkout behind it and must never become an anchor).
 * The derived state is exposed as `deriveInstrument` so tests and other views can reason about it.
 */

import { Commitment, CommitmentChain, InstrumentMode } from "../api";
import { ModeBadge, Pill } from "../ui";

export interface InstrumentFacts {
  state: string;
  instrument_id: string | null;
  payment_url: string | null;
  instrument_mode?: InstrumentMode;
  instrument_failed?: boolean | null;
  failure_reason?: string | null;
  due_at: string | null;
}

export type InstrumentView =
  | { kind: "paid" }
  | { kind: "cancelled" }
  | { kind: "superseded" }
  | { kind: "missed"; expiredUrl: string | null }
  | { kind: "failed"; reason: string | null }
  | { kind: "not_issued" }
  | { kind: "sandbox"; id: string | null }
  | { kind: "live"; url: string }
  | { kind: "expired"; url: string }
  | { kind: "issued"; id: string | null };

const LIVE_STATES = new Set(["active", "partially_fulfilled"]);

/**
 * Precedence, top wins:
 *  1. fulfilled → paid                       (no link — the money already arrived)
 *  2. cancelled / superseded → neutral       (no link)
 *  3. missed → red; a real URL is noted as expired, never offered
 *  4. instrument_failed → red               (rail refused; nothing was issued)
 *  5. no instrument id and no URL → not issued
 *  6. mode sandbox → "Sandbox instrument"    (never an anchor)
 *  7. mode razorpay_test, live, due_at in the future → the real link, verbatim
 *  8. mode razorpay_test, live, due_at passed → expired (URL as muted text)
 *  9. anything else with an id (mode unknown / not reported) → "Issued", no link
 */
export function deriveInstrument(f: InstrumentFacts, now: number = Date.now()): InstrumentView {
  const realUrl = f.instrument_mode === "razorpay_test" && f.payment_url ? f.payment_url : null;
  if (f.state === "fulfilled") return { kind: "paid" };
  if (f.state === "cancelled") return { kind: "cancelled" };
  if (f.state === "superseded") return { kind: "superseded" };
  if (f.state === "missed") return { kind: "missed", expiredUrl: realUrl };
  if (f.instrument_failed) return { kind: "failed", reason: f.failure_reason ?? null };
  if (!f.instrument_id && !f.payment_url) return { kind: "not_issued" };
  if (f.instrument_mode === "sandbox") return { kind: "sandbox", id: f.instrument_id };
  if (f.instrument_mode === "razorpay_test" && realUrl && LIVE_STATES.has(f.state)) {
    const due = f.due_at ? Date.parse(f.due_at) : NaN;
    if (!Number.isNaN(due) && due < now) return { kind: "expired", url: realUrl };
    return { kind: "live", url: realUrl };
  }
  return { kind: "issued", id: f.instrument_id };
}

export function factsFromCommitment(c: Commitment): InstrumentFacts {
  return {
    state: c.state, instrument_id: c.instrument_id, payment_url: c.payment_url,
    instrument_mode: c.instrument_mode, instrument_failed: c.instrument_failed ?? false, due_at: c.due_at,
  };
}

export function factsFromChain(chain: CommitmentChain): InstrumentFacts {
  const ins = chain.instrument;
  return {
    state: chain.state, instrument_id: ins.id, payment_url: ins.url, instrument_mode: ins.mode,
    instrument_failed: ins.failed ?? false, failure_reason: ins.failure_reason ?? null, due_at: chain.due_at,
  };
}

export function InstrumentAction({ facts, now, compact = false }: { facts: InstrumentFacts; now?: number; compact?: boolean }) {
  const view = deriveInstrument(facts, now);
  switch (view.kind) {
    case "paid":
      return <span className="instrument"><Pill tone="success" title="Fulfilled — verified on the payment rails">Paid ✓</Pill></span>;
    case "cancelled":
      return <span className="instrument"><Pill tone="neutral">Cancelled</Pill></span>;
    case "superseded":
      return <span className="instrument"><Pill tone="neutral" title="Replaced by a newer arrangement">Superseded</Pill></span>;
    case "missed":
      return (
        <span className="instrument">
          <Pill tone="danger">Missed</Pill>
          {view.expiredUrl && <span className="note" title={view.expiredUrl}>link expired</span>}
        </span>
      );
    case "failed":
      return (
        <span className="instrument">
          <Pill tone="danger" title={view.reason ?? "The payment rail refused to issue an instrument"}>Instrument failed</Pill>
          {!compact && view.reason && <span className="note">{view.reason}</span>}
        </span>
      );
    case "not_issued":
      return <span className="instrument"><Pill tone="neutral">Not issued</Pill></span>;
    case "sandbox":
      return (
        <span className="instrument">
          <Pill tone="info" title="Simulation only — no Razorpay checkout exists">Sandbox instrument</Pill>
          {!compact && view.id && <span className="url">{view.id}</span>}
        </span>
      );
    case "live":
      return (
        <span className="instrument">
          <a className="btn primary sm" href={view.url} target="_blank" rel="noopener noreferrer"
            title={view.url} aria-label={`Open Payment Link — ${view.url}`}>
            Open Payment Link ↗
          </a>
          <ModeBadge mode="razorpay_test" />
        </span>
      );
    case "expired":
      return (
        <span className="instrument">
          <Pill tone="warn" title="The commitment deadline has passed; the link is no longer offered">Expired</Pill>
          <span className="url" title={view.url}>{view.url}</span>
        </span>
      );
    case "issued":
      return (
        <span className="instrument">
          <Pill tone="neutral" title="Instrument mode was not reported by the API; the link is not offered">Issued</Pill>
          {!compact && view.id && <span className="url">{view.id}</span>}
        </span>
      );
  }
}

/**
 * A payment URL that appears in an audit payload or intervention. The mode is taken from the commitment
 * it belongs to (looked up by id); a URL with no known mode is shown as text, never as a link.
 */
export function PayloadLink({ url, mode, commitmentFacts }: {
  url: string; mode: InstrumentMode | undefined; commitmentFacts?: InstrumentFacts;
}) {
  if (mode === "sandbox") {
    return <span className="instrument"><Pill tone="info" title="Simulation only — no Razorpay checkout exists">Sandbox</Pill><span className="url">{url}</span></span>;
  }
  if (mode === "razorpay_test" && commitmentFacts) return <InstrumentAction facts={commitmentFacts} compact />;
  if (mode === "razorpay_test") {
    return <span className="instrument"><ModeBadge mode="razorpay_test" /><span className="url">{url}</span></span>;
  }
  return <span className="instrument"><Pill tone="neutral" title="Instrument mode not recorded for this URL">URL</Pill><span className="url">{url}</span></span>;
}
