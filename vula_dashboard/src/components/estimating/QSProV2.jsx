/**
 * QSProV2.jsx — the redesigned Estimating › QS Pro (2026-10-20, UX redesign Phase F). Same maths
 * as VulaQSPro (estimating/useQSPro.js): build an estimate line by line from your own rates (QS
 * Rates) and a static AECOM 2025/26 material reference, with a list price, discount, quantity and
 * markup per line; compare it against suppliers' quotes. A budgeting estimate in rand, shown
 * through formatMoney.
 */
import { ClipboardCopy, Plus, Search, Star, Trash2 } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, Field, IconButton, Input, PillTabs, StatCard, StatusChip } from "../ui/index.jsx";
import { lineCalc, summaryCalc, useQSPro, useQuoteCompare } from "./useQSPro";
import "./estimating.css";

const rands = (n) => formatMoney(Math.round(Number(n || 0) * 100), { cents: "never" });
const pc = (n) => `${Number(n).toFixed(1)}%`;
const CATS = ["All", "Structure", "Drywall & Ceilings", "Flooring", "Joinery & Doors", "MEP", "Finishes"].map((c) => ({ id: c, label: c }));

export default function QSProV2({ tenantId }) {
  const x = useQSPro({ tenantId });
  const s = summaryCalc(x.items, x.vatOn);
  return (
    <section className="ves" style={{ maxWidth: 1400 }} aria-label="QS Pro">
      <p className="ves-sub">Build an estimate line by line from your own rates and the AECOM 2025/26 reference, with a discount and markup per line, then compare it with suppliers' quotes.</p>
      <Card className="ves-card">
        <div className="ves-grid">
          <Field label="Estimate"><Input value={x.projectName} onChange={(e) => x.setProjectName(e.target.value)} /></Field>
          <Field label="Client"><Input value={x.client} onChange={(e) => x.setClient(e.target.value)} /></Field>
          <Field label={`Default markup: ${pc(x.defaultMarkup)}`}>
            <input type="range" min={0} max={50} step={0.5} value={x.defaultMarkup} onChange={(e) => x.setDefaultMarkup(Number(e.target.value))} style={{ width: "100%", accentColor: "var(--brand)" }} />
          </Field>
        </div>
        <div className="ves-row">
          <Button size="sm" variant="secondary" onClick={x.applyDefaultMarkup}>Apply {pc(x.defaultMarkup)} to every line</Button>
          <label className="ves-row" style={{ minHeight: 44 }}>
            <input type="checkbox" role="switch" checked={x.vatOn} onChange={() => x.setVatOn(!x.vatOn)} style={{ width: 18, height: 18, accentColor: "var(--brand)" }} /> Include VAT (15%)
          </label>
        </div>
      </Card>

      <div className="ves-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 190px), 1fr))" }}>
        <StatCard variant="tile" label="Net cost" value={rands(s.totalNet)} sub="after discounts" />
        <StatCard variant="tile" label="Supplier savings" value={rands(s.totalSaving)} sub="from discounts" />
        <StatCard variant="tile" label="Your margin" value={rands(s.totalProfit)} sub={`${pc(s.avgMarkup)} on cost`} />
        <StatCard variant="tile" label="Quote to client" value={rands(s.totalCO + s.vat)} sub={x.vatOn ? "incl. VAT" : "excl. VAT"} />
      </div>

      <div className="ves-cols" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))" }}>
        <Library x={x} />
        <div className="ves" style={{ gridColumn: "span 2", minWidth: 0 }}>
          <Lines x={x} s={s} />
          <Compare items={x.items} />
        </div>
      </div>
      <p className="ves-muted">Market ranges are a static AECOM 2025/26 reference to compare against. Your own rates (QS Rates) come first in the library.</p>
    </section>
  );
}

function Library({ x }) {
  const own = x.ownRates.filter((r) => !x.search || `${r.description} ${r.code || ""}`.toLowerCase().includes(x.search.toLowerCase())).slice(0, x.search ? 200 : 40);
  return (
    <Card className="ves-card ves-sticky" role="group" aria-labelledby="ves-lib" style={{ alignSelf: "start" }}>
      <h2 className="ves-h" id="ves-lib">Add a line</h2>
      <Input type="search" value={x.search} onChange={(e) => x.setSearch(e.target.value)} aria-label="Search materials" placeholder="Search materials…" />
      <PillTabs label="Material category" tabs={CATS} active={x.filterCat} onChange={x.setFilterCat} />
      <ul className="ves-list" aria-label="Materials" style={{ maxHeight: 460, overflowY: "auto" }}>
        {own.map((r) => (
          <li key={`own-${r.code || r.description}-${r.rate}`}>
            <button type="button" className="vui-row" onClick={() => x.addOwnRate(r)} style={{ width: "100%" }} aria-label={`Add ${r.description || r.code}`}>
              <span className="vui-row-main"><span className="vui-row-title" style={{ display: "block" }}>{r.description || r.code}</span>
                <span className="vui-row-detail" style={{ display: "block" }}>{r.learned ? `From your documents · ${r.unit}` : `Your rate · ${r.unit}`}</span></span>
              <span className="vui-row-trail ves-money">{rands(r.rate || 0)}</span>
            </button>
          </li>))}
        {x.filteredLibrary.map(([key, v]) => (
          <li key={key}>
            <button type="button" className="vui-row" onClick={() => x.addFromLibrary(key)} style={{ width: "100%" }} aria-label={`Add ${v.label}`}>
              <span className="vui-row-main"><span className="vui-row-title" style={{ display: "block" }}>{v.label}</span>
                <span className="vui-row-detail" style={{ display: "block" }}>{v.unit} · {v.source}</span></span>
              <span className="vui-row-trail">{rands(v.low)}–{rands(v.high)}</span>
            </button>
          </li>))}
        {!own.length && !x.filteredLibrary.length && <li className="ves-muted" style={{ padding: "var(--space-3)" }}><Search size={14} aria-hidden="true" /> Nothing matches.</li>}
      </ul>
      <Button size="sm" variant="secondary" Icon={Plus} onClick={x.addBlank}>Blank line</Button>
    </Card>
  );
}

function Lines({ x, s }) {
  return (
    <Card className="ves-card" role="group" aria-labelledby="ves-lines">
      <div className="ves-row"><h2 className="ves-h" id="ves-lines" style={{ flex: 1 }}>Bill of quantities · {x.items.length} {x.items.length === 1 ? "line" : "lines"}</h2>
        <Button size="sm" variant="secondary" Icon={ClipboardCopy} onClick={x.copyBoq}>Copy for Excel</Button></div>
      <div className="ves-scroll" tabIndex={0} role="region" aria-label="Estimate lines">
        <table className="ves-table">
          <thead><tr>
            <th>Item</th><th className="ves-num">Market</th><th className="ves-num">List price (R)</th><th className="ves-num">vs market</th>
            <th className="ves-num">Discount %</th><th className="ves-num">Qty</th><th className="ves-num">Net</th><th className="ves-num">Markup %</th><th className="ves-num">Charge-out</th><th><span className="ves-muted">Remove</span></th>
          </tr></thead>
          <tbody>
            {x.items.map((item) => {
              const c = lineCalc(item);
              const name = item.description || c.rate.label || "Item";
              const tone = c.variance === null ? "muted" : c.variance > 15 ? "danger" : c.variance < -10 ? "ok" : "warn";
              const num = (field, label, w = 88, extra = {}) => (
                <Input type="number" value={item[field]} aria-label={`${label} for ${name}`} {...extra}
                  onChange={(e) => x.updateItem(item.id, field, e.target.value)} style={{ width: w, textAlign: "right" }} />);
              return (
                <tr key={item.id}>
                  <td style={{ minWidth: 200 }}>
                    {item.materialKey ? <span className="ves-grow"><span>{name}</span>{(c.rate.source || item.source) && <span className="ves-muted">{c.rate.source || item.source}</span>}</span>
                      : <Input value={item.description} aria-label="Description" placeholder="Description" onChange={(e) => x.updateItem(item.id, "description", e.target.value)} />}
                  </td>
                  <td className="ves-num ves-muted">{c.rate.low ? `${rands(c.rate.low)}–${rands(c.rate.high)}` : "—"}{c.rate.unit ? ` /${c.rate.unit}` : ""}</td>
                  <td className="ves-num">{num("listPrice", "List price", 100, { placeholder: c.marketMid ? Math.round(c.marketMid) : "0" })}</td>
                  <td className="ves-num">{c.variance !== null && c.listPrice > 0 ? <StatusChip tone={tone}>{c.variance > 0 ? "+" : ""}{c.variance.toFixed(0)}%</StatusChip> : "—"}</td>
                  <td className="ves-num">{num("discount", "Discount", 70, { min: 0, max: 100 })}</td>
                  <td className="ves-num">{num("qty", "Quantity", 76)}<span className="ves-muted" style={{ display: "block" }}>{c.rate.unit || item.unit || ""}</span></td>
                  <td className="ves-num">{c.lineTotal > 0 ? rands(c.lineTotal) : "—"}</td>
                  <td className="ves-num">{num("markup", "Markup", 70, { min: 0, max: 100 })}</td>
                  <td className="ves-num ves-money">{c.chargeOut > 0 ? rands(c.chargeOut) : "—"}</td>
                  <td><IconButton quiet Icon={Trash2} className="ves-danger" label={`Remove ${name}`} onClick={() => x.removeItem(item.id)} /></td>
                </tr>);
            })}
          </tbody>
        </table>
      </div>
      {x.items.length > 0 && (
        <div style={{ maxWidth: 420, marginLeft: "auto", width: "100%" }}>
          <div className="ves-kv"><span>Net cost</span><span className="ves-money">{rands(s.totalNet)}</span></div>
          <div className="ves-kv"><span>Your margin</span><span className="ves-money">{rands(s.totalCO - s.totalNet)}</span></div>
          <div className="ves-kv ves-kv--total"><span>Quote to client {x.vatOn ? "incl." : "excl."} VAT</span><span className="ves-money">{rands(s.totalCO + s.vat)}</span></div>
        </div>)}
    </Card>
  );
}

function Compare({ items }) {
  const q = useQuoteCompare({ items });
  const top = q.allQuotes[q.allQuotes.length - 1]?.total;
  return (
    <Card className="ves-card" role="group" aria-labelledby="ves-cmp">
      <h2 className="ves-h" id="ves-cmp">Compare with suppliers' quotes</h2>
      <div className="ves-grid">
        {q.quotes.map((qt, i) => (
          <div key={qt.id} className="ves-form" style={{ gap: "var(--space-2)" }}>
            <Field label={`Supplier ${i + 1}`}><Input value={qt.supplier} onChange={(e) => q.setQuotes((p) => p.map((y) => y.id === qt.id ? { ...y, supplier: e.target.value } : y))} /></Field>
            <Field label="Their total (R)"><Input type="number" min="0" value={qt.total} onChange={(e) => q.setQuotes((p) => p.map((y) => y.id === qt.id ? { ...y, total: e.target.value } : y))} /></Field>
          </div>))}
      </div>
      {q.allQuotes.length > 0 && (
        <ol className="ves-list" aria-label="Cheapest first">
          {q.allQuotes.map((it, i) => {
            const overBy = q.cheapest > 0 && i > 0 ? ((it.total - q.cheapest) / q.cheapest) * 100 : 0;
            return (
              <li key={it.id} className="ves-item" style={{ paddingLeft: 0, paddingRight: 0 }}>
                <span className="ves-grow"><span>{i === 0 && <Star size={14} aria-label="Cheapest" />} {it.isVula ? "Your estimate (charge-out)" : it.supplier}</span>
                  <span aria-hidden="true" style={{ display: "block", height: 6, borderRadius: "var(--radius-pill)", background: "var(--surface-2)", overflow: "hidden" }}>
                    <span style={{ display: "block", height: "100%", width: `${top ? (it.total / top) * 100 : 50}%`, background: it.isVula ? "var(--brand)" : i === 0 ? "var(--ok)" : "var(--warn)" }} /></span></span>
                {i > 0 && <span className="ves-muted">+{overBy.toFixed(1)}% over cheapest</span>}
                <span className="ves-money">{rands(it.total)}</span>
              </li>);
          })}
        </ol>)}
      <p className="ves-muted">Your net cost {rands(q.vulaCost)} · charge-out {rands(q.vulaCO)} · margin {rands(q.vulaCO - q.vulaCost)} ({q.vulaCost > 0 ? pc((q.vulaCO - q.vulaCost) / q.vulaCost * 100) : "0%"})</p>
    </Card>
  );
}
