/**
 * VulaBankRec.jsx — bank reconciliation from the emailed statement (move off Xero, no API).
 * Upload/auto-ingest a Capitec statement → transactions auto-match to invoices (paid) + expenses.
 * Backend: /v1/commerce/{tenant}/admin/bank/* (migration 057).
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../lib/authFetch";

const C = { surface: "var(--surface)", border: "var(--border)", green: "var(--accent)", red: "var(--danger)", text: "var(--text)", muted: "var(--muted)", alt: "var(--surface-alt)" };
const R = (c) => `R${((c || 0) / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function VulaBankRec({ tenantId }) {
  const [sum, setSum] = useState(null);
  const [txns, setTxns] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [pendingOrders, setPendingOrders] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [workers, setWorkers] = useState([]);
  const [vatReg, setVatReg] = useState(false);
  const [filter, setFilter] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [noProject, setNoProject] = useState(false);
  // Project/trade allocation only for a business that works in projects (the `projects`
  // module — DIGG), not a shop like Off the Hook.
  const [hasProjects, setHasProjects] = useState(false);
  useEffect(() => {
    if (!tenantId) return;
    fetch(`${VULA_API}/v1/tenants/${tenantId}`).then(r => r.json())
      .then(d => { const t = d.tenant || d; const p = t.profile;   // the backend's tenant profile decides
        setHasProjects(p ? !!p.uses_projects : (t.modules || []).includes("projects")); })
      .catch(() => setHasProjects(false));
  }, [tenantId]);

  const load = useCallback(async () => {
    const [s, t, inv, ord, acc, wk] = await Promise.all([
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/reconciliation`).then(r => r.json()).catch(() => null),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/transactions${filter ? `?status=${filter}` : ""}`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices?status=sent`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/orders?status=pending_payment`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/accounts`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/workers`).then(r => r.json()).catch(() => ({})),
    ]);
    setSum(s); setTxns(t.transactions || []); setInvoices(inv.invoices || []);
    setPendingOrders(ord.orders || []);
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

  // Project + trade per line (job costing reads it; Vula learns it for the next statement).
  const allocate = async (id, project, trade) => {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/transactions/${id}/allocate`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: project || null, trade: trade || null }),
    }).catch(() => {});
    load();
  };
  const projectNames = [...new Set(txns.map(t => t.project).filter(Boolean))].sort();

  // A statement already categorised in a spreadsheet: preview → confirm project names → import.
  const [sheet, setSheet] = useState(null);   // { b64, name, preview, map }
  const pickSheet = async (file) => {
    if (!file) return;
    const b64 = await new Promise((res) => { const rd = new FileReader(); rd.onload = () => res(String(rd.result).split(",").pop()); rd.readAsDataURL(file); });
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/statement/sheet`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_base64: b64, filename: file.name, preview: true }),
    }).then(r => r.json()).catch(() => ({ detail: "network" }));
    if (r.detail || r.error) return flash(r.detail || r.error);
    const map = {};
    (r.projects || []).forEach(p => { map[p.label] = p.suggested || p.label; });
    setSheet({ b64, name: file.name, preview: r, map, replace: (r.existing_lines_in_period || 0) > 0 });
  };
  const importSheet = async () => {
    setBusy(true);
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/statement/sheet`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_base64: sheet.b64, filename: sheet.name, project_map: sheet.map, replace_existing: !!sheet.replace }),
    }).then(r => r.json()).catch(() => ({ detail: "network" }));
    setBusy(false);
    if (r.detail || r.error) return flash(r.detail || r.error);
    flash(`Imported ${r.saved} of ${r.parsed} lines ✓${r.set_aside ? ` · ${r.set_aside} earlier lines for the same dates set aside` : ""}.`);
    setSheet(null); load();
  };
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

  const act = async (id, action, invoice_id, order_id) => {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/transactions/${id}/match`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, invoice_id, order_id }),
    }).catch(() => {});
    load();
  };

  return (
    <div style={{ color: C.text }}>
      <h4 style={{ fontSize: 15, fontWeight: 600, margin: "2px 0 2px" }}>🏦 Bank reconciliation</h4>
      <p style={{ color: C.muted, fontSize: 13, marginTop: 0 }}>
        Vula reads your weekly Capitec statement (and payment-confirmation emails) and matches deposits to invoices or orders paid by EFT (marks them paid), flagging the rest. No accounting software needed.
      </p>

      {/* Summary */}
      {sum && !sum.error && (
        <div style={grid}>
          <Stat label="Money in" value={R(sum.money_in_cents)} color={'var(--ok)'} />
          <Stat label="Money out" value={R(sum.money_out_cents)} color={C.red} />
          <Stat label="Invoices → paid" value={sum.matched} sub="auto-matched" />
          <Stat label="Needs your input" value={sum.needs_input || 0} color={(sum.needs_input || 0) > 0 ? C.red : C.text} sub="Vula unsure" />
          <Stat label="To review" value={sum.unmatched_credits + sum.unmatched_debits} color={(sum.unmatched_credits + sum.unmatched_debits) > 0 ? C.red : C.text} />
          <Stat label="Invoiced, unpaid" value={R(sum.invoices_sent_unpaid_cents)} sub="still owed" />
          <Stat label="Invoiced, paid" value={R(sum.invoices_paid_cents)} color={'var(--ok)'} />
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
        <label style={{ ...btn, cursor: "pointer" }} title="A statement you've already categorised in Excel — categories, projects and trades are kept">
          ⬆ Import categorised sheet
          <input type="file" accept=".xlsx,.xlsm,.csv" style={{ display: "none" }} onChange={e => pickSheet(e.target.files[0])} disabled={busy} />
        </label>
        <button style={btn} onClick={recategorize} disabled={busy} title="Re-run the AI over everything still unallocated">🔁 Re-run AI</button>
        <button style={btn} onClick={reviewOnWhatsApp} title="Vula asks you about each unallocated item on WhatsApp — reply to allocate">📲 Review on WhatsApp</button>
        {msg && <span style={{ fontSize: 12, color: C.green, width: "100%" }}>{msg}</span>}
      </div>

      {sheet && (
        <div style={{ ...card, flexDirection: "column", alignItems: "stretch", marginTop: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>{sheet.name}: {sheet.preview.lines} lines, {sheet.preview.first} – {sheet.preview.last} · in {R(sheet.preview.money_in_cents)} · out {R(sheet.preview.money_out_cents)}</div>
          {(sheet.preview.projects || []).length > 0 && <div style={{ fontSize: 12, color: C.muted, margin: "4px 0 6px" }}>Which of your projects is each one? (Vula suggested; change a name to merge it with an existing project.)</div>}
          {(sheet.preview.projects || []).map(p => (
            <div key={p.label} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 12.5, marginBottom: 4 }}>
              <span style={{ minWidth: 140 }}>{p.label} <span style={{ color: C.muted }}>({p.lines} lines · out {R(p.out)} · in {R(p.in)})</span></span>
              <span>→</span>
              <input list="bank-projects" value={sheet.map[p.label] || ""} style={{ ...input, fontSize: 12, padding: "4px 8px" }}
                onChange={e => setSheet({ ...sheet, map: { ...sheet.map, [p.label]: e.target.value } })} />
            </div>))}
          {sheet.preview.existing_lines_in_period > 0 && (
            <label style={{ fontSize: 12.5, display: "flex", gap: 6, alignItems: "flex-start", marginTop: 6 }}>
              <input type="checkbox" checked={!!sheet.replace} onChange={e => setSheet({ ...sheet, replace: e.target.checked })} />
              <span>Vula already has {sheet.preview.existing_lines_in_period} bank lines for {sheet.preview.first} – {sheet.preview.last} (read from PDF statements). Set them aside and use this sheet instead, so nothing is counted twice. They're kept, not deleted.</span>
            </label>)}
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <button style={{ ...btn, ...btnOn }} disabled={busy} onClick={importSheet}>{busy ? "Importing…" : "Import"}</button>
            <button style={btn} onClick={() => setSheet(null)}>Cancel</button>
          </div>
        </div>
      )}
      <datalist id="bank-projects">{projectNames.map(p => <option key={p} value={p} />)}</datalist>

      {/* Filter + transactions */}
      <div style={{ display: "flex", gap: 6, margin: "16px 0 8px" }}>
        {[["", "All"], ["needs_input", "Needs input"], ["unmatched", "To review"], ["matched", "Matched"], ["ignored", "Ignored"]].map(([v, l]) => (
          <button key={v} onClick={() => { setFilter(v); setNoProject(false); }} style={{ ...chip, ...(filter === v && !noProject ? chipOn : {}) }}>{l}</button>
        ))}
        {hasProjects && <button onClick={() => { setFilter(""); setNoProject(true); }} style={{ ...chip, ...(noProject ? chipOn : {}) }} title="Materials and labour paid but not put on a project yet">Not on a project</button>}
      </div>
      {txns.length === 0 ? <div style={{ color: C.muted, fontSize: 13 }}>No transactions yet — upload a statement or wait for the weekly email.</div>
        : txns.filter(t => !noProject || (t.direction === "out" && !t.project && ["cost_of_sales", "casual_labour"].includes(t.account_code))).map(t => (
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
                {hasProjects && !t.worker_id && (
                  <>
                    <input list="bank-projects" defaultValue={t.project || ""} placeholder="project"
                      title={t.direction === "in" ? "Money in on a project, e.g. a payment certificate" : "Which project this was spent on"}
                      onBlur={e => e.target.value !== (t.project || "") && allocate(t.id, e.target.value, t.trade)}
                      style={{ ...input, fontSize: 11, padding: "3px 6px", width: 120 }} />
                    {t.direction === "out" && (
                      <input defaultValue={t.trade || ""} placeholder="trade"
                        onBlur={e => e.target.value !== (t.trade || "") && allocate(t.id, t.project, e.target.value)}
                        style={{ ...input, fontSize: 11, padding: "3px 6px", width: 120 }} />)}
                  </>
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
            {t.match_status === "matched" ? <span style={{ ...pill, color: 'var(--ok)', borderColor: 'var(--ok)' }}>matched</span>
              : t.match_status === "ignored" ? <span style={{ ...pill, color: C.muted, borderColor: C.border }}>ignored</span>
                : (
                  <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                    {t.direction === "in" && (
                      <select defaultValue="" onChange={e => e.target.value && act(t.id, "match", e.target.value)} style={{ ...input, maxWidth: 150 }}>
                        <option value="">Match invoice…</option>
                        {invoices.map(i => <option key={i.id} value={i.id}>{i.invoice_number} · {R(i.total_cents)}</option>)}
                      </select>
                    )}
                    {t.direction === "in" && (
                      <select defaultValue="" onChange={e => e.target.value && act(t.id, "match", null, e.target.value)} style={{ ...input, maxWidth: 150 }}>
                        <option value="">Match order…</option>
                        {pendingOrders.map(o => <option key={o.id} value={o.id}>{o.display_id} · {o.customer_name || "?"} · {R(o.total_cents)}</option>)}
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
  <div style={{ ...card, flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
    <div style={{ fontSize: 10, textTransform: "uppercase", color: C.muted, marginBottom: 3 }}>{label}</div>
    <div style={{ fontSize: 18, fontWeight: 700, color: color || C.text }}>{value}</div>
    {sub && <div style={{ fontSize: 10, color: C.muted }}>{sub}</div>}
  </div>
);

const grid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10 };
const card = { display: "flex", alignItems: "center", gap: 10, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 12px", marginBottom: 8 };
const btn = { padding: "7px 12px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "var(--on-accent)", borderColor: C.green };
const miniBtn = { padding: "4px 10px", border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const chip = { padding: "5px 12px", border: `1px solid ${C.border}`, borderRadius: 16, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const chipOn = { background: C.green, color: "var(--on-accent)", borderColor: C.green };
const input = { padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text };
const pill = { fontSize: 11, padding: "2px 8px", borderRadius: 10, border: "1px solid", fontWeight: 600 };
