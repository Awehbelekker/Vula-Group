/**
 * QuickCostV2.jsx — the redesigned Estimating › Quick Cost (2026-10-20, UX redesign Phase F). Same
 * maths as VulaQS (estimating/useQuickCost.js): floor area × an AECOM 2025/26 m² rate (or your own
 * m² rate, or supplier quotes), plus contingency, your markup, the SACAP BN672 architect's fee, PM
 * and PA fees, the council fee and VAT. Inputs on the left, the breakdown beside them (below on a
 * phone). A budgeting estimate in rand, shown through formatMoney.
 */
import { AlertTriangle, ClipboardCopy, Plus, Trash2 } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, Field, IconButton, Input, Select } from "../ui/index.jsx";
import { AECOM_RATES, useQuickCost } from "./useQuickCost";
import "./estimating.css";

const rands = (n) => formatMoney(Math.round(Number(n || 0) * 100), { cents: "never" });
const pc = (n) => `${Number(n).toFixed(1)}%`;
const LOCATIONS = { western_cape: "Western Cape (+5%)", gauteng: "Gauteng (baseline)", other: "Other province (−5%)" };
const SUPPLIER_CATS = ["Construction", "MEP", "Finishes", "Façade", "Professional", "Other"];

function Kv({ label, sub, value, total, hl }) {
  return (
    <div className={`ves-kv${total ? " ves-kv--total" : ""}${hl ? " ves-kv--hl" : ""}`}>
      <span><span>{label}</span>{sub && <span className="ves-muted">{sub}</span>}</span>
      <span className="ves-money">{value}</span>
    </div>
  );
}

function Slider({ label, value, min, max, step, onChange }) {
  return (
    <Field label={label}>
      <div className="ves-row" style={{ flexWrap: "nowrap" }}>
        <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} aria-hidden="true" tabIndex={-1}
          style={{ flex: 1, minWidth: 0, accentColor: "var(--brand)" }} />
        <Input type="number" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ width: 84 }} />
      </div>
    </Field>
  );
}

export default function QuickCostV2({ tenantId }) {
  const x = useQuickCost({ tenantId });
  const c = x.calc;
  const rate = AECOM_RATES[x.rateKey];
  return (
    <section className="ves" aria-label="Quick cost">
      <p className="ves-sub">An early cost estimate from the floor area and a rate per m², with your markup, professional fees, council fee and VAT.</p>
      <div className="ves-cols">
        <div className="ves">
          <Card className="ves-card" role="group" aria-labelledby="ves-qc-project">
            <h2 className="ves-h" id="ves-qc-project">Project</h2>
            <div className="ves-grid">
              <Field label="Project"><Input value={x.projectName} onChange={(e) => x.setProjectName(e.target.value)} /></Field>
              <Field label="Client"><Input value={x.clientName} onChange={(e) => x.setClientName(e.target.value)} /></Field>
              <Field label="Floor area (m²)"><Input type="number" min="0" value={x.area} onChange={(e) => x.setArea(Number(e.target.value))} /></Field>
              <Field label="Location"><Select value={x.location} onChange={(e) => x.setLocation(e.target.value)}>
                {Object.entries(LOCATIONS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
            </div>
          </Card>

          <Card className="ves-card" role="group" aria-labelledby="ves-qc-rate">
            <h2 className="ves-h" id="ves-qc-rate">Build type and rate</h2>
            <Field label="Build type">
              <Select value={x.rateKey} onChange={(e) => x.setRateKey(e.target.value)}>
                {x.categories.map((cat) => (
                  <optgroup key={cat} label={cat}>
                    {Object.entries(AECOM_RATES).filter(([, v]) => v.category === cat).map(([k, v]) =>
                      <option key={k} value={k}>{v.label} ({rands(v.low)}–{rands(v.high)}/m²)</option>)}
                  </optgroup>))}
              </Select>
            </Field>
            <div className="ves-grid">
              <Field label="Where in the range"><Select value={x.useRange} onChange={(e) => x.setUseRange(e.target.value)}>
                <option value="low">Conservative (low)</option><option value="mid">Typical (mid)</option><option value="high">Premium (high)</option></Select></Field>
              <Field label="Your own rate (R/m²)" hint="Optional: overrides the range">
                <Input type="number" min="0" value={x.customRate} onChange={(e) => x.setCustomRate(e.target.value)} />
              </Field>
              {x.ownM2Rates.length > 0 && (
                <Field label="Or one of your rates">
                  <Select value="" onChange={(e) => e.target.value && x.setCustomRate(e.target.value)}>
                    <option value="">Choose…</option>
                    {x.ownM2Rates.map((r) => <option key={`${r.code || r.description}-${r.rate}`} value={r.rate}>{r.description || r.code} ({rands(r.rate)}/m²)</option>)}
                  </Select>
                </Field>)}
            </div>
            <p className="ves-muted">AECOM 2025/26 range {c.rateRange.replace(/R /g, "R ")} · rate used {rands(c.baseRate)}/m²{rate ? ` · ${rate.category}` : ""}</p>
          </Card>

          <Card className="ves-card" role="group" aria-labelledby="ves-qc-fees">
            <h2 className="ves-h" id="ves-qc-fees">Markup, fees and contingency</h2>
            <Slider label={`Your markup (${pc(x.markupPct)} on construction)`} value={x.markupPct} min={0} max={40} step={0.5} onChange={x.setMarkupPct} />
            <p className="ves-muted">{rands(c.markupAmt)} · most businesses use 10–20%</p>
            <Slider label={`Contingency (${pc(x.contingency)} of construction)`} value={x.contingency} min={0} max={25} step={1} onChange={x.setContingency} />
            <div className="ves-grid">
              <Field label="SACAP complexity"><Select value={x.complexity} onChange={(e) => x.setComplexity(e.target.value)}>
                <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></Select></Field>
              <Field label="PA fixed fee (R)"><Input type="number" min="0" value={x.paFee} onChange={(e) => x.setPaFee(e.target.value)} /></Field>
              <Field label="Council fee (%)"><Input type="number" min="0" step={0.05} value={x.councilPct} onChange={(e) => x.setCouncilPct(Number(e.target.value))} /></Field>
              <Field label="PM months"><Input type="number" min="0" value={x.pmMonths} onChange={(e) => x.setPmMonths(Number(e.target.value))} /></Field>
              <Field label="PM per month (R)"><Input type="number" min="0" value={x.pmMonthly} onChange={(e) => x.setPmMonthly(Number(e.target.value))} /></Field>
            </div>
          </Card>

          <Card className="ves-card" role="group" aria-labelledby="ves-qc-sup">
            <h2 className="ves-h" id="ves-qc-sup">Supplier quotes</h2>
            <p className="ves-muted">Optional. Enter real quotes and your discount; once any quote is in, they replace the m² rate.</p>
            <ul className="ves-list" aria-label="Suppliers">
              {x.suppliers.map((s) => {
                const cost = s.customCost ? parseFloat(s.customCost) : parseFloat(s.listRate) || 0;
                const net = cost * (1 - s.discount / 100);
                return (
                  <li key={s.id} className="ves-item" style={{ paddingLeft: 0, paddingRight: 0, alignItems: "end" }}>
                    <div className="ves-grid" style={{ flex: "1 1 300px", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 130px), 1fr))" }}>
                      <Field label="Supplier"><Input value={s.name} onChange={(e) => x.updateSupplier(s.id, "name", e.target.value)} /></Field>
                      <Field label="Category"><Select value={s.category} onChange={(e) => x.updateSupplier(s.id, "category", e.target.value)}>{SUPPLIER_CATS.map((k) => <option key={k}>{k}</option>)}</Select></Field>
                      <Field label="Quote (R)"><Input type="number" min="0" value={s.customCost} onChange={(e) => x.updateSupplier(s.id, "customCost", e.target.value)} /></Field>
                      <Field label="Discount (%)"><Input type="number" min={0} max={100} value={s.discount} onChange={(e) => x.updateSupplier(s.id, "discount", Number(e.target.value))} /></Field>
                    </div>
                    <span className="ves-money" style={{ minWidth: 90, textAlign: "right" }}>{s.customCost ? rands(net) : "—"}</span>
                    <IconButton quiet Icon={Trash2} className="ves-danger" label={`Remove ${s.name}`} onClick={() => x.removeSupplier(s.id)} />
                  </li>);
              })}
            </ul>
            <div><Button size="sm" variant="secondary" Icon={Plus} onClick={x.addSupplier}>Add supplier</Button></div>
            {c.hasSupplierData && <p className="ves-note" role="status">Using the supplier quotes ({rands(c.supplierTotal)}) instead of the m² rate.</p>}
          </Card>
        </div>

        <div className="ves ves-sticky">
          <Card className="ves-card" role="group" aria-labelledby="ves-qc-cost">
            <h2 className="ves-h" id="ves-qc-cost">Cost breakdown</h2>
            <p className="ves-muted">{x.area} m² · {rate?.label}</p>
            <h3 className="ves-overline">Construction</h3>
            {!c.hasSupplierData ? (
              <>
                <Kv label={`${rands(c.baseRate)}/m² × ${x.area} m²`} value={rands(c.constructionBase)} />
                <Kv label={`Contingency (${pc(x.contingency)})`} value={rands(c.contingencyAmt)} />
                <Kv label="Construction total" value={rands(c.constructionTotal)} />
              </>
            ) : (
              <>
                {x.suppliers.filter((s) => s.customCost).map((s) => {
                  const cost = parseFloat(s.customCost); const net = cost * (1 - s.discount / 100);
                  return <Kv key={s.id} label={s.name} value={rands(net)} sub={s.discount > 0 ? `List ${rands(cost)} · ${pc(s.discount)} off · saves ${rands(cost - net)}` : undefined} />;
                })}
                <Kv label="Supplier total (after discounts)" value={rands(c.supplierTotal)} />
              </>)}
            <h3 className="ves-overline">Your margin</h3>
            <Kv hl label={`Markup (${pc(x.markupPct)})`} value={rands(c.markupAmt)} />
            <Kv label="Construction + markup" value={rands(c.subtotalAfterMarkup)} />
            <h3 className="ves-overline">Professional fees</h3>
            <Kv label={`SACAP architect's fee (${x.complexity})`} sub={`BN672 (2024) · ${pc(c.sacapFee / c.effectiveConstruction * 100)} of construction`} value={rands(c.sacapFee)} />
            {x.pmMonths > 0 && <Kv label={`Project management (${x.pmMonths} × ${rands(x.pmMonthly)})`} value={rands(c.pmTotal)} />}
            {c.paTotal > 0 && <Kv label="Principal agent fee" value={rands(c.paTotal)} />}
            <Kv label="Professional fees" value={rands(c.proFeesTotal)} />
            <h3 className="ves-overline">Statutory</h3>
            <Kv label={`Council submission (${pc(x.councilPct)}, CoCT)`} value={rands(c.councilFee)} />
            <h3 className="ves-overline">Total</h3>
            <Kv label="Subtotal excl. VAT" value={rands(c.grandSubtotal)} />
            {x.includeVAT && <Kv label="VAT (15%)" value={rands(c.vatAmt)} />}
            <Kv total label={`Total ${x.includeVAT ? "incl." : "excl."} VAT`} value={rands(c.grandTotal)} />
            <p className="ves-muted">{rands(c.grandTotal / x.area)}/m² all-in · your margin {rands(c.markupAmt)}</p>
            <label className="ves-row" style={{ minHeight: 44 }}>
              <input type="checkbox" role="switch" checked={x.includeVAT} onChange={() => x.setIncludeVAT(!x.includeVAT)} style={{ width: 18, height: 18, accentColor: "var(--brand)" }} />
              Include VAT (15%)
            </label>
            <Button Icon={ClipboardCopy} onClick={x.copySummary}>Copy quote summary</Button>
          </Card>
          <p className="ves-note"><AlertTriangle size={16} aria-hidden="true" style={{ flex: "none" }} />A preliminary estimate for budgeting. Rates from the AECOM Africa Cost Guide 2025/26 and SACAP Board Notice 672 (2024). A registered quantity surveyor must confirm final costs; VAT, escalation and site conditions can change the figure materially.</p>
        </div>
      </div>
    </section>
  );
}
