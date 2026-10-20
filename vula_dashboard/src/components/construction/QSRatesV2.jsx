/**
 * QSRatesV2.jsx — the redesigned Operate › QS Rates (2026-10-20, UX redesign Phase F). Same calls as
 * VulaQSRates (construction/useQSRates.js): the tenant's own unit rates first, then rates learned
 * from its own invoices, quotes, BOQs and labour payments. Own rates always win; a learned rate that
 * has moved more than 10% is flagged with "Update my rate"; nothing changes by itself. Add / edit
 * opens a Sheet; deleting asks first.
 */
import { useState } from "react";
import { BookOpen, Check, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, EmptyState, Field, IconButton, Input, PillTabs, Select, Sheet, StatusChip, confirmDialog } from "../ui/index.jsx";
import { KINDS, UNITS, blank, useQSRates } from "./useQSRates";
import "./construction.css";

// own rates are stored in rand (the QS API's contract); shown in the house money format
const rand = (n) => formatMoney(Math.round(Number(n || 0) * 100), { cents: "always" });
const TABS = KINDS.map(([id, label]) => ({ id: id || "all", label }));

export default function QSRatesV2({ tenantId }) {
  const x = useQSRates({ tenantId });
  const [open, setOpen] = useState(false);
  const f = x.form;
  const set = (k) => (e) => x.setForm({ ...f, [k]: e.target.value });
  const close = () => { setOpen(false); x.setForm(blank); };
  const save = async () => { if (!f.description.trim() || f.rate === "") return; await x.save(); setOpen(false); };
  const del = async (r) => { if (await confirmDialog(`Delete your rate "${r.description}"?`, { danger: true, confirmLabel: "Delete" })) x.del(r.id); };
  return (
    <section className="vco" aria-label="QS rate library">
      <p className="vcx-sub">Your own unit rates come first; below them, rates learned from your own invoices, quotes, BOQs and labour payments. The assistant, Quick Cost, QS Pro and Takeoff use both, and never guess a market rate.</p>
      <div className="vcx-row">
        <span className="vcx-grow"><Input type="search" value={x.q} onChange={(e) => x.setQ(e.target.value)} aria-label="Search rates" placeholder="e.g. board, tiling labour, cornice" /></span>
        <Button Icon={Plus} onClick={() => { x.setForm(blank); setOpen(true); }}>Add a rate</Button>
      </div>
      <PillTabs label="Show" tabs={TABS} active={x.kind || "all"} onChange={(id) => x.setKind(id === "all" ? "" : id)} />

      <h2 className="vcx-overline">Your rates · {x.own.length}</h2>
      {x.own.length === 0
        ? <EmptyState Icon={BookOpen} title="No rates of your own yet">Add one, or tap "Add to my rates" on a learned rate below.</EmptyState>
        : <Card pad={4}>
            <ul className="vcx-list" aria-label="Your rates">
              {x.own.map((r) => (
                <li key={r.id || r.description} className="vcx-item">
                  <span className="vcx-grow"><span>{r.description}{r.code ? <span className="vcx-muted"> · {r.code}</span> : null}</span>
                    {r.source && <span className="vcx-muted">{r.source}</span>}</span>
                  <span className="vcx-money">{rand(r.rate)}<span className="vcx-muted"> / {r.unit}</span></span>
                  {r.id && <IconButton quiet Icon={Pencil} label={`Edit ${r.description}`} onClick={() => { x.edit(r); setOpen(true); }} />}
                  {r.id && <IconButton quiet Icon={Trash2} className="vcx-danger" label={`Delete ${r.description}`} onClick={() => del(r)} />}
                  {r.drift && (
                    <div className="vcx-drift" role="note">
                      <span style={{ flex: 1, minWidth: 0 }}>Your documents say {rand(r.drift.learned_rate)} ({r.drift.change_pct > 0 ? "+" : ""}{r.drift.change_pct}%) · {r.drift.source}</span>
                      {r.id && <Button size="sm" variant="secondary" disabled={x.busy} onClick={() => x.updateToLearned(r)}>Update my rate</Button>}
                    </div>)}
                </li>))}
            </ul>
          </Card>}

      <h2 className="vcx-overline">Learned from your documents · {x.learned.length}</h2>
      <p className="vcx-muted">Rate = the median of what you paid (invoices, labour). Where nothing was paid yet, the median of what was quoted or priced in a BOQ.</p>
      {x.learned.length === 0
        ? <EmptyState Icon={Search} title="Nothing learned yet">Documents › "Learn from history" reads your filed invoices, quotes and BOQs into here.</EmptyState>
        : <Card pad={4}>
            <ul className="vcx-list" aria-label="Learned rates">
              {x.shown.map((r) => (
                <li key={`${r.description}-${r.unit}`} className="vcx-item">
                  <span className="vcx-grow"><span>{r.description}</span>
                    <span className="vcx-muted">{[r.source, r.latest_supplier ? `latest ${formatMoney(r.latest_cents)} from ${r.latest_supplier}` : null,
                      r.low_cents !== r.high_cents ? `range ${formatMoney(r.low_cents)}–${formatMoney(r.high_cents)}` : null, r.projects?.length ? r.projects.join(", ") : null].filter(Boolean).join(" · ")}</span></span>
                  {r.kind !== "material" && <StatusChip outline>{r.kind}</StatusChip>}
                  {r.basis === "quoted" && <StatusChip tone="warn">Quoted, not paid</StatusChip>}
                  <span className="vcx-money">{formatMoney(r.rate_cents, { cents: "always" })}<span className="vcx-muted"> / {r.unit}</span></span>
                  <Button size="sm" variant="secondary" Icon={Plus} disabled={x.busy} aria-label={`Add ${r.description} to my rates`} onClick={() => x.adopt(r)}>My rates</Button>
                </li>))}
            </ul>
          </Card>}
      {!x.showAll && x.learned.length > x.shown.length && <div><Button variant="quiet" onClick={() => x.setShowAll(true)}>Show all {x.learned.length}</Button></div>}

      <Sheet open={open} onClose={close} title={f.id ? "Edit rate" : "Add a rate"}
        footer={<><Button variant="quiet" onClick={close}>Cancel</Button>
          <Button Icon={Check} disabled={x.busy || !f.description.trim() || f.rate === ""} onClick={save}>{f.id ? "Save changes" : "Save rate"}</Button></>}>
        <form className="vcx-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <Field label="Description"><Input value={f.description} onChange={set("description")} autoFocus /></Field>
          <div className="vcx-grid">
            <Field label="Unit"><Select value={f.unit} onChange={set("unit")}>{[...new Set([...UNITS, f.unit])].map((u) => <option key={u}>{u}</option>)}</Select></Field>
            <Field label="Rate (R)"><Input type="number" min="0" step="0.01" inputMode="decimal" value={f.rate} onChange={set("rate")} /></Field>
            <Field label="Category" hint="Optional"><Input value={f.category} onChange={set("category")} /></Field>
            <Field label="Source" hint="e.g. DIGG 2026"><Input value={f.source} onChange={set("source")} /></Field>
          </div>
        </form>
      </Sheet>
    </section>
  );
}
