/**
 * useSignupsHQ.js — Master HQ's view of client signups (shared by VulaAdmin "Signups",
 * VulaSubscriptions and their redesigns): the signups list from /v1/admin/signups, and the
 * trial / plan helpers both screens use. Moved verbatim out of VulaAdmin.jsx and
 * VulaSubscriptions.jsx; the plan prices are list prices in rand for an estimated MRR.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../../lib/authFetch";

export const PLAN_PRICE = { starter: 1500, growth: 3500, business: 7500 };

export function daysUntil(isoDate) {
  if (!isoDate) return null;
  return Math.ceil((new Date(isoDate).getTime() - Date.now()) / 86400000);
}

/** Where a signup's subscription stands: paid, expired, or days left — priority sorts by urgency. */
export function subStateOf(t) {
  if (t.paid) return { label: "Paid", tone: "ok", priority: 0 };
  const d = daysUntil(t.trial_ends);
  if (d === null) return { label: "Unknown", tone: "muted", priority: 4 };
  if (d < 0) return { label: "Expired", tone: "danger", priority: 1 };
  if (d <= 3) return { label: `${d}d left`, tone: "danger", priority: 2 };
  if (d <= 7) return { label: `${d}d left`, tone: "warn", priority: 3 };
  return { label: `${d}d trial`, tone: "muted", priority: 5 };
}

/** VulaAdmin's list: the latest 50 signups, a filter, and its counts. */
export function useSignups() {
  const [signups, setSignups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("all");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(`${VULA_API}/v1/admin/signups?limit=50`);
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      const data = await resp.json();
      setSignups(data.signups || []);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = filter === "all" ? signups : signups.filter(s => s.plan === filter || s.status === filter);

  // Stats
  const active = signups.filter(s => s.status === "active").length;
  const mrr = signups.filter(s => s.status === "active").reduce((sum, s) => {
    const prices = { starter: 1500, growth: 3500, business: 7500 };
    return sum + (prices[s.plan] || 0);
  }, 0);
  const byPlan = signups.reduce((acc, s) => { acc[s.plan] = (acc[s.plan] || 0) + 1; return acc; }, {});
  return { signups, loading, error, filter, setFilter, load, filtered, active, mrr, byPlan };
}

/** VulaSubscriptions' view: the latest 100, paid MRR, and who's expiring. */
export function useSubscriptions() {
  const [tenants, setTenants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [view, setView] = useState("urgency"); // urgency | all

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(`${VULA_API}/v1/admin/signups?limit=100`);
      if (!resp.ok) throw new Error(`${resp.status}`);
      const data = await resp.json();
      setTenants(data.signups || []);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  // Stats
  const paid = tenants.filter((t) => t.paid);
  const mrr = paid.reduce((s, t) => s + (PLAN_PRICE[t.plan] || 0), 0);
  const expiredCount = tenants.filter((t) => !t.paid && daysUntil(t.trial_ends) !== null && daysUntil(t.trial_ends) < 0).length;
  const soonCount = tenants.filter((t) => {
    const d = daysUntil(t.trial_ends);
    return !t.paid && d !== null && d >= 0 && d <= 7;
  }).length;

  // Sort by urgency
  const sorted = [...tenants].sort((a, b) => subStateOf(a).priority - subStateOf(b).priority);
  const sections = [
    { title: "Expired — needs reactivation", tone: "danger", filter: (t) => !t.paid && (daysUntil(t.trial_ends) ?? 1) < 0 },
    { title: "Expiring in 3 days", tone: "danger", filter: (t) => { const d = daysUntil(t.trial_ends); return !t.paid && d !== null && d >= 0 && d <= 3; } },
    { title: "Expiring this week", tone: "warn", filter: (t) => { const d = daysUntil(t.trial_ends); return !t.paid && d !== null && d > 3 && d <= 7; } },
    { title: "Active paid subscribers", tone: "ok", filter: (t) => t.paid },
    { title: "On trial — healthy", tone: "muted", filter: (t) => { const d = daysUntil(t.trial_ends); return !t.paid && d !== null && d > 7; } },
  ];
  return { tenants, loading, error, selected, setSelected, view, setView, load, paid, mrr, expiredCount, soonCount, sorted, sections };
}
