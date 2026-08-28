import { describe, expect, it } from "vitest";
import { takeaway } from "../Overview";
import { whyAiLine } from "../Ops";
import { matchesQuery } from "../Commitments";
import { experiment, liveCommitment, replyEval } from "./fixtures";

describe("derived insight lines", () => {
  it("computes the takeaway only when Urudhi wins on recovery, nudges and efficiency", () => {
    const t = takeaway(experiment)!;
    expect(t.wins).toBe(true);
    expect(t.recovery).toEqual({ urudhi: 0.6833, baseline: 0.6449 });
    expect(t.nudges).toEqual({ urudhi: 317, baseline: 413 });
    expect(t.perContact).toEqual({ urudhi: 4110033, baseline: 2977206 });
    const worse = { ...experiment, arms: { ...experiment.arms, urudhi: { ...experiment.arms.urudhi, contact_attempts: 500 } } };
    expect(takeaway(worse)!.wins).toBe(false);
    expect(takeaway({ ...experiment, arms: { no_action: experiment.arms.no_action } as typeof experiment.arms })).toBeNull();
  });

  it("writes the 'why AI' line only when both brains were measured", () => {
    expect(whyAiLine(replyEval)).toMatch(/Claude reads debtor intent correctly 86\.7% of the time versus 58\.9% for the regex baseline — 27\.8 points better/);
    expect(whyAiLine({ claude: replyEval.claude })).toBeNull();
    expect(whyAiLine({ mock: replyEval.mock })).toBeNull();
    expect(whyAiLine({ mock: replyEval.mock, claude: { ...replyEval.claude!, intent_accuracy: null } })).toBeNull();
  });

  it("matches commitments on invoice number/id, debtor, commitment id, reference id and payment-link id", () => {
    for (const q of ["uru/2026/l170223", "inv_live_20260827170223", "kumar", "cmt_inv_live_20260827170223_1", "plink_tumlq82ccnfqwp"]) {
      expect(matchesQuery(liveCommitment, q)).toBe(true);
    }
    expect(matchesQuery(liveCommitment, "annapoorna")).toBe(false);
  });
});
