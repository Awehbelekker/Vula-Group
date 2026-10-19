/**
 * useExpenses.js — the Expenses screen's data and actions (2026-10-18, UX redesign Phase F).
 * Shared by the current screen (VulaExpenses.jsx) and expenses/ExpensesV2.jsx: same endpoints,
 * same filters, same writes. Moved out of VulaExpenses.jsx unchanged.
 */
import { useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";

export const api = (t, p, opts) => fetch(`${VULA_API}/v1/commerce/${t}/admin${p}`, opts).then(r => r.json());

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
  const [cards, setCards] = useState([]);
  const [newCard, setNewCard] = useState("");

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 4000); };

  const load = async () => {
    setBusy(true);
    const params = new URLSearchParams();
    if (filter === "owed") params.set("reimbursable", "true");
    else if (filter !== "all") params.set("status", filter);
    if (defaultPaidBy) params.set("paid_by", defaultPaidBy);
    const q = params.toString() ? `?${params}` : "";
    const [d, r, c] = await Promise.all([
      api(tenantId, `/expenses${q}`).catch(() => ({ expenses: [] })),
      api(tenantId, `/reports/expenses`).catch(() => null),
      api(tenantId, `/cards`).catch(() => ({ cards: [] })),
    ]);
    setRows(d.expenses || []);
    setProjects(d.projects || []);
    setCategories(d.categories || []);
    setSections(d.sections || []);
    setPurposeCategories(d.purpose_categories || []);
    setRep(r);
    setCards(c.cards || []);
    setBusy(false);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [tenantId, filter]);

  const addCard = async () => {
    const last4 = (newCard || "").replace(/\D/g, "").slice(-4);
    if (last4.length !== 4) return flash("Enter the card's last 4 digits.");
    await api(tenantId, `/cards`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ last4, label: "Company card" }),
    });
    setNewCard(""); flash("Company card saved — receipts on it are matched to the bank statement."); load();
  };
  const dropCard = async (id) => { await api(tenantId, `/cards/${id}`, { method: "DELETE" }); load(); };

  const setStatus = async (id, status) => {
    await api(tenantId, `/expenses/${id}/status`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
    });
    flash("Updated."); load();
  };
  const assign = async (id, patch) => {
    await api(tenantId, `/expenses/${id}/assign`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
    });
    flash("Allocated + learned."); load();
  };

  return {
    rows, projects, categories, sections, purposeCategories, rep, filter, setFilter, busy, msg,
    cards, newCard, setNewCard, load, addCard, dropCard, setStatus, assign,
    owed: rep?.reimbursable_owed_cents || 0,
  };
}
