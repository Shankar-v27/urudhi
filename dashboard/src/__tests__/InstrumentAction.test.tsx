import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InstrumentAction, deriveInstrument, factsFromCommitment } from "../components/InstrumentAction";
import { LIVE_URL, liveCommitment, sandboxCommitment } from "./fixtures";

// A clock before the live commitment's deadline (2026-08-28T23:59:59+05:30).
const BEFORE_DUE = Date.parse("2026-08-28T10:00:00+05:30");
const AFTER_DUE = Date.parse("2026-08-29T10:00:00+05:30");

describe("InstrumentAction", () => {
  it("renders a real, live Razorpay test-mode link as an anchor with the exact URL", () => {
    render(<InstrumentAction facts={factsFromCommitment(liveCommitment)} now={BEFORE_DUE} />);
    const link = screen.getByRole("link", { name: /Open Payment Link/ });
    expect(link).toHaveAttribute("href", LIVE_URL);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("title", LIVE_URL);
    expect(link).toHaveTextContent("Open Payment Link");
    expect(screen.getByText("Razorpay Test Mode")).toBeInTheDocument();
  });

  it("never renders a sandbox instrument as a link, whatever the URL looks like", () => {
    const facts = factsFromCommitment({ ...sandboxCommitment, state: "active", payment_url: "https://rzp.io/l/fake0076" });
    render(<InstrumentAction facts={facts} now={Date.parse("2026-08-25T10:00:00+05:30")} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(/Sandbox/)).toBeInTheDocument();
    expect(document.querySelector("a")).toBeNull();
    expect(document.body.innerHTML).not.toContain("href=");
  });

  it("shows Paid for a fulfilled commitment with no link", () => {
    render(<InstrumentAction facts={factsFromCommitment(sandboxCommitment)} />);
    expect(screen.getByText(/Paid/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("shows Missed and notes the expired real link without offering it", () => {
    render(<InstrumentAction facts={factsFromCommitment({ ...liveCommitment, state: "missed" })} now={AFTER_DUE} />);
    expect(screen.getByText("Missed")).toBeInTheDocument();
    expect(screen.getByText("link expired")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("shows Not issued when there is neither an instrument id nor a URL", () => {
    render(<InstrumentAction facts={factsFromCommitment({ ...liveCommitment, instrument_id: null, payment_url: null, instrument_mode: null })} />);
    expect(screen.getByText("Not issued")).toBeInTheDocument();
  });

  it("shows Instrument failed when the rail refused", () => {
    render(<InstrumentAction facts={{ ...factsFromCommitment({ ...liveCommitment, instrument_id: null, payment_url: null, instrument_mode: null }), instrument_failed: true, failure_reason: "rail unreachable" }} />);
    expect(screen.getByText("Instrument failed")).toBeInTheDocument();
    expect(screen.getByText("rail unreachable")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("shows Expired with the URL as text once the deadline has passed on a live commitment", () => {
    render(<InstrumentAction facts={factsFromCommitment(liveCommitment)} now={AFTER_DUE} />);
    expect(screen.getByText("Expired")).toBeInTheDocument();
    expect(screen.getByText(LIVE_URL)).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("shows Cancelled / Superseded as neutral text", () => {
    const { unmount } = render(<InstrumentAction facts={factsFromCommitment({ ...liveCommitment, state: "cancelled" })} />);
    expect(screen.getByText("Cancelled")).toBeInTheDocument();
    unmount();
    render(<InstrumentAction facts={factsFromCommitment({ ...liveCommitment, state: "superseded" })} />);
    expect(screen.getByText("Superseded")).toBeInTheDocument();
  });

  it("decides by instrument_mode, not by URL host", () => {
    // A rzp.io URL with a sandbox mode is sandbox; a real mode with a real URL is live.
    expect(deriveInstrument({ state: "active", instrument_id: "plink_fake_1", payment_url: "https://rzp.io/l/fake0001", instrument_mode: "sandbox", due_at: liveCommitment.due_at }, BEFORE_DUE).kind).toBe("sandbox");
    expect(deriveInstrument(factsFromCommitment(liveCommitment), BEFORE_DUE)).toEqual({ kind: "live", url: LIVE_URL });
    // Unknown mode with an id → issued but never a link.
    expect(deriveInstrument({ state: "active", instrument_id: "plink_x", payment_url: "https://rzp.io/rzp/x", instrument_mode: undefined, due_at: liveCommitment.due_at }, BEFORE_DUE).kind).toBe("issued");
  });
});
