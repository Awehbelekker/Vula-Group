/**
 * PipelineV2.jsx — the deals Pipeline board (2026-11-10, `pipeline` module), for every business
 * type: the columns are the business's own stages from the server (estate agency: Viewing → Offer
 * → Negotiation → Under contract → Won / Lost; others: Lead → Quoted → Negotiating → Won / Lost).
 *
 * Drag a card to another column on a computer; on a phone (or with a keyboard) open the card and
 * pick its stage, or use the card's "next stage" button. Totals come from the server.
 * Look follows docs/real-estate-workspace-brief.md, scoped to .vpl.
 */
import { useMemo, useState } from "react";
import { ArrowRight, Calendar, KanbanSquare, Plus, Trash2, User } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, EmptyState, ErrorCard, Field, Input, PageHeader, PillTabs, Select, Sheet, Skeleton, Textarea, confirmDialog, toast } from "../ui/index.jsx";
import { usePipeline } from "./usePipeline";
import "./pipeline.css";

const money = (c) => formatMoney(c, { cents: "never" });
const STAGE_TONE = ["muted", "warn", "brand", "ink", "info", "highlight"];
const toneFor = (stages, key) => (key === "won" ? "ok" : key === "lost" ? "danger" : STAGE_TONE[Math.max(0, stages.findIndex((s) => s.key === key)) % STAGE_TONE.length]);
function daysLeft(date) {
  if (!date) return null;
  const d = Math.round((new Date(date + "T12:00:00") - new Date()) / 86400000);
  if (d < 0) return { text: `${-d} d overdue`, late: true };
  if (d === 0) return { text: "Closes today", late: false };
  return { text: `${d} d left`, late: false };
}

export default function PipelineV2({ tenantId }) {
  const x = usePipeline(tenantId);
  const [show, setShow] = useState("open");
  const [editing, setEditing] = useState(null);     // null | {} new | deal
  const [dragId, setDragId] = useState(null);
  const [overStage, setOverStage] = useState(null);
  const stages = x.board?.stages || [];
  const columns = useMemo(() => stages.filter((s) => show === "all" || !["won", "lost"].includes(s.key)), [stages, show]);

  if (x.board === null) return (
    <div className="vpl"><PageHeader sub="Every deal from first contact to closed." />
      <div className="vpl-board">{[0, 1, 2, 3].map((i) => <Skeleton key={i} height={320} style={{ borderRadius: 20, minWidth: 260 }} />)}</div></div>
  );
  if (x.error && !x.board.deals.length) return <div className="vpl"><ErrorCard what="the pipeline" onRetry={x.load} /></div>;

  const s = x.board.summary || {};
  const deals = x.board.deals || [];
  const nextStage = (key) => {
    const open = stages.filter((st) => st.key !== "lost");
    const i = open.findIndex((st) => st.key === key);
    return i >= 0 && i < open.length - 1 ? open[i + 1] : null;
  };
  const drop = async (stageKey) => {
    const id = dragId; setDragId(null); setOverStage(null);
    const d = deals.find((dd) => dd.id === id);
    if (!d || d.stage === stageKey) return;
    const r = await x.move(id, stageKey);
    if (r.error) toast(r.error, "danger");
  };

  return (
    <div className="vpl">
      <PageHeader sub="Every deal from first contact to closed. Drag a card to move it, or tell Vula on WhatsApp: “move Priya to offer at R1.24m”." />
      <div className="vpl-kpis">
        <Kpi label="Open deals" value={s.open_count ?? 0} />
        <Kpi label="In the pipeline" value={money(s.open_value_cents || 0)} />
        <Kpi label="Weighted value" value={money(s.weighted_value_cents || 0)} hint="Each deal's value × its stage's chance of closing" />
        <Kpi label="Won this month" value={money(s.won_this_month_cents || 0)} sub={`${s.won_this_month_count || 0} deal${s.won_this_month_count === 1 ? "" : "s"}`} />
        <Kpi label="Win rate" value={s.win_rate == null ? "—" : `${s.win_rate}%`} />
      </div>
      <div className="vpl-bar">
        <PillTabs label="Which columns" active={show} onChange={setShow} tabs={[{ id: "open", label: "Open" }, { id: "all", label: "Show won & lost" }]} />
        <Button Icon={Plus} onClick={() => setEditing({})}>New deal</Button>
      </div>
      {deals.length === 0 ? (
        <EmptyState Icon={KanbanSquare} title="No deals yet" action={<Button Icon={Plus} onClick={() => setEditing({})}>Add your first deal</Button>}>
          Add one here, turn an enquiry into a deal, or tell Vula on WhatsApp.
        </EmptyState>
      ) : (
        <div className="vpl-board" role="list" aria-label="Pipeline stages">
          {columns.map((st) => {
            const rows = deals.filter((d) => d.stage === st.key);
            const total = s.by_stage?.[st.key];
            return (
              <section key={st.key} role="listitem" aria-label={`${st.label}: ${rows.length} deals`}
                className={`vpl-col${overStage === st.key ? " vpl-col--over" : ""}`}
                onDragOver={(e) => { if (dragId) { e.preventDefault(); setOverStage(st.key); } }}
                onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOverStage(null); }}
                onDrop={(e) => { e.preventDefault(); drop(st.key); }}>
                <header className="vpl-col-head">
                  <span className="vpl-col-title">{st.label} <span className="vpl-count">{rows.length}</span></span>
                  <span className="vpl-col-sum">{money(total?.value_cents || 0)}</span>
                  <span className={`vpl-rule vpl-tone-${toneFor(stages, st.key)}`} aria-hidden />
                </header>
                <div className="vpl-cards">
                  {rows.map((d) => (
                    <DealCard key={d.id} d={d} stages={stages} listing={d.ref_type === "property" ? x.board.listings?.[d.ref_id] : null}
                      next={nextStage(d.stage)} dragging={dragId === d.id}
                      onDragStart={() => setDragId(d.id)} onDragEnd={() => { setDragId(null); setOverStage(null); }}
                      onOpen={() => setEditing(d)}
                      onNext={async (n) => { const r = await x.move(d.id, n.key); if (r.error) toast(r.error, "danger"); else toast(`Moved to ${n.label}`); }} />
                  ))}
                  {rows.length === 0 && <p className="vpl-drop">{dragId ? "Drop here" : "No deals"}</p>}
                </div>
              </section>
            );
          })}
        </div>
      )}
      {editing && <DealSheet x={x} deal={editing} stages={stages} onClose={() => setEditing(null)} />}
    </div>
  );
}

function Kpi({ label, value, sub, hint }) {
  return (
    <div className="vpl-kpi" title={hint}>
      <span className="vpl-kpi-v">{value}</span>
      <span className="vpl-kpi-l">{label}{sub ? ` · ${sub}` : ""}</span>
    </div>
  );
}

function DealCard({ d, stages, listing, next, dragging, onDragStart, onDragEnd, onOpen, onNext }) {
  const due = daysLeft(d.expected_close);
  const closed = d.stage === "won" || d.stage === "lost";
  return (
    <article className={`vpl-card${dragging ? " vpl-card--drag" : ""}`} draggable onDragStart={(e) => { e.dataTransfer.effectAllowed = "move"; onDragStart(); }} onDragEnd={onDragEnd}>
      <button type="button" className="vpl-card-open" onClick={onOpen} aria-label={`Open ${d.title}`}>
        {listing?.image && (
          <span className="vpl-photo" style={{ backgroundImage: `url("${listing.image}")` }}>
            <span className="vpl-tag">{listing.listing_type === "rent" ? "RENT" : "SALE"}</span>
            {d.stage === "won" && <span className="vpl-stamp">{listing.listing_type === "rent" ? "LET" : "SOLD"}</span>}
          </span>
        )}
        <span className="vpl-card-body">
          <span className="vpl-card-title">{d.title}</span>
          {d.contact_name && <span className="vpl-meta"><User size={13} aria-hidden /> {d.contact_name}</span>}
          <span className="vpl-value-row">
            {d.value_cents == null ? <span className="vpl-novalue">No value yet</span> : <span className="vpl-value">{money(d.value_cents)}</span>}
            {listing?.price_cents && d.value_cents != null && !closed && <VsAsking value={d.value_cents} asking={listing.price_cents} />}
          </span>
          <span className="vpl-foot">
            {due && !closed ? <span className={due.late ? "vpl-late" : ""}><Calendar size={13} aria-hidden /> {due.text}</span>
              : closed && d.closed_at ? <span><Calendar size={13} aria-hidden /> {d.stage === "won" ? "Won" : "Lost"} {new Date(d.closed_at).toLocaleDateString("en-ZA", { day: "numeric", month: "short" })}</span>
              : <span />}
            {d.owner && <span className="vpl-owner" title={d.owner}>{initials(d.owner)}</span>}
          </span>
        </span>
      </button>
      {next && !closed && (
        <button type="button" className="vpl-next" onClick={() => onNext(next)} aria-label={`Move ${d.title} to ${next.label}`}>
          {next.label} <ArrowRight size={13} aria-hidden />
        </button>
      )}
    </article>
  );
}

function VsAsking({ value, asking }) {
  const pct = Math.round(((value - asking) / asking) * 1000) / 10;
  if (pct === 0) return <span className="vpl-pct vpl-pct--ok">At asking</span>;
  return <span className={`vpl-pct ${pct > 0 ? "vpl-pct--ok" : "vpl-pct--low"}`}>{pct > 0 ? "+" : "−"}{Math.abs(pct)}%</span>;
}

const initials = (n) => n.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
const rands = (c) => (c == null ? "" : String(Math.round(c / 100)));

function DealSheet({ x, deal, stages, onClose }) {
  const isNew = !deal.id;
  const [f, setF] = useState({
    title: deal.title || "", stage: deal.stage || stages[0]?.key || "", value: rands(deal.value_cents),
    contact_name: deal.contact_name || "", contact_phone: deal.contact_phone || "", contact_email: deal.contact_email || "",
    owner: deal.owner || "", expected_close: deal.expected_close || "", notes: deal.notes || "",
    ref_id: deal.ref_type === "property" ? deal.ref_id || "" : "",
  });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));
  const save = async () => {
    setSaving(true); setErr("");
    const body = { ...f, ref_type: f.ref_id ? "property" : null, ref_id: f.ref_id || null };
    if (!isNew) body.id = deal.id;
    const r = await x.save(body);
    setSaving(false);
    if (r.error) { setErr(r.error); return; }
    toast(isNew ? "Deal added" : "Deal saved");
    onClose();
  };
  const remove = async () => {
    if (!(await confirmDialog(`Delete the deal "${deal.title}"?`, { danger: true, confirmLabel: "Delete" }))) return;
    const r = await x.remove(deal.id);
    if (r.error) { setErr(r.error); return; }
    toast("Deal deleted");
    onClose();
  };
  const wa = f.contact_phone ? `https://wa.me/${f.contact_phone.replace(/\D/g, "").replace(/^0/, "27")}` : null;
  return (
    <Sheet open title={isNew ? "New deal" : "Deal"} onClose={onClose} footer={
      <div className="vpl-sheet-foot">
        {!isNew && <Button variant="danger" Icon={Trash2} onClick={remove}>Delete</Button>}
        <span style={{ flex: 1 }} />
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button onClick={save} disabled={saving || !f.title.trim()}>{saving ? "Saving…" : "Save"}</Button>
      </div>}>
      <div className="vpl-form">
        {err && <p className="vpl-err" role="alert">{err}</p>}
        <Field label="Deal"><Input value={f.title} onChange={set("title")} placeholder="Priya — Hollis Modern House" autoFocus /></Field>
        <div className="vpl-2">
          <Field label="Stage"><Select value={f.stage} onChange={set("stage")}>{stages.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</Select></Field>
          <Field label="Value (R)" hint="Offer, quote or deal value"><Input value={f.value} onChange={set("value")} inputMode="decimal" placeholder="1 240 000" /></Field>
        </div>
        {x.properties.length > 0 && (
          <Field label="Property"><Select value={f.ref_id} onChange={set("ref_id")}><option value="">— Not about a listing —</option>
            {x.properties.map((p) => <option key={p.id} value={p.id}>{p.title}{p.suburb ? ` · ${p.suburb}` : ""}</option>)}</Select></Field>
        )}
        <div className="vpl-2">
          <Field label="Contact name"><Input value={f.contact_name} onChange={set("contact_name")} /></Field>
          <Field label="WhatsApp"><Input value={f.contact_phone} onChange={set("contact_phone")} inputMode="tel" /></Field>
        </div>
        <Field label="Email"><Input type="email" value={f.contact_email} onChange={set("contact_email")} /></Field>
        {wa && <a className="vpl-wa" href={wa} target="_blank" rel="noreferrer">💬 WhatsApp {f.contact_name || "them"}</a>}
        <div className="vpl-2">
          <Field label="Who's on it"><Input value={f.owner} onChange={set("owner")} /></Field>
          <Field label="Expected to close"><Input type="date" value={f.expected_close} onChange={set("expected_close")} /></Field>
        </div>
        <Field label="Notes"><Textarea value={f.notes} onChange={set("notes")} rows={4} /></Field>
      </div>
    </Sheet>
  );
}
