import { useState, useRef, useCallback, useMemo, useEffect } from "react";

// Blueprint engineering aesthetic — dark navy, cyan grid lines, white annotations
const C = {
  bg:       "#0A0E1A",
  navy:     "#0D1426",
  surface:  "#111828",
  card:     "#161E32",
  border:   "#1E2D4A",
  cyan:     "#00C8E0",
  cyanDim:  "#007A8A",
  green:    "#2ECC71",
  amber:    "#F39C12",
  red:      "#E74C3C",
  violet:   "#8E6FD8",
  text:     "#DCE4F0",
  muted:    "#5A7090",
  dim:      "#2A3D5A",
  white:    "#FFFFFF",
};

const TRADES = {
  demolition:   { label: "Demolition",              color: "#E74C3C", icon: "🔨", abbr: "DEM" },
  concrete:     { label: "Concrete & Structure",     color: "#8B6914", icon: "🏗️", abbr: "STR" },
  brickwork:    { label: "Brickwork & Masonry",      color: "#C0392B", icon: "🧱", abbr: "BRK" },
  drywall:      { label: "Drywall & Ceilings",       color: "#3498DB", icon: "📐", abbr: "DRY" },
  flooring:     { label: "Flooring & Screed",        color: "#2ECC71", icon: "▪️", abbr: "FLR" },
  joinery:      { label: "Joinery & Doors",          color: "#8E6FD8", icon: "🚪", abbr: "JOI" },
  glazing:      { label: "Glazing & Shopfront",      color: "#00BCD4", icon: "🪟", abbr: "GLZ" },
  wet_works:    { label: "Wet Works & Waterproofing",color: "#1ABC9C", icon: "💧", abbr: "WET" },
  plumbing:     { label: "Plumbing & Drainage",      color: "#0984E3", icon: "🔧", abbr: "PLB" },
  electrical:   { label: "Electrical",               color: "#F39C12", icon: "⚡", abbr: "ELE" },
  hvac:         { label: "HVAC & Ventilation",       color: "#6C5CE7", icon: "🌀", abbr: "HVA" },
  fire:         { label: "Fire Protection",          color: "#FF6B6B", icon: "🔥", abbr: "FIR" },
  tiling:       { label: "Tiling",                   color: "#00B894", icon: "⬛", abbr: "TIL" },
  painting:     { label: "Painting & Finishes",      color: "#FDCB6E", icon: "🎨", abbr: "PNT" },
  external:     { label: "External Works",           color: "#55EFC4", icon: "🌿", abbr: "EXT" },
};

// Demo data — shown when no real job is loaded
const MOCK_TAKEOFF = {
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
    { id: "d1", trade: "demolition", description: "Strip out existing fit-out — full scope", unit: "m²", qty: 597, rate: 0, rateRange: [120, 280], source: "AECOM 2025/26", confidence: 95 },
    { id: "s1", trade: "concrete", description: "Mezzanine concrete slab — 150mm thk", unit: "m²", qty: 84, rate: 0, rateRange: [1850, 2600], source: "AfriSam 2025", confidence: 90 },
    { id: "s2", trade: "concrete", description: "Steel mezzanine structure (erected)", unit: "tonne", qty: 3.2, rate: 0, rateRange: [38000, 55000], source: "AECOM 2025/26", confidence: 75, notes: "Estimate pending structural engineer BOQ" },
    { id: "dry1", trade: "drywall", description: "Partition walls — single skin 70mm", unit: "m²", qty: 312, rate: 0, rateRange: [420, 680], source: "Gyproc 2025", confidence: 92 },
    { id: "dry2", trade: "drywall", description: "Partition walls — acoustic double skin", unit: "m²", qty: 86, rate: 0, rateRange: [650, 980], source: "AECOM 2025/26", confidence: 88, notes: "Studio and boardroom boundaries" },
    { id: "dry3", trade: "drywall", description: "Suspended ceiling — standard 600×600 grid", unit: "m²", qty: 298, rate: 0, rateRange: [380, 580], source: "Sky Ceilings 2025", confidence: 95 },
    { id: "dry4", trade: "drywall", description: "Suspended ceiling — acoustic tile", unit: "m²", qty: 196, rate: 0, rateRange: [680, 1200], source: "AECOM 2025/26", confidence: 88 },
    { id: "fl1", trade: "flooring", description: "Sand/cement screed 50mm — general", unit: "m²", qty: 485, rate: 0, rateRange: [95, 160], source: "AECOM 2025/26", confidence: 95 },
    { id: "fl2", trade: "flooring", description: "LVT / Vinyl Plank — office & kitchen", unit: "m²", qty: 271, rate: 0, rateRange: [280, 520], source: "Polyflor SA 2025", confidence: 90 },
    { id: "fl3", trade: "flooring", description: "Commercial carpet — boardroom & mezzanine", unit: "m²", qty: 120, rate: 0, rateRange: [220, 480], source: "Belgotex 2025", confidence: 88 },
    { id: "til1", trade: "tiling", description: "Porcelain floor tiles 600×600 — reception", unit: "m²", qty: 32, rate: 0, rateRange: [380, 650], source: "Italtile 2025", confidence: 95 },
    { id: "til2", trade: "tiling", description: "Porcelain floor tiles 600×600 — ablutions", unit: "m²", qty: 44, rate: 0, rateRange: [380, 650], source: "Italtile 2025", confidence: 95 },
    { id: "joi1", trade: "joinery", description: "Solid core door — Type A (internal office)", unit: "no", qty: 14, rate: 0, rateRange: [3500, 6500], source: "AECOM 2025/26", confidence: 95 },
    { id: "joi2", trade: "joinery", description: "Fire door 60-min rated — stairway & studio", unit: "no", qty: 4, rate: 0, rateRange: [12000, 22000], source: "AECOM 2025/26", confidence: 95 },
    { id: "plb1", trade: "plumbing", description: "Bathroom fit-out — male (3 WC, 3 URI, 3 WHB)", unit: "no", qty: 1, rate: 0, rateRange: [45000, 85000], source: "AECOM 2025/26", confidence: 85 },
    { id: "plb2", trade: "plumbing", description: "Bathroom fit-out — female (3 WC, 3 WHB)", unit: "no", qty: 1, rate: 0, rateRange: [35000, 65000], source: "AECOM 2025/26", confidence: 85 },
    { id: "ele1", trade: "electrical", description: "DB board — 3-phase (main distribution)", unit: "no", qty: 1, rate: 0, rateRange: [12000, 22000], source: "AECOM 2025/26", confidence: 88 },
    { id: "ele2", trade: "electrical", description: "Electrical fitout — office areas", unit: "m²", qty: 597, rate: 0, rateRange: [450, 900], source: "AECOM 2025/26", confidence: 85 },
    { id: "hva1", trade: "hvac", description: "VRF HVAC system — office areas", unit: "kW", qty: 45, rate: 0, rateRange: [5500, 9000], source: "ExtraAir 2025", confidence: 88 },
    { id: "fir1", trade: "fire", description: "Sprinkler system — office & mezzanine", unit: "m²", qty: 513, rate: 0, rateRange: [380, 650], source: "AECOM 2025/26", confidence: 88 },
    { id: "pnt1", trade: "painting", description: "Interior paint — 2 coats walls & ceilings", unit: "m²", qty: 1840, rate: 0, rateRange: [65, 130], source: "Plascon / Dulux 2025", confidence: 95 },
  ],
};

const R = n => `R ${Math.round(n).toLocaleString("en-ZA")}`;
const pct = n => `${Number(n).toFixed(0)}%`;
const mid = item => (item.rateRange[0] + item.rateRange[1]) / 2;
const net = (item, markup) => mid(item) * item.qty * (1 + markup / 100);

const mono = { fontFamily: "'DM Mono', monospace" };
const sans = { fontFamily: "'DM Sans', sans-serif" };

function ConfidenceDot({ value }) {
  const color = value >= 90 ? C.green : value >= 75 ? C.amber : C.red;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
      <div style={{ width: 7, height: 7, borderRadius: "50%", background: color }} />
      <span style={{ fontSize: 10, color, ...mono }}>{value}%</span>
    </div>
  );
}

function TradeChip({ trade }) {
  const t = TRADES[trade] || { label: trade, color: C.muted, icon: "▪", abbr: trade.slice(0,3).toUpperCase() };
  return (
    <span style={{ padding: "2px 7px", borderRadius: 3, background: `${t.color}20`, color: t.color, fontSize: 9, ...mono, whiteSpace: "nowrap" }}>
      {t.icon} {t.abbr}
    </span>
  );
}

function SummaryCard({ label, value, sub, color = C.cyan }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "14px 16px" }}>
      <div style={{ fontSize: 9, color: C.muted, ...mono, letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 8 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color, ...mono }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: C.muted, ...mono, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

function PlanViewer({ drawings, areas, rooms: _rooms, onUpload }) {
  return (
    <div style={{ background: C.navy, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden" }}>
      <div style={{ background: C.card, borderBottom: `1px solid ${C.border}`, padding: "10px 16px", display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{ fontSize: 11, color: C.cyan, ...mono, letterSpacing: "0.1em" }}>DRAWING REGISTER</span>
        <button onClick={onUpload} style={{ marginLeft: "auto", padding: "5px 14px", background: C.cyan, border: "none", borderRadius: 4, color: C.navy, fontSize: 11, fontWeight: 700, cursor: "pointer", ...mono }}>
          + Upload Plans
        </button>
      </div>
      <div style={{ padding: 16, backgroundImage: `linear-gradient(${C.dim}30 1px, transparent 1px), linear-gradient(90deg, ${C.dim}30 1px, transparent 1px)`, backgroundSize: "24px 24px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 8 }}>
          {drawings.map(d => (
            <div key={d.sheet} style={{ padding: "10px 12px", background: `${C.navy}CC`, border: `1px solid ${d.status === "read" ? C.cyanDim : C.border}`, borderRadius: 5 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: C.cyan, ...mono }}>{d.sheet}</span>
                <span style={{ fontSize: 9, color: d.status === "read" ? C.green : C.amber, ...mono }}>{d.status === "read" ? "✓ READ" : "PENDING"}</span>
              </div>
              <div style={{ fontSize: 11, color: C.text, ...sans, marginBottom: 2 }}>{d.title}</div>
              <div style={{ fontSize: 9, color: C.muted, ...mono }}>Scale: {d.scale}</div>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 20, marginTop: 14, padding: "10px 12px", background: `${C.navy}CC`, border: `1px solid ${C.border}`, borderRadius: 5 }}>
          {[
            { label: "Gross Floor Area", value: `${areas.gross} m²` },
            { label: "Net Usable Area", value: `${areas.net} m²` },
            { label: "Mezzanine", value: `${areas.mezzanine} m²` },
          ].map(s => (
            <div key={s.label}>
              <div style={{ fontSize: 9, color: C.muted, ...mono, textTransform: "uppercase" }}>{s.label}</div>
              <div style={{ fontSize: 13, fontWeight: 700, color: C.cyan, ...mono }}>{s.value}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function RoomSchedule({ rooms }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden" }}>
      <div style={{ padding: "10px 16px", borderBottom: `1px solid ${C.border}`, background: C.navy }}>
        <span style={{ fontSize: 11, color: C.cyan, ...mono, letterSpacing: "0.1em" }}>ROOM SCHEDULE — AI EXTRACTED</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ background: `${C.navy}80` }}>
              {["Room", "Area m²", "Floor", "Ceiling", "Walls"].map(h => (
                <th key={h} style={{ padding: "7px 12px", textAlign: "left", fontSize: 9, color: C.muted, ...mono, letterSpacing: "0.08em", textTransform: "uppercase", borderBottom: `1px solid ${C.border}` }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rooms.map((r, i) => (
              <tr key={i} style={{ borderBottom: `1px solid ${C.border}` }}>
                <td style={{ padding: "8px 12px", fontSize: 12, color: C.text, ...sans }}>{r.name}</td>
                <td style={{ padding: "8px 12px", fontSize: 12, color: C.cyan, ...mono, fontWeight: 600 }}>{r.area}</td>
                <td style={{ padding: "8px 12px", fontSize: 11, color: C.muted, ...mono }}>{r.floor.replace(/_/g," ")}</td>
                <td style={{ padding: "8px 12px", fontSize: 11, color: C.muted, ...mono }}>{r.ceiling.replace(/_/g," ")}</td>
                <td style={{ padding: "8px 12px", fontSize: 11, color: C.muted, ...mono }}>{r.walls.replace(/_/g," ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function OrderSheet({ items, markup, selectedTrades, projectName }) {
  const orderItems = items.filter(i => selectedTrades.includes(i.trade));
  const byTrade = Object.entries(TRADES).map(([key, t]) => ({
    key, trade: t,
    items: orderItems.filter(i => i.trade === key),
  })).filter(g => g.items.length > 0);

  const handleCopy = () => {
    const lines = byTrade.map(g => {
      const tradeLines = g.items.map(i =>
        `${i.description}\t${i.qty}\t${i.unit}\t${R(mid(i))}/unit\t${R(net(i, markup))}`
      ).join("\n");
      return `\n=== ${g.trade.label} ===\n${tradeLines}`;
    }).join("\n");
    navigator.clipboard?.writeText(`VULA TAKEOFF ORDER — ${projectName}\n${lines}`).catch(() => {});
    alert("Copied to clipboard — paste into your order sheet or email.");
  };

  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden" }}>
      <div style={{ padding: "10px 16px", borderBottom: `1px solid ${C.border}`, background: C.navy, display: "flex", alignItems: "center" }}>
        <span style={{ fontSize: 11, color: C.cyan, ...mono, letterSpacing: "0.1em" }}>MATERIAL ORDER LIST</span>
        <button onClick={handleCopy} style={{ marginLeft: "auto", padding: "4px 12px", background: C.cyan, border: "none", borderRadius: 4, color: C.navy, fontSize: 10, fontWeight: 700, cursor: "pointer", ...mono }}>
          Copy Order Sheet
        </button>
      </div>
      <div style={{ padding: 16 }}>
        {byTrade.map(g => (
          <div key={g.key} style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, paddingBottom: 6, borderBottom: `1px solid ${C.border}` }}>
              <span style={{ fontSize: 13, color: g.trade.color, fontWeight: 700, ...sans }}>{g.trade.icon} {g.trade.label}</span>
              <span style={{ fontSize: 11, color: C.muted, ...mono }}>{g.items.length} items · {R(g.items.reduce((s, i) => s + net(i, markup), 0))}</span>
            </div>
            {g.items.map(item => (
              <div key={item.id} style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "6px 0", borderBottom: `1px solid ${C.dim}` }}>
                <div style={{ flex: 1, fontSize: 12, color: C.text, ...sans }}>{item.description}</div>
                <div style={{ textAlign: "right", minWidth: 80, fontSize: 12, color: C.cyan, ...mono, fontWeight: 600 }}>{item.qty} {item.unit}</div>
                <div style={{ textAlign: "right", minWidth: 100, fontSize: 12, color: C.amber, ...mono, fontWeight: 700 }}>{R(net(item, markup))}</div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function RatesView({ apiHost }) {
  const [rates, setRates] = useState([]);
  const [loading, setLoading] = useState(false);
  const [changedOnly, setChangedOnly] = useState(false);
  const [error, setError] = useState(null);

  const fetchRates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const url = `${apiHost}/takeoff/rates${changedOnly ? "?changed_only=true" : ""}`;
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

  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden" }}>
      <div style={{ padding: "10px 16px", borderBottom: `1px solid ${C.border}`, background: C.navy, display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{ fontSize: 11, color: C.cyan, ...mono, letterSpacing: "0.1em" }}>SA CONSTRUCTION RATES — LIVE DB</span>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, color: C.muted, ...mono, marginLeft: "auto", cursor: "pointer" }}>
          <input type="checkbox" checked={changedOnly} onChange={e => setChangedOnly(e.target.checked)} />
          Changed only
        </label>
        <button onClick={fetchRates} disabled={loading} style={{ padding: "4px 12px", background: loading ? C.dim : C.cyanDim, border: "none", borderRadius: 4, color: C.cyan, fontSize: 10, cursor: "pointer", ...mono }}>
          {loading ? "…" : "Refresh"}
        </button>
      </div>
      {error && <div style={{ padding: 16, color: C.amber, fontSize: 12, ...mono }}>⚠ {error} — is the API running?</div>}
      {!error && rates.length === 0 && !loading && (
        <div style={{ padding: 24, textAlign: "center", color: C.muted, fontSize: 12, ...mono }}>
          No rates in database yet. Run <code style={{ color: C.cyan }}>POST /takeoff/rates/update</code> to scrape.
        </div>
      )}
      {rates.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: `${C.navy}80` }}>
                {["Trade", "Item", "Unit", "Low", "Mid", "High", "Source", "Updated"].map(h => (
                  <th key={h} style={{ padding: "7px 10px", textAlign: h.includes("Low") || h.includes("Mid") || h.includes("High") ? "right" : "left", fontSize: 9, color: C.muted, ...mono, textTransform: "uppercase", letterSpacing: "0.07em", borderBottom: `1px solid ${C.border}` }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rates.map((r, i) => (
                <tr key={i} style={{ borderBottom: `1px solid ${C.dim}` }}>
                  <td style={{ padding: "7px 10px" }}><TradeChip trade={r.trade || "concrete"} /></td>
                  <td style={{ padding: "7px 10px", fontSize: 12, color: C.text, ...sans, maxWidth: 240 }}>{r.item || r.description}</td>
                  <td style={{ padding: "7px 10px", fontSize: 10, color: C.muted, ...mono }}>{r.unit}</td>
                  <td style={{ padding: "7px 10px", textAlign: "right", fontSize: 11, color: C.muted, ...mono }}>{r.low ? R(r.low) : "—"}</td>
                  <td style={{ padding: "7px 10px", textAlign: "right", fontSize: 12, color: C.cyan, ...mono, fontWeight: 600 }}>{r.mid ? R(r.mid) : (r.rate ? R(r.rate) : "—")}</td>
                  <td style={{ padding: "7px 10px", textAlign: "right", fontSize: 11, color: C.muted, ...mono }}>{r.high ? R(r.high) : "—"}</td>
                  <td style={{ padding: "7px 10px", fontSize: 10, color: C.muted, ...mono }}>{r.source || "—"}</td>
                  <td style={{ padding: "7px 10px", fontSize: 10, color: C.muted, ...mono }}>{(r.updated_at || r.scraped_at || "")?.slice(0, 10) || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export default function VulaTakeoff() {
  const [tab, setTab] = useState("boq");
  const [markup, setMarkup] = useState(15);
  const [selectedTrades, setSelectedTrades] = useState(Object.keys(TRADES));

  // Per-item rate overrides (keyed by item id)
  const [itemRates, setItemRates] = useState(() =>
    Object.fromEntries(MOCK_TAKEOFF.items.map(i => [i.id, String(Math.round(mid(i)))]))
  );

  // Live API state
  const [apiHost] = useState(() => localStorage.getItem("vula_host") || "http://localhost:7438");
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
      fd.append("tenant_id", "dashboard");
      fd.append("markup", String(markup));
      const resp = await fetch(`${apiHost}/takeoff/upload`, { method: "POST", body: fd });
      if (!resp.ok) throw new Error(`Upload failed: HTTP ${resp.status}`);
      const data = await resp.json();
      setLiveJob({ job_id: data.job_id, status: "queued", filename: data.filename, boq: null });
    } catch (err) {
      alert(`Upload error: ${err.message}\nMake sure the Vula API is running on ${apiHost}`);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }, [apiHost, markup]);

  const handleUploadClick = useCallback(() => {
    fileRef.current?.click();
  }, []);

  // ── Poll job status ────────────────────────────────────────────────────────
  useEffect(() => {
    if (!liveJob?.job_id || liveJob.status === "complete" || liveJob.status === "failed") {
      clearInterval(pollRef.current);
      return;
    }
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const resp = await fetch(`${apiHost}/takeoff/${liveJob.job_id}`);
        const data = await resp.json();
        if (data.status === "complete") {
          const boqResp = await fetch(`${apiHost}/takeoff/${liveJob.job_id}/boq`);
          const boqData = await boqResp.json();
          setLiveJob(j => ({ ...j, status: "complete", boq: boqData.boq }));
        } else if (data.status === "failed") {
          setLiveJob(j => ({ ...j, status: "failed", error: data.error }));
        } else {
          setLiveJob(j => ({ ...j, status: data.status }));
        }
      } catch { /* network error — keep polling */ }
    }, 3000);
    return () => clearInterval(pollRef.current);
  }, [liveJob?.job_id, liveJob?.status, apiHost]);

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
    complete:        "Complete",
    failed:          "Failed",
  };

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500;700&family=DM+Sans:wght@400;500;600;700&display=swap');
        *{box-sizing:border-box;margin:0;padding:0;}
        body{background:${C.bg};}
        input[type=number]:focus,input[type=text]:focus,select:focus{border-color:${C.cyan}!important;}
        ::-webkit-scrollbar{width:5px;height:5px;}
        ::-webkit-scrollbar-track{background:${C.navy};}
        ::-webkit-scrollbar-thumb{background:${C.border};border-radius:3px;}
        table{border-collapse:collapse;width:100%;}
        th{white-space:nowrap;}
        tr:hover td{background:${C.navy}40;}
      `}</style>

      {/* Hidden file input */}
      <input
        ref={fileRef}
        type="file"
        accept=".pdf,.dxf,.dwg,.png,.jpg,.jpeg,.tiff"
        style={{ display: "none" }}
        onChange={handleFileSelected}
      />

      <div style={{ minHeight: "100vh", background: C.bg, color: C.text, ...sans }}>

        {/* Header */}
        <div style={{ background: C.navy, borderBottom: `1px solid ${C.border}`, padding: "12px 24px", display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ padding: "4px 10px", background: `${C.cyan}20`, border: `1px solid ${C.cyanDim}`, borderRadius: 4 }}>
              <span style={{ fontSize: 12, color: C.cyan, ...mono, fontWeight: 700, letterSpacing: "0.08em" }}>VULA TAKEOFF</span>
            </div>
            <div style={{ fontSize: 13, color: C.text, fontWeight: 600 }}>{projectName}</div>
            {!isLive && (
              <span style={{ fontSize: 9, padding: "2px 7px", background: `${C.amber}20`, color: C.amber, borderRadius: 3, ...mono, letterSpacing: "0.05em" }}>DEMO DATA</span>
            )}
            {isLive && (
              <span style={{ fontSize: 9, padding: "2px 7px", background: `${C.green}20`, color: C.green, borderRadius: 3, ...mono, letterSpacing: "0.05em" }}>LIVE</span>
            )}
          </div>

          {/* Markup slider */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginLeft: "auto" }}>
            <span style={{ fontSize: 10, color: C.muted, ...mono }}>MARKUP</span>
            <input type="range" min={0} max={40} step={0.5} value={markup}
              onChange={e => setMarkup(Number(e.target.value))}
              style={{ width: 120, accentColor: C.amber }} />
            <span style={{ fontSize: 15, fontWeight: 700, color: C.amber, ...mono, minWidth: 42 }}>{pct(markup)}</span>
          </div>

          {/* Upload */}
          <button onClick={handleUploadClick} disabled={uploading} style={{
            padding: "7px 18px",
            background: uploading ? C.cyanDim : C.cyan,
            border: "none", borderRadius: 5, color: C.navy, fontSize: 12, fontWeight: 700,
            cursor: uploading ? "default" : "pointer", ...mono,
          }}>
            {uploading ? "⏳ Uploading…" : "📐 Upload Plans"}
          </button>
        </div>

        {/* Job status banner */}
        {liveJob && liveJob.status !== "complete" && (
          <div style={{ background: liveJob.status === "failed" ? `${C.red}20` : `${C.cyan}15`, borderBottom: `1px solid ${liveJob.status === "failed" ? C.red : C.cyanDim}`, padding: "10px 24px", display: "flex", alignItems: "center", gap: 12 }}>
            {liveJob.status !== "failed" && (
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: C.cyan, boxShadow: `0 0 6px ${C.cyan}`, animation: "pulse 1.5s infinite" }} />
            )}
            <span style={{ fontSize: 12, color: liveJob.status === "failed" ? C.red : C.cyan, ...mono }}>
              {liveJob.filename} — {statusLabel[liveJob.status] || liveJob.status}
              {liveJob.error && ` — ${liveJob.error}`}
            </span>
            {liveJob.status === "failed" && (
              <button onClick={() => setLiveJob(null)} style={{ marginLeft: "auto", fontSize: 10, color: C.muted, background: "none", border: "none", cursor: "pointer", ...mono }}>Dismiss</button>
            )}
          </div>
        )}

        <div style={{ maxWidth: 1400, margin: "0 auto", padding: "20px" }}>

          {/* Summary row */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12, marginBottom: 20 }}>
            <SummaryCard label="Total Project Cost" value={R(totals.total)} sub={`incl. ${pct(markup)} markup`} color={C.cyan} />
            <SummaryCard label="Your Profit Margin" value={R(totals.profit)} sub={pct(markup) + " on net cost"} color={C.amber} />
            <SummaryCard label="Line Items" value={`${filtered.length}`} sub={`${selectedTrades.length} trades active`} color={C.text} />
            <SummaryCard label="Cost / m²" value={R(totals.total / (liveJob?.boq?.project?.gfa || MOCK_TAKEOFF.areas.gross))} sub="all-in incl. markup" color={C.violet} />
            <SummaryCard label="Net Cost" value={R(totals.total / (1 + markup / 100))} sub="before markup" color={C.muted} />
          </div>

          {/* Trade filters */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 16 }}>
            <button onClick={() => setSelectedTrades(Object.keys(TRADES))} style={{ padding: "4px 10px", borderRadius: 4, border: `1px solid ${C.border}`, background: "transparent", color: C.muted, fontSize: 10, cursor: "pointer", ...mono }}>All</button>
            {Object.entries(TRADES).map(([key, t]) => (
              <button key={key} onClick={() => toggleTrade(key)} style={{
                padding: "4px 10px", borderRadius: 4,
                border: `1px solid ${selectedTrades.includes(key) ? t.color : C.border}`,
                background: selectedTrades.includes(key) ? `${t.color}15` : "transparent",
                color: selectedTrades.includes(key) ? t.color : C.muted,
                fontSize: 10, cursor: "pointer", ...mono,
              }}>
                {t.icon} {t.abbr}{totals.byTrade[key] ? ` ${R(totals.byTrade[key])}` : ""}
              </button>
            ))}
          </div>

          {/* Tabs */}
          <div style={{ display: "flex", gap: 2, marginBottom: 16, background: C.surface, padding: 3, borderRadius: 7, border: `1px solid ${C.border}`, width: "fit-content" }}>
            {TABS.map(t => (
              <button key={t.id} onClick={() => setTab(t.id)} style={{
                padding: "7px 18px", borderRadius: 5, border: "none",
                background: tab === t.id ? C.card : "transparent",
                color: tab === t.id ? C.cyan : C.muted,
                fontSize: 12, fontWeight: tab === t.id ? 600 : 400, cursor: "pointer",
                boxShadow: tab === t.id ? `0 0 0 1px ${C.border}` : "none",
                transition: "all 0.15s", ...sans,
              }}>{t.label}</button>
            ))}
          </div>

          {/* Tab content */}
          {tab === "plans" && (
            <PlanViewer
              drawings={liveJob?.project?.sheets ? [] : MOCK_TAKEOFF.drawings}
              areas={MOCK_TAKEOFF.areas}
              rooms={MOCK_TAKEOFF.rooms}
              onUpload={handleUploadClick}
            />
          )}
          {tab === "rooms" && <RoomSchedule rooms={MOCK_TAKEOFF.rooms} />}
          {tab === "rates" && <RatesView apiHost={apiHost} />}

          {tab === "boq" && (
            <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden" }}>
              <div style={{ padding: "10px 16px", borderBottom: `1px solid ${C.border}`, background: C.navy, display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 11, color: C.cyan, ...mono, letterSpacing: "0.1em" }}>
                  BILL OF QUANTITIES{isLive ? " — AI GENERATED FROM YOUR PLANS" : " — DEMO DATA"}
                </span>
                <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                  {isLive && liveJob?.job_id && (
                    <a
                      href={`${apiHost}/takeoff/${liveJob.job_id}/boq/excel`}
                      target="_blank" rel="noreferrer"
                      style={{ padding: "4px 12px", background: C.green, borderRadius: 4, color: C.navy, fontSize: 10, fontWeight: 700, textDecoration: "none", ...mono }}
                    >
                      ↓ Download Excel
                    </a>
                  )}
                  <button onClick={() => {
                    const lines = filtered.map(i => `${i.description}\t${i.qty}\t${i.unit}\t${R(parseFloat(itemRates[i.id]) || mid(i))}\t${R((parseFloat(itemRates[i.id]) || mid(i)) * i.qty * (1 + markup / 100))}`).join("\n");
                    navigator.clipboard?.writeText(`BOQ — ${projectName}\nDescription\tQty\tUnit\tRate\tTotal\n${lines}`).catch(() => {});
                    alert("Copied! Paste into Excel.");
                  }} style={{ padding: "4px 12px", background: C.cyan, border: "none", borderRadius: 4, color: C.navy, fontSize: 10, fontWeight: 700, cursor: "pointer", ...mono }}>
                    Copy TSV
                  </button>
                </div>
              </div>
              <div style={{ overflowX: "auto" }}>
                <table>
                  <thead>
                    <tr style={{ background: `${C.navy}80` }}>
                      {["Trade","Description","Qty","Unit","Rate Range","Your Rate","Line Total","Markup","Charge-out","Confidence"].map(h => (
                        <th key={h} style={{ padding: "8px 10px", textAlign: h.includes("Rate") || h.includes("Total") || h.includes("out") ? "right" : "left", fontSize: 9, color: C.muted, ...mono, letterSpacing: "0.07em", textTransform: "uppercase", borderBottom: `1px solid ${C.border}` }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map(item => {
                      const userRate = parseFloat(itemRates[item.id]) || mid(item);
                      const lineNet = userRate * item.qty;
                      const lineOut = lineNet * (1 + markup / 100);
                      const isOverMarket = userRate > item.rateRange[1] * 1.1;
                      return (
                        <tr key={item.id} style={{ borderBottom: `1px solid ${C.dim}` }}>
                          <td style={{ padding: "7px 10px" }}><TradeChip trade={item.trade} /></td>
                          <td style={{ padding: "7px 10px", maxWidth: 260 }}>
                            <div style={{ fontSize: 12, color: C.text, ...sans }}>{item.description}</div>
                            {item.notes && <div style={{ fontSize: 10, color: C.amber, ...mono, marginTop: 2 }}>⚠ {item.notes}</div>}
                          </td>
                          <td style={{ padding: "7px 10px", textAlign: "right", fontSize: 12, ...mono, color: C.text, fontWeight: 600 }}>{item.qty}</td>
                          <td style={{ padding: "7px 10px", fontSize: 10, ...mono, color: C.muted }}>{item.unit}</td>
                          <td style={{ padding: "7px 10px", textAlign: "right", fontSize: 10, ...mono, color: C.muted }}>
                            {R(item.rateRange[0])} – {R(item.rateRange[1])}
                          </td>
                          <td style={{ padding: "7px 8px" }}>
                            <input
                              type="number"
                              value={itemRates[item.id] || ""}
                              onChange={e => setItemRates(prev => ({ ...prev, [item.id]: e.target.value }))}
                              style={{ background: C.surface, border: `1px solid ${isOverMarket ? C.red : C.border}`, borderRadius: 4, color: isOverMarket ? C.red : C.text, fontSize: 11, ...mono, padding: "4px 7px", width: 90, textAlign: "right", outline: "none" }}
                            />
                          </td>
                          <td style={{ padding: "7px 10px", textAlign: "right", fontSize: 12, ...mono, color: C.text, fontWeight: 600 }}>{R(lineNet)}</td>
                          <td style={{ padding: "7px 10px", textAlign: "right", fontSize: 11, ...mono, color: C.amber }}>{pct(markup)}</td>
                          <td style={{ padding: "7px 10px", textAlign: "right", fontSize: 13, ...mono, color: C.cyan, fontWeight: 700 }}>{R(lineOut)}</td>
                          <td style={{ padding: "7px 12px" }}><ConfidenceDot value={item.confidence} /></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div style={{ padding: "14px 20px", borderTop: `2px solid ${C.border}`, display: "flex", justifyContent: "flex-end", gap: 40, background: C.navy }}>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 9, color: C.muted, ...mono, textTransform: "uppercase", marginBottom: 4 }}>Net Cost</div>
                  <div style={{ fontSize: 18, ...mono, fontWeight: 700, color: C.text }}>{R(totals.total / (1 + markup / 100))}</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 9, color: C.amber, ...mono, textTransform: "uppercase", marginBottom: 4 }}>Your Margin</div>
                  <div style={{ fontSize: 18, ...mono, fontWeight: 700, color: C.amber }}>{R(totals.profit)}</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 9, color: C.cyan, ...mono, textTransform: "uppercase", marginBottom: 4 }}>Total Quote to Client</div>
                  <div style={{ fontSize: 26, ...mono, fontWeight: 700, color: C.cyan }}>{R(totals.total)}</div>
                </div>
              </div>
            </div>
          )}

          {tab === "order" && (
            <OrderSheet items={filtered} markup={markup} selectedTrades={selectedTrades} projectName={projectName} />
          )}

          {/* Footer */}
          <div style={{ marginTop: 16, padding: "10px 14px", background: C.surface, border: `1px solid ${C.border}`, borderRadius: 6, display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: isLive ? C.green : C.amber }} />
            <span style={{ fontSize: 10, color: C.muted, ...mono }}>
              {isLive
                ? `Live BOQ from ${liveJob?.filename} · ${filtered.length} line items · Rates: Vula AI + SA construction data`
                : `Demo data — ${MOCK_TAKEOFF.drawings.length} drawing sheets · Rates: AECOM 2025/26 · Upload real plans to generate live BOQ`}
            </span>
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              {[
                { label: "High confidence (≥90%)", color: C.green },
                { label: "Review needed (75–89%)", color: C.amber },
                { label: "Specialist required (<75%)", color: C.red },
              ].map(l => (
                <div key={l.label} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 9, color: C.muted, ...mono }}>
                  <div style={{ width: 6, height: 6, borderRadius: "50%", background: l.color }} />{l.label}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
