/**
 * useQuickCost.js — Quick Cost (shared by VulaQS and QuickCostV2): an early construction-cost
 * estimate from a floor area and an AECOM 2025/26 m² rate (or the business's own m² rate, or
 * supplier quotes), plus markup, contingency, SACAP BN672 architect's fee, PM and PA fees,
 * council fee and VAT. Moved verbatim out of VulaQS.jsx: the figures are a budgeting estimate
 * in rand, not booked money.
 */
import { useState, useMemo, useEffect } from "react";
import { VULA_API } from "../../lib/authFetch";
import { toast } from "../ui/index.jsx";

// ─── AECOM 2025/26 COST DATA (scraped & verified from AECOM Africa Cost Guide)
// Source: AECOM Africa Property & Construction Cost Guide 2025/26, pp.47–62
// Rates excl. VAT, current to 1 July 2025. Cape Town / Western Cape baseline.
// ─────────────────────────────────────────────────────────────────────────────

export const AECOM_RATES = {
  // ── OFFICE ──────────────────────────────────────────────────────────────
  office_lowrise_std:      { label: "Office — Low-rise, Standard Spec",        low: 10700, high: 13100, category: "Office" },
  office_lowrise_prestige: { label: "Office — Low-rise, Prestigious",          low: 13800, high: 20500, category: "Office" },
  office_highrise_std:     { label: "Office — High-rise Tower, Standard",      low: 15500, high: 20500, category: "Office" },
  office_highrise_prestige:{ label: "Office — High-rise Tower, Prestigious",   low: 20500, high: 25900, category: "Office" },

  // ── RETAIL ──────────────────────────────────────────────────────────────
  retail_convenience:      { label: "Retail — Convenience Centre (≤5,000m²)", low: 10500, high: 13800, category: "Retail" },
  retail_neighbourhood:    { label: "Retail — Neighbourhood Centre",           low: 11500, high: 15200, category: "Retail" },
  retail_community:        { label: "Retail — Community Centre",               low: 12500, high: 16800, category: "Retail" },
  retail_regional:         { label: "Retail — Regional Mall",                  low: 15000, high: 20000, category: "Retail" },
  retail_fitout:           { label: "Retail — Tenant Fit-out (shell)",         low: 6500,  high: 9500,  category: "Retail" },

  // ── COMMERCIAL FIT-OUT (Judy's primary work) ─────────────────────────────
  fitout_basic:            { label: "Commercial Fit-out — Basic/Utilitarian",  low: 5500,  high: 8000,  category: "Fit-out" },
  fitout_standard:         { label: "Commercial Fit-out — Standard Office",    low: 7500,  high: 11000, category: "Fit-out" },
  fitout_premium:          { label: "Commercial Fit-out — Premium/Specialist", low: 11000, high: 17000, category: "Fit-out" },
  fitout_media_studio:     { label: "Media Studio / Broadcast Fit-out",        low: 15000, high: 25000, category: "Fit-out" },

  // ── RESIDENTIAL ─────────────────────────────────────────────────────────
  res_entry:               { label: "Residential — Entry Level",               low: 8000,  high: 10000, category: "Residential" },
  res_mid:                 { label: "Residential — Mid-range",                 low: 11000, high: 15000, category: "Residential" },
  res_high:                { label: "Residential — High Specification",        low: 15000, high: 22000, category: "Residential" },
  res_luxury:              { label: "Residential — Luxury / Ultra-Premium",    low: 22000, high: 50000, category: "Residential" },

  // ── INDUSTRIAL ──────────────────────────────────────────────────────────
  industrial_warehouse:    { label: "Industrial — Warehouse / Logistics",      low: 5000,  high: 8000,  category: "Industrial" },
  industrial_light:        { label: "Industrial — Light Manufacturing",        low: 8000,  high: 12000, category: "Industrial" },
  industrial_hitech:       { label: "Industrial — Hi-tech / Data Centre",      low: 18000, high: 35000, category: "Industrial" },

  // ── HOSPITALITY ─────────────────────────────────────────────────────────
  hotel_budget:            { label: "Hotel — Budget / 3-star",                 low: 18000, high: 25000, category: "Hospitality" },
  hotel_midscale:          { label: "Hotel — Mid-scale / 4-star",              low: 25000, high: 38000, category: "Hospitality" },
  hotel_luxury:            { label: "Hotel — Luxury / 5-star",                 low: 38000, high: 60000, category: "Hospitality" },

  // ── PARKING ─────────────────────────────────────────────────────────────
  parking_grade:           { label: "Parking — At Grade (landscaped)",         low: 750,   high: 950,   category: "Parking" },
  parking_structured:      { label: "Parking — Structured Deck",               low: 5200,  high: 5800,  category: "Parking" },
  parking_semi_basement:   { label: "Parking — Semi-basement",                 low: 5800,  high: 7800,  category: "Parking" },
  parking_basement:        { label: "Parking — Full Basement",                 low: 6100,  high: 10700, category: "Parking" },

  // ── MEP / BUILDING SERVICES ──────────────────────────────────────────────
  mep_standard:            { label: "MEP / Building Services — Standard",      low: 1500,  high: 2500,  category: "MEP" },
  mep_complex:             { label: "MEP / Building Services — Complex",       low: 2500,  high: 5000,  category: "MEP" },
  mep_hvac_only:           { label: "HVAC Only (commercial fitout)",           low: 800,   high: 1800,  category: "MEP" },
};

// ─── SACAP BOARD NOTICE 672 (2024) FEE TABLES ────────────────────────────────
export const SACAP = {
  low: [
    { to: 200000,    base: 11341.85,   pct: 17.53, over: 0 },
    { to: 650000,    base: 46393.33,   pct: 16.85, over: 200000 },
    { to: 2000000,   base: 122193.97,  pct: 12.43, over: 650000 },
    { to: 4000000,   base: 289927.74,  pct: 10.83, over: 2000000 },
    { to: 6500000,   base: 506559.80,  pct: 10.55, over: 4000000 },
    { to: 13000000,  base: 770251.28,  pct: 9.16,  over: 6500000 },
    { to: 40000000,  base: 1365321.64, pct: 8.86,  over: 13000000 },
    { to: Infinity,  base: 3755421.23, pct: 8.85,  over: 40000000 },
  ],
  medium: [
    { to: 200000,    base: 13570.07,   pct: 20.96, over: 0 },
    { to: 650000,    base: 55507.74,   pct: 20.16, over: 200000 },
    { to: 2000000,   base: 146200.15,  pct: 14.87, over: 650000 },
    { to: 4000000,   base: 346886.84,  pct: 12.96, over: 2000000 },
    { to: 6500000,   base: 606078.35,  pct: 12.62, over: 4000000 },
    { to: 13000000,  base: 921574.57,  pct: 10.95, over: 6500000 },
    { to: 40000000,  base: 1633552.23, pct: 10.60, over: 13000000 },
    { to: Infinity,  base: 4493209.93, pct: 10.59, over: 40000000 },
  ],
  high: [
    { to: 200000,    base: 15798.28,   pct: 24.41, over: 0 },
    { to: 650000,    base: 64622.16,   pct: 23.47, over: 200000 },
    { to: 2000000,   base: 170206.35,  pct: 17.31, over: 650000 },
    { to: 4000000,   base: 403845.93,  pct: 15.09, over: 2000000 },
    { to: 6500000,   base: 705596.92,  pct: 14.69, over: 4000000 },
    { to: 13000000,  base: 1072897.87, pct: 12.76, over: 6500000 },
    { to: 40000000,  base: 1901782.84, pct: 12.33, over: 13000000 },
    { to: Infinity,  base: 5230998.63, pct: 12.33, over: 40000000 },
  ],
};

// ─── DEFAULT SUPPLIERS ────────────────────────────────────────────────────────
export const DEFAULT_SUPPLIERS = [
  { id: "s1", name: "General Contractor",       category: "Construction",   listRate: 0,     discount: 0,    customCost: "" },
  { id: "s2", name: "HVAC / ExtraAir",          category: "MEP",            listRate: 0,     discount: 0,    customCost: "" },
  { id: "s3", name: "Electrical Contractor",    category: "MEP",            listRate: 0,     discount: 0,    customCost: "" },
  { id: "s4", name: "Ceilings / Drywalling",   category: "Finishes",       listRate: 0,     discount: 0,    customCost: "" },
  { id: "s5", name: "Flooring Supplier",        category: "Finishes",       listRate: 0,     discount: 0,    customCost: "" },
  { id: "s6", name: "Glazing / Shopfront",      category: "Façade",         listRate: 0,     discount: 0,    customCost: "" },
  { id: "s7", name: "Joinery / Millwork",       category: "Finishes",       listRate: 0,     discount: 0,    customCost: "" },
  { id: "s8", name: "Structural Engineer",      category: "Professional",   listRate: 0,     discount: 0,    customCost: "" },
  { id: "s9", name: "Fire Engineer",            category: "Professional",   listRate: 0,     discount: 0,    customCost: "" },
];

export const R = (n) => `R ${Number(n).toLocaleString("en-ZA", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

export const pct = (n) => `${Number(n).toFixed(1)}%`;

export function calcSACAPFee(value, complexity) {
  const table = SACAP[complexity] || SACAP.medium;
  const row = table.find(r => value <= r.to);
  if (!row) return 0;
  return row.base + ((value - row.over) * row.pct / 100);
}

export function useQuickCost({ tenantId }) {
  const categories = [...new Set(Object.values(AECOM_RATES).map(r => r.category))];

  // Project inputs
  const [projectName, setProjectName] = useState("New Project");
  const [clientName, setClientName]   = useState("");
  const [area, setArea]               = useState(500);
  const [rateKey, setRateKey]         = useState("fitout_standard");
  const [useRange, setUseRange]       = useState("mid"); // low | mid | high
  const [customRate, setCustomRate]   = useState("");
  // The business's own per-m² rates (QS Rates tab) — pick one instead of typing it in.
  const [ownM2Rates, setOwnM2Rates]   = useState([]);
  useEffect(() => {
    if (!tenantId) return;
    fetch(`${VULA_API}/v1/qs/rates/${tenantId}`).then(r => r.json())
      .then(d => setOwnM2Rates((d.rates || []).filter(r => /^(m2|m²|sqm|per m2|per m²)$/i.test((r.unit || "").trim()))))
      .catch(() => setOwnM2Rates([]));
  }, [tenantId]);
  const [complexity, setComplexity]   = useState("medium");
  const [location, setLocation]       = useState("western_cape");

  // Markup & fees
  const [markupPct, setMarkupPct]     = useState(15);   // Judy's profit margin %
  const [paFee, setPaFee]             = useState(0);     // Principal Agent fixed fee
  const [pmMonths, setPmMonths]       = useState(3);
  const [pmMonthly, setPmMonthly]     = useState(30000);
  const [includeVAT, setIncludeVAT]   = useState(true);
  const [contingency, setContingency] = useState(10);   // % of construction

  // Council
  const [councilPct, setCouncilPct]   = useState(0.85); // CoCT 0.85% of construction

  // Suppliers
  const [suppliers, setSuppliers]     = useState(DEFAULT_SUPPLIERS);

  const updateSupplier = (id, field, value) =>
    setSuppliers(prev => prev.map(s => s.id === id ? { ...s, [field]: value } : s));

  const addSupplier = () => setSuppliers(prev => [...prev, {
    id: `s${Date.now()}`, name: "New Supplier", category: "Construction",
    listRate: 0, discount: 0, customCost: "",
  }]);

  const removeSupplier = (id) => setSuppliers(prev => prev.filter(s => s.id !== id));

  // ─── CALCULATIONS ──────────────────────────────────────────────────────────
  const calc = useMemo(() => {
    const rate = AECOM_RATES[rateKey];
    const locationFactor = location === "western_cape" ? 1.05 : location === "gauteng" ? 1.0 : 0.95;

    // Base construction rate
    let baseRate;
    if (customRate && !isNaN(parseFloat(customRate))) {
      baseRate = parseFloat(customRate);
    } else {
      const mid = (rate.low + rate.high) / 2;
      baseRate = (useRange === "low" ? rate.low : useRange === "high" ? rate.high : mid) * locationFactor;
    }

    const constructionBase = baseRate * area;
    const contingencyAmt   = constructionBase * contingency / 100;
    const constructionTotal = constructionBase + contingencyAmt;

    // Supplier costs (if entered override construction)
    const supplierTotal = suppliers.reduce((sum, s) => {
      if (s.customCost && !isNaN(parseFloat(s.customCost))) {
        const net = parseFloat(s.customCost) * (1 - s.discount / 100);
        return sum + net;
      }
      if (s.listRate && !isNaN(parseFloat(s.listRate))) {
        const net = parseFloat(s.listRate) * (1 - s.discount / 100);
        return sum + net;
      }
      return sum;
    }, 0);

    const hasSupplierData = supplierTotal > 0;
    const effectiveConstruction = hasSupplierData ? supplierTotal : constructionTotal;

    // Markup (Judy's profit)
    const markupAmt = effectiveConstruction * markupPct / 100;
    const subtotalAfterMarkup = effectiveConstruction + markupAmt;

    // SACAP architectural fee
    const sacapFee = calcSACAPFee(effectiveConstruction, complexity);

    // PM fee
    const pmTotal = pmMonths * pmMonthly;

    // PA fee
    const paTotal = parseFloat(paFee) || 0;

    // Council fee
    const councilFee = effectiveConstruction * councilPct / 100;

    // Professional fees total
    const proFeesTotal = sacapFee + pmTotal + paTotal;

    // Grand total
    const grandSubtotal = subtotalAfterMarkup + proFeesTotal + councilFee;
    const vatAmt = includeVAT ? grandSubtotal * 0.15 : 0;
    const grandTotal = grandSubtotal + vatAmt;

    return {
      baseRate, constructionBase, contingencyAmt, constructionTotal,
      supplierTotal, hasSupplierData, effectiveConstruction,
      markupAmt, subtotalAfterMarkup,
      sacapFee, pmTotal, paTotal, councilFee, proFeesTotal,
      grandSubtotal, vatAmt, grandTotal,
      rateRange: rate ? `${R(rate.low * locationFactor)} – ${R(rate.high * locationFactor)}/m²` : "",
    };
  }, [area, rateKey, useRange, customRate, complexity, location, markupPct, paFee, pmMonths, pmMonthly, contingency, councilPct, includeVAT, suppliers]);

  const copySummary = () => {
    const text = `VULA QS ESTIMATE\n${projectName}${clientName ? ` — ${clientName}` : ""}\n${"─".repeat(40)}\nArea: ${area}m²\nBuild type: ${AECOM_RATES[rateKey]?.label}\nRate: ${R(calc.baseRate)}/m²\n\nConstruction: ${R(calc.effectiveConstruction)}\nMarkup (${pct(markupPct)}): ${R(calc.markupAmt)}\nSACAPFee: ${R(calc.sacapFee)}\nPM (${pmMonths}mo): ${R(calc.pmTotal)}\nPA fee: ${R(calc.paTotal)}\nCouncil: ${R(calc.councilFee)}\n${includeVAT ? `VAT: ${R(calc.vatAmt)}\n` : ""}${"─".repeat(40)}\nTOTAL: ${R(calc.grandTotal)} ${includeVAT ? "incl. VAT" : "excl. VAT"}\n\nRates: AECOM 2025/26 · SACAP BN672 · CoCT tariff`;
    navigator.clipboard?.writeText(text).catch(() => {});
    toast("Copied to clipboard — paste into your quote document.");
  };

  // ─── RENDER ────────────────────────────────────────────────────────────────
  return { categories, projectName, setProjectName, clientName, setClientName, area, setArea, rateKey, setRateKey, useRange, setUseRange, customRate, setCustomRate, ownM2Rates, setOwnM2Rates, complexity, setComplexity, location, setLocation, markupPct, setMarkupPct, paFee, setPaFee, pmMonths, setPmMonths, pmMonthly, setPmMonthly, includeVAT, setIncludeVAT, contingency, setContingency, councilPct, setCouncilPct, suppliers, setSuppliers, updateSupplier, addSupplier, removeSupplier, calc, copySummary };
}
