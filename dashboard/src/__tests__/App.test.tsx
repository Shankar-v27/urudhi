import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App, { bootstrapToken, parseHash } from "../App";
import { TOKEN_KEY } from "../api";
import { escalation, health, liveCommitment, liveDetail, liveInvoice, mockFetch, summary } from "./fixtures";

const TOKEN = "test-token-9f3a7c1d2e";

const routes = {
  "/health": health,
  "/api/summary": summary,
  "/api/invoices": [liveInvoice],
  "/api/invoices/inv_live_20260827170223": liveDetail,
  "/api/commitments": [liveCommitment],
  "/api/promises": liveDetail.promises,
  "/api/concessions": [],
  "/api/escalations": [escalation],
  "/api/timeline": { series: [] },
};

function textNodes(root: Node): string[] {
  const out: string[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) out.push(walker.currentNode.textContent ?? "");
  return out;
}

describe("App shell", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch(routes));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses hash routes and falls back to overview", () => {
    expect(parseHash("#/commitments")).toEqual({ tab: "commitments", id: null });
    expect(parseHash("#/commitments/cmt_inv_003_1")).toEqual({ tab: "commitments", id: "cmt_inv_003_1" });
    expect(parseHash("#/invoices/inv%2Fslash")).toEqual({ tab: "invoices", id: "inv/slash" });
    expect(parseHash("")).toEqual({ tab: "overview", id: null });
    expect(parseHash("#/nonsense/abc")).toEqual({ tab: "overview", id: null });
  });

  it("switches tabs from the URL hash", async () => {
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.location.hash = "#/commitments";
    render(<App />);
    expect(screen.getByRole("link", { name: "Commitments" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("heading", { name: "Commitments" })).toBeInTheDocument();
    expect(await screen.findByText("Policy-accepted promises converted into executable payment commitments.")).toBeInTheDocument();

    await act(async () => {
      window.location.hash = "#/escalations";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(screen.getByRole("link", { name: "Escalations" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("heading", { name: "Escalations" })).toBeInTheDocument();
    expect(await screen.findByText("URU/2026/0101")).toBeInTheDocument();
  });

  it("opens the commitment drawer from a deep link and shows the live payment link", async () => {
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.location.hash = "#/commitments/cmt_inv_live_20260827170223_1";
    render(<App />);
    await waitFor(() => expect(screen.getAllByRole("link", { name: /Open Payment Link/ }).length).toBeGreaterThan(0));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Commitment integrity");
    expect(dialog).toHaveTextContent("₹50,000.00");
  });

  it("never renders the token as text and uses a password field", async () => {
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.location.hash = "#/overview";
    render(<App />);
    await screen.findByText("Overview", { selector: "h1" });
    const input = screen.getByLabelText("API token");
    expect(input).toHaveAttribute("type", "password");
    expect((input as HTMLInputElement).value).toBe("");
    expect(textNodes(document.body).some((t) => t.includes(TOKEN))).toBe(false);
    expect(document.body.innerHTML).not.toContain(TOKEN);
    // …but the token is sent as a bearer header.
    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls as [string, RequestInit][];
    const apiCall = calls.find(([url]) => url.startsWith("/api/"));
    expect(apiCall).toBeDefined();
    expect((apiCall![1].headers as Headers).get("Authorization")).toBe(`Bearer ${TOKEN}`);
  });

  it("bootstraps a #token= hash into storage and strips it from the URL", () => {
    window.location.hash = `#token=${TOKEN}`;
    expect(bootstrapToken()).toBe(TOKEN);
    expect(window.localStorage.getItem(TOKEN_KEY)).toBe(TOKEN);
    expect(window.location.hash).toBe("#/overview");
    expect(window.location.href).not.toContain(TOKEN);
  });

  it("tells the user to connect on 401", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/health": health,
      "/api/*": () => new Response(JSON.stringify({ detail: "invalid token" }), { status: 401, headers: { "Content-Type": "application/json" } }),
    }));
    window.location.hash = "#/invoices";
    render(<App />);
    expect(await screen.findByText("Not connected")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Retry" }).length).toBeGreaterThan(0);
  });

  it("passes masked PII through untouched", async () => {
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.location.hash = "#/invoices/inv_live_20260827170223";
    render(<App />);
    await screen.findByRole("dialog");
    expect(await screen.findByText("+91••••••••01")).toBeInTheDocument();
    expect(screen.getByText("v•••@razorpay.com")).toBeInTheDocument();
    expect(screen.getAllByText("Kumar Textiles").length).toBeGreaterThan(0);
  });
});
