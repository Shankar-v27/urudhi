import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CommitmentDrawerBody } from "../components/CommitmentDrawer";
import { inr } from "../api";
import { LIVE_URL, SANDBOX_URL, failedCommitment, liveCommitmentDetail, sandboxCommitmentDetail } from "./fixtures";

const BEFORE_DUE = Date.parse("2026-08-28T10:00:00+05:30");

describe("CommitmentDrawerBody (GET /api/commitments/{id})", () => {
  it("renders exact amounts, the promise quote, the AI interpretation, the policy checklist and the six integrity steps", () => {
    render(<CommitmentDrawerBody detail={liveCommitmentDetail} now={BEFORE_DUE} />);

    expect(screen.getAllByText(inr(5000000)).length).toBeGreaterThan(0);
    expect(inr(5000000)).toBe("₹50,000.00");
    expect(screen.getAllByText(inr(0)).length).toBeGreaterThan(0);

    // The promise, verbatim, in quotes (in the PROMISE section and again in step 1).
    expect(screen.getAllByText("“Cash konjam tight ah iruku. Friday 50000 kudukuren.”").length).toBeGreaterThan(0);

    // AI interpretation: intent, amount, date, confidence, brain.
    expect(screen.getAllByText("promise").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2026-08-28").length).toBeGreaterThan(0);
    expect(screen.getAllByText("0.88").length).toBeGreaterThan(0);
    expect(screen.getByText("brain claude")).toBeInTheDocument();

    // Policy checklist with ✓ and reasons.
    expect(screen.getAllByText("invoice active").length).toBeGreaterThan(0);
    expect(screen.getAllByText("invoice is promised").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1 day(s) out, within the 30-day horizon").length).toBeGreaterThan(0);
    expect(screen.getByText("4/4 checks passed")).toBeInTheDocument();

    // Integrity steps, in order, with their statuses.
    const steps = screen.getByRole("list", { name: "Commitment integrity" });
    const items = within(steps).getAllByRole("listitem").filter((li) => li.classList.contains("step"));
    expect(items.map((li) => li.getAttribute("aria-label"))).toEqual([
      "What the debtor said: evidence recorded",
      "What AI understood: evidence recorded",
      "What policy accepted: evidence recorded",
      "What payment instrument was issued: evidence recorded",
      "What money arrived: pending",
      "Final outcome: pending",
    ]);

    // Instrument section: rail, link id, reference id = commitment id, notes, URL status, the live link verbatim.
    expect(screen.getAllByText("Razorpay Test Mode").length).toBeGreaterThan(0);
    expect(screen.getAllByText("plink_TUmLQ82CcnfqwP").length).toBeGreaterThan(0);
    expect(screen.getByText("live")).toBeInTheDocument();
    expect(screen.getAllByText("Payment pending").length).toBeGreaterThan(0);
    const links = screen.getAllByRole("link", { name: /Open Payment Link/ });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute("href", LIVE_URL);
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }

    // Provenance + audit refs and chain status from the detail payload.
    expect(screen.getAllByText("Live Test").length).toBeGreaterThan(0);
    expect(screen.getByText("verified · 63 events")).toBeInTheDocument();
    expect(screen.getByText(/Audit chain verified · 63 events/)).toBeInTheDocument();
    expect(screen.getAllByText("#9").length).toBeGreaterThan(0);
    expect(screen.getByText("+91••••••••01", { exact: false })).toBeInTheDocument();
  });

  it("renders the Razorpay ↔ Urudhi mapping block from chain.instrument.notes and the row", () => {
    render(<CommitmentDrawerBody detail={liveCommitmentDetail} now={BEFORE_DUE} />);
    const map = screen.getByRole("table", { name: "Razorpay to Urudhi mapping" });
    expect(map).toHaveTextContent("Razorpay");
    expect(map).toHaveTextContent("Payment Linkplink_TUmLQ82CcnfqwP");
    expect(map).toHaveTextContent("Amount₹50,000.00");
    expect(map).toHaveTextContent("Reference IDcmt_inv_live_20260827170223_1");
    expect(map).toHaveTextContent("notes.commitment_idcmt_inv_live_20260827170223_1");
    expect(map).toHaveTextContent("notes.invoice_idinv_live_20260827170223");
    expect(map).toHaveTextContent("Urudhi");
    expect(map).toHaveTextContent("Commitmentcmt_inv_live_20260827170223_1");
    expect(map).toHaveTextContent("Committed₹50,000.00");
    expect(map).toHaveTextContent("InvoiceURU/2026/L170223");
    expect(map).toHaveTextContent("DebtorKumar Textiles");
  });

  it("renders a fulfilled sandbox commitment: paid, no anchor, rail payment with razorpay ids, mock brain", () => {
    render(<CommitmentDrawerBody detail={sandboxCommitmentDetail} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(document.body.innerHTML).not.toContain(`href="${SANDBOX_URL}"`);
    expect(screen.getAllByText("Paid ✓").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Simulation").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Sandbox").length).toBeGreaterThan(0);
    expect(screen.getByText("brain mock")).toBeInTheDocument();
    expect(screen.getAllByText("exact · instrument").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/pay_sim_00024/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/evt_sim_00024/).length).toBeGreaterThan(0);
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("verified · 3,648 events")).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Commitment integrity" });
    const items = within(steps).getAllByRole("listitem").filter((li) => li.classList.contains("step"));
    expect(items.every((li) => li.classList.contains("done"))).toBe(true);
  });

  it("shows the rail's failure text when the instrument failed", () => {
    const detail = {
      ...liveCommitmentDetail, commitment: failedCommitment,
      chain: { ...liveCommitmentDetail.chain, instrument: { ...liveCommitmentDetail.chain.instrument, id: null, url: null, mode: null, failed: true, failure_reason: null } },
    };
    render(<CommitmentDrawerBody detail={detail} now={BEFORE_DUE} />);
    expect(screen.getAllByText("Instrument failed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("BadRequestError: amount exceeds maximum amount allowed.").length).toBeGreaterThan(0);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("none — rail refused")).toBeInTheDocument();
  });
});
