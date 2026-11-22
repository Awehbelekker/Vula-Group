/**
 * useTradeCosting.js — the data behind Estimating › Costing by trade (15 Nov 2026; trade costing,
 * vula/api/trade_costing.py). Plain fetch (the installed auth patch adds the login): a 403 from the
 * build-ups list means the business doesn't have the switch on, which the screen shows as such.
 * Every figure is the server's — this file never adds, multiplies or rounds money.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";

const base = (tenantId) => `${VULA_API}/v1/commerce/${tenantId}/admin`;

async function call(url, opts) {
  const r = await fetch(url, opts).catch(() => null);
  if (!r) return { ok: false, status: 0, data: {} };
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, data };
}

const post = (url, body) => call(url, { method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}) });

/** The server's reason, as it wrote it, or a plain fallback. */
export const reason = (res, fallback) =>
  (typeof res?.data?.detail === "string" && res.data.detail) || fallback;

export function useTradeCosting(tenantId) {
  const [buildups, setBuildups] = useState(null);
  const [canConfirm, setCanConfirm] = useState(false);
  const [state, setState] = useState("loading");     // loading | ready | off | failed
  const [estimates, setEstimates] = useState([]);
  const [projects, setProjects] = useState([]);
  const [suggestions, setSuggestions] = useState([]);

  const reload = useCallback(async () => {
    if (!tenantId) return;
    setState((s) => (s === "ready" ? s : "loading"));
    const res = await call(`${base(tenantId)}/buildups`);
    if (res.status === 403) { setState("off"); return; }
    if (!res.ok) { setState("failed"); return; }
    setBuildups(res.data.buildups || []);
    setCanConfirm(!!res.data.can_confirm);
    setState("ready");
    const [est, sug, proj] = await Promise.all([
      call(`${base(tenantId)}/estimates`), call(`${base(tenantId)}/buildup-suggestions`),
      call(`${VULA_API}/v1/projects/${tenantId}`)]);
    if (est.ok) setEstimates(est.data.estimates || []);
    if (sug.ok) setSuggestions(sug.data.suggestions || []);
    if (proj.ok) setProjects((proj.data.projects || []).filter((p) => p.status !== "complete"));
  }, [tenantId]);

  useEffect(() => { reload(); }, [reload]);

  const research = (body) => post(`${base(tenantId)}/buildups/research`, body);
  const confirm = (id, changes) => post(`${base(tenantId)}/buildups/${id}/confirm`, { changes: changes || [] });
  const retire = (id) => post(`${base(tenantId)}/buildups/${id}/retire`);
  const estimate = (body) => post(`${base(tenantId)}/estimates`, body);
  // phase 4: the estimate beside what was paid on its project, and what Vula suggests from it
  const actuals = (id) => call(`${base(tenantId)}/estimates/${id}/actuals`);
  const learn = (id) => post(`${base(tenantId)}/estimates/${id}/learn`);
  const decide = (id, decision) => post(`${base(tenantId)}/buildup-suggestions/${id}/${decision}`);

  /** Save an estimate's Excel breakdown. Resolves to "ok", "forbidden" or "failed". */
  async function download(id) {
    const r = await fetch(`${base(tenantId)}/estimates/${id}.xlsx`).catch(() => null);
    if (!r || !r.ok) return r && r.status === 403 ? "forbidden" : "failed";
    const cd = r.headers.get("content-disposition") || "";
    const name = (cd.match(/filename="?([^";]+)"?/i) || [])[1] || "Estimate.xlsx";
    const url = URL.createObjectURL(await r.blob());
    Object.assign(document.createElement("a"), { href: url, download: name }).click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return "ok";
  }

  return { buildups, canConfirm, state, estimates, projects, suggestions, reload, research, confirm, retire,
    estimate, download, actuals, learn, decide };
}
