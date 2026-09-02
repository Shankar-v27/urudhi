/** Shell: header with the data-source selector and truthful health indicators, token connect, hash-routed tabs, and the drawers. */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  DATA_SOURCES, DataSource, Health, Ledger, RowSource, SOURCE_LABEL, TOKEN_KEY, api, num, storageGet, storageSet, useLoad,
} from "./api";
import { SourceContext, rememberSource, sourceFromHash, storedSource } from "./source";
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

/** `#/commitments/cmt_x?source=live_test` → { tab: "commitments", id: "cmt_x" }; anything unknown → overview. The query is read by `sourceFromHash`. */
export function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, "").replace(/\?.*$/, "");
  const [tab, ...rest] = path.split("/");
  const id = rest.length > 0 && rest.join("/") ? decodeURIComponent(rest.join("/")) : null;
  return { tab: isTab(tab) ? tab : "overview", id: isTab(tab) ? id : null };
}

/** The hash for a tab (and optional record id), carrying the data source so a copied link keeps it. */
export function hashFor(tab: Tab, id?: string | null, source?: DataSource): string {
  const path = `#/${tab}${id ? `/${encodeURIComponent(id)}` : ""}`;
  return source ? `${path}?source=${source}` : path;
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

function useHashRoute(source: DataSource): [Route, (tab: Tab, id?: string | null) => void] {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const navigate = useCallback((tab: Tab, id?: string | null) => {
    const next = hashFor(tab, id, source);
    if (window.location.hash !== next) window.location.hash = next;
    else setRoute(parseHash(next));
  }, [source]);
  return [route, navigate];
}

/** The selected source: seeded from the hash query or storage; written back to both whenever it changes. */
function useDataSource(): [DataSource, (next: DataSource) => void] {
  const [source, setSourceState] = useState<DataSource>(() => sourceFromHash(window.location.hash) ?? storedSource());
  useEffect(() => {
    // A deep link (or back/forward) that names a source wins over the stored choice.
    const onChange = () => {
      const fromHash = sourceFromHash(window.location.hash);
      if (fromHash) setSourceState((current) => (current === fromHash ? current : fromHash));
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const setSource = useCallback((next: DataSource) => {
    setSourceState(next);
    rememberSource(next);
    const { tab, id } = parseHash(window.location.hash);
    const hash = hashFor(tab, id, next);
    if (window.location.hash !== hash) {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${hash}`);
    }
  }, []);
  useEffect(() => { rememberSource(source); }, [source]);
  return [source, setSource];
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

function SourceSelector({ source, onChange }: { source: DataSource; onChange: (next: DataSource) => void }) {
  return (
    <div className="source-select">
      <span>Data</span>
      <div className="segmented" role="group" aria-label="Data source">
        {DATA_SOURCES.map((s) => (
          <button key={s} type="button" aria-pressed={source === s} onClick={() => onChange(s)}>{SOURCE_LABEL[s]}</button>
        ))}
      </div>
    </div>
  );
}

/** Rail vocabulary → label. Accepts both the current (`razorpay_test`/`sandbox`) and older (`razorpay-test`/`fake`) spellings. */
export function railsLabel(rails: string | null | undefined): { label: string; tone: "ok" | "warn" } {
  if (rails === "razorpay_test" || rails === "razorpay-test") return { label: "Razorpay Test Mode", tone: "ok" };
  if (rails === "sandbox" || rails === "fake") return { label: "Sandbox", tone: "warn" };
  return { label: rails ?? "unknown", tone: "warn" };
}

function brainLabel(brain: string | null | undefined): string {
  if (!brain) return "unknown";
  if (brain === "claude") return "Claude";
  if (brain === "mock") return "Mock";
  return brain;
}

export interface HeaderFacts {
  ai: string;
  data: string;
  payment: string;
  paymentTone: "ok" | "warn";
  audit: { verified: boolean; events: number | null; error?: string; missing?: boolean };
}

/**
 * What the header says for the selected source. Live Test / All describe the running process (its brain, its rail);
 * Simulation describes the simulation ledger (the brain that produced it, the sandbox rail) so "AI · Mock" never sits
 * next to "Payment · Razorpay Test Mode".
 */
export function headerFacts(h: Health, source: DataSource): HeaderFacts {
  const ledgers: Ledger[] = h.ledgers ?? [];
  const ledger = (s: RowSource) => ledgers.find((l) => l.source === s);
  const sim = ledger("simulation");
  const live = ledger("live_test");
  const rail = railsLabel(h.rails);
  const sum = (rows: Ledger[]) => rows.reduce((n, l) => n + (l.audit_chain.events ?? 0), 0);

  if (source === "simulation") {
    return {
      ai: brainLabel(sim?.brain ?? "mock"),
      data: SOURCE_LABEL.simulation,
      payment: "Sandbox", paymentTone: "warn",
      audit: sim ? { verified: sim.audit_chain.verified, events: sim.audit_chain.events ?? null, error: sim.audit_chain.error } : { verified: false, events: null, missing: true },
    };
  }
  if (source === "live_test") {
    return {
      ai: brainLabel(h.brain),
      data: SOURCE_LABEL.live_test,
      payment: rail.label, paymentTone: rail.tone,
      audit: live ? { verified: live.audit_chain.verified, events: live.audit_chain.events ?? null, error: live.audit_chain.error } : { verified: false, events: null, missing: true },
    };
  }
  const payments = ledgers.length > 0
    ? ledgers.map((l) => (l.source === "simulation" ? "Sandbox" : rail.label))
    : [rail.label];
  const verified = ledgers.length > 0 ? ledgers.every((l) => l.audit_chain.verified) : h.audit_chain.verified;
  return {
    ai: brainLabel(h.brain),
    data: SOURCE_LABEL.all,
    payment: Array.from(new Set(payments)).join(" · "),
    paymentTone: ledgers.some((l) => l.source === "simulation") ? "warn" : rail.tone,
    audit: { verified, events: ledgers.length > 0 ? sum(ledgers) : h.audit_chain.events ?? null, error: ledgers.find((l) => l.audit_chain.error)?.audit_chain.error ?? h.audit_chain.error },
  };
}

function HealthIndicators({ health, source }: { health: { data: Health | null; error: Error | null; reload?: () => void }; source: DataSource }) {
  const [open, setOpen] = useState(false);
  if (health.error) {
    return (
      <span className="indicator bad" title={health.error.message}>
        <span className="led" />API unreachable
        {health.reload && (
          <button
            type="button"
            className="btn xs ghost"
            style={{ marginLeft: 6, padding: "0 4px", height: "auto", fontSize: 11 }}
            onClick={health.reload}
            title="Retry health check"
          >
            Retry
          </button>
        )}
      </span>
    );
  }
  if (!health.data) return <span className="indicator"><span className="led" />Checking health…</span>;
  const h = health.data;
  const ok = h.status === "ok";
  const f = headerFacts(h, source);
  const ledgers = h.ledgers ?? [];
  const auditText = f.audit.missing ? "no ledger" : f.audit.verified ? "Verified" : "Broken";
  return (
    <>
      <span className={`indicator ${ok ? "ok" : "warn"}`} data-indicator="ai"
        title={`API v${h.version} · status ${h.status} · process brain ${h.brain}${source === "simulation" ? " · simulation ledger produced by the mock brain" : ""}`}>
        <span className="led" />AI · <b>{f.ai}</b>
      </span>
      <span className="indicator ok" data-indicator="data" title={`Showing ${f.data} records${h.sources ? ` · ledgers: ${h.sources.join(", ")}` : ""}`}>
        <span className="led" />Data · <b>{f.data}</b>
      </span>
      <span className={`indicator ${f.paymentTone}`} data-indicator="payment" title={`process rails: ${h.rails} · transport: ${h.transport}`}>
        <span className="led" />Payment · <b>{f.payment}</b>
      </span>
      <span className="popover-anchor">
        <button type="button" className={`indicator ${f.audit.verified ? "ok" : "bad"}`} data-indicator="audit" onClick={() => setOpen((o) => !o)} aria-expanded={open}
          title={f.audit.verified ? `Audit chain verified · ${num(f.audit.events ?? 0)} events` : `Audit chain broken: ${f.audit.error ?? "unknown"}`}>
          <span className="led" />Audit · {auditText}
          {typeof f.audit.events === "number" && <b>{" · "}{num(f.audit.events)} events</b>}
        </button>
        {open && (
          <div className="popover" role="dialog" aria-label="Audit chain status">
            <dl>
              <dt>Status</dt><dd>{f.audit.verified ? "verified — every hash links to the previous event" : `broken — ${f.audit.error ?? "unknown"}`}</dd>
              <dt>Events</dt><dd>{typeof f.audit.events === "number" ? num(f.audit.events) : "—"}</dd>
              {ledgers.map((l) => (
                <span key={l.source} style={{ display: "contents" }}>
                  <dt>{SOURCE_LABEL[l.source]}</dt>
                  <dd>{l.audit_chain.verified ? "verified" : "broken"} · {num(l.audit_chain.events ?? 0)} events · {num(l.invoices)} invoices{l.brain ? ` · brain ${l.brain}` : ""}</dd>
                </span>
              ))}
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

function Workspace({ route, navigate, token, source, onApiSuccess }: {
  route: Route; navigate: (tab: Tab, id?: string | null) => void; token: string; source: DataSource; onApiSuccess?: () => void;
}) {
  const summary = useLoad(() => api.summary(source), [source]);
  const invoices = useLoad(() => api.invoices(source), [source]);
  const openInvoice = (id: string) => navigate("invoices", id);
  const openCommitment = (id: string) => navigate("commitments", id);
  const close = () => navigate(route.tab);

  useEffect(() => {
    if (summary.data || invoices.data) {
      onApiSuccess?.();
    }
  }, [summary.data, invoices.data, onApiSuccess]);

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
        <CommitmentDrawer id={route.id} onClose={close} onOpenInvoice={openInvoice} />
      )}
    </>
  );
}

export default function App() {
  const [token, setTokenState] = useState(() => bootstrapToken());
  const [generation, setGeneration] = useState(0);
  const [source, setSource] = useDataSource();
  const [route, navigate] = useHashRoute(source);
  const health = useLoad(api.health, [generation]);
  const connect = (value: string) => {
    storageSet(TOKEN_KEY, value);
    setTokenState(value);
    setGeneration((g) => g + 1);
  };
  const sourceState = useMemo(() => ({ source, setSource }), [source, setSource]);

  return (
    <SourceContext.Provider value={sourceState}>
      <header className="app-header">
        <div className="inner">
          <div className="brand">
            <span className="name">URUDHI</span>
            <span className="dot">·</span>
            <span className="tamil" lang="ta">உறுதி</span>
            <span className="dot">·</span>
            <span className="tagline">Revenue Recovery</span>
          </div>
          <div className="health"><HealthIndicators health={health} source={source} /></div>
          <SourceSelector source={source} onChange={setSource} />
          <TokenForm token={token} onConnect={connect} />
          <nav className="tabs" aria-label="Sections">
            {TABS.map((t) => (
              <a key={t.id} href={hashFor(t.id, null, source)} aria-current={route.tab === t.id ? "page" : undefined}>{t.label}</a>
            ))}
          </nav>
        </div>
      </header>
      <main key={generation}>
        <Workspace route={route} navigate={navigate} token={token} source={source} onApiSuccess={health.error ? health.reload : undefined} />
      </main>
    </SourceContext.Provider>
  );
}
