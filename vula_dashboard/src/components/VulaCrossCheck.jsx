/**
 * VulaCrossCheck.jsx — documents ↔ books ↔ bank, and VAT in vs out.
 *
 * 2026-09-28: bills filed but never booked, supplier bills not marked paid (with the bank debit
 * that probably paid each — one tap to confirm), payments with no invoice on file (no VAT claim
 * without one), unpaid sales invoices, and a VAT view: what the business charges and can claim —
 * or, not registered, what it would be — per month. Backend: vula/commerce/cross_check.py.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../lib/authFetch";

const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", amber: "#B7791F",
  text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const R = (c) => "R" + ((Number(c) || 0) / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const btn = { padding: "5px 10px", borderRadius: 6, border: `1px solid ${C.border}`, background: C.surface, cursor: "pointer", fontSize: 12 };
const row = { display: "flex", flexWrap: "wrap", gap: 8, alignItems: "baseline", fontSize: 12.5, padding: "6px 0", borderBottom: `1px solid ${C.alt}` };

function Section({ title, count, children, open, onToggle }) {
  return (
    <div style={{ border: `1px solid ${C.border}`, borderRadius: 8, marginBottom: 8 }}>
      <button onClick={onToggle} style={{ width: "100%", textAlign: "left", background: "none", border: "none", padding: "10px 12px", cursor: "pointer", fontSize: 13, fontWeight: 600, color: C.text }}>
        {open ? "▾" : "▸"} {title} <span style={{ color: count ? C.amber : C.muted, fontWeight: 400 }}>· {count}</span>
      </button>
      {open && <div style={{ padding: "0 12px 10px" }}>{children}</div>}
    </div>
  );
}

export default function VulaCrossCheck({ tenantId }) {
  const [rep, setRep] = useState(null);
  const [vat, setVat] = useState(null);
  const [open, setOpen] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin`;

  const load = useCallback(async () => {
    if (!tenantId) return;
    const [a, b] = await Promise.all([
      fetch(`${base}/crosscheck`).then(r => r.json()).catch(() => null),
      fetch(`${base}/reports/vat-scenario`).then(r => r.json()).catch(() => null),
    ]);
    setRep(a); setVat(b);
  }, [tenantId, base]);
  useEffect(() => { load(); }, [load]);

  const toggle = (k) => setOpen(open === k ? "" : k);
  const bookAll = async () => {
    setBusy(true);
    const r = await fetch(`${base}/crosscheck/book-unbooked`, { method: "POST" }).then(r => r.json()).catch(() => ({}));
    setBusy(false); setMsg(r.detail || `Booked ${r.booked ?? 0}; ${r.skipped ?? 0} couldn't be.`); load();
  };
  const confirm = async (txnId, invoiceId) => {
    await fetch(`${base}/bank/transactions/${txnId}/match`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "match", invoice_id: invoiceId }) }).catch(() => {});
    load();
  };

  if (!rep || rep.detail) return null;

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 20 }}>
      <h3 style={{ margin: "0 0 4px", fontSize: 15, fontWeight: 700, color: C.text }}>🔎 Cross-check: documents ↔ books ↔ bank</h3>
      <p style={{ fontSize: 12.5, color: C.muted, margin: "0 0 12px" }}>{rep.text}</p>

      {vat && !vat.detail && (
        <div style={{ background: C.alt, borderRadius: 8, padding: 12, marginBottom: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: C.text, marginBottom: 4 }}>
            🧾 VAT {vat.vat_registered ? "in vs out" : "— if you were VAT registered"}
          </div>
          <div style={{ fontSize: 12.5, color: vat.over_threshold ? C.red : C.text, marginBottom: 8 }}>{vat.text}</div>
          {vat.months?.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                <thead><tr style={{ color: C.muted, textAlign: "right" }}>
                  <th style={{ textAlign: "left" }}>Month</th><th>Sales</th><th>VAT {vat.vat_registered ? "on sales" : "you'd charge"}</th>
                  <th>VAT claimable</th><th>No supplier VAT no.</th><th>Net</th></tr></thead>
                <tbody>{vat.months.map(m => (
                  <tr key={m.month} style={{ textAlign: "right", borderTop: `1px solid ${C.border}` }}>
                    <td style={{ textAlign: "left" }}>{m.month}</td><td>{R(m.sales)}</td><td>{R(m.output)}</td>
                    <td>{R(m.input_claimable)}</td><td style={{ color: C.muted }}>{R(m.input_no_vat_number)}</td>
                    <td style={{ fontWeight: 600 }}>{R(m.net_cents)}</td></tr>))}
                </tbody>
              </table>
            </div>)}
          <div style={{ fontSize: 11, color: C.muted, marginTop: 6 }}>Claimable = VAT on suppliers' tax invoices whose VAT number is on file. Guidance only — confirm with your accountant.</div>
        </div>)}

      <Section title="Bills filed but not in the books" count={rep.unbooked.length} open={open === "u"} onToggle={() => toggle("u")}>
        <div style={{ fontSize: 12, color: C.muted, marginBottom: 6 }}>{R(rep.unbooked_total_cents)} of supplier bills/quotes Vula read but never booked — so they're not in payables, VAT or matching.</div>
        {rep.unbooked.length > 0 && <button style={{ ...btn, background: C.green, color: "#fff", border: "none" }} disabled={busy} onClick={bookAll}>{busy ? "Booking…" : "Book them"}</button>}
        {rep.unbooked.slice(0, 30).map(u => (
          <div key={u.id} style={row}><span style={{ flex: "1 1 200px" }}>{u.supplier || u.filename}</span>
            <span style={{ color: C.muted }}>{u.category} · {u.date || "—"}</span><b>{R(u.total_cents)}</b></div>))}
      </Section>

      <Section title="Supplier bills not marked paid" count={rep.bills_unpaid.length} open={open === "b"} onToggle={() => toggle("b")}>
        {rep.bills_unpaid.slice(0, 50).map(b => (
          <div key={b.invoice_id} style={row}>
            <span style={{ flex: "1 1 200px" }}>{b.supplier || "?"} <span style={{ color: C.muted }}>{b.invoice_number} · {b.issue_date}</span></span>
            <b>{R(b.total_cents)}</b>
            {b.likely_payment
              ? <span>paid {b.likely_payment.txn_date}? <i style={{ color: C.muted }}>{b.likely_payment.description}</i>{" "}
                  <button style={btn} onClick={() => confirm(b.likely_payment.txn_id, b.invoice_id)}>Yes, that's it</button></span>
              : <span style={{ color: C.muted }}>{b.possible_payments > 1 ? `${b.possible_payments} possible payments — match in Bank` : "no matching payment in the bank"}</span>}
          </div>))}
      </Section>

      <Section title="Payments with no invoice or receipt on file" count={rep.no_document.length} open={open === "n"} onToggle={() => toggle("n")}>
        <div style={{ fontSize: 12, color: C.muted, marginBottom: 6 }}>{R(rep.no_document_total_cents)} paid out with nothing filed behind it. Without a tax invoice there's no VAT claim, and SARS can ask. Forward the invoice to Vula on WhatsApp or email.</div>
        {rep.no_document.map(g => (
          <div key={g.counterparty} style={row}><span style={{ flex: "1 1 200px" }}>{g.counterparty}</span>
            <span style={{ color: C.muted }}>{g.lines} payment{g.lines === 1 ? "" : "s"} · last {g.last}{g.projects.length ? ` · ${g.projects.join(", ")}` : ""}</span>
            <b>{R(g.total_cents)}</b></div>))}
      </Section>

      <Section title="Your invoices not yet paid" count={rep.sales_unpaid.length} open={open === "s"} onToggle={() => toggle("s")}>
        {rep.sales_unpaid.map(s => (
          <div key={s.invoice_id} style={row}><span style={{ flex: "1 1 200px" }}>{s.customer || "?"} <span style={{ color: C.muted }}>{s.invoice_number} · {s.issue_date}</span></span>
            <b>{R(s.total_cents)}</b>
            {s.likely_payment && <span>received {s.likely_payment.txn_date}? <button style={btn} onClick={() => confirm(s.likely_payment.txn_id, s.invoice_id)}>Yes, mark paid</button></span>}
          </div>))}
      </Section>

      <Section title="Money in not matched to an invoice" count={rep.money_in_unmatched_lines} open={open === "i"} onToggle={() => toggle("i")}>
        <div style={{ fontSize: 12, color: C.muted, marginBottom: 6 }}>{R(rep.money_in_unmatched_cents)} received without an invoice in Vula. For project work, bill through Vula (Finances › progress claims) so payments can be matched.</div>
        {rep.unexplained_in_sample.map(t => (
          <div key={t.txn_id} style={row}><span style={{ flex: "1 1 200px" }}>{t.description}</span><span style={{ color: C.muted }}>{t.txn_date}</span><b>{R(t.amount_cents)}</b></div>))}
      </Section>
      {msg && <div style={{ fontSize: 12.5, color: C.muted, marginTop: 6 }}>{msg}</div>}
    </div>
  );
}
