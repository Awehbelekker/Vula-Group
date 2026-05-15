import { useState, useCallback, useMemo } from "react";

// ─── AECOM 2025/26 COST DATA (scraped & verified from AECOM Africa Cost Guide)
// Source: AECOM Africa Property & Construction Cost Guide 2025/26, pp.47–62
// Rates excl. VAT, current to 1 July 2025. Cape Town / Western Cape baseline.
// ─────────────────────────────────────────────────────────────────────────────

const AECOM_RATES = {
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
const SACAP = {
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
const DEFAULT_SUPPLIERS = [
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

// ─── UTILITIES ────────────────────────────────────────────────────────────────
const R = (n) => `R ${Number(n).toLocaleString("en-ZA", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
const pct = (n) => `${Number(n).toFixed(1)}%`;

function calcSACAPFee(value, complexity) {
  const table = SACAP[complexity] || SACAP.medium;
  const row = table.find(r => value <= r.to);
  if (!row) return 0;
  return row.base + ((value - row.over) * row.pct / 100);
}

// ─── STYLES ───────────────────────────────────────────────────────────────────
const C = {
  bg: "#F5F2EB",
  card: "#FFFFFF",
  border: "#E0DAD0",
  green: "#2D5A3D",
  greenLight: "#3D7A52",
  amber: "#C4861A",
  text: "#1A1A1A",
  muted: "#6B6560",
  light: "#8A857E",
  red: "#C0392B",
  highlight: "#FFF8EE",
};

const inp = {
  width: "100%", padding: "10px 12px", borderRadius: 6,
  border: `1px solid ${C.border}`, fontSize: 13,
  background: "#FAFAF8", color: C.text, outline: "none",
  fontFamily: "'DM Mono', monospace", boxSizing: "border-box",
};

const lbl = {
  display: "block", fontSize: 10, letterSpacing: "0.1em",
  textTransform: "uppercase", color: C.muted,
  fontFamily: "'DM Mono', monospace", marginBottom: 5,
};

// ─── SECTION COMPONENT ────────────────────────────────────────────────────────
function Section({ title, badge, children }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, marginBottom: 20, overflow: "hidden" }}>
      <div style={{ padding: "14px 20px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", gap: 10, background: "#FAFAF8" }}>
        <span style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 16, fontWeight: 700, color: C.text }}>{title}</span>
        {badge && <span style={{ padding: "2px 8px", borderRadius: 10, background: `${C.green}15`, color: C.green, fontSize: 10, fontFamily: "'DM Mono', monospace" }}>{badge}</span>}
      </div>
      <div style={{ padding: 20 }}>{children}</div>
    </div>
  );
}

function Row({ label, value, sub, highlight, bold }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", padding: "9px 0", borderBottom: `1px solid ${C.border}`, background: highlight ? C.highlight : "transparent" }}>
      <div>
        <div style={{ fontSize: 13, color: bold ? C.text : C.muted, fontWeight: bold ? 600 : 400, fontFamily: "'DM Sans', sans-serif" }}>{label}</div>
        {sub && <div style={{ fontSize: 11, color: C.light, fontFamily: "'DM Mono', monospace", marginTop: 2 }}>{sub}</div>}
      </div>
      <div style={{ fontSize: 13, fontWeight: bold ? 700 : 500, color: highlight ? C.green : C.text, fontFamily: "'DM Mono', monospace", textAlign: "right" }}>{value}</div>
    </div>
  );
}

// ─── MAIN APP ─────────────────────────────────────────────────────────────────
export default function VulaQS() {
  const categories = [...new Set(Object.values(AECOM_RATES).map(r => r.category))];

  // Project inputs
  const [projectName, setProjectName] = useState("New Project");
  const [clientName, setClientName]   = useState("");
  const [area, setArea]               = useState(500);
  const [rateKey, setRateKey]         = useState("fitout_standard");
  const [useRange, setUseRange]       = useState("mid"); // low | mid | high
  const [customRate, setCustomRate]   = useState("");
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

  // ─── RENDER ────────────────────────────────────────────────────────────────
  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600;700&family=DM+Sans:wght@400;500;600&family=DM+Mono:wght@400;500&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: ${C.bg}; }
        input:focus, select:focus { border-color: ${C.green} !important; box-shadow: 0 0 0 2px ${C.green}20; }
        input[type=range] { accent-color: ${C.green}; }
      `}</style>

      <div style={{ minHeight: "100vh", background: C.bg, fontFamily: "'DM Sans', sans-serif", padding: "0 0 60px" }}>

        {/* Header */}
        <div style={{ background: C.green, padding: "18px 28px", display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ width: 36, height: 36, background: "rgba(255,255,255,0.15)", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "'Cormorant Garamond', serif", fontSize: 20, color: "#fff", fontWeight: 700 }}>V</div>
          <div>
            <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 20, color: "#fff", fontWeight: 700 }}>Vula QS</div>
            <div style={{ fontSize: 10, color: "rgba(255,255,255,0.65)", letterSpacing: "0.12em", fontFamily: "'DM Mono', monospace" }}>CONSTRUCTION COST ESTIMATOR · DIGG</div>
          </div>
          <div style={{ marginLeft: "auto", fontSize: 11, color: "rgba(255,255,255,0.5)", fontFamily: "'DM Mono', monospace" }}>AECOM 2025/26 · SACAP BN672</div>
        </div>

        <div style={{ maxWidth: 1100, margin: "0 auto", padding: "24px 20px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>

          {/* LEFT COL */}
          <div>
            {/* Project Info */}
            <Section title="Project Details">
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
                <div>
                  <label style={lbl}>Project Name</label>
                  <input value={projectName} onChange={e => setProjectName(e.target.value)} style={inp} />
                </div>
                <div>
                  <label style={lbl}>Client</label>
                  <input value={clientName} onChange={e => setClientName(e.target.value)} style={inp} placeholder="Client name" />
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div>
                  <label style={lbl}>Floor Area (m²)</label>
                  <input type="number" value={area} onChange={e => setArea(Number(e.target.value))} style={inp} />
                </div>
                <div>
                  <label style={lbl}>Location</label>
                  <select value={location} onChange={e => setLocation(e.target.value)} style={inp}>
                    <option value="western_cape">Western Cape (+5%)</option>
                    <option value="gauteng">Gauteng (baseline)</option>
                    <option value="other">Other Province (−5%)</option>
                  </select>
                </div>
              </div>
            </Section>

            {/* Build Type */}
            <Section title="Build Type & Rate" badge={AECOM_RATES[rateKey]?.category}>
              <div style={{ marginBottom: 14 }}>
                <label style={lbl}>Category</label>
                <select value={rateKey} onChange={e => setRateKey(e.target.value)} style={inp}>
                  {categories.map(cat => (
                    <optgroup key={cat} label={cat}>
                      {Object.entries(AECOM_RATES)
                        .filter(([, v]) => v.category === cat)
                        .map(([k, v]) => (
                          <option key={k} value={k}>{v.label} — {R(v.low)}–{R(v.high)}/m²</option>
                        ))}
                    </optgroup>
                  ))}
                </select>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 14 }}>
                <div>
                  <label style={lbl}>Rate Position</label>
                  <select value={useRange} onChange={e => setUseRange(e.target.value)} style={inp}>
                    <option value="low">Conservative (Low)</option>
                    <option value="mid">Mid-range (Typical)</option>
                    <option value="high">Premium (High)</option>
                  </select>
                </div>
                <div>
                  <label style={lbl}>Custom Rate R/m² (overrides)</label>
                  <input type="number" value={customRate} onChange={e => setCustomRate(e.target.value)} style={inp} placeholder={`e.g. ${R(AECOM_RATES[rateKey]?.low || 0).replace("R ", "")}`} />
                </div>
              </div>

              <div style={{ padding: "10px 14px", background: `${C.green}10`, borderRadius: 6, fontSize: 12, color: C.green, fontFamily: "'DM Mono', monospace" }}>
                AECOM 2025/26 range: {calc.rateRange} · Your rate: {R(calc.baseRate)}/m²
              </div>
            </Section>

            {/* Fees & Markup */}
            <Section title="Fees, Markup & Contingency">
              <div style={{ marginBottom: 16 }}>
                <label style={lbl}>Your Profit Markup: {pct(markupPct)} on construction cost</label>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <input type="range" min={0} max={40} step={0.5} value={markupPct} onChange={e => setMarkupPct(Number(e.target.value))} style={{ flex: 1 }} />
                  <input type="number" value={markupPct} onChange={e => setMarkupPct(Number(e.target.value))} style={{ ...inp, width: 70 }} />
                </div>
                <div style={{ fontSize: 11, color: C.light, fontFamily: "'DM Mono', monospace", marginTop: 4 }}>
                  Markup amount: {R(calc.markupAmt)} · Standard range: 10–20%
                </div>
              </div>

              <div style={{ marginBottom: 16 }}>
                <label style={lbl}>Contingency: {pct(contingency)} of construction</label>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <input type="range" min={0} max={25} step={1} value={contingency} onChange={e => setContingency(Number(e.target.value))} style={{ flex: 1 }} />
                  <input type="number" value={contingency} onChange={e => setContingency(Number(e.target.value))} style={{ ...inp, width: 70 }} />
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 16 }}>
                <div>
                  <label style={lbl}>SACAP Complexity</label>
                  <select value={complexity} onChange={e => setComplexity(e.target.value)} style={inp}>
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                </div>
                <div>
                  <label style={lbl}>PA Fixed Fee (R)</label>
                  <input type="number" value={paFee} onChange={e => setPaFee(e.target.value)} style={inp} placeholder="0" />
                </div>
                <div>
                  <label style={lbl}>Council Fee %</label>
                  <input type="number" step={0.05} value={councilPct} onChange={e => setCouncilPct(Number(e.target.value))} style={inp} placeholder="0.85" />
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div>
                  <label style={lbl}>PM Months</label>
                  <input type="number" value={pmMonths} onChange={e => setPmMonths(Number(e.target.value))} style={inp} />
                </div>
                <div>
                  <label style={lbl}>PM Monthly Rate (R)</label>
                  <input type="number" value={pmMonthly} onChange={e => setPmMonthly(Number(e.target.value))} style={inp} />
                </div>
              </div>
            </Section>

            {/* Supplier Costs */}
            <Section title="Supplier Costs & Discounts" badge="optional — overrides m² rate">
              <p style={{ fontSize: 12, color: C.muted, marginBottom: 14, fontFamily: "'DM Mono', monospace", lineHeight: 1.5 }}>
                Enter actual supplier quotes below. Add your discount and Vula calculates your net cost. Leave blank to use the AECOM m² rate above.
              </p>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: "#F0EDE5" }}>
                      {["Supplier", "Category", "List Cost (R)", "Discount %", "Net Cost", ""].map(h => (
                        <th key={h} style={{ padding: "8px 10px", textAlign: "left", fontFamily: "'DM Mono', monospace", fontSize: 10, letterSpacing: "0.08em", color: C.muted, borderBottom: `1px solid ${C.border}` }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {suppliers.map(s => {
                      const cost = s.customCost ? parseFloat(s.customCost) : parseFloat(s.listRate) || 0;
                      const net = cost * (1 - s.discount / 100);
                      return (
                        <tr key={s.id} style={{ borderBottom: `1px solid ${C.border}` }}>
                          <td style={{ padding: "6px 8px" }}>
                            <input value={s.name} onChange={e => updateSupplier(s.id, "name", e.target.value)} style={{ ...inp, padding: "5px 8px", fontSize: 12 }} />
                          </td>
                          <td style={{ padding: "6px 8px" }}>
                            <select value={s.category} onChange={e => updateSupplier(s.id, "category", e.target.value)} style={{ ...inp, padding: "5px 8px", fontSize: 12 }}>
                              {["Construction","MEP","Finishes","Façade","Professional","Other"].map(c => <option key={c}>{c}</option>)}
                            </select>
                          </td>
                          <td style={{ padding: "6px 8px" }}>
                            <input type="number" value={s.customCost} onChange={e => updateSupplier(s.id, "customCost", e.target.value)} placeholder="Quote" style={{ ...inp, padding: "5px 8px", fontSize: 12, width: 110 }} />
                          </td>
                          <td style={{ padding: "6px 8px" }}>
                            <input type="number" min={0} max={100} value={s.discount} onChange={e => updateSupplier(s.id, "discount", Number(e.target.value))} style={{ ...inp, padding: "5px 8px", fontSize: 12, width: 70 }} />
                          </td>
                          <td style={{ padding: "6px 10px", fontFamily: "'DM Mono', monospace", color: s.customCost ? C.green : C.light }}>
                            {s.customCost ? R(net) : "—"}
                          </td>
                          <td style={{ padding: "6px 4px" }}>
                            <button onClick={() => removeSupplier(s.id)} style={{ background: "none", border: "none", color: C.red, cursor: "pointer", fontSize: 14 }}>×</button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <button onClick={addSupplier} style={{ marginTop: 12, padding: "8px 16px", background: "transparent", border: `1px dashed ${C.border}`, borderRadius: 6, fontSize: 12, color: C.muted, cursor: "pointer", fontFamily: "'DM Mono', monospace" }}>
                + Add Supplier
              </button>
              {calc.hasSupplierData && (
                <div style={{ marginTop: 10, padding: "8px 12px", background: `${C.amber}15`, borderRadius: 6, fontSize: 11, color: C.amber, fontFamily: "'DM Mono', monospace" }}>
                  ✓ Using supplier totals ({R(calc.supplierTotal)}) instead of AECOM m² rate
                </div>
              )}
            </Section>
          </div>

          {/* RIGHT COL — COST BREAKDOWN */}
          <div>
            <Section title="Cost Breakdown" badge={`${area}m² · ${AECOM_RATES[rateKey]?.label}`}>

              {/* Construction */}
              <div style={{ marginBottom: 4 }}>
                <div style={{ fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase", color: C.amber, fontFamily: "'DM Mono', monospace", marginBottom: 6 }}>Construction Cost</div>
                {!calc.hasSupplierData ? (
                  <>
                    <Row label={`Base rate: ${R(calc.baseRate)}/m² × ${area}m²`} value={R(calc.constructionBase)} />
                    <Row label={`Contingency (${pct(contingency)})`} value={R(calc.contingencyAmt)} />
                    <Row label="Construction Total" value={R(calc.constructionTotal)} bold />
                  </>
                ) : (
                  <>
                    {suppliers.filter(s => s.customCost).map(s => {
                      const cost = parseFloat(s.customCost);
                      const net = cost * (1 - s.discount / 100);
                      return (
                        <Row key={s.id} label={s.name} value={R(net)}
                          sub={s.discount > 0 ? `List: ${R(cost)} · Discount: ${pct(s.discount)} · Saving: ${R(cost - net)}` : undefined} />
                      );
                    })}
                    <Row label="Supplier Total (net after discounts)" value={R(calc.supplierTotal)} bold />
                  </>
                )}
              </div>

              {/* Markup */}
              <div style={{ marginBottom: 4, marginTop: 16 }}>
                <div style={{ fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase", color: C.green, fontFamily: "'DM Mono', monospace", marginBottom: 6 }}>Your Margin</div>
                <Row label={`Markup (${pct(markupPct)} on construction)`} value={R(calc.markupAmt)} highlight bold />
                <Row label="Construction + Markup" value={R(calc.subtotalAfterMarkup)} bold />
              </div>

              {/* Professional Fees */}
              <div style={{ marginBottom: 4, marginTop: 16 }}>
                <div style={{ fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase", color: C.muted, fontFamily: "'DM Mono', monospace", marginBottom: 6 }}>Professional Fees</div>
                <Row label={`SACAP Architectural Fee (${complexity} complexity)`} value={R(calc.sacapFee)} sub={`SACAP BN672 2024 · ${pct(calc.sacapFee / calc.effectiveConstruction * 100)} of construction`} />
                {pmMonths > 0 && <Row label={`Project Management (${pmMonths} months × ${R(pmMonthly)}/mo)`} value={R(calc.pmTotal)} />}
                {calc.paTotal > 0 && <Row label="Principal Agent Fixed Fee" value={R(calc.paTotal)} />}
                <Row label="Professional Fees Total" value={R(calc.proFeesTotal)} bold />
              </div>

              {/* Council */}
              <div style={{ marginBottom: 4, marginTop: 16 }}>
                <div style={{ fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase", color: C.muted, fontFamily: "'DM Mono', monospace", marginBottom: 6 }}>Statutory</div>
                <Row label={`Council Submission Fee (${pct(councilPct)} of construction — CoCT)`} value={R(calc.councilFee)} />
              </div>

              {/* Totals */}
              <div style={{ marginTop: 20, padding: 16, background: `${C.green}08`, border: `1px solid ${C.green}30`, borderRadius: 8 }}>
                <Row label="Subtotal (excl. VAT)" value={R(calc.grandSubtotal)} bold />
                {includeVAT && <Row label="VAT (15%)" value={R(calc.vatAmt)} />}
                <div style={{ display: "flex", justifyContent: "space-between", padding: "12px 0 0", marginTop: 8, borderTop: `2px solid ${C.green}40` }}>
                  <span style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: C.green }}>Total Project Cost</span>
                  <span style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 24, fontWeight: 700, color: C.green }}>{R(calc.grandTotal)}</span>
                </div>
                <div style={{ fontSize: 11, color: C.light, fontFamily: "'DM Mono', monospace", marginTop: 6 }}>
                  {R(calc.grandTotal / area)}/m² all-in · Your margin: {R(calc.markupAmt)} ({pct(markupPct)})
                </div>
              </div>

              {/* VAT toggle */}
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 14, cursor: "pointer" }} onClick={() => setIncludeVAT(!includeVAT)}>
                <div style={{ width: 36, height: 20, borderRadius: 10, background: includeVAT ? C.green : C.border, position: "relative", transition: "background 0.2s" }}>
                  <div style={{ position: "absolute", top: 2, left: includeVAT ? 18 : 2, width: 16, height: 16, borderRadius: "50%", background: "#fff", transition: "left 0.2s" }} />
                </div>
                <span style={{ fontSize: 12, color: C.muted, fontFamily: "'DM Mono', monospace" }}>Include VAT (15%)</span>
              </div>
            </Section>

            {/* Summary Card */}
            <Section title="Quote Summary" badge="ready to send">
              <div style={{ fontSize: 12, color: C.muted, fontFamily: "'DM Mono', monospace", lineHeight: 1.8 }}>
                <div><strong style={{ color: C.text }}>{projectName}</strong>{clientName ? ` — ${clientName}` : ""}</div>
                <div>Area: {area}m² · {AECOM_RATES[rateKey]?.label}</div>
                <div>Location: {location === "western_cape" ? "Western Cape" : location === "gauteng" ? "Gauteng" : "Other Province"}</div>
                <div>Rate used: {R(calc.baseRate)}/m² ({useRange} of AECOM range)</div>
                <div style={{ marginTop: 8, paddingTop: 8, borderTop: `1px solid ${C.border}` }}>
                  <div>Construction: {R(calc.effectiveConstruction)}</div>
                  <div>Your markup ({pct(markupPct)}): {R(calc.markupAmt)}</div>
                  <div>Professional fees: {R(calc.proFeesTotal)}</div>
                  <div>Council: {R(calc.councilFee)}</div>
                  {includeVAT && <div>VAT: {R(calc.vatAmt)}</div>}
                </div>
                <div style={{ marginTop: 10, padding: "10px 12px", background: C.highlight, borderRadius: 6, border: `1px solid ${C.amber}30` }}>
                  <strong style={{ color: C.green, fontSize: 16, fontFamily: "'Cormorant Garamond', serif" }}>
                    Total: {R(calc.grandTotal)} {includeVAT ? "incl. VAT" : "excl. VAT"}
                  </strong>
                </div>
                <div style={{ fontSize: 10, marginTop: 8, color: C.light }}>
                  Rates: AECOM Africa Cost Guide 2025/26 · SACAP BN672 (2024) · CoCT tariff 2025/26<br/>
                  Excludes site development, escalation, specialist consultants not listed above.
                </div>
              </div>

              <button
                onClick={() => {
                  const text = `VULA QS ESTIMATE\n${projectName}${clientName ? ` — ${clientName}` : ""}\n${"─".repeat(40)}\nArea: ${area}m²\nBuild type: ${AECOM_RATES[rateKey]?.label}\nRate: ${R(calc.baseRate)}/m²\n\nConstruction: ${R(calc.effectiveConstruction)}\nMarkup (${pct(markupPct)}): ${R(calc.markupAmt)}\nSACAPFee: ${R(calc.sacapFee)}\nPM (${pmMonths}mo): ${R(calc.pmTotal)}\nPA fee: ${R(calc.paTotal)}\nCouncil: ${R(calc.councilFee)}\n${includeVAT ? `VAT: ${R(calc.vatAmt)}\n` : ""}${"─".repeat(40)}\nTOTAL: ${R(calc.grandTotal)} ${includeVAT ? "incl. VAT" : "excl. VAT"}\n\nRates: AECOM 2025/26 · SACAP BN672 · CoCT tariff`;
                  navigator.clipboard?.writeText(text).catch(() => {});
                  alert("Copied to clipboard — paste into your quote document.");
                }}
                style={{ marginTop: 14, width: "100%", padding: "11px 0", background: C.green, color: "#fff", border: "none", borderRadius: 7, fontSize: 13, fontWeight: 600, cursor: "pointer", fontFamily: "'DM Sans', sans-serif" }}
              >
                Copy Quote Summary
              </button>
            </Section>

            {/* Disclaimer */}
            <div style={{ padding: "12px 16px", background: "#FFF8EE", border: `1px solid ${C.amber}30`, borderRadius: 8, fontSize: 11, color: C.light, fontFamily: "'DM Mono', monospace", lineHeight: 1.6 }}>
              ⚠️ This is a preliminary estimate for budgeting purposes. Rates sourced from AECOM Africa Cost Guide 2025/26 and SACAP Board Notice 672 (2024). Final costs must be confirmed by a registered Quantity Surveyor. VAT, escalation, and site-specific conditions may alter the final figure materially.
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
