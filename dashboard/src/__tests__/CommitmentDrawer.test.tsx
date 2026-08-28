import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CommitmentDrawerBody } from "../components/CommitmentDrawer";
import { inr } from "../api";
import { LIVE_URL, health, liveDetail } from "./fixtures";

const BEFORE_DUE = Date.parse("2026-08-28T10:00:00+05:30");

describe("CommitmentDrawerBody", () => {
  it("renders exact amounts, the evidence quote, the policy checklist and the integrity steps", () => {
    render(<CommitmentDrawerBody detail={liveDetail} id="cmt_inv_live_20260827170223_1" chainStatus={health.audit_chain} now={BEFORE_DUE} />);

    // Amounts as formatted by `inr` — the exact strings, several times (header, outcome).
    expect(screen.getAllByText(inr(5000000)).length).toBeGreaterThan(0);
    expect(inr(5000000)).toBe("₹50,000.00");
    expect(screen.getAllByText(inr(0)).length).toBeGreaterThan(0);

    // The promise, verbatim, in quotes.
    expect(screen.getByText("“Cash konjam tight ah iruku. Friday 50000 kudukuren.”")).toBeInTheDocument();

    // Policy checklist with ✓ and reasons.
    expect(screen.getByText("invoice active")).toBeInTheDocument();
    expect(screen.getByText("invoice is promised")).toBeInTheDocument();
    expect(screen.getByText("1 day(s) out, within the 30-day horizon")).toBeInTheDocument();
    expect(screen.getByText(/Accepted/)).toBeInTheDocument();

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

    // Instrument section: mode badge, link id, reference, the live link with the verbatim URL.
    expect(screen.getAllByText("Razorpay Test Mode").length).toBeGreaterThan(0);
    expect(screen.getByText("plink_TUmLQ82CcnfqwP")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: /Open Payment Link/ });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) expect(link).toHaveAttribute("href", LIVE_URL);

    // Audit refs and chain status.
    expect(screen.getByText(/Audit chain verified · 13 events/)).toBeInTheDocument();
    expect(screen.getAllByText("#9").length).toBeGreaterThan(0);
  });

  it("explains a missing commitment instead of rendering nothing", () => {
    render(<CommitmentDrawerBody detail={liveDetail} id="cmt_nope" chainStatus={null} />);
    expect(screen.getByText("Commitment not found")).toBeInTheDocument();
  });
});
