import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InstrumentAction, deriveInstrument, factsFromCommitment } from "../components/InstrumentAction";
import { LIVE_URL, SANDBOX_URL, failedCommitment, liveCommitment, sandboxCommitment } from "./fixtures";

// A clock before the live commitment's deadline (2026-08-28T23:59:59+05:30).
const BEFORE_DUE = Date.parse("2026-08-28T10:00:00+05:30");
const AFTER_DUE = Date.parse("2026-08-29T10:00:00+05:30");
const SANDBOX_TIP = "Simulation only — no Razorpay checkout exists";

describe("InstrumentAction (driven by instrument_mode, never by the URL)", () => {
  it("renders a live razorpay_test instrument as a real anchor with the exact href, target and rel", () => {
    render(<InstrumentAction facts={factsFromCommitment(liveCommitment)} now={BEFORE_DUE} />);
    const link = screen.getByRole("link", { name: /Open Payment Link/ });
    expect(link).toHaveAttribute("href", LIVE_URL);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("title", LIVE_URL);
    expect(link).toHaveTextContent("Open Payment Link ↗");
    expect(screen.getByText("Razorpay Test Mode")).toBeInTheDocument();
  });

  it("renders a sandbox instrument as a non-clickable pill with the simulation tooltip, whatever the URL looks like", () => {
    const facts = factsFromCommitment({ ...sandboxCommitment, state: "active", payment_url: "https://rzp.io/l/fake0076" });
    render(<InstrumentAction facts={facts} now={Date.parse("2026-08-25T10:00:00+05:30")} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(document.querySelector("a")).toBeNull();
    expect(document.body.innerHTML).not.toContain("href=");
    expect(screen.getByText("Sandbox instrument")).toHaveAttribute("title", SANDBOX_TIP);
    expect(screen.getByText("Sandbox instrument").closest(".instrument")).toHaveAttribute("title", SANDBOX_TIP);
    expect(screen.getByText("plink_fake_0076")).toBeInTheDocument();
  });

  it("shows Paid for a fulfilled commitment with no link", () => {
    render(<InstrumentAction facts={factsFromCommitment(sandboxCommitment)} />);
    expect(screen.getByText("Paid ✓")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
    expect(document.body.innerHTML).not.toContain(SANDBOX_URL);
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

  it("shows Instrument failed with the rail's failure text from instrument_failure", () => {
    render(<InstrumentAction facts={factsFromCommitment(failedCommitment)} now={BEFORE_DUE} />);
    expect(screen.getByText("Instrument failed")).toHaveAttribute("title", "BadRequestError: amount exceeds maximum amount allowed.");
    expect(screen.getByText("BadRequestError: amount exceeds maximum amount allowed.")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("keeps the failure text as a tooltip only when compact", () => {
    render(<InstrumentAction facts={factsFromCommitment(failedCommitment)} now={BEFORE_DUE} compact />);
    expect(screen.getByText("Instrument failed").closest(".instrument")).toHaveAttribute("title", "BadRequestError: amount exceeds maximum amount allowed.");
    expect(screen.queryByText("BadRequestError: amount exceeds maximum amount allowed.")).toBeNull();
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
    // A sandbox-looking URL with razorpay_test mode is still live — the API's field is the truth.
    expect(deriveInstrument({ state: "active", instrument_id: "plink_x", payment_url: SANDBOX_URL, instrument_mode: "razorpay_test", due_at: liveCommitment.due_at }, BEFORE_DUE)).toEqual({ kind: "live", url: SANDBOX_URL });
    // Unknown mode with an id → issued but never a link.
    expect(deriveInstrument({ state: "active", instrument_id: "plink_x", payment_url: "https://rzp.io/rzp/x", instrument_mode: undefined, due_at: liveCommitment.due_at }, BEFORE_DUE).kind).toBe("issued");
    // Failed wins over everything except a terminal state.
    expect(deriveInstrument(factsFromCommitment(failedCommitment), BEFORE_DUE)).toEqual({ kind: "failed", reason: "BadRequestError: amount exceeds maximum amount allowed." });
  });
});
