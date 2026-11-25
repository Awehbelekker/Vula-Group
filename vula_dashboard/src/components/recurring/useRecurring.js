/**
 * useRecurring.js — customer standing orders ("bread every Friday", migration 051): list, new,
 * edit, pause / resume / cancel, and "run due now" (2026-10-19, UX redesign Phase F). Shared by
 * VulaRecurringOrders.jsx and recurring/RecurringV2.jsx; same /v1/subscriptions/{tenant} calls.
 */
import { useCallback, useEffect, useState } from "react";
import { tryApi } from "../../lib/api";
import { toast } from "../ui/index.jsx";

export const CAD = { weekly: "Weekly", biweekly: "Every 2 weeks", monthly: "Monthly" };
export const PAY = { cod: "Pay on delivery", eft: "EFT", online: "Card / online" };
export const itemsTotal = (items) => (items || []).reduce((s, i) => s + Math.round((i.quantity || 0) * (i.unit_price_cents || 0)), 0);

export function useRecurring(tenantId) {
  const [subs, setSubs] = useState([]);
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const [s, p] = await Promise.all([
      tryApi(`/v1/subscriptions/${tenantId}`),
      tryApi(`/v1/commerce/${tenantId}/admin/products`),
    ]);
    if (s.ok) setSubs(s.data.subscriptions || []);
    if (p.ok) setProducts(p.data.products || []);
    if (!s.ok) toast(`Couldn't load your standing orders: ${s.error}`, "danger");
    else if (!p.ok) toast(`Couldn't load your products: ${p.error}`, "danger");
    setLoading(false);
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 3000); };

  const setStatus = async (id, status) => {
    const res = await tryApi(`/v1/subscriptions/${tenantId}/${id}/status`, {
      method: "POST", body: { status },
    });
    if (!res.ok) toast(`That standing order wasn't changed: ${res.error}`, "danger");
    load();
  };

  const runDue = async () => {
    const res = await tryApi(`/v1/subscriptions/${tenantId}/jobs/run`, { method: "POST" });
    if (!res.ok) { toast(`The due orders weren't created: ${res.error}`, "danger"); return; }
    flash(`Created ${res.data.created ?? 0} due order(s)`);
    load();
  };

  return { subs, products, loading, showNew, setShowNew, editingId, setEditingId, msg, flash, load, setStatus, runDue };
}

/** The new / edit form's state and save (one per open form). `sub` is the row being edited. */
export function useSubForm({ tenantId, products, sub, flash, onDone }) {
  const editing = !!sub;
  const [f, setF] = useState(editing
    ? { customer_name: sub.customer_name || "", customer_phone: sub.customer_phone || "", cadence: sub.cadence || "weekly", payment_method: sub.payment_method || "cod" }
    : { customer_name: "", customer_phone: "", cadence: "weekly", payment_method: "cod" });
  const [lines, setLines] = useState(editing && sub.items?.length
    ? sub.items.map(i => ({ product_id: i.product_id, quantity: i.quantity }))
    : [{ product_id: "", quantity: 1 }]);
  const [saving, setSaving] = useState(false);

  const setLine = (i, patch) => setLines(ls => ls.map((l, j) => j === i ? { ...l, ...patch } : l));
  const addLine = () => setLines(ls => [...ls, { product_id: "", quantity: 1 }]);
  const removeLine = (i) => setLines(ls => ls.filter((_, j) => j !== i));

  const save = async () => {
    const items = lines.filter(l => l.product_id).map(l => {
      const p = products.find(p => p.id === l.product_id);
      return { product_id: l.product_id, product_name: p?.name || "", quantity: +l.quantity || 1, unit_price_cents: p?.price_cents || 0 };
    });
    if (!items.length) return flash("Add at least one product");
    if (!editing && !f.customer_phone.trim()) return flash("Customer phone is required");
    setSaving(true);
    const url = editing ? `/v1/subscriptions/${tenantId}/${sub.id}` : `/v1/subscriptions/${tenantId}`;
    const res = await tryApi(url, {
      method: editing ? "PATCH" : "POST",
      body: editing ? { items, cadence: f.cadence, payment_method: f.payment_method, customer_name: f.customer_name } : { ...f, items },
    });
    setSaving(false);
    if (!res.ok) return flash(res.error);
    onDone();
  };

  return { editing, f, setF, lines, setLine, addLine, removeLine, saving, save };
}
