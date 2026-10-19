/**
 * CrossCheckV2.jsx — the redesigned cross-check in Money › Finances › Ledger (2026-10-19, UX
 * redesign Phase F). Same data and actions as VulaCrossCheck (finances/useCrossCheck.js): the VAT
 * view as a table, and the five gaps between documents, books and bank as disclosures with
 * their counts, "Book them" and the one-tap "Yes, that's it" bank matches.
 */
import { ChevronRight, ReceiptText, SearchCheck } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, ErrorCard, Skeleton, StatusChip } from "../ui/index.jsx";
import { useCrossCheck } from "./useCrossCheck";
import "./finances.css";

const money = (c) => formatMoney(Number(c) || 0, { cents: "always" });

export default function CrossCheckV2({ tenantId }) {
  const x = useCrossCheck(tenantId);
  if (!x.loaded) return <Card><Skeleton lines={3} /></Card>;
  const { rep, vat } = x;
  if (!rep || rep.detail) return <ErrorCard what="the cross-check" onRetry={x.load} />;
  return (
    <section className="vfn-stack" aria-labelledby="vcc-h">
      <div className="vfn-head"><h3 id="vcc-h" className="vfn-h3"><SearchCheck size={18} aria-hidden="true" />Cross-check: documents, books and bank</h3></div>
      <p className="vfn-sub">{rep.text}</p>

      {vat && !vat.detail && (
        <Card pad={0} className="vfn-card">
          <h4 className="vfn-card-h"><ReceiptText size={16} aria-hidden="true" />VAT {vat.vat_registered ? "in vs out" : "— if you were VAT registered"}</h4>
          <p className={`vfn-pad vfn-summary${vat.over_threshold ? " vfn-neg" : ""}`}>{vat.text}</p>
          {vat.months?.length > 0 && (
            <div className="vfn-tb-wrap" role="region" aria-label="VAT per month" tabIndex={0}>
              <table className="vfn-tb">
                <thead><tr><th scope="col">Month</th><th scope="col">Sales</th><th scope="col">VAT {vat.vat_registered ? "on sales" : "you'd charge"}</th>
                  <th scope="col">VAT claimable</th><th scope="col">No supplier VAT no.</th><th scope="col">Net</th></tr></thead>
                <tbody>{vat.months.map((m) => (
                  <tr key={m.month}><th scope="row">{m.month}</th><td className="vula-money">{money(m.sales)}</td><td className="vula-money">{money(m.output)}</td>
                    <td className="vula-money">{money(m.input_claimable)}</td><td className="vula-money vfn-sub">{money(m.input_no_vat_number)}</td>
                    <td className="vula-money vfn-strong">{money(m.net_cents)}</td></tr>))}
                </tbody>
              </table>
            </div>)}
          <p className="vfn-pad vfn-sub">Claimable = VAT on suppliers' tax invoices whose VAT number is on file. Guidance only — confirm with your accountant.</p>
        </Card>)}

      <ul className="vfn-projects" aria-label="Cross-check">
        <Gap k="u" x={x} title="Bills filed but not in the books" count={rep.unbooked.length}>
          <p className="vfn-sub">{money(rep.unbooked_total_cents)} of supplier bills/quotes Vula read but never booked — so they're not in payables, VAT or matching.</p>
          {rep.unbooked.length > 0 && <div><Button size="sm" disabled={x.busy} onClick={x.bookAll}>{x.busy ? "Booking…" : "Book them"}</Button></div>}
          <Lines items={rep.unbooked.slice(0, 30)} label="Unbooked bills" render={(u) => [u.id,
            <span className="vfn-name">{u.supplier || u.filename}</span>, <span className="vfn-sub">{u.category} · {u.date || "—"}</span>, money(u.total_cents)]} />
        </Gap>

        <Gap k="b" x={x} title="Supplier bills not marked paid" count={rep.bills_unpaid.length}>
          <Lines items={rep.bills_unpaid.slice(0, 50)} label="Unpaid supplier bills" render={(b) => [b.invoice_id,
            <span className="vfn-name">{b.supplier || "?"} <span className="vfn-sub">{b.invoice_number} · {b.issue_date}</span></span>,
            b.likely_payment
              ? <span className="vfn-row">Paid {b.likely_payment.txn_date}? <span className="vfn-sub">{b.likely_payment.description}</span>
                  <Button size="sm" variant="secondary" onClick={() => x.confirm(b.likely_payment.txn_id, b.invoice_id)}
                    aria-label={`Yes, ${b.supplier || "this bill"} was paid on ${b.likely_payment.txn_date}`}>Yes, that's it</Button></span>
              : <span className="vfn-sub">{b.possible_payments > 1 ? `${b.possible_payments} possible payments — match in Bank` : "No matching payment in the bank"}</span>,
            money(b.total_cents)]} />
        </Gap>

        <Gap k="n" x={x} title="Payments with no invoice or receipt on file" count={rep.no_document.length}>
          <p className="vfn-sub">{money(rep.no_document_total_cents)} paid out with nothing filed behind it. Without a tax invoice there's no VAT claim, and SARS can ask. Forward the invoice to Vula on WhatsApp or email.</p>
          <Lines items={rep.no_document} label="Payments with no document" render={(g) => [g.counterparty,
            <span className="vfn-name">{g.counterparty}</span>,
            <span className="vfn-sub">{g.lines} payment{g.lines === 1 ? "" : "s"} · last {g.last}{g.projects.length ? ` · ${g.projects.join(", ")}` : ""}</span>,
            money(g.total_cents)]} />
        </Gap>

        <Gap k="s" x={x} title="Your invoices not yet paid" count={rep.sales_unpaid.length}>
          <Lines items={rep.sales_unpaid} label="Unpaid sales invoices" render={(s) => [s.invoice_id,
            <span className="vfn-name">{s.customer || "?"} <span className="vfn-sub">{s.invoice_number} · {s.issue_date}</span></span>,
            s.likely_payment && <span className="vfn-row">Received {s.likely_payment.txn_date}?
              <Button size="sm" variant="secondary" onClick={() => x.confirm(s.likely_payment.txn_id, s.invoice_id)}
                aria-label={`Yes, mark ${s.invoice_number || "this invoice"} paid`}>Yes, mark paid</Button></span>,
            money(s.total_cents)]} />
        </Gap>

        <Gap k="i" x={x} title="Money in not matched to an invoice" count={rep.money_in_unmatched_lines}>
          <p className="vfn-sub">{money(rep.money_in_unmatched_cents)} received without an invoice or order in Vula. Invoice through Vula (or progress claims, for project work) so payments can be matched.</p>
          <Lines items={rep.unexplained_in_sample} label="Unmatched money in" render={(t) => [t.txn_id,
            <span className="vfn-name">{t.description}</span>, <span className="vfn-sub">{t.txn_date}</span>, money(t.amount_cents)]} />
        </Gap>
      </ul>
      {x.msg && <p className="vfn-sub" role="status">{x.msg}</p>}
    </section>
  );
}

function Gap({ k, x, title, count, children }) {
  const open = x.open === k;
  return (
    <li className="vfn-proj">
      <button type="button" className="vfn-proj-head" aria-expanded={open} aria-controls={`vcc-${k}`} onClick={() => x.toggle(k)}>
        <ChevronRight size={18} className="vfn-chev" aria-hidden="true" />
        <span className="vfn-proj-name">{title}</span>
        <StatusChip tone={count ? "warn" : "muted"}>{count}</StatusChip>
      </button>
      {open && <div id={`vcc-${k}`} className="vfn-detail">{children}</div>}
    </li>
  );
}

/** render(item) → [key, name, detail, amount] */
function Lines({ items, label, render }) {
  if (!items?.length) return null;
  return (
    <ul className="vfn-list vfn-list--boxed" aria-label={label}>
      {items.map((it) => {
        const [key, name, detail, amount] = render(it);
        return <li key={key} className="vfn-line">{name}{detail}<span className="vula-money vfn-amt">{amount}</span></li>;
      })}
    </ul>
  );
}
