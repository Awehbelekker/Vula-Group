/**
 * useBank.js — Bank & matching's data and actions (2026-10-19, UX redesign Phase F). Shared by
 * VulaBankRec.jsx and bank/BankV2.jsx; moved out unchanged — same endpoints
 * (/v1/commerce/{tenant}/admin/bank/*, migration 057), same bodies, same messages. Money stays
 * integer cents from the server; nothing here does arithmetic on it.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";

export const R = (c) => `R${((c || 0) / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const BANK_FILTERS = [["", "All"], ["needs_input", "Needs input"], ["unmatched", "To review"], ["matched", "Matched"], ["ignored", "Ignored"]];
const PROJECT_ACCOUNTS = ["cost_of_sales", "casual_labour", "subcontractors"];

/** The invoices (money in) or supplier bills (money out) a line could settle, closest amount first. */
export const fetchCandidates = (tenantId, txnId) =>
  fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/transactions/${txnId}/candidates`)
    .then(r => r.json()).then(d => d.candidates || []).catch(() => []);

export function useBank(tenantId) {
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
  // Re-file a whole category at once (DIGG, 6 Oct: 157 lines booked as "casual labour" that were
  // really materials and subcontractors): filter by category, then group by payee and apply one
  // category / project / trade to the group. Vula learns each choice for the next statement.
  const [cat, setCat] = useState("");
  const [grouped, setGrouped] = useState(false);
  const [groups, setGroups] = useState([]);
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

  // Returns the fresh summary, so the statement-upload poll can watch it settle.
  const load = useCallback(async () => {
    const [s, t, inv, ord, acc, wk] = await Promise.all([
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/reconciliation`).then(r => r.json()).catch(() => null),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/transactions?limit=500${noProject ? "&status=no_project" : filter ? `&status=${filter}` : ""}${cat ? `&account_code=${encodeURIComponent(cat)}` : ""}`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices?status=sent`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/orders?status=pending_payment`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/accounts`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/workers`).then(r => r.json()).catch(() => ({})),
    ]);
    setSum(s); setTxns(t.transactions || []); setInvoices(inv.invoices || []);
    setPendingOrders(ord.orders || []);
    setAccounts(acc.accounts || []); setVatReg(!!acc.vat_registered); setWorkers(wk.workers || []);
    if (grouped) {
      const g = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/transactions/groups?direction=out${cat ? `&account_code=${encodeURIComponent(cat)}` : ""}`)
        .then(r => r.json()).catch(() => ({}));
      setGroups(g.groups || []);
    }
    return s;
  }, [tenantId, filter, noProject, cat, grouped]);

  // The project register (with phases) for the project box — not just names already on a line.
  const [registered, setRegistered] = useState([]);
  useEffect(() => {
    if (!tenantId || !hasProjects) return;
    fetch(`${VULA_API}/v1/projects/${tenantId}`).then(r => r.json())
      .then(d => setRegistered((d.projects || d || []).map(p => p.name).filter(Boolean)))
      .catch(() => setRegistered([]));
  }, [tenantId, hasProjects]);

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 4000); };

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
  // One category (and optionally project + trade) for every line in a payee group.
  const applyGroup = async (g, form) => {
    if (!form.account_code) return flash("Pick a category for the group first.");
    setBusy(true);
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/transactions/bulk-categorize`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ txn_ids: g.txn_ids, account_code: form.account_code,
        project: form.project || null, trade: form.trade || null }),
    }).then(r => r.json()).catch(() => ({ detail: "network" }));
    setBusy(false);
    if (r.detail || r.error) return flash(String(r.detail || r.error));
    flash(`Updated ${r.updated} line${r.updated === 1 ? "" : "s"} — Vula will file the next ones the same way.`);
    load();
  };
  const projectNames = [...new Set([...registered, ...txns.map(t => t.project).filter(Boolean)])].sort();

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
    // (The count comes from each fresh load; it used to read the summary from before the upload,
    // so the poll never saw it settle and always ran the full 10 minutes.)
    flash("Reconciling… this takes a few minutes. The numbers below update as it works.");
    let last = -1, stable = 0;
    for (let i = 0; i < 40 && stable < 3; i++) {
      await new Promise(res => setTimeout(res, 15000));
      const s = await load();
      const n = (s && s.txn_count) || 0;
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
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/bank/transactions/${id}/match`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, invoice_id, order_id }),
    }).then(r => r.json()).catch(() => ({ detail: "network" }));
    if (r && (r.detail || r.error)) flash(String(r.detail || r.error));
    else if (r && r.invoice_status) flash(r.invoice_status === "paid"
      ? `Matched — ${R(r.paid_cents)} paid, invoice settled.`
      : `Matched — ${R(r.paid_cents)} paid, ${R(r.balance_due_cents)} still owed.`);
    load();
  };

  // "Not on a project": materials and labour paid but not put on a project yet.
  const shown = txns.filter(t => !noProject || (t.direction === "out" && !t.project && PROJECT_ACCOUNTS.includes(t.account_code)));
  const chooseFilter = (v) => { setFilter(v); setNoProject(false); };
  const chooseNoProject = () => { setFilter(""); setNoProject(true); };

  return {
    sum, txns, shown, invoices, pendingOrders, accounts, workers, vatReg, filter, chooseFilter, noProject, chooseNoProject,
    pw, setPw, busy, msg, cat, setCat, grouped, setGrouped, groups, hasProjects, projectNames, sheet, setSheet,
    categorize, assignWorker, workerName, allocate, applyGroup, pickSheet, importSheet, saveSettings, upload, recategorize,
    reviewOnWhatsApp, act,
  };
}
