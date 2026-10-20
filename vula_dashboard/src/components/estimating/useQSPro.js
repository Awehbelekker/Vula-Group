/**
 * useQSPro.js — QS Pro (shared by VulaQSPro and QSProV2): a line-by-line estimate built from the
 * business's own rates and a static AECOM 2025/26 material reference — list price, discount,
 * quantity and markup per line — plus a comparison against suppliers' quotes. Moved verbatim
 * out of VulaQSPro.jsx: the figures are a budgeting estimate in rand, not booked money.
 */
import { useState, useEffect } from "react";
import { VULA_API } from "../../lib/authFetch";
import { toast } from "../ui/index.jsx";

// ─── LIVE SCRAPE SOURCES (for the backend scraper to hit) ─────────────────────
// These URLs feed into vula/skills/web_scraper.py → scrape_construction_rates()
// Judy uses the pre-seeded data; scraper refreshes it weekly via n8n

export const SCRAPE_SOURCES = [
  { name: "Builders Warehouse", url: "https://www.builders.co.za", category: "retail" },
  { name: "Cashbuild", url: "https://www.cashbuild.co.za", category: "retail" },
  { name: "Italtile", url: "https://www.italtile.co.za", category: "tiles" },
  { name: "Buildmate", url: "https://buildmate.co.za", category: "materials" },
  { name: "AECOM Cost Guide 2025/26", url: "https://estimationqs.com/building-costs-per-square-metre-in-south-africa-2025-to-2026-aecom-guide/", category: "benchmark" },
  { name: "BER Building Cost Index", url: "https://www.ber.ac.za", category: "index" },
];
// ─── MATERIAL UNIT RATES (seeded from AECOM + Builders Warehouse 2025/26) ──────
// Format: { label, unit, marketLow, marketHigh, lastScraped, source }
export const MATERIAL_RATES = {
  // ── STRUCTURE ───────────────────────────────────────────────────────────────
  concrete_readymix_25mpa: { label: "Concrete — Readymix 25MPa",      unit: "m³",   low: 1850,  high: 2300,  source: "AfriSam / PPC 2025" },
  concrete_readymix_30mpa: { label: "Concrete — Readymix 30MPa",      unit: "m³",   low: 2100,  high: 2600,  source: "AfriSam / PPC 2025" },
  brickwork_face:          { label: "Face Brick — Labour + material",  unit: "m²",   low: 680,   high: 950,   source: "AECOM 2025/26" },
  brickwork_plaster:       { label: "Plaster Brick — Labour + mat.",   unit: "m²",   low: 520,   high: 750,   source: "AECOM 2025/26" },
  steel_reo_y10:           { label: "Rebar Y10 (reinforcing steel)",   unit: "tonne",low: 16500, high: 19000, source: "Steel & Pipes SA 2025" },
  structural_steel:        { label: "Structural Steelwork (erected)",  unit: "tonne",low: 38000, high: 55000, source: "AECOM 2025/26" },
  // ── DRYWALL / PARTITIONS ────────────────────────────────────────────────────
  drywall_single:          { label: "Drywall — Single skin partition",  unit: "m²",  low: 420,   high: 680,   source: "Gyproc / AECOM 2025" },
  drywall_double:          { label: "Drywall — Double skin (acoustic)", unit: "m²",  low: 650,   high: 980,   source: "Gyproc / AECOM 2025" },
  drywall_ceiling_std:     { label: "Suspended Ceiling — Standard",    unit: "m²",  low: 380,   high: 580,   source: "Sky Ceilings / AECOM" },
  drywall_ceiling_acoustic:{ label: "Suspended Ceiling — Acoustic",    unit: "m²",  low: 680,   high: 1200,  source: "AECOM 2025/26" },
  // ── FLOORING ────────────────────────────────────────────────────────────────
  tiles_porcelain_600:     { label: "Porcelain Tiles 600×600 (laid)",  unit: "m²",  low: 380,   high: 650,   source: "Italtile / Builders 2025" },
  tiles_porcelain_large:   { label: "Porcelain Tiles 900×900 (laid)",  unit: "m²",  low: 550,   high: 950,   source: "Italtile 2025" },
  carpet_commercial:       { label: "Commercial Carpet (laid)",         unit: "m²",  low: 220,   high: 480,   source: "Belgotex / AECOM 2025" },
  vinyl_lvt:               { label: "LVT / Vinyl Plank (laid)",         unit: "m²",  low: 280,   high: 520,   source: "Polyflor SA 2025" },
  screed_50mm:             { label: "Sand/cement screed 50mm",         unit: "m²",  low: 95,    high: 160,   source: "AECOM 2025/26" },
  epoxy_coating:           { label: "Epoxy floor coating",             unit: "m²",  low: 180,   high: 380,   source: "Sika SA 2025" },
  // ── JOINERY / FITOUT ────────────────────────────────────────────────────────
  door_hollow_core:        { label: "Hollow core door (supplied+hung)", unit: "no", low: 2800,  high: 4500,  source: "Doors Inc / AECOM 2025" },
  door_solid_timber:       { label: "Solid timber door (supplied+hung)",unit: "no", low: 5500,  high: 12000, source: "AECOM 2025/26" },
  door_steel_fire:         { label: "Fire door — 60min (supply+hang)", unit: "no",  low: 12000, high: 22000, source: "AECOM 2025/26" },
  glass_partition:         { label: "Glass partition (frameless)",      unit: "m²",  low: 1800,  high: 3500,  source: "AECOM 2025/26" },
  joinery_kitchen_comm:    { label: "Kitchen unit (commercial grade)",  unit: "m run",low: 4500, high: 9000,  source: "AECOM 2025/26" },
  // ── MEP ─────────────────────────────────────────────────────────────────────
  hvac_vrf:                { label: "HVAC — VRF system (installed)",    unit: "kW",  low: 5500,  high: 9000,  source: "ExtraAir / AECOM 2025" },
  hvac_split:              { label: "HVAC — Split unit (installed)",    unit: "kW",  low: 3800,  high: 6500,  source: "AECOM 2025/26" },
  electrical_db:           { label: "DB board — full installation",     unit: "no",  low: 8500,  high: 18000, source: "AECOM 2025/26" },
  electrical_fit:          { label: "Electrical fitout (offices)",      unit: "m²",  low: 450,   high: 900,   source: "AECOM 2025/26" },
  plumbing_bathroom:       { label: "Bathroom — full installation",     unit: "no",  low: 18000, high: 45000, source: "AECOM 2025/26" },
  fire_sprinkler:          { label: "Sprinkler system (installed)",      unit: "m²",  low: 380,   high: 650,   source: "AECOM 2025/26" },
  // ── FINISHES ────────────────────────────────────────────────────────────────
  paint_interior:          { label: "Paint — interior (2 coats)",       unit: "m²",  low: 65,    high: 130,   source: "Plascon / Dulux 2025" },
  paint_exterior:          { label: "Paint — exterior (2 coats)",       unit: "m²",  low: 85,    high: 160,   source: "Plascon / Dulux 2025" },
  plaster_internal:        { label: "Plaster — internal walls",         unit: "m²",  low: 95,    high: 165,   source: "AECOM 2025/26" },
  // ── DEMOLITION ──────────────────────────────────────────────────────────────
  demolition_light:        { label: "Light demolition (strip out)",     unit: "m²",  low: 120,   high: 280,   source: "AECOM 2025/26" },
  demolition_structural:   { label: "Structural demolition",            unit: "m²",  low: 380,   high: 750,   source: "AECOM 2025/26" },
  // ── EXTERNAL ────────────────────────────────────────────────────────────────
  paving_brick:            { label: "Brick paving (laid)",              unit: "m²",  low: 280,   high: 520,   source: "Terraforce / AECOM" },
  fencing_palisade:        { label: "Palisade fencing (erected)",       unit: "m run",low: 850,  high: 1600,  source: "AECOM 2025/26" },
};
export const R = (n) => `R ${Math.round(n).toLocaleString("en-ZA")}`;
export const pct = (n) => `${Number(n).toFixed(1)}%`;
export const categories = [...new Set(Object.values(MATERIAL_RATES).map(v => {
  if (["concrete_readymix_25mpa","concrete_readymix_30mpa","brickwork_face","brickwork_plaster","steel_reo_y10","structural_steel"].includes(
    Object.keys(MATERIAL_RATES).find(k => MATERIAL_RATES[k] === v) || ""
  )) return "Structure";
  if (v.label.includes("Drywall") || v.label.includes("Ceiling") || v.label.includes("Partition")) return "Drywall & Ceilings";
  if (v.label.includes("Tile") || v.label.includes("Carpet") || v.label.includes("Vinyl") || v.label.includes("Screed") || v.label.includes("Epoxy") || v.label.includes("floor")) return "Flooring";
  if (v.label.includes("Door") || v.label.includes("Glass") || v.label.includes("Kitchen") || v.label.includes("Joinery")) return "Joinery & Doors";
  if (v.label.includes("HVAC") || v.label.includes("Electric") || v.label.includes("Plumb") || v.label.includes("Sprink")) return "MEP";
  if (v.label.includes("Paint") || v.label.includes("Plaster")) return "Finishes";
  if (v.label.includes("demol") || v.label.includes("Demol")) return "Demolition";
  return "External";
}))];
let nextId = 1;
export const newItem = (materialKey = "") => ({
  id: `item_${nextId++}`,
  materialKey,
  description: materialKey ? MATERIAL_RATES[materialKey]?.label || "" : "",
  listPrice: materialKey ? Math.round((MATERIAL_RATES[materialKey]?.low + MATERIAL_RATES[materialKey]?.high) / 2) : "",
  discount: 0,
  qty: 1,
  markup: 15,
});

export function useQSPro({ tenantId }) {
  const [projectName, setProjectName] = useState("New Estimate");
  const [client, setClient]           = useState("");
  const [items, setItems]             = useState([newItem("drywall_ceiling_std"), newItem("tiles_porcelain_600"), newItem("hvac_split")]);
  const [defaultMarkup, setDefaultMarkup] = useState(15);
  const [vatOn, setVatOn]             = useState(true);
  const [filterCat, setFilterCat]     = useState("All");
  const [search, setSearch]           = useState("");
  // The business's own unit rates (QS Rates tab, /v1/qs/rates) — what they actually charge,
  // listed above the static market reference so estimates start from their numbers.
  const [ownRates, setOwnRates]       = useState([]);
  useEffect(() => {
    if (!tenantId) return;
    fetch(`${VULA_API}/v1/qs/rates/${tenantId}`).then(r => r.json())
      .then(d => setOwnRates(d.rates || [])).catch(() => setOwnRates([]));
  }, [tenantId]);
  const addOwnRate = (r) => setItems(prev => [...prev, {
    ...newItem(), description: r.description || r.code || "Item", listPrice: r.rate ?? "",
    unit: r.unit || "", source: r.learned ? (r.source || "Learned from your documents") : `Your rate${r.code ? ` · ${r.code}` : ""}`, markup: defaultMarkup,
  }]);

  const updateItem = (id, field, value) =>
    setItems(prev => prev.map(i => i.id === id ? { ...i, [field]: value } : i));

  const removeItem = (id) => setItems(prev => prev.filter(i => i.id !== id));

  const addFromLibrary = (key) => {
    const item = newItem(key);
    item.markup = defaultMarkup;
    setItems(prev => [...prev, item]);
  };

  const addBlank = () => setItems(prev => [...prev, { ...newItem(), markup: defaultMarkup }]);

  const applyDefaultMarkup = () =>
    setItems(prev => prev.map(i => ({ ...i, markup: defaultMarkup })));

  const filteredLibrary = Object.entries(MATERIAL_RATES).filter(([k, v]) => {
    const catMatch = filterCat === "All" || categories.find(c =>
      (filterCat === "Drywall & Ceilings" && (v.label.includes("Drywall") || v.label.includes("Ceiling"))) ||
      (filterCat === "Flooring" && (v.label.includes("Tile") || v.label.includes("Carpet") || v.label.includes("Vinyl") || v.label.includes("Screed") || v.label.includes("Epoxy"))) ||
      (filterCat === "Joinery & Doors" && (v.label.includes("Door") || v.label.includes("Glass") || v.label.includes("Kitchen"))) ||
      (filterCat === "MEP" && (v.label.includes("HVAC") || v.label.includes("Electric") || v.label.includes("Plumb") || v.label.includes("Sprink"))) ||
      (filterCat === "Finishes" && (v.label.includes("Paint") || v.label.includes("Plaster"))) ||
      (filterCat === "Structure" && (v.label.includes("Concrete") || v.label.includes("Brick") || v.label.includes("steel") || v.label.includes("Steel")))
    );
    const searchMatch = !search || v.label.toLowerCase().includes(search.toLowerCase());
    return (filterCat === "All" || catMatch) && searchMatch;
  });

  const copyBoq = () => {
      const lines = items.map(i => {
        const rate = MATERIAL_RATES[i.materialKey] || {};
        const net = (parseFloat(i.listPrice)||0) * (1-(parseFloat(i.discount)||0)/100) * (parseFloat(i.qty)||0);
        const co = net * (1+(parseFloat(i.markup)||0)/100);
        return `${i.description || rate.label || "Item"}\t${parseFloat(i.qty)||0} ${rate.unit||""}\t${R(parseFloat(i.listPrice)||0)}\t${R(net)}\t${R(co)}`;
      }).join("\n");
      navigator.clipboard?.writeText(`VULA QS — ${projectName}\n${client ? `Client: ${client}\n` : ""}${"─".repeat(60)}\nDescription\tQty\tList Price\tNet Cost\tCharge-out\n${lines}`).catch(()=>{});
      toast("Copied! Paste into Excel or Word.");
  };
  return { projectName, setProjectName, client, setClient, items, setItems, defaultMarkup, setDefaultMarkup, vatOn, setVatOn, filterCat, setFilterCat, search, setSearch, ownRates, setOwnRates, addOwnRate, updateItem, removeItem, addFromLibrary, addBlank, applyDefaultMarkup, filteredLibrary, copyBoq };
}

export function useQuoteCompare({ items }) {
  const [quotes, setQuotes] = useState([
    { id: "q1", supplier: "Quote A", total: "" },
    { id: "q2", supplier: "Quote B", total: "" },
    { id: "q3", supplier: "Quote C", total: "" },
  ]);

  const vulaCost = items.reduce((sum, item) => {
    const net = (parseFloat(item.listPrice) || 0) * (1 - (parseFloat(item.discount) || 0) / 100) * (parseFloat(item.qty) || 0);
    return sum + net;
  }, 0);

  const vulaCO = items.reduce((sum, item) => {
    const net = (parseFloat(item.listPrice) || 0) * (1 - (parseFloat(item.discount) || 0) / 100) * (parseFloat(item.qty) || 0);
    return sum + net * (1 + (parseFloat(item.markup) || 0) / 100);
  }, 0);

  const allQuotes = [
    { id: "vula", supplier: "Vula QS Estimate", total: vulaCO, isVula: true },
    ...quotes.map(q => ({ ...q, total: parseFloat(q.total) || 0 })),
  ].filter(q => q.total > 0).sort((a, b) => a.total - b.total);

  const cheapest = allQuotes[0]?.total || 0;
  return { quotes, setQuotes, vulaCost, vulaCO, allQuotes, cheapest };
}

/** One line's figures, as VulaQSPro's LineItem works them out. */
export function lineCalc(item) {
  const rate = MATERIAL_RATES[item.materialKey] || {};
  const marketMid = rate.low && rate.high ? (rate.low + rate.high) / 2 : 0;
  const listPrice = parseFloat(item.listPrice) || 0;
  const discountPct = parseFloat(item.discount) || 0;
  const qty = parseFloat(item.qty) || 0;
  const netUnit = listPrice * (1 - discountPct / 100);
  const lineTotal = netUnit * qty;
  const markupAmt = lineTotal * (parseFloat(item.markup) || 0) / 100;
  const chargeOut = lineTotal + markupAmt;
  const variance = marketMid > 0 ? ((listPrice - marketMid) / marketMid) * 100 : null;
  return { rate, marketMid, listPrice, netUnit, lineTotal, markupAmt, chargeOut, variance };
}

/** The estimate's totals, as VulaQSPro's SummaryBar works them out. */
export function summaryCalc(items, vatOn) {
  const totalNet = items.reduce((s, i) => s + (parseFloat(i.listPrice) || 0) * (1 - (parseFloat(i.discount) || 0) / 100) * (parseFloat(i.qty) || 0), 0);
  const totalCO = items.reduce((s, i) => {
    const net = (parseFloat(i.listPrice) || 0) * (1 - (parseFloat(i.discount) || 0) / 100) * (parseFloat(i.qty) || 0);
    return s + net * (1 + (parseFloat(i.markup) || 0) / 100);
  }, 0);
  const totalProfit = totalCO - totalNet;
  const avgMarkup = totalNet > 0 ? (totalProfit / totalNet) * 100 : 0;
  const totalSaving = items.reduce((s, i) => {
    const list = (parseFloat(i.listPrice) || 0) * (parseFloat(i.qty) || 0);
    const net = list * (1 - (parseFloat(i.discount) || 0) / 100);
    return s + (list - net);
  }, 0);
  const vat = vatOn ? totalCO * 0.15 : 0;
  return { totalNet, totalCO, totalProfit, avgMarkup, totalSaving, vat };
}
