import { AECOM_RATES, R, pct, useQuickCost } from "./estimating/useQuickCost";
import QuickCostV2 from "./estimating/QuickCostV2";
import { sidePad } from "../lib/layout";




// ─── UTILITIES ────────────────────────────────────────────────────────────────


// ─── STYLES ───────────────────────────────────────────────────────────────────
const C = {
  bg: "var(--bg)",
  card: "var(--surface)",
  border: "var(--border)",
  green: "var(--accent)",
  greenLight: "var(--accent-dark)",
  amber: "var(--warn)",
  text: "var(--ink)",
  muted: "var(--muted)",
  light: "var(--muted)",
  red: "var(--danger)",
  highlight: "var(--warn-soft)",
};

const inp = {
  width: "100%", padding: "10px 12px", borderRadius: 6,
  border: `1px solid ${C.border}`, fontSize: 13,
  background: "var(--bg)", color: C.text, outline: "none",
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
      <div style={{ padding: "14px 20px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", gap: 10, background: "var(--bg)" }}>
        <span style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 700, color: C.text }}>{title}</span>
        {badge && <span style={{ padding: "2px 8px", borderRadius: 10, background: `color-mix(in srgb, ${C.green} 8%, transparent)`, color: C.green, fontSize: 10, fontFamily: "'DM Mono', monospace" }}>{badge}</span>}
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
function VulaQSV1({ tenantId }) {
  const { categories, projectName, setProjectName, clientName, setClientName, area, setArea, rateKey, setRateKey, useRange, setUseRange, customRate, setCustomRate, ownM2Rates, complexity, setComplexity, location, setLocation, markupPct, setMarkupPct, paFee, setPaFee, pmMonths, setPmMonths, pmMonthly, setPmMonthly, includeVAT, setIncludeVAT, contingency, setContingency, councilPct, setCouncilPct, suppliers, updateSupplier, addSupplier, removeSupplier, calc, copySummary } = useQuickCost({ tenantId });

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600;700&family=DM+Sans:wght@400;500;600&family=DM+Mono:wght@400;500&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: ${C.bg}; }
        input:focus, select:focus { border-color: ${C.green} !important; box-shadow: 0 0 0 2px color-mix(in srgb, ${C.green} 13%, transparent); }
        input[type=range] { accent-color: ${C.green}; }
      `}</style>

      <div style={{ minHeight: "100vh", background: C.bg, fontFamily: "'DM Sans', sans-serif", padding: "0 0 60px" }}>

        {/* Header */}
        <div style={{ background: C.green, padding: "18px 28px", display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ width: 36, height: 36, background: "rgba(255,255,255,0.15)", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--font-display)", fontSize: 20, color: "#fff", fontWeight: 700 }}>V</div>
          <div>
            <div style={{ fontFamily: "var(--font-display)", fontSize: 20, color: "#fff", fontWeight: 700 }}>Vula QS</div>
            <div style={{ fontSize: 10, color: "rgba(255,255,255,0.65)", letterSpacing: "0.12em", fontFamily: "'DM Mono', monospace" }}>CONSTRUCTION COST ESTIMATOR · DIGG</div>
          </div>
          <div style={{ marginLeft: "auto", fontSize: 11, color: "rgba(255,255,255,0.5)", fontFamily: "'DM Mono', monospace" }}>AECOM 2025/26 · SACAP BN672</div>
        </div>

        <div style={{ maxWidth: 1100, margin: "0 auto", padding: `24px ${sidePad(20)}`, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 420px), 1fr))", gap: 20 }}>

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
                  {ownM2Rates.length > 0 && (
                    <select value="" onChange={e => e.target.value && setCustomRate(e.target.value)} style={{ ...inp, marginTop: 6 }}>
                      <option value="">Use one of your rates…</option>
                      {ownM2Rates.map(r => <option key={`${r.code || r.description}-${r.rate}`} value={r.rate}>{r.description || r.code} — {R(r.rate)}/m²</option>)}
                    </select>
                  )}
                </div>
              </div>

              <div style={{ padding: "10px 14px", background: `color-mix(in srgb, ${C.green} 6%, transparent)`, borderRadius: 6, fontSize: 12, color: C.green, fontFamily: "'DM Mono', monospace" }}>
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
                    <tr style={{ background: "var(--surface-alt)" }}>
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
                <div style={{ marginTop: 10, padding: "8px 12px", background: `color-mix(in srgb, ${C.amber} 8%, transparent)`, borderRadius: 6, fontSize: 11, color: C.amber, fontFamily: "'DM Mono', monospace" }}>
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
              <div style={{ marginTop: 20, padding: 16, background: `color-mix(in srgb, ${C.green} 3%, transparent)`, border: `1px solid color-mix(in srgb, ${C.green} 19%, transparent)`, borderRadius: 8 }}>
                <Row label="Subtotal (excl. VAT)" value={R(calc.grandSubtotal)} bold />
                {includeVAT && <Row label="VAT (15%)" value={R(calc.vatAmt)} />}
                <div style={{ display: "flex", justifyContent: "space-between", padding: "12px 0 0", marginTop: 8, borderTop: `2px solid color-mix(in srgb, ${C.green} 25%, transparent)` }}>
                  <span style={{ fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: C.green }}>Total Project Cost</span>
                  <span style={{ fontFamily: "var(--font-display)", fontSize: 24, fontWeight: 700, color: C.green }}>{R(calc.grandTotal)}</span>
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
                <div style={{ marginTop: 10, padding: "10px 12px", background: C.highlight, borderRadius: 6, border: `1px solid color-mix(in srgb, ${C.amber} 19%, transparent)` }}>
                  <strong style={{ color: C.green, fontSize: 16, fontFamily: "var(--font-display)" }}>
                    Total: {R(calc.grandTotal)} {includeVAT ? "incl. VAT" : "excl. VAT"}
                  </strong>
                </div>
                <div style={{ fontSize: 10, marginTop: 8, color: C.light }}>
                  Rates: AECOM Africa Cost Guide 2025/26 · SACAP BN672 (2024) · CoCT tariff 2025/26<br/>
                  Excludes site development, escalation, specialist consultants not listed above.
                </div>
              </div>

              <button
                onClick={copySummary}
                style={{ marginTop: 14, width: "100%", padding: "11px 0", background: C.green, color: "var(--on-accent)", border: "none", borderRadius: 7, fontSize: 13, fontWeight: 600, cursor: "pointer", fontFamily: "'DM Sans', sans-serif" }}
              >
                Copy Quote Summary
              </button>
            </Section>

            {/* Disclaimer */}
            <div style={{ padding: "12px 16px", background: "var(--warn-soft)", border: `1px solid color-mix(in srgb, ${C.amber} 19%, transparent)`, borderRadius: 8, fontSize: 11, color: C.light, fontFamily: "'DM Mono', monospace", lineHeight: 1.6 }}>
              ⚠️ This is a preliminary estimate for budgeting purposes. Rates sourced from AECOM Africa Cost Guide 2025/26 and SACAP Board Notice 672 (2024). Final costs must be confirmed by a registered Quantity Surveyor. VAT, escalation, and site-specific conditions may alter the final figure materially.
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

export default function VulaQS({ v2 = false, ...props }) {
  return v2 ? <QuickCostV2 {...props} /> : <VulaQSV1 {...props} />;
}
