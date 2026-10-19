/**
 * useFinances.js — Money › Finances' data and actions (2026-10-19, UX redesign Phase F): the
 * insights snapshot, the sales report and the project ledger. Shared by VulaFinanceInsights.jsx,
 * VulaReports.jsx, VulaFinances.jsx and finances/FinancesV2.jsx; moved out unchanged — same
 * endpoints and bodies. The server computes every figure; the screens only format them.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";
import { toast } from "../ui/index.jsx";

export const INSIGHT_PERIODS = [{ d: 7, l: "7 days" }, { d: 30, l: "30 days" }, { d: 90, l: "90 days" }];
export const RISK_LABELS = { high_risk: "High risk", frequently_late: "Often late" };
export const REPORT_DAYS = [7, 14, 30, 90];

/** The P&L snapshot (revenue, expenses, profit, VAT, receivables, payers) and "Explain my numbers". */
export function useFinanceInsights(tenantId) {
  const [days, setDays] = useState(30);
  const [d, setD] = useState(null);
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState("");
  const [asking, setAsking] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setSummary("");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/finances/insights?days=${days}`)
      .then(r => r.json()).catch(() => null);
    setD(r); setLoading(false);
  }, [tenantId, days]);

  useEffect(() => { load(); }, [load]);

  const askVula = async () => {
    setAsking(true);
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/finances/insights?days=${days}&explain=true`)
      .then(r => r.json()).catch(() => null);
    setSummary(r?.summary || "Couldn't generate a summary right now.");
    setAsking(false);
  };

  return { days, setDays, d, loading, summary, asking, askVula };
}

/** Sales over the last N days: totals, the daily revenue trend and the top products. */
export function useSalesReport(tenantId) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (d) => {
    if (!tenantId) return;
    setLoading(true); setError("");
    try {
      const res = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports?days=${d}`);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      setData(await res.json());
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [tenantId]);

  useEffect(() => { load(days); }, [load, days]);

  const avgCents = data?.total_orders > 0 ? Math.round(data.total_revenue_cents / data.total_orders) : 0;
  return { days, setDays, data, loading, error, avgCents };
}

/** Money in/out per project, budgets, contract values, progress claims and the trial balance. */
export function useLedger(tenantId) {
  const [data, setData] = useState({ projects: [], transactions: [], total_in: 0, total_out: 0 });
  const [editing, setEditing] = useState(null);
  const [budget, setBudget] = useState("");
  const [openProj, setOpenProj] = useState(null);
  const [detail, setDetail] = useState(null);
  const [tb, setTb] = useState(null);
  const [tbOpen, setTbOpen] = useState(false);

  const loadTrialBalance = useCallback(async () => {
    if (!tenantId) return;
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports/trial-balance`);
    setTb(await r.json());
  }, [tenantId]);

  const load = useCallback(async () => {
    if (!tenantId) return;
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}/finances`);
    setData(await r.json());
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const [contractDraft, setContractDraft] = useState("");
  const [claims, setClaims] = useState([]);
  const [newClaim, setNewClaim] = useState({ value: "", retention: "5" });
  const [claimBusy, setClaimBusy] = useState(false);
  const [invoicing, setInvoicing] = useState(null); // { claimId, name, phone, email, address }

  const loadClaims = async (project) => {
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/claims`).then(r => r.json()).catch(() => null);
    setClaims(r?.claims || []);
  };

  const openDetail = async (project) => {
    if (openProj === project) { setOpenProj(null); return; }
    setOpenProj(project); setDetail(null); setContractDraft(""); setClaims([]); setInvoicing(null);
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/financials`).then(r => r.json()).catch(() => null);
    setDetail(r);
    loadClaims(project);
  };

  const refreshProject = async (project) => {
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/financials`).then(r => r.json()).catch(() => null);
    setDetail(r);
    loadClaims(project);
  };

  const addClaim = async (project) => {
    const value = Number(newClaim.value);
    if (!value) return;
    setClaimBusy(true);
    const d = await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/claims`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cumulative_value: value, retention_pct: Number(newClaim.retention) || 5 }),
    }).then(r => r.json()).catch(() => ({}));
    setClaimBusy(false);
    if (d.claim) { setNewClaim({ value: "", retention: "5" }); refreshProject(project); }
    else toast(d.detail || d.error || "Could not create claim.");
  };

  const certifyClaim = async (project, claimId) => {
    await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/claims/${claimId}/certify`, { method: "POST" });
    refreshProject(project);
  };

  const submitInvoice = async (project) => {
    if (!invoicing?.name) return;
    setClaimBusy(true);
    const d = await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/claims/${invoicing.claimId}/invoice`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        customer_name: invoicing.name, customer_phone: invoicing.phone || undefined,
        customer_email: invoicing.email || undefined, customer_address: invoicing.address || undefined,
      }),
    }).then(r => r.json()).catch(() => ({}));
    setClaimBusy(false);
    if (d.invoice) { toast(`Invoice ${d.invoice.invoice_number || ""} created for this claim.`); setInvoicing(null); refreshProject(project); }
    else toast(d.detail || "Could not create invoice.");
  };

  const saveContract = async (project) => {
    const total = Number(contractDraft);
    if (!total) return;
    await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/boq`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ total }),
    });
    setContractDraft("");
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/financials`).then(r => r.json()).catch(() => null);
    setDetail(r); load();
  };

  const saveBudget = async (project) => {
    await fetch(`${VULA_API}/v1/projects/${tenantId}/budget`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, budget: Number(budget) || 0 }),
    });
    setEditing(null); setBudget(""); load();
  };
  const startBudget = (p) => { setEditing(p.project); setBudget(p.budget || ""); };
  const toggleTrialBalance = () => { setTbOpen(!tbOpen); if (!tb) loadTrialBalance(); };

  return {
    data, editing, setEditing, budget, setBudget, startBudget, openProj, detail, tb, tbOpen, toggleTrialBalance,
    contractDraft, setContractDraft, claims, newClaim, setNewClaim, claimBusy, invoicing, setInvoicing,
    openDetail, addClaim, certifyClaim, submitInvoice, saveContract, saveBudget,
  };
}
