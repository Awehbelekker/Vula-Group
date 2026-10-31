/**
 * useTakeoff.js — Vula Takeoff (shared by VulaTakeoff and TakeoffV2): upload plans, poll the
 * takeoff job, the BOQ with per-line rate overrides and markup, totals by trade, the Excel
 * download and the live SA rates table. The demo data
 * shows until a real job loads.
 */
import { useState, useRef, useCallback, useMemo, useEffect } from "react";
import { VULA_API } from "../../lib/authFetch";
import { toast } from "../ui/index.jsx";

export const TRADE_LABELS = {
  demolition: "Demolition",
  concrete: "Concrete & Structure",
  brickwork: "Brickwork & Masonry",
  drywall: "Drywall & Ceilings",
  flooring: "Flooring & Screed",
  joinery: "Joinery & Doors",
  glazing: "Glazing & Shopfront",
  wet_works: "Wet Works & Waterproofing",
  plumbing: "Plumbing & Drainage",
  electrical: "Electrical",
  hvac: "HVAC & Ventilation",
  fire: "Fire Protection",
  tiling: "Tiling",
  painting: "Painting & Finishes",
  external: "External Works",
};

// Demo data — shown when no real job is loaded
export const MOCK_TAKEOFF = {
  projectName: "Sporty.TV Fitout — Phase 2 (DEMO)",
  drawings: [
    { sheet: "A001", title: "Site Plan", scale: "1:200", status: "read" },
    { sheet: "A100", title: "Floor Plan — Level 1", scale: "1:100", status: "read" },
    { sheet: "A101", title: "Floor Plan — Mezzanine", scale: "1:100", status: "read" },
    { sheet: "A200", title: "Elevations", scale: "1:50", status: "read" },
    { sheet: "A300", title: "Sections", scale: "1:50", status: "read" },
    { sheet: "A500", title: "Door & Window Schedule", scale: "N/A", status: "read" },
    { sheet: "M100", title: "HVAC Layout", scale: "1:100", status: "read" },
    { sheet: "E100", title: "Electrical Layout", scale: "1:100", status: "read" },
    { sheet: "P100", title: "Plumbing Layout", scale: "1:100", status: "read" },
  ],
  areas: { gross: 597, net: 528, mezzanine: 84 },
  rooms: [
    { name: "Open Plan Office", area: 210, floor: "lvt", ceiling: "acoustic_tile", walls: "painted_plaster" },
    { name: "Commentators Studio", area: 48, floor: "epoxy", ceiling: "acoustic_treated", walls: "acoustic_panel" },
    { name: "Server Room", area: 18, floor: "raised_access", ceiling: "standard", walls: "painted_plaster" },
    { name: "Boardroom", area: 36, floor: "carpet", ceiling: "acoustic_tile", walls: "painted_plaster" },
    { name: "Reception", area: 32, floor: "porcelain_tile", ceiling: "standard", walls: "feature_wall" },
    { name: "Ablutions (Male)", area: 24, floor: "porcelain_tile", ceiling: "standard", walls: "porcelain_tile" },
    { name: "Ablutions (Female)", area: 20, floor: "porcelain_tile", ceiling: "standard", walls: "porcelain_tile" },
    { name: "Kitchen / Breakroom", area: 28, floor: "vinyl_lvt", ceiling: "standard", walls: "painted_plaster" },
    { name: "Mezzanine Office", area: 84, floor: "carpet", ceiling: "exposed", walls: "painted_plaster" },
    { name: "Green Screen Studio", area: 64, floor: "studio_floor", ceiling: "acoustic_treated", walls: "cyclorama" },
    { name: "Corridors / Circulation", area: 33, floor: "vinyl_lvt", ceiling: "standard", walls: "painted_plaster" },
  ],
  items: [
    { id: "s1", trade: "concrete", description: "Mezzanine concrete slab — 150mm thk", unit: "m²", qty: 84, rate: 0, rateRange: [1850, 2600], source: "AfriSam 2025", confidence: 90 },
    { id: "dry1", trade: "drywall", description: "Partition walls — single skin 70mm", unit: "m²", qty: 312, rate: 0, rateRange: [420, 680], source: "Gyproc 2025", confidence: 92 },
    { id: "dry2", trade: "drywall", description: "Partition walls — acoustic double skin", unit: "m²", qty: 86, rate: 0, rateRange: [650, 980], source: "AECOM 2025/26", confidence: 88, notes: "Studio and boardroom boundaries" },
    { id: "dry3", trade: "drywall", description: "Suspended ceiling — standard 600×600 grid", unit: "m²", qty: 298, rate: 0, rateRange: [380, 580], source: "Sky Ceilings 2025", confidence: 95 },
    { id: "dry4", trade: "drywall", description: "Suspended ceiling — acoustic tile", unit: "m²", qty: 196, rate: 0, rateRange: [680, 1200], source: "AECOM 2025/26", confidence: 88 },
    { id: "fl2", trade: "flooring", description: "LVT / Vinyl Plank — office & kitchen", unit: "m²", qty: 271, rate: 0, rateRange: [280, 520], source: "Polyflor SA 2025", confidence: 90 },
    { id: "fl3", trade: "flooring", description: "Commercial carpet — boardroom & mezzanine", unit: "m²", qty: 120, rate: 0, rateRange: [220, 480], source: "Belgotex 2025", confidence: 88 },
    { id: "til1", trade: "tiling", description: "Porcelain floor tiles 600×600 — reception", unit: "m²", qty: 32, rate: 0, rateRange: [380, 650], source: "Italtile 2025", confidence: 95 },
    { id: "til2", trade: "tiling", description: "Porcelain floor tiles 600×600 — ablutions", unit: "m²", qty: 44, rate: 0, rateRange: [380, 650], source: "Italtile 2025", confidence: 95 },
    { id: "joi1", trade: "joinery", description: "Solid core door — Type A (internal office)", unit: "no", qty: 14, rate: 0, rateRange: [3500, 6500], source: "AECOM 2025/26", confidence: 95 },
    { id: "joi2", trade: "joinery", description: "Fire door 60-min rated — stairway & studio", unit: "no", qty: 4, rate: 0, rateRange: [12000, 22000], source: "AECOM 2025/26", confidence: 95 },
    { id: "plb1", trade: "plumbing", description: "Bathroom fit-out — male (3 WC, 3 URI, 3 WHB)", unit: "no", qty: 1, rate: 0, rateRange: [45000, 85000], source: "AECOM 2025/26", confidence: 85 },
    { id: "plb2", trade: "plumbing", description: "Bathroom fit-out — female (3 WC, 3 WHB)", unit: "no", qty: 1, rate: 0, rateRange: [35000, 65000], source: "AECOM 2025/26", confidence: 85 },
    { id: "pnt1", trade: "painting", description: "Interior paint — 2 coats walls & ceilings", unit: "m²", qty: 1840, rate: 0, rateRange: [65, 130], source: "Plascon / Dulux 2025", confidence: 95 },
  ],
};
export const R = n => `R ${Math.round(n).toLocaleString("en-ZA")}`;
export const pct = n => `${Number(n).toFixed(0)}%`;
export const mid = item => (item.rateRange[0] + item.rateRange[1]) / 2;
export const net = (item, markup) => mid(item) * item.qty * (1 + markup / 100);
// The API authorizes a signed-in member by the tenant a request names, and only shows that
// tenant's jobs — every takeoff call carries ?tenant_id=.
export const withTenant = (url, tenantId) =>
  tenantId ? `${url}${url.includes("?") ? "&" : "?"}tenant_id=${encodeURIComponent(tenantId)}` : url;

export function useTakeoff({ tenantId = "" }) {
  const [tab, setTab] = useState("boq");
  const [markup, setMarkup] = useState(15);
  const [selectedTrades, setSelectedTrades] = useState(Object.keys(TRADE_LABELS));

  // Per-item rate overrides (keyed by item id)
  const [itemRates, setItemRates] = useState(() =>
    Object.fromEntries(MOCK_TAKEOFF.items.map(i => [i.id, String(Math.round(mid(i)))]))
  );

  // Live API state
  // The real API (nothing ever set localStorage.vula_host, so production always hit
  // localhost:7438, failed, and fell back to mock data). A local override still works.
  const [apiHost] = useState(() => localStorage.getItem("vula_host") || VULA_API);
  const [liveJob, setLiveJob] = useState(null); // { job_id, status, filename, boq }
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef();
  const pollRef = useRef(null);

  // ── Adapted items: real API data or mock fallback ──────────────────────────
  const liveItems = useMemo(() => {
    const boqItems = liveJob?.boq?.items;
    if (!boqItems?.length) return null;
    return boqItems.map((item, i) => ({
      id: `live_${i}`,
      trade: item.trade,
      description: item.description,
      unit: item.unit,
      qty: item.qty,
      rate: item.rate_used,
      rateRange: [Math.round(item.rate_used * 0.9), Math.round(item.rate_used * 1.1)],
      source: item.source || "Vula AI",
      confidence: item.confidence || 75,
      notes: item.notes,
    }));
  }, [liveJob?.boq]);

  // Seed itemRates when live items first load
  useEffect(() => {
    if (!liveItems) return;
    setItemRates(Object.fromEntries(liveItems.map(i => [i.id, String(Math.round(i.rate))])));
  }, [liveItems]);

  const displayItems = liveItems || MOCK_TAKEOFF.items;
  // A real job shows its own rooms and areas (empty if the reader found none), never the demo's.
  const liveRooms = liveJob?.project ? (liveJob.project.room_list || []) : null;
  const rooms = liveRooms ?? MOCK_TAKEOFF.rooms;
  const areas = liveJob?.project
    ? { gross: Math.round(liveJob.project.gfa || 0),
        net: Math.round((liveRooms || []).reduce((s, r) => s + (r.area || 0), 0)), mezzanine: "—" }
    : MOCK_TAKEOFF.areas;
  const isLive = !!liveItems;
  const projectName = liveJob?.boq?.project_name || MOCK_TAKEOFF.projectName;

  // ── Upload real plans ──────────────────────────────────────────────────────
  const handleFileSelected = useCallback(async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("tenant_id", tenantId || "dashboard");
      fd.append("markup", String(markup));
      const resp = await fetch(withTenant(`${apiHost}/takeoff/upload`, tenantId), { method: "POST", body: fd });
      if (!resp.ok) throw new Error(`Upload failed: HTTP ${resp.status}`);
      const data = await resp.json();
      setLiveJob({ job_id: data.job_id, status: "queued", filename: data.filename, boq: null });
    } catch (err) {
      toast(`Upload error: ${err.message}\nMake sure the Vula API is running on ${apiHost}`);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }, [apiHost, markup, tenantId]);

  const handleUploadClick = useCallback(() => {
    fileRef.current?.click();
  }, []);

  // ── Poll job status ────────────────────────────────────────────────────────
  useEffect(() => {
    // needs_review / rejected wait for a person (submitReview), so polling stops there too.
    if (!liveJob?.job_id || ["complete", "failed", "needs_review", "rejected"].includes(liveJob.status)) {
      clearInterval(pollRef.current);
      return;
    }
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const resp = await fetch(withTenant(`${apiHost}/takeoff/${liveJob.job_id}`, tenantId));
        const data = await resp.json();
        if (data.status === "complete") {
          const boqResp = await fetch(withTenant(`${apiHost}/takeoff/${liveJob.job_id}/boq`, tenantId));
          const boqData = await boqResp.json();
          // project carries the rooms and floor area read from the plans (room schedule, cost/m²)
          setLiveJob(j => ({ ...j, status: "complete", project: data.project, boq: boqData.boq }));
        } else if (data.status === "failed") {
          setLiveJob(j => ({ ...j, status: "failed", error: data.error }));
        } else if (data.status === "needs_review") {
          setLiveJob(j => ({ ...j, status: "needs_review", project: data.project, review: data.review }));
        } else {
          setLiveJob(j => ({ ...j, status: data.status, project: data.project || j.project }));
        }
      } catch { /* network error — keep polling */ }
    }, 3000);
    return () => clearInterval(pollRef.current);
  }, [liveJob?.job_id, liveJob?.status, apiHost, tenantId]);

  // ── Review (the quantities a person must confirm before anything is priced) ──
  // decision: "approve" | "reject"; opts: { use_measured, rooms, gross_floor_area, scale_confirmed, note }
  const [reviewing, setReviewing] = useState(false);
  const submitReview = useCallback(async (decision, opts = {}) => {
    if (!liveJob?.job_id) return;
    setReviewing(true);
    try {
      const resp = await fetch(withTenant(`${apiHost}/takeoff/${liveJob.job_id}/review`, tenantId), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, ...opts }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      setLiveJob(j => ({ ...j, status: data.status }));   // approved → polling resumes until complete
    } catch (e) {
      toast(`Review not saved: ${e.message}`);
    } finally {
      setReviewing(false);
    }
  }, [apiHost, liveJob?.job_id, tenantId]);

  // ── Totals ─────────────────────────────────────────────────────────────────
  const filtered = displayItems.filter(i => selectedTrades.includes(i.trade));

  const totals = useMemo(() => {
    const byTrade = {};
    let total = 0;
    for (const item of filtered) {
      const r = parseFloat(itemRates[item.id]) || mid(item);
      const line = r * item.qty;
      const withMarkup = line * (1 + markup / 100);
      byTrade[item.trade] = (byTrade[item.trade] || 0) + withMarkup;
      total += withMarkup;
    }
    return { byTrade, total, profit: total * markup / (100 + markup) };
  }, [filtered, itemRates, markup]);

  // Fetched rather than linked: a plain link can't carry the sign-in token.
  const downloadExcel = async () => {
    try {
      const r = await fetch(withTenant(`${apiHost}/takeoff/${liveJob.job_id}/boq/excel`, tenantId));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url; a.download = `BOQ_${liveJob.job_id}.xlsx`; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) { toast(`Download failed: ${e.message}`); }
  };
  const copyTSV = () => {
    const lines = filtered.map(i => `${i.description}\t${i.qty}\t${i.unit}\t${R(parseFloat(itemRates[i.id]) || mid(i))}\t${R((parseFloat(itemRates[i.id]) || mid(i)) * i.qty * (1 + markup / 100))}`).join("\n");
    navigator.clipboard?.writeText(`BOQ — ${projectName}\nDescription\tQty\tUnit\tRate\tTotal\n${lines}`).catch(() => {});
    toast("Copied! Paste into Excel.");
  };

  const toggleTrade = key => setSelectedTrades(prev =>
    prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
  );

  const TABS = [
    { id: "plans",  label: "Plans & Drawings" },
    { id: "rooms",  label: "Room Schedule" },
    { id: "boq",    label: "Bill of Quantities" },
    { id: "order",  label: "Order Sheet" },
    { id: "rates",  label: "Market Rates" },
  ];

  // ── Job status banner text ─────────────────────────────────────────────────
  const statusLabel = {
    queued:          "Queued — waiting to start…",
    reading_plans:   "Reading plans (OCR)…",
    generating_boq:  "Generating BOQ from drawings…",
    building_orders: "Building order package…",
    needs_review:    "Check the quantities before pricing",
    approved:        "Approved — pricing…",
    rejected:        "Rejected — not priced",
    complete:        "Complete",
    failed:          "Failed",
  };
  return { reviewing, submitReview, tab, setTab, markup, setMarkup, selectedTrades, setSelectedTrades, itemRates, setItemRates, apiHost, liveJob, setLiveJob, uploading, setUploading, fileRef, pollRef, liveItems, displayItems, liveRooms, rooms, areas, isLive, projectName, handleFileSelected, handleUploadClick, filtered, totals, downloadExcel, copyTSV, toggleTrade, TABS, statusLabel };
}

export function useTakeoffRates({ apiHost, tenantId }) {
  const [rates, setRates] = useState([]);
  const [loading, setLoading] = useState(false);
  const [changedOnly, setChangedOnly] = useState(false);
  const [error, setError] = useState(null);

  const fetchRates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const url = withTenant(`${apiHost}/takeoff/rates${changedOnly ? "?changed_only=true" : ""}`, tenantId);
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setRates(data.rates || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [apiHost, changedOnly]);

  useEffect(() => { fetchRates(); }, [fetchRates]);
  return { rates, setRates, loading, setLoading, changedOnly, setChangedOnly, error, setError, fetchRates };
}

/** The order sheet's groups and its copied text, as VulaTakeoff's OrderSheet builds them. */
export function orderGroups(items, selectedTrades) {
  const orderItems = items.filter(i => selectedTrades.includes(i.trade));
  return Object.entries(TRADE_LABELS).map(([key, label]) => ({
    key, label, items: orderItems.filter(i => i.trade === key),
  })).filter(g => g.items.length > 0);
}
export function copyOrderSheet(groups, markup, projectName) {
  const lines = groups.map(g => {
    const tradeLines = g.items.map(i =>
      `${i.description}\t${i.qty}\t${i.unit}\t${R(mid(i))}/unit\t${R(net(i, markup))}`
    ).join("\n");
    return `\n=== ${g.label} ===\n${tradeLines}`;
  }).join("\n");
  navigator.clipboard?.writeText(`VULA TAKEOFF ORDER — ${projectName}\n${lines}`).catch(() => {});
  toast("Copied to clipboard — paste into your order sheet or email.");
}
