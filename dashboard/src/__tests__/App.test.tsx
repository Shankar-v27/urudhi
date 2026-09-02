import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App, { bootstrapToken, headerFacts, parseHash } from "../App";
import { SOURCE_KEY, TOKEN_KEY, apiUrl } from "../api";
import { sourceFromHash } from "../source";
import {
  bySource, escalation, experiment, health, liveCommitment, liveCommitmentDetail, liveDetail, liveInvoice, livePromise, mockFetch,
  notFound, replyEval, sandboxCommitment, sandboxCommitmentDetail, simInvoice, simPromise, summaryFor,
} from "./fixtures";

const TOKEN = "test-token-9f3a7c1d2e";

const routes = {
  "/health": health,
  "/api/summary": (_init: RequestInit | undefined, url: string) => summaryFor(url),
  "/api/invoices": (_init: RequestInit | undefined, url: string) => bySource([liveInvoice, simInvoice], url),
  "/api/invoices/inv_live_20260827170223": liveDetail,
  "/api/commitments": (_init: RequestInit | undefined, url: string) => bySource([liveCommitment, sandboxCommitment], url),
  "/api/commitments/cmt_inv_live_20260827170223_1": liveCommitmentDetail,
  "/api/commitments/cmt_inv_003_1": sandboxCommitmentDetail,
  "/api/commitments/cmt_nope": () => notFound("No matching commitment in current data source"),
  "/api/promises": (_init: RequestInit | undefined, url: string) => bySource([livePromise, simPromise], url),
  "/api/concessions": [],
  "/api/escalations": (_init: RequestInit | undefined, url: string) => bySource([escalation], url),
  "/api/timeline": { series: [] },
  "/api/experiment": experiment,
  "/api/reply-eval": replyEval,
};

function textNodes(root: Node): string[] {
  const out: string[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) out.push(walker.currentNode.textContent ?? "");
  return out;
}

function fetchedUrls(): string[] {
  return ((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls as [string, RequestInit][]).map(([url]) => url);
}

function indicator(name: "ai" | "data" | "payment" | "audit"): HTMLElement {
  const el = document.querySelector<HTMLElement>(`[data-indicator="${name}"]`);
  if (!el) throw new Error(`no ${name} indicator`);
  return el;
}

describe("App shell", () => {
  beforeEach(() => {
    vi.setSystemTime(new Date("2026-08-28T10:00:00+05:30"));
    vi.stubGlobal("fetch", mockFetch(routes));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("parses hash routes (with or without a ?source= query) and falls back to overview", () => {
    expect(parseHash("#/commitments")).toEqual({ tab: "commitments", id: null });
    expect(parseHash("#/commitments/cmt_inv_003_1")).toEqual({ tab: "commitments", id: "cmt_inv_003_1" });
    expect(parseHash("#/commitments/cmt_inv_003_1?source=live_test")).toEqual({ tab: "commitments", id: "cmt_inv_003_1" });
    expect(parseHash("#/invoices/inv%2Fslash")).toEqual({ tab: "invoices", id: "inv/slash" });
    expect(parseHash("")).toEqual({ tab: "overview", id: null });
    expect(parseHash("#/nonsense/abc")).toEqual({ tab: "overview", id: null });
    expect(sourceFromHash("#/commitments?source=live_test")).toBe("live_test");
    expect(sourceFromHash("#/commitments?source=bogus")).toBeNull();
    expect(sourceFromHash("#/commitments")).toBeNull();
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
    // Escalation rows carry the debtor name and their ledger.
    expect(screen.getByText("Nagercoil Spices Unit 4")).toBeInTheDocument();
    const row = screen.getByText("URU/2026/0101").closest("tr")!;
    expect(within(row).getByText("Simulation")).toHaveClass("source-badge", "simulation");
  });

  it("switches the data source: API calls carry ?source=, the header flips, the hash and storage keep it", async () => {
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.location.hash = "#/commitments";
    render(<App />);
    await screen.findByText("URU/2026/L170223");
    expect(fetchedUrls()).toContain("/api/commitments?source=all");

    // All: process brain + both rails + summed audit events.
    await waitFor(() => expect(indicator("ai")).toHaveTextContent("AI · Claude"));
    expect(indicator("data")).toHaveTextContent("Data · All");
    expect(indicator("payment")).toHaveTextContent("Payment · Razorpay Test Mode · Sandbox");
    expect(indicator("audit")).toHaveTextContent("Audit · Verified · 3,711 events");

    const group = screen.getByRole("group", { name: "Data source" });
    fireEvent.click(within(group).getByRole("button", { name: "Live Test" }));
    await waitFor(() => expect(fetchedUrls()).toContain("/api/commitments?source=live_test"));
    expect(within(group).getByRole("button", { name: "Live Test" })).toHaveAttribute("aria-pressed", "true");
    expect(window.location.hash).toBe("#/commitments?source=live_test");
    expect(window.localStorage.getItem(SOURCE_KEY)).toBe("live_test");
    expect(indicator("ai")).toHaveTextContent("AI · Claude");
    expect(indicator("data")).toHaveTextContent("Data · Live Test");
    expect(indicator("payment")).toHaveTextContent("Payment · Razorpay Test Mode");
    expect(indicator("payment")).not.toHaveTextContent("Sandbox");
    expect(indicator("audit")).toHaveTextContent("Audit · Verified · 63 events");
    // Only the live row remains and the page did not reload.
    await waitFor(() => expect(screen.queryByText("URU/2026/0003")).toBeNull());
    expect(screen.getByText("URU/2026/L170223")).toBeInTheDocument();

    fireEvent.click(within(group).getByRole("button", { name: "Simulation" }));
    await waitFor(() => expect(fetchedUrls()).toContain("/api/commitments?source=simulation"));
    expect(indicator("ai")).toHaveTextContent("AI · Mock");
    expect(indicator("data")).toHaveTextContent("Data · Simulation");
    expect(indicator("payment")).toHaveTextContent("Payment · Sandbox");
    expect(indicator("payment")).not.toHaveTextContent("Razorpay");
    expect(indicator("audit")).toHaveTextContent("Audit · Verified · 3,648 events");
    expect(window.location.hash).toBe("#/commitments?source=simulation");
    await screen.findByText("URU/2026/0003");
    expect(screen.queryByText("URU/2026/L170223")).toBeNull();
    // The in-page source filter mirrors the global selector.
    expect(within(screen.getByRole("group", { name: "Filter by source" })).getByRole("button", { name: "Simulation" })).toHaveAttribute("aria-pressed", "true");
    // Tab links carry the source so deep links keep it.
    expect(screen.getByRole("link", { name: "Invoices" })).toHaveAttribute("href", "#/invoices?source=simulation");
  });

  it("seeds the data source from a deep link and from storage", async () => {
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.localStorage.setItem(SOURCE_KEY, "live_test");
    window.location.hash = "#/commitments?source=simulation";
    render(<App />);
    await waitFor(() => expect(fetchedUrls()).toContain("/api/commitments?source=simulation"));
    expect(fetchedUrls().some((u) => u.includes("source=live_test"))).toBe(false);
    expect(indicator("data")).toHaveTextContent("Data · Simulation");
  });

  it("derives truthful header facts per source from /health", () => {
    expect(headerFacts(health, "all")).toMatchObject({ ai: "Claude", data: "All", payment: "Razorpay Test Mode · Sandbox", audit: { verified: true, events: 3711 } });
    expect(headerFacts(health, "live_test")).toMatchObject({ ai: "Claude", data: "Live Test", payment: "Razorpay Test Mode", audit: { verified: true, events: 63 } });
    expect(headerFacts(health, "simulation")).toMatchObject({ ai: "Mock", data: "Simulation", payment: "Sandbox", audit: { verified: true, events: 3648 } });
    // An older /health without ledgers still describes the process.
    expect(headerFacts({ ...health, ledgers: undefined, sources: undefined }, "all")).toMatchObject({ ai: "Claude", payment: "Razorpay Test Mode", audit: { events: 3711 } });
    expect(headerFacts({ ...health, ledgers: undefined }, "simulation").audit.missing).toBe(true);
  });

  it("labels every commitment row with its ledger and shows debtor, and reports no match with the API's wording", async () => {
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.location.hash = "#/commitments";
    render(<App />);
    const live = (await screen.findByText("URU/2026/L170223")).closest("tr")!;
    const sim = screen.getByText("URU/2026/0003").closest("tr")!;
    expect(within(live).getByText("Live Test")).toHaveClass("source-badge", "live_test");
    expect(within(live).getByText("Kumar Textiles")).toBeInTheDocument();
    expect(within(live).getByText("Payment pending")).toBeInTheDocument();
    expect(within(sim).getByText("Simulation")).toHaveClass("source-badge", "simulation");
    expect(within(sim).getByText("Annapoorna Foods")).toBeInTheDocument();
    expect(within(sim).getByText("Paid ✓")).toBeInTheDocument();

    const search = screen.getByLabelText("Search commitments");
    fireEvent.change(search, { target: { value: "plink_TUmLQ82CcnfqwP" } });
    expect(screen.getByText("URU/2026/L170223")).toBeInTheDocument();
    expect(screen.queryByText("URU/2026/0003")).toBeNull();
    fireEvent.change(search, { target: { value: "annapoorna" } });
    expect(screen.getByText("URU/2026/0003")).toBeInTheDocument();
    fireEvent.change(search, { target: { value: "cmt_inv_003_1" } });
    expect(screen.getByText("URU/2026/0003")).toBeInTheDocument();
    fireEvent.change(search, { target: { value: "no-such-thing" } });
    expect(screen.getByText("No matching commitment in current data source")).toBeInTheDocument();
  });

  it("opens the commitment drawer from a deep link via /api/commitments/{id} and shows the live payment link", async () => {
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.location.hash = "#/commitments/cmt_inv_live_20260827170223_1";
    render(<App />);
    await waitFor(() => expect(screen.getAllByRole("link", { name: /Open Payment Link/ }).length).toBeGreaterThan(0));
    expect(fetchedUrls()).toContain("/api/commitments/cmt_inv_live_20260827170223_1");
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Commitment integrity");
    expect(dialog).toHaveTextContent("Razorpay ↔ Urudhi mapping");
    expect(dialog).toHaveTextContent("₹50,000.00");
    expect(within(dialog).getAllByText("Live Test").length).toBeGreaterThan(0);
    // Masked contact details pass through untouched.
    expect(dialog).toHaveTextContent("+91••••••••01");
    expect(dialog).toHaveTextContent("v•••@razorpay.com");
  });

  it("explains an unknown commitment id with the API's wording", async () => {
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.location.hash = "#/commitments/cmt_nope";
    render(<App />);
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("No matching commitment in current data source")).toBeInTheDocument();
  });

  it("links promises to their commitment and shows the payment outcome per ledger", async () => {
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.location.hash = "#/promises";
    render(<App />);
    const kept = (await screen.findByText("“Will transfer ₹157,721 by Tuesday. Rest next month.”")).closest("tr")!;
    expect(within(kept).getByText("URU/2026/0003")).toBeInTheDocument();
    expect(within(kept).getByRole("button", { name: "Open commitment cmt_inv_003_1" })).toBeInTheDocument();
    expect(within(kept).getByText("paid in full")).toBeInTheDocument();
    expect(within(kept).getByText("Simulation")).toHaveClass("source-badge");
    const open = screen.getByText("“Cash konjam tight ah iruku. Friday 50000 kudukuren.”").closest("tr")!;
    expect(within(open).getByText("payment pending")).toBeInTheDocument();
    expect(within(open).getByText("Live Test")).toHaveClass("source-badge", "live_test");
    fireEvent.click(within(open).getByRole("button", { name: "Open commitment cmt_inv_live_20260827170223_1" }));
    expect(window.location.hash).toBe("#/commitments/cmt_inv_live_20260827170223_1?source=all");
  });

  it("hides the simulation benchmark for Live Test and shows the takeaway otherwise", async () => {
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.location.hash = "#/overview?source=live_test";
    render(<App />);
    expect(await screen.findByText(/Benchmark comparison is a simulation artefact/)).toBeInTheDocument();
    expect(fetchedUrls()).toContain("/api/summary?source=live_test");
    expect(fetchedUrls()).not.toContain("/api/experiment");
    expect(screen.getAllByText("Razorpay Test Mode · observed via signed webhook").length).toBeGreaterThan(0);

    fireEvent.click(within(screen.getByRole("group", { name: "Data source" })).getByRole("button", { name: "All" }));
    expect(await screen.findByText(/Takeaway: more recovery · fewer nudges · better efficiency/)).toBeInTheDocument();
    expect(fetchedUrls()).toContain("/api/experiment");
    // Merged KPIs carry the per-source split.
    await waitFor(() => expect(screen.getAllByLabelText("Per-source split").length).toBeGreaterThan(0));
    expect(screen.queryByText(/Benchmark comparison is a simulation artefact/)).toBeNull();
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

  it("shows API unreachable when fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.location.hash = "#/commitments";
    render(<App />);
    expect((await screen.findAllByText("API unreachable")).length).toBeGreaterThan(0);
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

  it("resolves API URLs safely with and without VITE_API_BASE_URL", () => {
    expect(apiUrl("/api/commitments", "")).toBe("/api/commitments");
    expect(apiUrl("/api/commitments", "https://urudhi.onrender.com")).toBe("https://urudhi.onrender.com/api/commitments");
    expect(apiUrl("/api/commitments", "https://urudhi.onrender.com/")).toBe("https://urudhi.onrender.com/api/commitments");
    expect(apiUrl("api/commitments", "https://urudhi.onrender.com")).toBe("https://urudhi.onrender.com/api/commitments");
    expect(apiUrl("https://other.example.com/api", "https://urudhi.onrender.com")).toBe("https://other.example.com/api");
  });

  it("recovers from initial health failure when workspace API requests succeed", async () => {
    let healthCallCount = 0;
    const baseMock = mockFetch(routes);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url.includes("/health")) {
        healthCallCount++;
        if (healthCallCount === 1) {
          throw new TypeError("Failed to fetch");
        }
      }
      return baseMock(input, init);
    }));
    window.localStorage.setItem(TOKEN_KEY, TOKEN);
    window.location.hash = "#/overview";
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Claude")).toBeInTheDocument();
      expect(screen.queryByText("API unreachable")).not.toBeInTheDocument();
    });
  });

  it("displays public demo read-only indicator and omits warning banner when public_readonly is true", async () => {
    vi.stubGlobal("fetch", mockFetch({
      ...routes,
      "/health": { ...health, public_readonly: true },
    }));
    window.localStorage.removeItem(TOKEN_KEY);
    window.location.hash = "#/overview";
    render(<App />);

    expect(await screen.findByText("Public demo · Read-only")).toBeInTheDocument();
    expect(screen.queryByText("Not connected")).not.toBeInTheDocument();
  });
});
