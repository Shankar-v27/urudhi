/** Shell: header with health indicators and token connect, hash-routed tabs, and the drawers. */

import { useCallback, useEffect, useState } from "react";
import { Health, TOKEN_KEY, api, num, storageGet, storageSet, useLoad } from "./api";
import { Overview } from "./Overview";
import { InvoiceDrawer, InvoicesPage } from "./Invoices";
import { CommitmentsPage } from "./Commitments";
import { PromiseLedgerPage } from "./Promises";
import { Escalations, ReplyEvaluation } from "./Ops";
import { CommitmentDrawer } from "./components/CommitmentDrawer";

// -- routing ------------------------------------------------------------------

export type Tab = "overview" | "invoices" | "commitments" | "promises" | "escalations" | "eval";

export const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "invoices", label: "Invoices" },
  { id: "commitments", label: "Commitments" },
  { id: "promises", label: "Promise Ledger" },
  { id: "escalations", label: "Escalations" },
  { id: "eval", label: "Reply Evaluation" },
];

export interface Route { tab: Tab; id: string | null }

function isTab(value: string): value is Tab {
  return TABS.some((t) => t.id === value);
}

/** `#/commitments/cmt_x` → { tab: "commitments", id: "cmt_x" }; anything unknown → overview. */
export function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, "");
  const [tab, ...rest] = path.split("/");
  const id = rest.length > 0 && rest.join("/") ? decodeURIComponent(rest.join("/")) : null;
  return { tab: isTab(tab) ? tab : "overview", id: isTab(tab) ? id : null };
}

export function hashFor(tab: Tab, id?: string | null): string {
  return `#/${tab}${id ? `/${encodeURIComponent(id)}` : ""}`;
}

/**
 * One-time token bootstrap (dev convenience): opening `…/#token=XYZ` stores XYZ as the bearer token and
 * immediately rewrites the URL to `#/overview` so the token never stays in the address bar, history entry,
 * or a copied link. Returns the token now in storage.
 */
export function bootstrapToken(): string {
  const match = /^#token=(.+)$/.exec(window.location.hash);
  if (match) {
    storageSet(TOKEN_KEY, decodeURIComponent(match[1]).trim());
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${hashFor("overview")}`);
  }
  return storageGet(TOKEN_KEY);
}

function useHashRoute(): [Route, (tab: Tab, id?: string | null) => void] {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const navigate = useCallback((tab: Tab, id?: string | null) => {
    const next = hashFor(tab, id);
    if (window.location.hash !== next) window.location.hash = next;
    else setRoute(parseHash(next));
  }, []);
  return [route, navigate];
}

// -- header ---------------------------------------------------------------------

function TokenForm({ token, onConnect }: { token: string; onConnect: (token: string) => void }) {
  const [draft, setDraft] = useState("");
  return (
    <form className="connect" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) onConnect(draft.trim()); setDraft(""); }}>
      {token && <span className="connected" title="A bearer token is stored in this browser"><span className="led" style={{ width: 8, height: 8, borderRadius: 999, background: "var(--accent)", display: "inline-block" }} />Connected</span>}
      <input type="password" placeholder={token ? "Replace token" : "URUDHI_API_TOKEN"} value={draft}
        onChange={(e) => setDraft(e.target.value)} autoComplete="off" aria-label="API token" />
      <button type="submit" className="btn sm" disabled={!draft.trim()}>{token ? "Reconnect" : "Connect"}</button>
      {token && <button type="button" className="btn sm ghost" onClick={() => onConnect("")} aria-label="Forget stored token">Forget</button>}
    </form>
  );
}

function railsLabel(rails: string): { label: string; tone: "ok" | "warn" } {
  if (rails === "razorpay-test") return { label: "Razorpay Test Mode", tone: "ok" };
  if (rails === "fake" || rails === "sandbox") return { label: "Sandbox", tone: "warn" };
  return { label: rails, tone: "warn" };
}

function HealthIndicators({ health }: { health: { data: Health | null; error: Error | null } }) {
  const [open, setOpen] = useState(false);
  if (health.error) return <span className="indicator bad"><span className="led" />API unreachable</span>;
  if (!health.data) return <span className="indicator"><span className="led" />Checking health…</span>;
  const h = health.data;
  const ok = h.status === "ok";
  const rails = railsLabel(h.rails);
  const chain = h.audit_chain;
  return (
    <>
      <span className={`indicator ${ok ? "ok" : "warn"}`} title={`API v${h.version} · status ${h.status} · policy timezone ${h.policy_timezone}`}>
        <span className="led" />{ok ? "AI healthy" : `AI ${h.status}`} <b>{h.brain}</b>
      </span>
      <span className={`indicator ${rails.tone}`} title={`rails: ${h.rails} · transport: ${h.transport}`}>
        <span className="led" />Payment rails: <b>{rails.label}</b>
      </span>
      <span className="popover-anchor">
        <button type="button" className={`indicator ${chain.verified ? "ok" : "bad"}`} onClick={() => setOpen((o) => !o)} aria-expanded={open}
          title={chain.verified ? `Audit chain verified · ${num(chain.events ?? 0)} events` : `Audit chain broken: ${chain.error ?? "unknown"}`}>
          <span className="led" />{chain.verified ? "Audit chain verified" : "Audit chain broken"}
          {typeof chain.events === "number" && <b>· {num(chain.events)} events</b>}
        </button>
        {open && (
          <div className="popover" role="dialog" aria-label="Audit chain status">
            <dl>
              <dt>Status</dt><dd>{chain.verified ? "verified — every hash links to the previous event" : `broken — ${chain.error ?? "unknown"}`}</dd>
              <dt>Events</dt><dd>{typeof chain.events === "number" ? num(chain.events) : "—"}</dd>
              <dt>Invoices</dt><dd>{num(h.invoices)}</dd>
              <dt>Transport</dt><dd>{h.transport}</dd>
              <dt>Version</dt><dd>{h.version}</dd>
            </dl>
          </div>
        )}
      </span>
    </>
  );
}

// -- workspace ----------------------------------------------------------------------

function Workspace({ route, navigate, token }: { route: Route; navigate: (tab: Tab, id?: string | null) => void; token: string }) {
  const summary = useLoad(api.summary);
  const invoices = useLoad(api.invoices);
  const [commitmentInvoice, setCommitmentInvoice] = useState<string | null>(null);
  const openInvoice = (id: string) => navigate("invoices", id);
  const openCommitment = (id: string, invoiceId?: string) => { setCommitmentInvoice(invoiceId ?? null); navigate("commitments", id); };
  const close = () => navigate(route.tab);

  return (
    <>
      {!token && (
        <div className="note warn" style={{ marginBottom: 16 }} role="status">
          Not connected. Paste the value of <code>URUDHI_API_TOKEN</code> into the Connect field in the header — every <code>/api</code> request is bearer-token protected.
        </div>
      )}
      {route.tab === "overview" && <Overview summary={summary} invoices={invoices} />}
      {route.tab === "invoices" && <InvoicesPage invoices={invoices} onOpen={openInvoice} />}
      {route.tab === "commitments" && <CommitmentsPage onOpen={openCommitment} />}
      {route.tab === "promises" && <PromiseLedgerPage invoices={invoices} onOpenCommitment={openCommitment} />}
      {route.tab === "escalations" && (
        <Escalations invoices={invoices} summary={summary} selectedId={route.id} onSelect={(id) => navigate("escalations", id)} />
      )}
      {route.tab === "eval" && <ReplyEvaluation />}

      {route.tab === "invoices" && route.id && (
        <InvoiceDrawer id={route.id} onClose={close} onOpenCommitment={openCommitment} />
      )}
      {route.tab === "commitments" && route.id && (
        <CommitmentDrawer id={route.id} invoiceId={commitmentInvoice} onClose={close} onOpenInvoice={openInvoice} />
      )}
    </>
  );
}

export default function App() {
  const [token, setTokenState] = useState(() => bootstrapToken());
  const [generation, setGeneration] = useState(0);
  const [route, navigate] = useHashRoute();
  const health = useLoad(api.health, [generation]);
  const connect = (value: string) => {
    storageSet(TOKEN_KEY, value);
    setTokenState(value);
    setGeneration((g) => g + 1);
  };

  return (
    <>
      <header className="app-header">
        <div className="inner">
          <div className="brand">
            <span className="name">URUDHI</span>
            <span className="dot">·</span>
            <span className="tamil" lang="ta">உறுதி</span>
            <span className="dot">·</span>
            <span className="tagline">Revenue Recovery</span>
          </div>
          <div className="health"><HealthIndicators health={health} /></div>
          <TokenForm token={token} onConnect={connect} />
          <nav className="tabs" aria-label="Sections">
            {TABS.map((t) => (
              <a key={t.id} href={hashFor(t.id)} aria-current={route.tab === t.id ? "page" : undefined}>{t.label}</a>
            ))}
          </nav>
        </div>
      </header>
      <main key={generation}>
        <Workspace route={route} navigate={navigate} token={token} />
      </main>
    </>
  );
}
