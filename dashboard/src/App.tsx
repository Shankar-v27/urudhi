import { useState } from "react";
import { TOKEN_KEY, api, storageGet, storageSet, useLoad } from "./api";
import { Overview } from "./Overview";
import { CommitmentLedger, Detail, InvoiceTable, PromiseLedger } from "./Ledger";
import { Escalations, ReplyEvaluation } from "./Ops";
import { Status } from "./ui";

type Tab = "overview" | "invoices" | "commitments" | "promises" | "escalations" | "eval";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "invoices", label: "Invoices" },
  { id: "commitments", label: "Commitments" },
  { id: "promises", label: "Promise ledger" },
  { id: "escalations", label: "Escalations" },
  { id: "eval", label: "Reply evaluation" },
];

function TokenForm({ token, onConnect }: { token: string; onConnect: (token: string) => void }) {
  const [draft, setDraft] = useState(token);
  return (
    <form
      className="token"
      onSubmit={(e) => {
        e.preventDefault();
        onConnect(draft.trim());
      }}
    >
      <input
        type="password"
        placeholder="URUDHI_API_TOKEN"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        autoComplete="off"
        aria-label="API token"
      />
      <button type="submit">{token && draft.trim() === token ? "reconnect" : "connect"}</button>
      {token && <span className="muted small">token saved</span>}
    </form>
  );
}

function HealthChip() {
  const health = useLoad(api.health);
  if (health.error) return <span className="chip bad">API unreachable</span>;
  if (!health.data) return <span className="chip">health…</span>;
  const h = health.data;
  return (
    <span className={`chip ${h.status === "ok" ? "ok" : "warn"}`} title={`v${h.version} · ${h.policy_timezone}`}>
      {h.status} · brain {h.brain} · transport {h.transport} · rails {h.rails}
    </span>
  );
}

function ChainBadge() {
  const audit = useLoad(api.audit);
  if (audit.error || !audit.data) return null;
  const { chain, total } = audit.data;
  return (
    <span className={`chain ${chain.verified ? "ok" : "bad"}`}>
      {chain.verified ? `audit chain verified — ${total} events` : `audit chain BROKEN: ${chain.error}`}
    </span>
  );
}

/** Everything that needs the token lives under here, keyed on it so a new token refetches all. */
function Workspace() {
  const summary = useLoad(api.summary);
  const invoices = useLoad(api.invoices);
  const [tab, setTab] = useState<Tab>("overview");
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id}
            className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "overview" && <Overview summary={summary} />}
      {tab === "invoices" && (
        <Status load={invoices}>{(rows) => <InvoiceTable invoices={rows} onOpen={setOpenId} />}</Status>
      )}
      {tab === "commitments" && <CommitmentLedger onOpen={setOpenId} />}
      {tab === "promises" && <PromiseLedger />}
      {tab === "escalations" && <Escalations />}
      {tab === "eval" && <ReplyEvaluation />}
      {openId && <Detail id={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}

export default function App() {
  const [token, setTokenState] = useState(() => storageGet(TOKEN_KEY));
  const [generation, setGeneration] = useState(0);
  const connect = (value: string) => {
    storageSet(TOKEN_KEY, value);
    setTokenState(value);
    setGeneration((g) => g + 1);
  };

  return (
    <>
      <header>
        <div className="title">
          <h1>
            Urudhi <small>உறுதி — the receivables agent that remembers every promise</small>
          </h1>
        </div>
        <div className="status-row">
          <HealthChip />
          <ChainBadge key={generation} />
          <TokenForm token={token} onConnect={connect} />
        </div>
      </header>

      {!token && (
        <div className="note">
          No API token set. Paste the value of <code>URUDHI_API_TOKEN</code> above and press connect —
          every <code>/api</code> request is bearer-token protected.
        </div>
      )}

      <main key={generation}>
        <Workspace />
      </main>
    </>
  );
}
