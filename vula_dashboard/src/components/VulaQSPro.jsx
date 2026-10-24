import { MATERIAL_RATES, R, pct, useQSPro, useQuoteCompare } from "./estimating/useQSPro";
import QSProV2 from "./estimating/QSProV2";
import { sidePad } from "../lib/layout";

// ─── DESIGN ──────────────────────────────────────────────────────────────────
// Aesthetic: precision instrument — graph paper grid, monospace data, blueprint blue
// Tone: a QS's working tool — functional, dense, trustworthy
// Memorable detail: every number is live — change one thing, everything updates

const C = {
  bg: "#0F1117",
  surface: "#161820",
  card: "#1C1F2B",
  border: "#262A38",
  borderLight: "#2E3345",
  blue: "#4A8FD4",
  blueLight: "#6AAEE8",
  green: "#3DAA6B",
  greenDim: "#2D7A4E",
  amber: "#D4923A",
  red: "#D45A4A",
  text: "#E8EAF0",
  muted: "#7A8099",
  dim: "#6A7090",
  highlight: "#1E2438",
};




// ─── COMPONENTS ───────────────────────────────────────────────────────────────
const inp = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 5, color: C.text, fontSize: 12, fontFamily: "'DM Mono', monospace", padding: "7px 10px", width: "100%", outline: "none", boxSizing: "border-box" };
const lbl = { display: "block", fontSize: 9, letterSpacing: "0.12em", textTransform: "uppercase", color: C.muted, fontFamily: "'DM Mono', monospace", marginBottom: 4 };

function Tag({ children, color = C.blue }) {
  return <span style={{ padding: "2px 7px", borderRadius: 3, background: `color-mix(in srgb, ${color} 13%, transparent)`, color, fontSize: 9, fontFamily: "'DM Mono', monospace", letterSpacing: "0.08em" }}>{children}</span>;
}

function Card({ children, style }) {
  return <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, ...style }}>{children}</div>;
}

function CardHead({ title, right, tag }) {
  return (
    <div style={{ padding: "11px 16px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ fontSize: 11, fontWeight: 600, color: C.text, fontFamily: "'DM Mono', monospace", letterSpacing: "0.05em" }}>{title}</span>
      {tag && <Tag>{tag}</Tag>}
      {right && <div style={{ marginLeft: "auto" }}>{right}</div>}
    </div>
  );
}

// ─── LINE ITEM ROW ─────────────────────────────────────────────────────────────
function LineItem({ item, onUpdate, onRemove }) {
  const rate = MATERIAL_RATES[item.materialKey] || {};
  const marketMid = rate.low && rate.high ? (rate.low + rate.high) / 2 : 0;
  const listPrice = parseFloat(item.listPrice) || 0;
  const discountPct = parseFloat(item.discount) || 0;
  const qty = parseFloat(item.qty) || 0;
  const netUnit = listPrice * (1 - discountPct / 100);
  const lineTotal = netUnit * qty;
  const markupAmt = lineTotal * (parseFloat(item.markup) || 0) / 100;
  const chargeOut = lineTotal + markupAmt;

  // Variance vs market
  const variance = marketMid > 0 ? ((listPrice - marketMid) / marketMid) * 100 : null;
  const varColor = variance === null ? C.muted : variance > 15 ? C.red : variance < -10 ? C.green : C.amber;

  return (
    <tr style={{ borderBottom: `1px solid ${C.border}` }}>
      {/* Description */}
      <td style={{ padding: "8px 10px", minWidth: 160 }}>
        <div style={{ fontSize: 12, color: C.text, marginBottom: 3 }}>{item.description || rate.label || "Item"}</div>
        {(rate.source || item.source) && <div style={{ fontSize: 9, color: C.dim, fontFamily: "'DM Mono', monospace" }}>{rate.source || item.source}</div>}
      </td>
      {/* Market range */}
      <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "'DM Mono', monospace", fontSize: 11, color: C.muted }}>
        {rate.low ? `${R(rate.low)} – ${R(rate.high)}` : "—"}
        {rate.unit && <div style={{ fontSize: 9, color: C.dim }}>/{rate.unit}</div>}
      </td>
      {/* List price */}
      <td style={{ padding: "8px 8px" }}>
        <input type="number" value={item.listPrice} onChange={e => onUpdate("listPrice", e.target.value)} placeholder={marketMid ? Math.round(marketMid) : "0"} style={{ ...inp, width: 90, textAlign: "right" }} />
      </td>
      {/* Variance indicator */}
      <td style={{ padding: "8px 8px", textAlign: "center", fontFamily: "'DM Mono', monospace", fontSize: 11, color: varColor }}>
        {variance !== null && listPrice > 0 ? `${variance > 0 ? "+" : ""}${variance.toFixed(0)}%` : "—"}
      </td>
      {/* Discount */}
      <td style={{ padding: "8px 8px" }}>
        <input type="number" min={0} max={100} value={item.discount} onChange={e => onUpdate("discount", e.target.value)} style={{ ...inp, width: 60, textAlign: "right" }} />
      </td>
      {/* Net unit */}
      <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "'DM Mono', monospace", fontSize: 11, color: C.green }}>
        {listPrice > 0 ? R(netUnit) : "—"}
      </td>
      {/* Qty */}
      <td style={{ padding: "8px 8px" }}>
        <input type="number" value={item.qty} onChange={e => onUpdate("qty", e.target.value)} style={{ ...inp, width: 70, textAlign: "right" }} />
        {(rate.unit || item.unit) && <div style={{ fontSize: 9, color: C.dim, fontFamily: "'DM Mono', monospace", marginTop: 2 }}>{rate.unit || item.unit}</div>}
      </td>
      {/* Line total (net) */}
      <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "'DM Mono', monospace", fontSize: 12, color: C.text, fontWeight: 600 }}>
        {lineTotal > 0 ? R(lineTotal) : "—"}
      </td>
      {/* Markup */}
      <td style={{ padding: "8px 8px" }}>
        <input type="number" min={0} max={100} value={item.markup} onChange={e => onUpdate("markup", e.target.value)} style={{ ...inp, width: 60, textAlign: "right", color: C.amber }} />
      </td>
      {/* Charge-out */}
      <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "'DM Mono', monospace", fontSize: 12, color: C.amber, fontWeight: 700 }}>
        {chargeOut > 0 ? R(chargeOut) : "—"}
      </td>
      {/* Profit */}
      <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "'DM Mono', monospace", fontSize: 11, color: markupAmt > 0 ? C.green : C.dim }}>
        {markupAmt > 0 ? R(markupAmt) : "—"}
      </td>
      {/* Remove */}
      <td style={{ padding: "8px 6px", textAlign: "center" }}>
        <button onClick={onRemove} style={{ background: "none", border: "none", color: C.dim, cursor: "pointer", fontSize: 16, lineHeight: 1 }}>×</button>
      </td>
    </tr>
  );
}

// ─── QUOTE COMPARE ────────────────────────────────────────────────────────────
function QuoteCompare({ items }) {
  const { quotes, setQuotes, vulaCost, vulaCO, allQuotes, cheapest } = useQuoteCompare({ items });

  return (
    <Card style={{ marginBottom: 20 }}>
      <CardHead title="Quote Comparison" tag="vs market" />
      <div style={{ padding: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 16 }}>
          {quotes.map(q => (
            <div key={q.id}>
              <label style={lbl}>Supplier Name</label>
              <input value={q.supplier} onChange={e => setQuotes(prev => prev.map(x => x.id === q.id ? { ...x, supplier: e.target.value } : x))} style={{ ...inp, marginBottom: 6 }} placeholder="Supplier name" />
              <label style={lbl}>Their Total Quote (R)</label>
              <input type="number" value={q.total} onChange={e => setQuotes(prev => prev.map(x => x.id === q.id ? { ...x, total: e.target.value } : x))} style={inp} placeholder="0" />
            </div>
          ))}
        </div>

        {allQuotes.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {allQuotes.map((q, i) => {
              const width = cheapest > 0 ? (q.total / (allQuotes[allQuotes.length - 1]?.total || q.total)) * 100 : 50;
              const overBy = cheapest > 0 && i > 0 ? ((q.total - cheapest) / cheapest) * 100 : 0;
              return (
                <div key={q.id} style={{ position: "relative" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ fontSize: 12, color: q.isVula ? C.blue : C.text, fontFamily: "'DM Mono', monospace", fontWeight: q.isVula ? 700 : 400 }}>
                      {i === 0 && "★ "}{q.supplier}
                    </span>
                    <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                      {i > 0 && <span style={{ fontSize: 11, color: C.red, fontFamily: "'DM Mono', monospace" }}>+{overBy.toFixed(1)}% over cheapest</span>}
                      <span style={{ fontSize: 13, fontWeight: 700, color: q.isVula ? C.blue : i === 0 ? C.green : C.text, fontFamily: "'DM Mono', monospace" }}>{R(q.total)}</span>
                    </div>
                  </div>
                  <div style={{ height: 6, background: C.border, borderRadius: 3, overflow: "hidden" }}>
                    <div style={{ height: "100%", width: `${width}%`, background: q.isVula ? C.blue : i === 0 ? C.green : C.amber, borderRadius: 3, transition: "width 0.3s" }} />
                  </div>
                </div>
              );
            })}
            <div style={{ marginTop: 10, padding: "10px 14px", background: C.highlight, borderRadius: 6, fontSize: 11, color: C.muted, fontFamily: "'DM Mono', monospace" }}>
              Vula net cost: {R(vulaCost)} · Charge-out: {R(vulaCO)} · Your margin: {R(vulaCO - vulaCost)} ({vulaCost > 0 ? pct((vulaCO - vulaCost) / vulaCost * 100) : "0%"})
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

// ─── SUMMARY BAR ──────────────────────────────────────────────────────────────
function SummaryBar({ items, projectName, vatOn }) {
  const totalNet = items.reduce((s, i) => s + (parseFloat(i.listPrice) || 0) * (1 - (parseFloat(i.discount) || 0) / 100) * (parseFloat(i.qty) || 0), 0);
  const totalCO  = items.reduce((s, i) => {
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

  const stats = [
    { label: "Net Cost (after discounts)", value: R(totalNet), color: C.text },
    { label: "Supplier Savings", value: R(totalSaving), color: C.green, sub: "from discounts" },
    { label: "Your Margin", value: R(totalProfit), color: C.amber, sub: pct(avgMarkup) + " on cost" },
    { label: "Quote to Client", value: R(totalCO + vat), color: C.blue, sub: vatOn ? "incl. VAT" : "excl. VAT", big: true },
  ];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 20 }}>
      {stats.map(s => (
        <Card key={s.label} style={{ padding: 16 }}>
          <div style={{ fontSize: 9, color: C.muted, fontFamily: "'DM Mono', monospace", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 8 }}>{s.label}</div>
          <div style={{ fontSize: s.big ? 22 : 18, fontWeight: 700, color: s.color, fontFamily: "'DM Mono', monospace" }}>{s.value}</div>
          {s.sub && <div style={{ fontSize: 10, color: C.muted, fontFamily: "'DM Mono', monospace", marginTop: 4 }}>{s.sub}</div>}
        </Card>
      ))}
    </div>
  );
}

// ─── MAIN ─────────────────────────────────────────────────────────────────────

function VulaQSProV1({ tenantId }) {
  const { projectName, setProjectName, client, setClient, items, defaultMarkup, setDefaultMarkup, vatOn, setVatOn, filterCat, setFilterCat, search, setSearch, ownRates, addOwnRate, updateItem, removeItem, addFromLibrary, addBlank, applyDefaultMarkup, filteredLibrary, copyBoq } = useQSPro({ tenantId });

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600&display=swap');
        *{box-sizing:border-box;margin:0;padding:0;}
        body{background:${C.bg};}
        input:focus,select:focus{border-color:${C.blue}!important;}
        ::-webkit-scrollbar{width:5px;height:5px;}
        ::-webkit-scrollbar-track{background:${C.surface};}
        ::-webkit-scrollbar-thumb{background:${C.border};border-radius:3px;}
        table{border-collapse:collapse;width:100%;}
        th{text-align:left;font-family:'DM Mono',monospace;font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:${C.muted};padding:8px 10px;border-bottom:1px solid ${C.border};white-space:nowrap;}
        tr:hover td{background:${C.highlight};}
      `}</style>

      <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "'DM Sans', sans-serif" }}>

        {/* Header */}
        <div style={{ background: C.surface, borderBottom: `1px solid ${C.border}`, padding: "12px 24px", display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 14, fontWeight: 500, color: C.blue, letterSpacing: "0.05em" }}>VULA QS PRO</div>
          <div style={{ width: 1, height: 20, background: C.border }} />
          <input value={projectName} onChange={e => setProjectName(e.target.value)} style={{ ...inp, background: "transparent", border: "none", fontSize: 14, fontWeight: 600, color: C.text, width: 220 }} />
          <input value={client} onChange={e => setClient(e.target.value)} style={{ ...inp, background: "transparent", border: "none", fontSize: 12, color: C.muted, width: 160 }} placeholder="Client name" />
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 16 }}>
            <div style={{ fontSize: 10, color: C.dim, fontFamily: "'DM Mono', monospace" }}>Market reference: AECOM 2025/26 (static ranges){ownRates.length ? ` · ${ownRates.length} of your own rates (incl. learned from your documents)` : ""}</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }} onClick={() => setVatOn(!vatOn)}>
              <div style={{ width: 32, height: 17, borderRadius: 9, background: vatOn ? C.blue : C.border, position: "relative", transition: "background 0.2s" }}>
                <div style={{ position: "absolute", top: 2, left: vatOn ? 16 : 2, width: 13, height: 13, borderRadius: "50%", background: "#fff", transition: "left 0.2s" }} />
              </div>
              <span style={{ fontSize: 10, color: C.muted, fontFamily: "'DM Mono', monospace" }}>VAT</span>
            </div>
          </div>
        </div>

        <div style={{ maxWidth: 1400, margin: "0 auto", padding: `20px ${sidePad(20)}` }}>

          {/* Global markup */}
          <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 16, padding: "12px 16px", background: C.card, border: `1px solid ${C.border}`, borderRadius: 7 }}>
            <span style={{ fontSize: 11, color: C.muted, fontFamily: "'DM Mono', monospace" }}>DEFAULT MARKUP</span>
            <input type="range" min={0} max={50} step={0.5} value={defaultMarkup} onChange={e => setDefaultMarkup(Number(e.target.value))} style={{ width: 180, accentColor: C.amber }} />
            <span style={{ fontSize: 16, fontWeight: 700, color: C.amber, fontFamily: "'DM Mono', monospace", minWidth: 50 }}>{pct(defaultMarkup)}</span>
            <button onClick={applyDefaultMarkup} style={{ padding: "5px 14px", background: C.highlight, border: `1px solid ${C.border}`, borderRadius: 5, color: C.muted, fontSize: 11, cursor: "pointer", fontFamily: "'DM Mono', monospace" }}>
              Apply to all
            </button>
            <span style={{ fontSize: 10, color: C.dim, fontFamily: "'DM Mono', monospace", marginLeft: "auto" }}>
              Set your profit target here — apply to all line items at once, or override per line
            </span>
          </div>

          {/* Summary */}
          <SummaryBar items={items} projectName={projectName} vatOn={vatOn} />

          {/* Quote Compare */}
          <QuoteCompare items={items} />

          <div style={{ display: "grid", gridTemplateColumns: "280px 1fr", gap: 16 }}>

            {/* Material Library */}
            <Card style={{ height: "fit-content", position: "sticky", top: 16 }}>
              <CardHead title="Material Library" tag={`${Object.keys(MATERIAL_RATES).length} items`} />
              <div style={{ padding: "10px 12px", borderBottom: `1px solid ${C.border}` }}>
                <input value={search} onChange={e => setSearch(e.target.value)} style={{ ...inp, marginBottom: 8 }} placeholder="Search materials..." />
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {["All", "Structure", "Drywall & Ceilings", "Flooring", "Joinery & Doors", "MEP", "Finishes"].map(c => (
                    <button key={c} onClick={() => setFilterCat(c)} style={{ padding: "3px 8px", borderRadius: 4, border: "none", background: filterCat === c ? C.blue : C.border, color: filterCat === c ? "#fff" : C.muted, fontSize: 9, cursor: "pointer", fontFamily: "'DM Mono', monospace" }}>{c}</button>
                  ))}
                </div>
              </div>
              <div style={{ maxHeight: 520, overflowY: "auto" }}>
                {ownRates.filter(r => !search || `${r.description} ${r.code || ""}`.toLowerCase().includes(search.toLowerCase())).slice(0, search ? 200 : 40).map(r => (
                  <div key={`own-${r.code || r.description}-${r.rate}`} onClick={() => addOwnRate(r)} style={{ padding: "8px 12px", borderBottom: `1px solid ${C.border}`, cursor: "pointer", background: `color-mix(in srgb, ${C.green} 5%, transparent)` }}>
                    <div style={{ fontSize: 11, color: C.text, marginBottom: 3 }}>{r.description || r.code}</div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ fontSize: 9, color: C.dim, fontFamily: "'DM Mono', monospace" }} title={r.source || ""}>{r.learned ? `From your documents (${r.observations}) · ${r.unit}` : `Your rate · ${r.unit}`}</span>
                      <span style={{ fontSize: 10, color: C.green, fontFamily: "'DM Mono', monospace" }}>{R(r.rate || 0)}</span>
                    </div>
                  </div>
                ))}
                {filteredLibrary.map(([key, v]) => (
                  <div key={key} onClick={() => addFromLibrary(key)} style={{ padding: "8px 12px", borderBottom: `1px solid ${C.border}`, cursor: "pointer", transition: "background 0.1s" }}
                    onMouseEnter={e => e.currentTarget.style.background = C.highlight}
                    onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                    <div style={{ fontSize: 11, color: C.text, marginBottom: 3 }}>{v.label}</div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ fontSize: 9, color: C.dim, fontFamily: "'DM Mono', monospace" }}>{v.unit}</span>
                      <span style={{ fontSize: 10, color: C.blue, fontFamily: "'DM Mono', monospace" }}>{R(v.low)}–{R(v.high)}</span>
                    </div>
                  </div>
                ))}
              </div>
              <div style={{ padding: 10, borderTop: `1px solid ${C.border}` }}>
                <button onClick={addBlank} style={{ width: "100%", padding: "7px 0", background: "transparent", border: `1px dashed ${C.border}`, borderRadius: 5, color: C.muted, fontSize: 11, cursor: "pointer", fontFamily: "'DM Mono', monospace" }}>
                  + Add blank line item
                </button>
              </div>
            </Card>

            {/* Line Items Table */}
            <div>
              <Card>
                <CardHead
                  title="Bill of Quantities"
                  tag={`${items.length} items`}
                  right={
                    <button onClick={copyBoq} style={{ padding: "4px 12px", background: C.blue, border: "none", borderRadius: 4, color: "#fff", fontSize: 10, cursor: "pointer", fontFamily: "'DM Mono', monospace" }}>
                      Export
                    </button>
                  }
                />
                <div style={{ overflowX: "auto" }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Description</th>
                        <th style={{ textAlign: "right" }}>Market Range</th>
                        <th>List Price</th>
                        <th style={{ textAlign: "center" }}>vs Market</th>
                        <th>Disc %</th>
                        <th style={{ textAlign: "right" }}>Net Unit</th>
                        <th>Qty</th>
                        <th style={{ textAlign: "right" }}>Net Total</th>
                        <th>Markup %</th>
                        <th style={{ textAlign: "right" }}>Charge-out</th>
                        <th style={{ textAlign: "right" }}>Profit</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map(item => (
                        <LineItem key={item.id} item={item}
                          onUpdate={(field, value) => updateItem(item.id, field, value)}
                          onRemove={() => removeItem(item.id)} />
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Totals */}
                {items.length > 0 && (() => {
                  const totalNet = items.reduce((s,i)=>{
                    return s+(parseFloat(i.listPrice)||0)*(1-(parseFloat(i.discount)||0)/100)*(parseFloat(i.qty)||0);
                  },0);
                  const totalCO = items.reduce((s,i)=>{
                    const net=(parseFloat(i.listPrice)||0)*(1-(parseFloat(i.discount)||0)/100)*(parseFloat(i.qty)||0);
                    return s+net*(1+(parseFloat(i.markup)||0)/100);
                  },0);
                  const vat = vatOn ? totalCO * 0.15 : 0;
                  return (
                    <div style={{ padding: "16px 20px", borderTop: `2px solid ${C.border}`, display: "flex", justifyContent: "flex-end", gap: 40 }}>
                      <div style={{ textAlign: "right" }}>
                        <div style={{ fontSize: 10, color: C.muted, fontFamily: "'DM Mono', monospace", marginBottom: 4 }}>NET COST</div>
                        <div style={{ fontSize: 18, color: C.text, fontFamily: "'DM Mono', monospace", fontWeight: 700 }}>{R(totalNet)}</div>
                      </div>
                      <div style={{ textAlign: "right" }}>
                        <div style={{ fontSize: 10, color: C.amber, fontFamily: "'DM Mono', monospace", marginBottom: 4 }}>YOUR MARGIN</div>
                        <div style={{ fontSize: 18, color: C.amber, fontFamily: "'DM Mono', monospace", fontWeight: 700 }}>{R(totalCO - totalNet)}</div>
                      </div>
                      <div style={{ textAlign: "right" }}>
                        <div style={{ fontSize: 10, color: C.blue, fontFamily: "'DM Mono', monospace", marginBottom: 4 }}>QUOTE TO CLIENT {vatOn ? "(incl. VAT)" : "(excl. VAT)"}</div>
                        <div style={{ fontSize: 24, color: C.blue, fontFamily: "'DM Mono', monospace", fontWeight: 700 }}>{R(totalCO + vat)}</div>
                      </div>
                    </div>
                  );
                })()}
              </Card>

              {/* Scraper note */}
              <div style={{ marginTop: 12, padding: "10px 14px", background: C.surface, border: `1px solid ${C.border}`, borderRadius: 6, display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{ width: 8, height: 8, borderRadius: "50%", background: C.green }} />
                <span style={{ fontSize: 10, color: C.muted, fontFamily: "'DM Mono', monospace" }}>
                  Market ranges are a static AECOM 2025/26 reference for comparison. Your own rates (QS Rates tab) are listed first in the library.
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

export default function VulaQSPro({ v2 = false, ...props }) {
  return v2 ? <QSProV2 {...props} /> : <VulaQSProV1 {...props} />;
}
