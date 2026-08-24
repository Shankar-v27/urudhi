import { useEffect, useState } from "react";
import { api, inr, Audit, Invoice, InvoiceDetail, Promise_, Summary } from "./api";

function StateChip({ state }: { state: string }) {
  return <span className={`state ${state}`}>{state.replace("_", " ")}</span>;
}

function Tiles({ summary }: { summary: Summary }) {
  const rate =
    summary.outstanding_paise > 0
      ? ((100 * summary.recovered_paise) / summary.outstanding_paise).toFixed(1)
      : "0";
  return (
    <div className="tiles">
      <div className="tile">
        <div className="label">Invoices</div>
        <div className="value">{summary.invoices}</div>
      </div>
      <div className="tile">
        <div className="label">Outstanding</div>
        <div className="value">{inr(summary.outstanding_paise)}</div>
      </div>
      <div className="tile">
        <div className="label">Recovered — observed on rails</div>
        <div className="value">{inr(summary.recovered_paise)}</div>
      </div>
      <div className="tile">
        <div className="label">Recovery rate</div>
        <div className="value">{rate}%</div>
      </div>
    </div>
  );
}

function InvoiceTable({ invoices, onOpen }: { invoices: Invoice[]; onOpen: (id: string) => void }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Invoice</th>
          <th>State</th>
          <th className="num">Amount</th>
          <th className="num">Recovered</th>
          <th className="num">Balance</th>
          <th>Due</th>
        </tr>
      </thead>
      <tbody>
        {invoices.map((invoice) => (
          <tr key={invoice.id} className="row" onClick={() => onOpen(invoice.id)}>
            <td>{invoice.number}</td>
            <td><StateChip state={invoice.state} /></td>
            <td className="num">{inr(invoice.amount)}</td>
            <td className="num">{inr(invoice.amount_paid)}</td>
            <td className="num">{inr(invoice.balance)}</td>
            <td className="muted">{invoice.due_on}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PromiseTable({ promises }: { promises: Promise_[] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Invoice</th>
          <th>State</th>
          <th className="num">Amount</th>
          <th>Promised for</th>
          <th className="num">Confidence</th>
          <th>Their words</th>
        </tr>
      </thead>
      <tbody>
        {promises.map((promise) => (
          <tr key={promise.id}>
            <td>{promise.invoice_id}</td>
            <td><StateChip state={promise.state} /></td>
            <td className="num">{inr(promise.amount)}</td>
            <td>{promise.promised_on}</td>
            <td className="num">{promise.confidence.toFixed(2)}</td>
            <td className="muted">“{promise.verbatim}”</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const [detail, setDetail] = useState<InvoiceDetail | null>(null);
  useEffect(() => {
    api.invoice(id).then(setDetail).catch(console.error);
  }, [id]);
  if (!detail) return null;
  const { invoice, debtor, events } = detail;
  return (
    <div className="detail">
      <button className="close" onClick={onClose}>close</button>
      <h1>{invoice.number}</h1>
      <p className="muted">
        {debtor.name} · {debtor.contact_name} · {debtor.preferred_channel}
      </p>
      <p>
        <StateChip state={invoice.state} /> &nbsp; {inr(invoice.amount_paid)} of{" "}
        {inr(invoice.amount)} recovered
      </p>
      <h2>Full audit timeline</h2>
      <ul className="timeline">
        {events.map((event) => (
          <li key={event.seq}>
            <span className={`kind ${event.kind}`}>{event.kind.replace(/_/g, " ")}</span>
            <span className="when">
              #{event.seq} · {new Date(event.at).toLocaleString("en-IN")} · {event.actor}
            </span>
            {typeof event.payload.verbatim === "string" && (
              <div className="verbatim">“{event.payload.verbatim}”</div>
            )}
            {typeof event.payload.text === "string" && (
              <div className="verbatim">{event.payload.text}</div>
            )}
            {typeof event.payload.reason === "string" && (
              <div className="muted">{event.payload.reason}</div>
            )}
            {typeof event.payload.amount === "number" && (
              <div>{inr(event.payload.amount)}</div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [promises, setPromises] = useState<Promise_[]>([]);
  const [audit, setAudit] = useState<Audit | null>(null);
  const [tab, setTab] = useState<"invoices" | "promises">("invoices");
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    api.summary().then(setSummary).catch(console.error);
    api.invoices().then(setInvoices).catch(console.error);
    api.promises().then(setPromises).catch(console.error);
    api.audit().then(setAudit).catch(console.error);
  }, []);

  return (
    <>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>
          Urudhi <small>உறுதி — the receivables agent that remembers every promise</small>
        </h1>
        {audit && (
          <span className={`chain ${audit.chain.verified ? "ok" : "bad"}`}>
            {audit.chain.verified
              ? `audit chain verified — ${audit.total} events`
              : `audit chain BROKEN: ${audit.chain.error}`}
          </span>
        )}
      </header>

      {summary && <Tiles summary={summary} />}

      <div className="tabs">
        <button className={tab === "invoices" ? "active" : ""} onClick={() => setTab("invoices")}>
          Invoices
        </button>
        <button className={tab === "promises" ? "active" : ""} onClick={() => setTab("promises")}>
          Promise ledger
        </button>
      </div>

      {tab === "invoices" && <InvoiceTable invoices={invoices} onOpen={setOpenId} />}
      {tab === "promises" && <PromiseTable promises={promises} />}
      {openId && <Detail id={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}
