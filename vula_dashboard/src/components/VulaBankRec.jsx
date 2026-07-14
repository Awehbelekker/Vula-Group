/**
 * VulaBankRec.jsx — bank reconciliation from the emailed statement (move off Xero, no API).
 * Upload/auto-ingest a Capitec statement → transactions auto-match to invoices (paid) + expenses.
 * Backend: /v1/commerce/{tenant}/admin/bank/* (migration 057).
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const R = (c) => `R${((c || 0) / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function VulaBankRec({ tenantId }) {
  const [sum, setSum] = useState(null);
  const [txns, setTxns] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [workers, setWorkers] = useState([]);
  const [vatReg, setVatReg] = useState(false);
  const [filter, setFilter] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    const [s, t, inv, acc, wk] = await Promise.all([
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/reconciliation`).then(r => r.json()).catch(() => null),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/transactions${filter ? `?status=${filter}` : ""}`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices?status=sent`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/accounts`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/workers`).then(r => r.json()).catch(() => ({})),
    ]);
    setSum(s); setTxns(t.transactions || []); setInvoices(inv.invoices || []);
    setAccounts(acc.accounts || []); setVatReg(!!acc.vat_registered); setWorkers(wk.workers || []);
  }, [tenantId, filter]);

  const categorize = async (id, account_code) => {
    if (!account_code) return;
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/transactions/${id}/categorize`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_code }),
    }).catch(() => {});
    load();
  };

  const assignWorker = async (id, worker_id) => {
    if (!worker_id) return;
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/transactions/${id}/worker`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ worker_id }),
    }).catch(() => {});
    load();
  };
  const workerName = (id) => (workers.find(w => w.id === id) || {}).name;
  useEffect(() => { load(); }, [load]);

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 4000); };

  const saveSettings = async () => {
    if (!pw.trim()) return flash("Enter the statement password (ID number).");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/settings`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw, bank: "Capitec" }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    if (r.error) flash(r.error); else { flash("Saved ✓ — statements now auto-unlock."); setPw(""); }
  };

  const upload = async (file) => {
    if (!file) return;
    setBusy(true); flash("Reading statement…");
    const b64 = await new Promise((res) => { const rd = new FileReader(); rd.onload = () => res(rd.result); rd.readAsDataURL(file); });
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/statement`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pdf_base64: b64, filename: file.name }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    if (r.error) { setBusy(false); return flash(r.error); }
    // Reconciliation runs server-side (takes minutes) — poll until transactions stop growing.
    flash("Reconciling… this takes a few minutes. The numbers below update as it works.");
    let last = -1, stable = 0;
    for (let i = 0; i < 40 && stable < 3; i++) {
      await new Promise(res => setTimeout(res, 15000));
      await load();
      const n = (sum && sum.txn_count) || 0;
      if (n === last && n > 0) stable++; else stable = 0;
      last = n;
    }
    setBusy(false); flash("Statement reconciled ✓"); load();
  };

  const recategorize = async () => {
    setBusy(true); flash("Re-running AI on unallocated items…");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/recategorize`, { method: "POST" })
      .then(r => r.json()).catch(() => ({ error: "network" }));
    setBusy(false);
    flash(r.error ? r.error : `Allocated ${r.updated} of ${r.reviewed} — ${r.still_pending} still need you.`);
    load();
  };

  const reviewOnWhatsApp = async () => {
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/review/start`, { method: "POST" })
      .then(r => r.json()).catch(() => ({ error: "network" }));
    flash(r.error ? r.error : (r.started ? "📲 Sent to your WhatsApp — answer one at a time there." : "Nothing needs review 🎉"));
  };

  const act = async (id, action, invoice_id) => {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/transactions/${id}/match`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, invoice_id }),
    }).catch(() => {});
    load();
  };

  return (
    <div style={{ fontFamily: "system-ui", color: C.text }}>
      <h4 style={{ fontSize: 15, fontWeight: 600, margin: "2px 0 2px" }}>🏦 Bank reconciliation</h4>
      <p style={{ color: C.muted, fontSize: 13, marginTop: 0 }}>
        Vula reads your weekly Capitec statement, matches deposits to invoices (marks them paid), and flags the rest. No accounting software needed.
      </p>

      {/* Summary */}
      {sum && !sum.error && (
        <div style={grid}>
          <Stat label="Money in" value={R(sum.money_in_cents)} color={C.green} />
          <Stat label="Money out" value={R(sum.money_out_cents)} color={C.red} />
          <Stat label="Invoices → paid" value={sum.matched} sub="auto-matched" />
          <Stat label="Needs your input" value={sum.needs_input || 0} color={(sum.needs_input || 0) > 0 ? C.red : C.text} sub="Vula unsure" />
          <Stat label="To review" value={sum.unmatched_credits + sum.unmatched_debits} color={(sum.unmatched_credits + sum.unmatched_debits) > 0 ? C.red : C.text} />
          <Stat label="Invoiced, unpaid" value={R(sum.invoices_sent_unpaid_cents)} sub="still owed" />
          <Stat label="Invoiced, paid" value={R(sum.invoices_paid_cents)} color={C.green} />
        </div>
      )}

      {/* Settings + upload */}
      <div style={{ ...card, background: C.alt, marginTop: 14, display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Statement password (ID number)</span>
        <input type="password" value={pw} onChange={e => setPw(e.target.value)} placeholder="stored encrypted" style={input} />
        <button style={{ ...btn, ...btnOn }} onClick={saveSettings}>Save</button>
        <label style={{ ...btn, cursor: "pointer" }}>
          {busy ? "Working…" : "⬆ Upload statement"}
          <input type="file" accept="application/pdf" style={{ display: "none" }} onChange={e => upload(e.target.files[0])} disabled={busy} />
        </label>
        <button style={btn} onClick={recategorize} disabled={busy} title="Re-run the AI over everything still unallocated">🔁 Re-run AI</button>
        <button style={btn} onClick={reviewOnWhatsApp} title="Vula asks you about each unallocated item on WhatsApp — reply to allocate">📲 Review on WhatsApp</button>
        {msg && <span style={{ fontSize: 12, color: C.green, width: "100%" }}>{msg}</span>}
      </div>

      {/* Filter + transactions */}
      <div style={{ display: "flex", gap: 6, margin: "16px 0 8px" }}>
        {[["", "All"], ["needs_input", "Needs input"], ["unmatched", "To review"], ["matched", "Matched"], ["ignored", "Ignored"]].map(([v, l]) => (
          <button key={v} onClick={() => setFilter(v)} style={{ ...chip, ...(filter === v ? chipOn : {}) }}>{l}</button>
        ))}
      </div>
      {txns.length === 0 ? <div style={{ color: C.muted, fontSize: 13 }}>No transactions yet — upload a statement or wait for the weekly email.</div>
        : txns.map(t => (
          <div key={t.id} style={card}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 13 }}>
                <span style={{ color: t.direction === "in" ? C.green : C.red }}>{t.direction === "in" ? "▲" : "▼"} {R(t.amount_cents)}</span>
                <span style={{ color: C.muted, fontWeight: 400 }}> · {t.txn_date || ""}</span>
                {vatReg && t.vat_cents > 0 && <span style={{ color: C.muted, fontWeight: 400, fontSize: 11 }}> · VAT {R(t.vat_cents)}</span>}
              </div>
              <div style={{ fontSize: 12, color: C.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.description}</div>
              <div style={{ display: "flex", gap: 6, marginTop: 3, flexWrap: "wrap", alignItems: "center" }}>
                {t.categorized_by === "default" && <span style={{ fontSize: 11, color: C.red }} title="Vula wasn't sure — please confirm the category">⚠ confirm</span>}
                {accounts.length > 0 && (
                  <select value={t.account_code || ""} onChange={e => categorize(t.id, e.target.value)}
                    style={{ ...input, fontSize: 11, padding: "3px 6px", maxWidth: 180, borderColor: t.categorized_by === "default" ? C.red : C.border }}
                    title={t.categorized_by ? `allocated by ${t.categorized_by}` : "allocate account"}>
                    <option value="">— category —</option>
                    {accounts.map(a => <option key={a.code} value={a.code}>{a.name}</option>)}
                  </select>
                )}
                {t.direction === "out" && workers.length > 0 && (
                  t.worker_id
                    ? <span style={{ fontSize: 11, color: C.green, alignSelf: "center" }}>👷 {workerName(t.worker_id)}{t.project ? ` · ${t.project}` : ""}</span>
                    : <select defaultValue="" onChange={e => e.target.value && assignWorker(t.id, e.target.value)}
                        style={{ ...input, fontSize: 11, padding: "3px 6px", maxWidth: 150 }}>
                        <option value="">— worker —</option>
                        {workers.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                      </select>
                )}
              </div>
            </div>
            {t.match_status === "matched" ? <span style={{ ...pill, color: C.green, borderColor: C.green }}>matched</span>
              : t.match_status === "ignored" ? <span style={{ ...pill, color: C.muted, borderColor: C.border }}>ignored</span>
                : (
                  <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                    {t.direction === "in" && (
                      <select defaultValue="" onChange={e => e.target.value && act(t.id, "match", e.target.value)} style={{ ...input, maxWidth: 150 }}>
                        <option value="">Match invoice…</option>
                        {invoices.map(i => <option key={i.id} value={i.id}>{i.invoice_number} · {R(i.total_cents)}</option>)}
                      </select>
                    )}
                    {t.direction === "out" && <button style={miniBtn} onClick={() => act(t.id, "expense")}>Log expense</button>}
                    <button style={miniBtn} onClick={() => act(t.id, "ignore")}>Ignore</button>
                  </div>
                )}
          </div>
        ))}
    </div>
  );
}

const Stat = ({ label, value, sub, color }) => (
  <div style={card}>
    <div style={{ fontSize: 10, textTransform: "uppercase", color: C.muted, marginBottom: 3 }}>{label}</div>
    <div style={{ fontSize: 18, fontWeight: 700, color: color || C.text }}>{value}</div>
    {sub && <div style={{ fontSize: 10, color: C.muted }}>{sub}</div>}
  </div>
);

const grid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10 };
const card = { display: "flex", alignItems: "center", gap: 10, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 12px", marginBottom: 8 };
const btn = { padding: "7px 12px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "#fff", borderColor: C.green };
const miniBtn = { padding: "4px 10px", border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const chip = { padding: "5px 12px", border: `1px solid ${C.border}`, borderRadius: 16, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const chipOn = { background: C.green, color: "#fff", borderColor: C.green };
const input = { padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text };
const pill = { fontSize: 11, padding: "2px 8px", borderRadius: 10, border: "1px solid", fontWeight: 600 };
