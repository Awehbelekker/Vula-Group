/**
 * TradeCosting.jsx — Estimating › Costing by trade (15 Nov 2026; trade costing phase 3b).
 *
 *  Estimate   items of work with their sizes → the server's estimate (vula/commerce/estimate.py):
 *             the total, then per item every line with where its price came from and when, what
 *             isn't priced, what the build-up has no figure for, and the business's own rate
 *             beside it; the Excel breakdown to download. Recent estimates below.
 *  Build-ups  what goes into one unit of each item (vula/commerce/buildups.py), by trade. An owner
 *             or manager (`can_confirm` from the server) confirms, changes or removes one; anyone
 *             can ask Vula to research a new one.
 *
 * Shown only where the business has the `trade_costing` switch on; a 403 says so. Every figure is
 * the server's, formatted with formatMoney — nothing is added up here.
 */
import { useMemo, useState } from "react";
import { Button, Card, DataTable, EmptyState, ErrorCard, Field, Input, PageHeader, PillTabs, Select,
  Skeleton, StatusChip, confirmDialog, toast } from "../ui/index.jsx";
import { formatMoney } from "../../lib/money.js";
import { reason, useTradeCosting } from "./useTradeCosting.js";
import "./tradecosting.css";

const UNITS = [["m2", "m²"], ["m", "m"], ["m3", "m³"], ["each", "each"]];
const unitLabel = (u) => (UNITS.find(([v]) => v === u) || [u, u])[1];
const money = (c) => formatMoney(c, { cents: "always" });
const num = (v) => { const n = Number(v); return Number.isFinite(n) ? String(n) : String(v ?? ""); };
const specText = (spec) => Object.values(spec || {}).filter(Boolean).join(", ");
const nameOf = (b) => b.item + (specText(b.spec) ? ` (${specText(b.spec)})` : "");
const FROM = { paid: "What you paid", quoted: "Your quotes / BOQs", market: "Market price" };
const ORIGIN = {
  own: ["Confirmed", "ok"], proposed: ["Waiting for an owner or manager", "warn"],
  shared: ["Shared by another Vula business", "accent"], retired: ["Replaced", "muted"],
};

export default function TradeCosting({ tenantId }) {
  const x = useTradeCosting(tenantId);
  const [tab, setTab] = useState("estimate");
  if (x.state === "loading") return <Card><Skeleton lines={5} /></Card>;
  if (x.state === "failed") return <ErrorCard what="costing by trade" onRetry={x.reload} />;
  if (x.state === "off") {
    return (
      <EmptyState title="Costing by trade isn't switched on for this business">
        Ask Vula's team to switch it on — then estimates are built up trade by trade from your own prices.
      </EmptyState>
    );
  }
  return (
    <div className="vtc-page">
      <PageHeader title="Costing by trade"
        sub="Estimates built up from what goes into each item, priced from what you paid first, then your quotes, then checked market prices." />
      <PillTabs label="Costing by trade" active={tab} onChange={setTab}
        tabs={[{ id: "estimate", label: "Estimate" }, { id: "buildups", label: `Build-ups (${(x.buildups || []).length})` }]} />
      {tab === "estimate" ? <EstimateTab x={x} /> : <BuildupsTab x={x} />}
    </div>
  );
}

// ── Estimate ─────────────────────────────────────────────────────────────────

const emptyRow = () => ({ item: "", detail: "", spec: null, size: "", unit: "m2" });

function EstimateTab({ x }) {
  const [rows, setRows] = useState([emptyRow()]);
  const [excluded, setExcluded] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const known = useMemo(() => (x.buildups || []).filter((b) => b.status !== "retired"), [x.buildups]);

  const set = (i, patch) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const pickItem = (i, value) => {
    const b = known.find((k) => nameOf(k) === value);
    set(i, b ? { item: b.item, detail: specText(b.spec), spec: b.spec || {}, unit: b.unit || "m2" }
      : { item: value, spec: null });
  };

  async function run(e) {
    e.preventDefault();
    const items = rows.filter((r) => r.item.trim() && r.size.trim()).map((r) => ({
      item: r.item.trim(), quantity_expr: r.size.trim(), unit: r.unit,
      spec: r.spec || (r.detail.trim() ? { detail: r.detail.trim() } : {}),
    }));
    if (!items.length) { toast("Add an item of work and its size, e.g. one-brick wall, 6 x 4.", "danger"); return; }
    setBusy(true);
    const res = await x.estimate({
      items, research: true,
      exclusions: excluded.split(",").map((s) => s.trim()).filter(Boolean),
    });
    setBusy(false);
    if (!res.ok) { toast(reason(res, "That estimate didn't work out — try again."), "danger"); return; }
    setResult(res.data.estimate);
    x.reload();
  }

  return (
    <>
      <Card>
        <form className="vtc-form" onSubmit={run} aria-label="New estimate">
          <datalist id="vtc-known">{known.map((b) => <option key={b.id} value={nameOf(b)} />)}</datalist>
          {rows.map((r, i) => (
            <div className="vtc-row" key={i}>
              <Field label="Item of work" style={{ minWidth: 0 }}>
                <Input list="vtc-known" value={r.spec ? nameOf({ item: r.item, spec: r.spec }) : r.item}
                  placeholder="e.g. one-brick wall" onChange={(e) => pickItem(i, e.target.value)} />
              </Field>
              {!r.spec && (
                <Field label="Details" style={{ minWidth: 0 }}>
                  <Input value={r.detail} placeholder="e.g. 220 mm" onChange={(e) => set(i, { detail: e.target.value })} />
                </Field>
              )}
              <Field label="Size" hint="Your own figures, e.g. 6 x 4" style={{ minWidth: 0 }}>
                <Input value={r.size} placeholder="6 x 4" onChange={(e) => set(i, { size: e.target.value })} />
              </Field>
              <Field label="Per" style={{ minWidth: 0 }}>
                <Select value={r.unit} onChange={(e) => set(i, { unit: e.target.value })}>
                  {UNITS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </Select>
              </Field>
              {rows.length > 1 && (
                <Button type="button" variant="ghost" size="sm" aria-label={`Remove item ${i + 1}`}
                  onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}>Remove</Button>
              )}
            </div>
          ))}
          <div className="vtc-actions">
            <Button type="button" variant="secondary" size="sm" onClick={() => setRows((rs) => [...rs, emptyRow()])}>Add item</Button>
          </div>
          <Field label="Not included" hint="Comma separated, e.g. painting, foundations">
            <Input value={excluded} onChange={(e) => setExcluded(e.target.value)} placeholder="painting" />
          </Field>
          <div className="vtc-actions">
            <Button type="submit" disabled={busy}>{busy ? "Working it out…" : "Work it out"}</Button>
            {busy && <span className="vtc-muted" role="status">A build-up Vula hasn't seen before takes about a minute.</span>}
          </div>
        </form>
      </Card>
      {result && <EstimateResult est={result} onDownload={() => x.download(result.id)} />}
      <RecentEstimates x={x} />
    </>
  );
}

function EstimateResult({ est, onDownload }) {
  const t = est.totals_cents || {};
  async function save() {
    const got = await onDownload();
    if (got !== "ok") toast(got === "forbidden" ? "You don't have access to that file." : "The spreadsheet didn't download — try again.", "danger");
  }
  return (
    <Card>
      <div className="vtc-head">
        <div style={{ minWidth: 0 }}>
          <h2 className="vtc-h2">{est.title}</h2>
          <p className="vtc-total"><strong>{money(est.total_cents)}</strong></p>
          <p className="vtc-muted">Materials {money(t.material || 0)} · Labour {money(t.labour || 0)}
            {t.plant ? ` · Plant ${money(t.plant)}` : ""}</p>
        </div>
        {est.id && <Button variant="secondary" onClick={save}>Download Excel</Button>}
      </div>
      {(est.items || []).map((it, i) => <EstimateItem key={i} it={it} />)}
      {(est.unpriced || []).length > 0 && (
        <p className="vtc-note"><strong>Not priced</strong> (no price on file or checked online): {[...new Set(est.unpriced)].join(", ")}</p>
      )}
      {(est.exclusions || []).length > 0 && <p className="vtc-note"><strong>Not included</strong>, as asked: {est.exclusions.join(", ")}</p>}
    </Card>
  );
}

function EstimateItem({ it }) {
  if (it.status !== "priced") {
    return <div className="vtc-item"><h3 className="vtc-h3">{it.item}</h3><p className="vtc-muted">{it.message}</p></div>;
  }
  const [label, tone] = ORIGIN[it.buildup_origin] || ORIGIN.own;
  const columns = [
    { key: "label", label: "Component" },
    { key: "quantity", label: "Quantity", align: "right", render: (l) => `${num(l.quantity)} ${l.unit}` },
    { key: "unit_price_cents", label: "Unit price", align: "right", render: (l) => (l.priced ? money(l.unit_price_cents) : "Not priced") },
    { key: "cost_cents", label: "Cost", align: "right", render: (l) => (l.priced ? money(l.cost_cents) : "—") },
    { key: "source", label: "Price from", render: (l) => (l.priced ? (
      <span className="vtc-from">
        {FROM[l.source] || l.source}{l.date ? `, ${l.date}` : ""}
        {l.market_now_cents ? <span className="vtc-muted"> · market now {money(l.market_now_cents)}</span> : null}
      </span>) : "—") },
  ];
  return (
    <div className="vtc-item">
      <div className="vtc-item-head">
        <h3 className="vtc-h3">{it.item} — {num(it.quantity)} {unitLabel(it.unit)}</h3>
        <StatusChip tone={tone}>{label}</StatusChip>
        <strong className="vtc-item-total">{money(it.total_cents)}</strong>
      </div>
      <DataTable columns={columns} rows={(it.lines || []).map((l, k) => ({ ...l, id: `${l.component}-${k}` }))}
        caption={`${it.item}: cost per component`}
        stack={{ title: (l) => l.label, detail: (l) => `${num(l.quantity)} ${l.unit} · ${l.priced ? FROM[l.source] || l.source : "not priced"}`,
          trailing: (l) => (l.priced ? money(l.cost_cents) : "—") }} />
      {it.compare && (
        <p className="vtc-note">Your own rate: {money(it.compare.rate_cents)}/{unitLabel(it.unit)} ({it.compare.where}) →{" "}
          <strong>{money(it.compare.total_cents)}</strong></p>
      )}
      {(it.needs_figure || []).length > 0 && <p className="vtc-note"><strong>Needs a figure:</strong> {it.needs_figure.join(", ")}</p>}
      {(it.not_in_buildup || []).length > 0 && <p className="vtc-note vtc-muted">Not in the build-up: {it.not_in_buildup.join(", ")}</p>}
    </div>
  );
}

function RecentEstimates({ x }) {
  if (!(x.estimates || []).length) return null;
  async function save(id) {
    const got = await x.download(id);
    if (got !== "ok") toast("The spreadsheet didn't download — try again.", "danger");
  }
  return (
    <Card>
      <h2 className="vtc-h2">Recent estimates</h2>
      <DataTable rows={x.estimates} caption="Recent estimates" columns={[
        { key: "title", label: "Estimate" },
        { key: "total_cents", label: "Total", align: "right", render: (e) => money(e.total_cents) },
        { key: "created_at", label: "Made", render: (e) => String(e.created_at || "").slice(0, 10) },
        { key: "id", label: "", render: (e) => <Button variant="ghost" size="sm" onClick={() => save(e.id)}
          aria-label={`Download ${e.title}`}>Excel</Button> },
      ]} />
    </Card>
  );
}

// ── Build-ups ────────────────────────────────────────────────────────────────

function BuildupsTab({ x }) {
  const groups = useMemo(() => {
    const by = {};
    for (const b of x.buildups || []) (by[b.trade] = by[b.trade] || []).push(b);
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
  }, [x.buildups]);
  return (
    <>
      <ResearchForm x={x} />
      {!groups.length && (
        <EmptyState title="No build-ups yet">
          Research one above, or ask Vula on WhatsApp ("what goes into a m² of 220 mm brick wall?").
        </EmptyState>
      )}
      {groups.map(([trade, list]) => (
        <section key={trade} aria-label={trade}>
          <h2 className="vtc-h2 vtc-trade">{trade}</h2>
          {list.map((b) => <BuildupCard key={b.id} b={b} x={x} />)}
        </section>
      ))}
    </>
  );
}

function ResearchForm({ x }) {
  const [item, setItem] = useState("");
  const [detail, setDetail] = useState("");
  const [unit, setUnit] = useState("m2");
  const [busy, setBusy] = useState(false);
  async function go(e) {
    e.preventDefault();
    if (!item.trim()) return;
    setBusy(true);
    const res = await x.research({ item: item.trim(), unit, spec: detail.trim() ? { detail: detail.trim() } : {} });
    setBusy(false);
    if (!res.ok) { toast(reason(res, "Vula couldn't research that just now — try again."), "danger"); return; }
    toast(res.data.researched ? "Researched — an owner or manager confirms it before it's relied on."
      : "Vula already has a build-up for that.");
    setItem(""); setDetail("");
    x.reload();
  }
  return (
    <Card>
      <form className="vtc-form" onSubmit={go} aria-label="Research a build-up">
        <h2 className="vtc-h2">Research a build-up</h2>
        <div className="vtc-row">
          <Field label="Item of work" style={{ minWidth: 0 }}>
            <Input value={item} onChange={(e) => setItem(e.target.value)} placeholder="e.g. plaster to brick walls" />
          </Field>
          <Field label="Details" style={{ minWidth: 0 }}>
            <Input value={detail} onChange={(e) => setDetail(e.target.value)} placeholder="e.g. 15 mm, both sides" />
          </Field>
          <Field label="Per" style={{ minWidth: 0 }}>
            <Select value={unit} onChange={(e) => setUnit(e.target.value)}>
              {UNITS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </Field>
        </div>
        <div className="vtc-actions">
          <Button type="submit" disabled={busy}>{busy ? "Researching…" : "Research it"}</Button>
          <span className="vtc-muted">Only quantities printed in a cited source are kept. It takes about a minute.</span>
        </div>
      </form>
    </Card>
  );
}

function BuildupCard({ b, x }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState(false);
  const [label, tone] = ORIGIN[b.origin] || ORIGIN[b.status] || ORIGIN.own;
  const sources = Object.fromEntries((b.sources || []).map((s) => [String(s.n), s]));

  async function confirm(changes) {
    setBusy(true);
    const res = await x.confirm(b.id, changes);
    setBusy(false);
    if (!res.ok) { toast(reason(res, "That didn't save — try again."), "danger"); return; }
    toast("Confirmed — Vula uses this for every estimate of this item.");
    setEditing(false);
    x.reload();
  }
  async function confirmAsIs() {
    if (await confirmDialog(`Confirm the build-up for ${nameOf(b)}? Vula will use it for every estimate of this item.`,
      { confirmLabel: "Confirm" })) confirm([]);
  }
  function saveChanges() {
    const changes = [];
    for (const l of b.lines || []) {
      const d = draft[l.component] || {};
      const qty = d.qty !== undefined && d.qty !== "" && Number(d.qty) !== Number(l.qty_per_unit) ? Number(d.qty) : undefined;
      const waste = d.waste !== undefined && d.waste !== "" && Number(d.waste) !== Number(l.waste_pct) ? Number(d.waste) : undefined;
      if (qty !== undefined || waste !== undefined) {
        changes.push({ component: l.component, ...(qty !== undefined ? { qty_per_unit: qty } : {}),
          ...(waste !== undefined ? { waste_pct: waste } : {}) });
      }
    }
    if (!changes.length) { setEditing(false); return; }
    confirm(changes);
  }
  async function remove() {
    if (!(await confirmDialog(`Stop using the build-up for ${nameOf(b)}?`, { confirmLabel: "Remove" }))) return;
    const res = await x.retire(b.id);
    if (!res.ok) { toast(reason(res, "That didn't save — try again."), "danger"); return; }
    toast("Removed.");
    x.reload();
  }

  return (
    <Card>
      <div className="vtc-item-head">
        <h3 className="vtc-h3">{nameOf(b)} <span className="vtc-muted">· per {unitLabel(b.unit)} · v{b.version}</span></h3>
        <StatusChip tone={tone}>{label}</StatusChip>
      </div>
      <ul className="vtc-lines" aria-label={`What goes into ${nameOf(b)}`}>
        {(b.lines || []).map((l) => {
          const src = sources[String(l.source_ref || "")];
          return (
            <li key={l.component} className="vtc-line">
              <span className="vtc-line-label">{l.label}</span>
              {editing ? (
                <span className="vtc-edit">
                  <Input aria-label={`${l.label}: quantity per ${unitLabel(b.unit)}`} inputMode="decimal"
                    defaultValue={num(l.qty_per_unit)} onChange={(e) => setDraft((d) => ({ ...d, [l.component]: { ...d[l.component], qty: e.target.value } }))} />
                  <span>{l.unit}</span>
                  <Input aria-label={`${l.label}: waste %`} inputMode="decimal" defaultValue={num(l.waste_pct)}
                    onChange={(e) => setDraft((d) => ({ ...d, [l.component]: { ...d[l.component], waste: e.target.value } }))} />
                  <span>% waste</span>
                </span>
              ) : (
                <span>{num(l.qty_per_unit)} {l.unit}{Number(l.waste_pct) ? ` + ${num(l.waste_pct)}% waste` : ""}
                  {src ? <> · <a href={src.url} target="_blank" rel="noreferrer">{src.title || "source"}</a></> : null}
                  {l.source_ref === "confirmed by owner" ? <span className="vtc-muted"> · set by you</span> : null}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {(b.unknown || []).length > 0 && (
        <p className="vtc-note"><strong>Needs a figure:</strong> {b.unknown.map((u) => u.label || u.key).join(", ")}</p>
      )}
      {x.canConfirm && (
        <div className="vtc-actions">
          {editing ? (
            <>
              <Button size="sm" disabled={busy} onClick={saveChanges}>Save and confirm</Button>
              <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setDraft({}); }}>Cancel</Button>
            </>
          ) : (
            <>
              {b.origin !== "own" && <Button size="sm" disabled={busy} onClick={confirmAsIs}>Confirm</Button>}
              <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>Change</Button>
              <Button size="sm" variant="ghost" onClick={remove}>Remove</Button>
            </>
          )}
        </div>
      )}
    </Card>
  );
}
