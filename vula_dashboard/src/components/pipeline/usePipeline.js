/**
 * usePipeline.js — data and actions for the deals Pipeline board (2026-11-10, `pipeline` module).
 * Backend: /v1/commerce/{tenant}/admin/deals (vula_mind/vula/api/pipeline.py, migration 258).
 * Stages come from the server (they depend on the business type); the board's totals are
 * computed server-side from integer cents. Moves are optimistic and roll back if the server
 * doesn't confirm them.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";

const base = (t) => `${VULA_API}/v1/commerce/${t}/admin`;

export async function call(url, opts = {}) {
  try {
    const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
    const d = await r.json().catch(() => ({}));
    return r.ok ? d : { error: d.detail || "Something went wrong — please try again." };
  } catch {
    return { error: "Couldn't reach Vula — check your connection." };
  }
}

/** Turn an enquiry (Listings › Enquiries) into a pipeline deal. */
export function dealFromEnquiry(tenantId, enquiryId) {
  return call(`${base(tenantId)}/deals/from-enquiry/${enquiryId}`, { method: "POST" });
}

export function usePipeline(tenantId) {
  const [board, setBoard] = useState(null);      // {stages, deals, summary, listings}
  const [error, setError] = useState("");
  const [properties, setProperties] = useState([]);   // for linking a deal to a listing

  const load = useCallback(async () => {
    const d = await call(`${base(tenantId)}/deals`);
    if (d.error) { setError(d.error); setBoard((b) => b || { stages: [], deals: [], summary: null, listings: {} }); return; }
    setError("");
    setBoard(d);
  }, [tenantId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    // Only a business with listings has properties to link; any other gets an empty list.
    fetch(`${VULA_API}/v1/tenants/${tenantId}`).then((r) => r.json()).then((t) => {
      if (!(t.modules || []).includes("listings")) return;
      call(`${base(tenantId)}/properties`).then((p) => setProperties(p.properties || []));
    }).catch(() => {});
  }, [tenantId]);

  const save = async (body) => {
    const r = await call(`${base(tenantId)}/deals`, { method: "POST", body: JSON.stringify(body) });
    if (!r.error) await load();
    return r;
  };
  const remove = async (id) => {
    const r = await call(`${base(tenantId)}/deals/${id}`, { method: "DELETE" });
    if (!r.error) await load();
    return r;
  };
  const move = async (id, stage, position) => {
    const before = board;
    setBoard((b) => b && { ...b, deals: b.deals.map((d) => (d.id === id ? { ...d, stage, position: position ?? d.position } : d)) });
    const r = await call(`${base(tenantId)}/deals/${id}/move`, { method: "POST", body: JSON.stringify({ stage, position }) });
    if (r.error) { setBoard(before); return r; }
    await load();      // fresh totals + closed dates from the server
    return r;
  };

  return { board, error, properties, load, save, remove, move };
}
