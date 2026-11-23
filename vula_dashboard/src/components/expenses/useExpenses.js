/**
 * useExpenses.js — the Expenses screen's data and actions (2026-10-18, UX redesign Phase F).
 * Shared by the current screen (VulaExpenses.jsx) and expenses/ExpensesV2.jsx: same endpoints,
 * same filters, same writes. Moved out of VulaExpenses.jsx unchanged.
 */
import { useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";
import { ApiError, errorText } from "../../lib/api";
import { toast } from "../ui/index.jsx";

/** Same request as before; a refusal (non-2xx, or a body with `error`) now throws an ApiError
 * with the server's own words instead of passing for success (23 Nov 2026). */
export const api = async (t, p, opts) => {
  let r;
  try { r = await fetch(`${VULA_API}/v1/commerce/${t}/admin${p}`, opts); }
  catch { throw new ApiError("Couldn't reach Vula. Check your connection and try again.", 0); }
  const d = await r.json().catch(() => null);
  if (!r.ok || (d && typeof d === "object" && !Array.isArray(d) && d.error && d.ok !== true)) {
    throw new ApiError(r.status === 403 && !(d && (d.detail || d.error)) ? "Your login doesn't include this." : errorText(d), r.status, d);
  }
  return d ?? {};
};

export const EXPENSE_FILTERS = ["all", "submitted", "approved", "owed", "reimbursed"];

/** POST a manual expense from the add form's fields (amount in rand, as typed). */
export function addExpense(tenantId, f) {
  return api(tenantId, "/expenses", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      amount_cents: Math.round(parseFloat(f.amount) * 100), description: f.description,
      supplier: f.supplier || null, date: f.date || null, project: f.project || null,
      section: f.section || null, paid_by_name: f.paid_by_name || null, reimbursable: f.reimbursable,
    }),
  });
}

export function useExpenses(tenantId, defaultPaidBy) {
  const [rows, setRows] = useState([]);
  const [projects, setProjects] = useState([]);
  const [categories, setCategories] = useState([]);
  const [sections, setSections] = useState([]);
  const [purposeCategories, setPurposeCategories] = useState([]);
  const [rep, setRep] = useState(null);
  const [filter, setFilter] = useState("all");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [loadError, setLoadError] = useState("");
  const [cards, setCards] = useState([]);
  const [newCard, setNewCard] = useState("");

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 4000); };

  const load = async () => {
    setBusy(true);
    try {
      const params = new URLSearchParams();
      if (filter === "owed") params.set("reimbursable", "true");
      else if (filter !== "all") params.set("status", filter);
      if (defaultPaidBy) params.set("paid_by", defaultPaidBy);
      const q = params.toString() ? `?${params}` : "";
      let failed = "";
      const miss = (fallback) => (e) => { failed = failed || e.message; return fallback; };
      const [d, r, c] = await Promise.all([
        api(tenantId, `/expenses${q}`).catch(miss({ expenses: [] })),
        api(tenantId, `/reports/expenses`).catch(miss(null)),
        api(tenantId, `/cards`).catch(miss({ cards: [] })),
      ]);
      setRows(d.expenses || []);
      setProjects(d.projects || []);
      setCategories(d.categories || []);
      setSections(d.sections || []);
      setPurposeCategories(d.purpose_categories || []);
      setRep(r);
      setCards(c.cards || []);
      setLoadError(failed);
    } finally { setBusy(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [tenantId, filter]);

  const addCard = async () => {
    const last4 = (newCard || "").replace(/\D/g, "").slice(-4);
    if (last4.length !== 4) return flash("Enter the card's last 4 digits.");
    try {
      await api(tenantId, `/cards`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ last4, label: "Company card" }),
      });
    } catch (e) { return toast(`Couldn't save the card: ${e.message}`, "danger"); }
    setNewCard(""); flash("Company card saved — receipts on it are matched to the bank statement."); load();
  };
  const dropCard = async (id) => {
    try { await api(tenantId, `/cards/${id}`, { method: "DELETE" }); }
    catch (e) { toast(`Couldn't remove the card: ${e.message}`, "danger"); }
    load();
  };

  const setStatus = async (id, status) => {
    try {
      await api(tenantId, `/expenses/${id}/status`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
      });
      flash("Updated.");
    } catch (e) { toast(`Couldn't update this expense: ${e.message}`, "danger"); }
    load();
  };
  const assign = async (id, patch) => {
    try {
      await api(tenantId, `/expenses/${id}/assign`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
      });
      flash("Allocated + learned.");
    } catch (e) { toast(`Couldn't save this change: ${e.message}`, "danger"); }
    load();
  };

  return {
    rows, projects, categories, sections, purposeCategories, rep, filter, setFilter, busy, msg, loadError,
    cards, newCard, setNewCard, load, addCard, dropCard, setStatus, assign,
    owed: rep?.reimbursable_owed_cents || 0,
  };
}
