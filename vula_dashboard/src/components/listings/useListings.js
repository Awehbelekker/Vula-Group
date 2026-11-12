/**
 * useListings.js — data and actions for the real-estate Listings screen (2026-11-10).
 * Backend: /v1/commerce/{tenant}/admin/properties | property-agents | property-enquiries
 * (vula_mind/vula/api/listings.py, migration 257). Prices go to the server as rands text
 * ("R2.4m", "2 450 000") and come back as integer cents — the browser never does the maths.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";

const base = (t) => `${VULA_API}/v1/commerce/${t}/admin`;

async function call(url, opts = {}) {
  try {
    const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
    const d = await r.json().catch(() => ({}));
    return r.ok ? d : { error: d.detail || "Something went wrong — please try again." };
  } catch {
    return { error: "Couldn't reach Vula — check your connection." };
  }
}

export const STATUS = {
  draft: { label: "Draft", tone: "muted" }, active: { label: "On the market", tone: "info" },
  under_offer: { label: "Under offer", tone: "highlight" }, sold: { label: "Sold", tone: "ok" },
  let: { label: "Let", tone: "ok" }, archived: { label: "Archived", tone: "muted" },
};
export const ENQUIRY_STATUS = {
  new: { label: "New", tone: "highlight" }, contacted: { label: "Contacted", tone: "info" },
  viewing: { label: "Viewing", tone: "info" }, closed: { label: "Closed", tone: "muted" },
};
export const PROPERTY_TYPES = ["house", "apartment", "townhouse", "land", "commercial", "farm", "other"];

export function useListings(tenantId) {
  const [view, setView] = useState("listings");     // listings | enquiries | agents
  const [listings, setListings] = useState(null);
  const [enquiries, setEnquiries] = useState(null);
  const [agents, setAgents] = useState(null);
  const [msg, setMsg] = useState("");
  const [modules, setModules] = useState([]);     // "Add to pipeline" only with the pipeline module
  useEffect(() => {
    fetch(`${VULA_API}/v1/tenants/${tenantId}`).then((r) => r.json()).then((t) => setModules(t.modules || [])).catch(() => {});
  }, [tenantId]);

  const load = useCallback(async () => {
    const [l, e, a] = await Promise.all([
      call(`${base(tenantId)}/properties`), call(`${base(tenantId)}/property-enquiries`),
      call(`${base(tenantId)}/property-agents`),
    ]);
    setListings(l.properties || []);
    setEnquiries(e.enquiries || []);
    setAgents(a.agents || []);
    if (l.error || e.error || a.error) setMsg(l.error || e.error || a.error);
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const saveListing = async (body) => {
    const r = await call(`${base(tenantId)}/properties`, { method: "POST", body: JSON.stringify(body) });
    if (!r.error) await load();
    return r;
  };
  const deleteListing = async (id) => {
    const r = await call(`${base(tenantId)}/properties/${id}`, { method: "DELETE" });
    if (!r.error) await load();
    return r;
  };
  const saveAgent = async (body) => {
    const r = await call(`${base(tenantId)}/property-agents`, { method: "POST", body: JSON.stringify(body) });
    if (!r.error) await load();
    return r;
  };
  const deleteAgent = async (id) => {
    const r = await call(`${base(tenantId)}/property-agents/${id}`, { method: "DELETE" });
    if (!r.error) await load();
    return r;
  };
  const setEnquiryStatus = async (id, status) => {
    const r = await call(`${base(tenantId)}/property-enquiries/${id}/status`, { method: "POST", body: JSON.stringify({ status }) });
    if (!r.error) setEnquiries((rows) => rows.map((e) => (e.id === id ? r.enquiry : e)));
    return r;
  };

  const toPipeline = async (enquiryId) => {
    const { dealFromEnquiry } = await import("../pipeline/usePipeline");
    const r = await dealFromEnquiry(tenantId, enquiryId);
    if (!r.error) await load();          // the enquiry moves to Contacted
    return r;
  };

  return { view, setView, listings, enquiries, agents, msg, setMsg, load, modules, toPipeline, saveListing, deleteListing,
           saveAgent, deleteAgent, setEnquiryStatus };
}
