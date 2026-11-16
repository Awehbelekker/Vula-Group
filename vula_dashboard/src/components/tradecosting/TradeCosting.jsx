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
 *  Actuals    (phase 4) an estimate made for a project beside what was paid on it
 *             (vula/commerce/actuals.py); once the work is finished, the build-up changes Vula
 *             suggests — an owner or manager accepts (a new version) or dismisses each.
 *
 * Shown only where the business has the `trade_costing` switch on; a 403 says so. Every figure is
 * the server's, formatted with formatMoney — nothing is added up here.
 */
import { useEffect, useMemo, useState } from "react";
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
  const [project, setProject] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [comparing, setComparing] = useState(null);
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
      items, research: true, ...(project ? { project } : {}),
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
          {(x.projects || []).length > 0 && (
            <Field label="Project" hint="Optional — later Vula compares the estimate with what was paid on it">
              <Select value={project} onChange={(e) => setProject(e.target.value)}>
                <option value="">Not for a project</option>
                {x.projects.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
              </Select>
            </Field>
          )}
          <Field label="Not included" hint="Comma separated, e.g. painting, foundations">
            <Input value={excluded} onChange={(e) => setExcluded(e.target.value)} placeholder="painting" />
          </Field>
          <div className="vtc-actions">
            <Button type="submit" disabled={busy}>{busy ? "Working it out…" : "Work it out"}</Button>
            {busy && <span className="vtc-muted" role="status">A build-up Vula hasn't seen before takes about a minute.</span>}
          </div>
        </form>
      </Card>
      {result && <EstimateResult est={result} onDownload={() => x.download(result.id)}
        onCompare={result.project && result.id ? () => setComparing(result.id) : null} />}
      {comparing && <Actuals key={comparing} id={comparing} x={x} onClose={() => setComparing(null)} />}
      <RecentEstimates x={x} onCompare={setComparing} />
    </>
  );
}

function EstimateResult({ est, onDownload, onCompare }) {
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
          {est.project && <p className="vtc-muted">Project {est.project}</p>}
          <p className="vtc-total"><strong>{money(est.total_cents)}</strong></p>
          <p className="vtc-muted">Materials {money(t.material || 0)} · Labour {money(t.labour || 0)}
            {t.plant ? ` · Plant ${money(t.plant)}` : ""}</p>
        </div>
        <div className="vtc-actions">
          {onCompare && <Button variant="ghost" onClick={onCompare}>Compare with what was paid</Button>}
          {est.id && <Button variant="secondary" onClick={save}>Download Excel</Button>}
        </div>
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

function RecentEstimates({ x, onCompare }) {
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
        { key: "project", label: "Project", render: (e) => e.project || "—" },
        { key: "total_cents", label: "Total", align: "right", render: (e) => money(e.total_cents) },
        { key: "created_at", label: "Made", render: (e) => String(e.created_at || "").slice(0, 10) },
        { key: "id", label: "", render: (e) => (
          <span className="vtc-actions">
            {e.project && <Button variant="ghost" size="sm" onClick={() => onCompare(e.id)}
              aria-label={`Compare ${e.title} with what was paid`}>Compare</Button>}
            <Button variant="ghost" size="sm" onClick={() => save(e.id)} aria-label={`Download ${e.title}`}>Excel</Button>
          </span>) },
      ]} />
    </Card>
  );
}

// ── What it actually cost (phase 4) ──────────────────────────────────────────

const gapText = (g) => (g.gap_pct == null ? "—" : `${Number(g.gap_pct) > 0 ? "+" : ""}${g.gap_pct}%`);
const VERDICT = { close: ["Close to the estimate", "ok"], suggest: ["Suggested change", "warn"],
  check: ["Too far off to learn from — check the invoices", "danger"] };
const DECIDED = { accepted: ["Accepted", "ok"], dismissed: ["Dismissed", "muted"], pending: ["Waiting for a decision", "warn"] };

function Actuals({ id, x, onClose }) {
  const [res, setRes] = useState(null);
  const [failed, setFailed] = useState("");
  const [busy, setBusy] = useState(false);
  async function load() {
    const r = await x.actuals(id);
    if (!r.ok) { setFailed(reason(r, "The comparison didn't load — try again.")); return; }
    setFailed(""); setRes(r.data);
  }
  useEffect(() => { load(); }, [id]);

  async function finished() {
    if (!(await confirmDialog("Is this work finished? Vula will compare it with what was paid and suggest build-up "
      + "changes for you to accept or dismiss — nothing changes until you do.", { confirmLabel: "Yes, it's finished" }))) return;
    setBusy(true);
    const r = await x.learn(id);
    setBusy(false);
    if (!r.ok) { toast(reason(r, "That didn't work — try again."), "danger"); return; }
    toast(r.data.new ? `${r.data.new} suggested change${r.data.new > 1 ? "s" : ""} to decide.` : "Nothing far enough off to change.");
    x.reload(); load();
  }

  if (failed) return <Card><p className="vtc-note" role="alert">{failed}</p><Button size="sm" variant="ghost" onClick={onClose}>Close</Button></Card>;
  if (!res) return <Card><Skeleton lines={4} /></Card>;
  const c = res.comparison;
  const verdict = Object.fromEntries((res.proposals || []).map((p) => [p.group, p]));
  const columns = [
    { key: "label", label: "Component" },
    { key: "estimated_qty", label: "Estimated", align: "right", render: (g) => `${num(g.estimated_qty)} ${g.unit}` },
    { key: "bought_qty", label: "Bought", align: "right", render: (g) => (g.bought_qty == null ? "Nothing on file yet" : `${num(g.bought_qty)} ${g.unit}`) },
    { key: "gap_pct", label: "Gap", align: "right", render: gapText },
    { key: "estimated_cents", label: "Estimated cost", align: "right", render: (g) => money(g.estimated_cents) },
    { key: "paid_cents", label: "Paid", align: "right", render: (g) => money(g.paid_cents) },
    { key: "key", label: "", render: (g) => {
      if (g.shared) return <span className="vtc-muted">Used by {g.items.join(" and ")} — total only</span>;
      const v = verdict[g.key];
      if (!v || (!res.finished && v.verdict === "suggest")) return null;
      const [label, tone] = VERDICT[v.verdict] || [];
      return label ? <StatusChip tone={tone}>{label}</StatusChip> : null;
    } },
  ];
  return (
    <Card>
      <div className="vtc-head">
        <div style={{ minWidth: 0 }}>
          <h2 className="vtc-h2">{c.title} — compared with what was paid</h2>
          <p className="vtc-muted">Project {c.project} · estimated {money(c.estimated_cents)} · paid on what it covers {money(c.paid_cents)}</p>
        </div>
        <div className="vtc-actions">
          {!res.finished && x.canConfirm && <Button size="sm" disabled={busy} onClick={finished}>This work is finished — learn from it</Button>}
          <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>
        </div>
      </div>
      {!res.finished && <p className="vtc-note">The work isn't marked finished, so this is progress only — nothing is suggested yet.</p>}
      <DataTable columns={columns} rows={c.groups.map((g) => ({ ...g, id: g.key }))} caption={`${c.title}: estimated against paid`}
        stack={{ title: (g) => g.label, detail: (g) => `Estimated ${num(g.estimated_qty)} ${g.unit} · bought ${g.bought_qty == null ? "nothing yet" : `${num(g.bought_qty)} ${g.unit}`}`
          + (g.shared ? ` · used by ${g.items.join(" and ")}, total only` : ""),
          trailing: gapText }} />
      {c.groups.filter((g) => g.lines.length || g.not_comparable.length).map((g) => (
        <details key={g.key} className="vtc-details">
          <summary>{g.label}: invoice lines counted ({g.lines.length}){g.not_comparable.length ? `, not counted (${g.not_comparable.length})` : ""}</summary>
          <ul className="vtc-lines">
            {g.lines.map((l, i) => <li key={i} className="vtc-line"><span className="vtc-line-label">{l.description}</span>
              <span>{l.supplier ? `${l.supplier}, ` : ""}{l.date} · {num(l.counted)} {g.unit}{l.how ? ` (${l.how})` : ""}</span></li>)}
            {g.not_comparable.map((l, i) => <li key={`n${i}`} className="vtc-line"><span className="vtc-line-label">{l.description}</span>
              <span className="vtc-muted">Not counted: {l.why}</span></li>)}
          </ul>
        </details>
      ))}
      {c.unclear.length > 0 && <p className="vtc-note"><strong>Couldn't tell which component:</strong> {c.unclear.map((u) => `${u.description} (${u.could_be.join(" or ")})`).join("; ")}</p>}
      {c.not_in_estimate.length > 0 && <p className="vtc-note vtc-muted">Also paid on this project but not in the estimate (other work?): {c.not_in_estimate.length} lines, {money(c.not_in_estimate_cents)}.</p>}
      {(res.suggestions || []).length > 0 && <Suggestions list={res.suggestions} x={x} onDecided={() => { x.reload(); load(); }} />}
    </Card>
  );
}

function Suggestions({ list, x, onDecided }) {
  const [busy, setBusy] = useState(null);
  async function decide(s, decision) {
    if (decision === "accept" && !(await confirmDialog(`Change the build-up: ${s.text}? Vula saves a new version and uses it for every estimate of this item.`,
      { confirmLabel: "Accept" }))) return;
    setBusy(s.id);
    const r = await x.decide(s.id, decision);
    setBusy(null);
    if (!r.ok) { toast(reason(r, "That didn't save — try again."), "danger"); return; }
    toast(decision === "accept" ? "Changed — the new build-up is used from now on." : "Dismissed — it won't be suggested again.");
    onDecided();
  }
  return (
    <section aria-label="Suggested build-up changes">
      <h3 className="vtc-h3 vtc-trade">Suggested build-up changes</h3>
      <ul className="vtc-lines">
        {list.map((s) => {
          const [label, tone] = DECIDED[s.status] || DECIDED.pending;
          return (
            <li key={s.id} className="vtc-line">
              <span className="vtc-line-label">{s.text}</span>
              {s.status === "pending" && x.canConfirm ? (
                <span className="vtc-actions">
                  <Button size="sm" disabled={busy === s.id} onClick={() => decide(s, "accept")}>Accept</Button>
                  <Button size="sm" variant="ghost" disabled={busy === s.id} onClick={() => decide(s, "dismiss")}>Dismiss</Button>
                </span>
              ) : <StatusChip tone={tone}>{label}</StatusChip>}
            </li>
          );
        })}
      </ul>
    </section>
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
      {(x.suggestions || []).length > 0 && (
        <Card><Suggestions list={x.suggestions} x={x} onDecided={x.reload} /></Card>
      )}
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
        {(x.suggestions || []).some((s) => s.buildup_id === b.id) && <StatusChip tone="warn">Suggested change</StatusChip>}
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
                  {src ? <> · {src.url
                    ? <a href={src.url} target="_blank" rel="noreferrer">{src.title || "source"}</a>
                    : <span className="vtc-muted">your {src.title || "document"}</span>}</> : null}
                  {l.source_ref === "a Vula business's records" ? <span className="vtc-muted"> · from a Vula business's records</span> : null}
                  {l.note ? <span className="vtc-muted"> · {l.note}</span> : null}
                  {l.source_ref === "confirmed by owner" ? <span className="vtc-muted"> · set by you</span> : null}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {(b.unknown || []).length > 0 && (
        <p className="vtc-note"><strong>Needs a figure:</strong> {b.unknown.map((u) => (u.why ? `${u.label || u.key} (${u.why})` : u.label || u.key)).join("; ")}</p>
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
