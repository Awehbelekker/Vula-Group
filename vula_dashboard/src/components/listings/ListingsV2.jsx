/**
 * ListingsV2.jsx — real-estate Listings (2026-11-10): the agency's properties (grid, add/edit),
 * the enquiry inbox (website + WhatsApp leads, moved New → Contacted → Viewing → Closed) and its
 * agents. Shown only to businesses with the `listings` module (navConfig: tab id = module id).
 * Look follows docs/real-estate-workspace-brief.md — scoped to .vls so no other screen changes.
 */
import { useEffect, useMemo, useState } from "react";
import { BellRing, Building2, Inbox, KanbanSquare, MessageCircle, Pencil, Plus, Send, Trash2, Users } from "lucide-react";
import { formatMoney } from "../../lib/money";
import VulaImageUpload from "../VulaImageUpload";
import { Button, EmptyState, Field, Input, PageHeader, PillTabs, Select, Sheet, Skeleton, StatusChip, Textarea, confirmDialog, toast } from "../ui/index.jsx";
import { ENQUIRY_STATUS, PROPERTY_TYPES, STATUS, useListings } from "./useListings";
import { ListingsMap, PinPicker } from "./ListingMap";
import "./listings.css";

const price = (p) => (p.price_on_request || p.price_cents == null ? "Price on request"
  : formatMoney(p.price_cents, { cents: "never" }) + (p.listing_type === "rent" ? " pm" : ""));
const num = (v) => (v == null ? null : Number(v) % 1 === 0 ? Number(v) : Number(v).toFixed(1));
const facts = (p) => [p.bedrooms != null && `${num(p.bedrooms)} bed`, p.bathrooms != null && `${num(p.bathrooms)} bath`,
  p.garages && `${p.garages} garage${p.garages > 1 ? "s" : ""}`, p.floor_m2 && `${p.floor_m2} m²`].filter(Boolean);
const ago = (iso) => {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 60) return `${Math.max(m, 1)} min ago`;
  if (m < 1440) return `${Math.round(m / 60)} h ago`;
  return new Date(iso).toLocaleDateString("en-ZA", { day: "numeric", month: "short" });
};

export default function ListingsV2({ tenantId }) {
  const x = useListings(tenantId);
  const fresh = (x.enquiries || []).filter((e) => e.status === "new").length;
  return (
    <div className="vls">
      <PageHeader sub="Your properties, the people asking about them, and your agents. Agents can also add listings and photos on WhatsApp." />
      <Kpis x={x} />
      <div className="vls-bar">
        <PillTabs label="Listings view" active={x.view} onChange={x.setView} tabs={[
          { id: "listings", label: "Listings" },
          { id: "enquiries", label: fresh ? `Enquiries (${fresh} new)` : "Enquiries" },
          { id: "clients", label: "Clients" },
          { id: "agents", label: "Agents" },
        ]} />
      </div>
      {x.view === "listings" && <ListingsView x={x} tenantId={tenantId} />}
      {x.view === "enquiries" && <EnquiriesView x={x} />}
      {x.view === "clients" && <ClientsView x={x} />}
      {x.view === "agents" && <AgentsView x={x} />}
    </div>
  );
}

function Kpis({ x }) {
  const l = x.listings || [];
  const tiles = [
    { label: "On the market", value: l.filter((p) => p.status === "active").length },
    { label: "Under offer", value: l.filter((p) => p.status === "under_offer").length },
    { label: "Sold / let", value: l.filter((p) => p.status === "sold" || p.status === "let").length },
    { label: "New enquiries", value: (x.enquiries || []).filter((e) => e.status === "new").length },
  ];
  return (
    <div className="vls-kpis">
      {tiles.map((t) => (
        <div key={t.label} className="vls-kpi">
          <span className="vls-kpi-v">{x.listings === null ? "…" : t.value}</span>
          <span className="vls-kpi-l">{t.label}</span>
        </div>
      ))}
    </div>
  );
}

// ── Listings ──────────────────────────────────────────────────────────────────
function ListingsView({ x, tenantId }) {
  const [filter, setFilter] = useState("all");
  const [editing, setEditing] = useState(null);     // null | {} (new) | listing
  const [layout, setLayout] = useState("grid");     // grid | map
  const rows = useMemo(() => (x.listings || []).filter((p) => filter === "all" || p.status === filter
    || (filter === "closed" && (p.status === "sold" || p.status === "let"))), [x.listings, filter]);
  const agentName = (id) => (x.agents || []).find((a) => a.id === id)?.name;
  return (
    <>
      <div className="vls-bar">
        <PillTabs label="Filter listings" active={filter} onChange={setFilter} tabs={[
          { id: "all", label: "All" }, { id: "active", label: "On the market" }, { id: "under_offer", label: "Under offer" },
          { id: "closed", label: "Sold / let" }, { id: "draft", label: "Drafts" },
        ]} />
        <span className="vls-row">
          <PillTabs label="Show as" active={layout} onChange={setLayout} tabs={[{ id: "grid", label: "Grid" }, { id: "map", label: "Map" }]} />
          <Button Icon={Plus} onClick={() => setEditing({})}>New listing</Button>
        </span>
      </div>
      {x.listings === null ? (
        <div className="vls-grid">{[0, 1, 2].map((i) => <Skeleton key={i} height={280} style={{ borderRadius: 20 }} />)}</div>
      ) : rows.length === 0 ? (
        <EmptyState Icon={Building2} title={filter === "all" ? "No listings yet" : "Nothing here"}
          action={<Button Icon={Plus} onClick={() => setEditing({})}>Add your first listing</Button>}>
          Add one here, or WhatsApp Vula: "new listing, 3 bed house in Bellville, R1.95m".
        </EmptyState>
      ) : layout === "map" ? (
        <ListingsMap listings={rows} onOpen={setEditing} />
      ) : (
        <div className="vls-grid" role="list">
          {rows.map((p) => {
            const st = STATUS[p.status] || { label: p.status, tone: "muted" };
            const img = (p.images || [])[0];
            return (
              <button key={p.id} type="button" role="listitem" className="vls-card" onClick={() => setEditing(p)}>
                <span className="vls-photo" style={img ? { backgroundImage: `url("${img}")` } : undefined}>
                  <span className="vls-tag">{p.listing_type === "rent" ? "RENT" : "SALE"}</span>
                  {!img && <Building2 aria-hidden size={28} />}
                </span>
                <span className="vls-body">
                  <span className="vls-title">{p.title}</span>
                  <span className="vls-muted">{[p.suburb, p.city].filter(Boolean).join(", ") || "No area set"}</span>
                  <span className="vls-row">
                    <span className="vls-price">{price(p)}</span>
                    <StatusChip tone={st.tone}>{st.label}</StatusChip>
                  </span>
                  <span className="vls-facts">{facts(p).map((f) => <span key={f}>{f}</span>)}</span>
                  {agentName(p.agent_id) && <span className="vls-muted">Agent: {agentName(p.agent_id)}</span>}
                </span>
              </button>
            );
          })}
        </div>
      )}
      {editing && <ListingSheet x={x} tenantId={tenantId} listing={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

const rands = (cents) => (cents == null ? "" : String(Math.round(cents / 100)));

function ListingSheet({ x, tenantId, listing, onClose }) {
  const isNew = !listing.id;
  const [f, setF] = useState(() => ({
    title: listing.title || "", listing_type: listing.listing_type || "sale", status: listing.status || "active",
    property_type: listing.property_type || "house", price: rands(listing.price_cents),
    price_on_request: !!listing.price_on_request, suburb: listing.suburb || "", city: listing.city || "",
    address: listing.address || "", show_address: !!listing.show_address,
    bedrooms: listing.bedrooms ?? "", bathrooms: listing.bathrooms ?? "", garages: listing.garages ?? "",
    parking: listing.parking ?? "", floor_m2: listing.floor_m2 ?? "", erf_m2: listing.erf_m2 ?? "",
    levies: rands(listing.levies_cents), rates: rands(listing.rates_cents),
    description: listing.description || "", features: (listing.features || []).join(", "),
    images: listing.images || [], agent_id: listing.agent_id || "", featured: !!listing.featured,
    available_from: listing.available_from || "",
    pin: listing.latitude != null && listing.longitude != null ? { lat: listing.latitude, lng: listing.longitude } : null,
  }));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e?.target ? (e.target.type === "checkbox" ? e.target.checked : e.target.value) : e }));

  const save = async () => {
    setSaving(true); setErr("");
    const { pin, ...rest } = f;
    const body = { ...rest, agent_id: f.agent_id || null, latitude: pin?.lat ?? null, longitude: pin?.lng ?? null };
    if (!isNew) body.id = listing.id;
    const r = await x.saveListing(body);
    setSaving(false);
    if (r.error) { setErr(r.error); return; }
    toast(isNew ? "Listing added" : "Listing saved");
    onClose();
    const saved = r.property;
    if (saved && (saved.status === "active" || saved.status === "under_offer")) {
      const m = await x.matchesFor(saved.id);
      const n = (m.matches || []).length;
      if (n) toast(`${n} client${n === 1 ? " is" : "s are"} looking for something like "${saved.title}" — open it to send it to them.`, "info");
    }
  };
  const remove = async () => {
    if (!(await confirmDialog(`Delete "${listing.title}"? Enquiries about it are kept.`, { danger: true, confirmLabel: "Delete" }))) return;
    const r = await x.deleteListing(listing.id);
    if (r.error) { setErr(r.error); return; }
    toast("Listing deleted");
    onClose();
  };

  return (
    <Sheet open title={isNew ? "New listing" : "Edit listing"} onClose={onClose} footer={
      <div className="vls-sheet-foot">
        {!isNew && <Button variant="danger" Icon={Trash2} onClick={remove}>Delete</Button>}
        <span style={{ flex: 1 }} />
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button onClick={save} disabled={saving || !f.title.trim()}>{saving ? "Saving…" : "Save"}</Button>
      </div>}>
      <div className="vls-form">
        {err && <p className="vls-err" role="alert">{err}</p>}
        {!isNew && (listing.status === "active" || listing.status === "under_offer") && <ListingMatches x={x} listing={listing} />}
        <Field label="Title"><Input value={f.title} onChange={set("title")} placeholder="3 bedroom family home with pool" autoFocus /></Field>
        <div className="vls-2">
          <Field label="For"><Select value={f.listing_type} onChange={set("listing_type")}><option value="sale">Sale</option><option value="rent">Rent</option></Select></Field>
          <Field label="Status"><Select value={f.status} onChange={set("status")}>{Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</Select></Field>
        </div>
        <div className="vls-2">
          <Field label={f.listing_type === "rent" ? "Rent per month (R)" : "Asking price (R)"} hint="e.g. 2 450 000 or R2.45m">
            <Input value={f.price} onChange={set("price")} inputMode="decimal" disabled={f.price_on_request} /></Field>
          <Field label="Type"><Select value={f.property_type} onChange={set("property_type")}>{PROPERTY_TYPES.map((t) => <option key={t} value={t}>{t[0].toUpperCase() + t.slice(1)}</option>)}</Select></Field>
        </div>
        <label className="vls-check"><input type="checkbox" checked={f.price_on_request} onChange={set("price_on_request")} /> Show “Price on request” instead</label>
        <div className="vls-2">
          <Field label="Suburb"><Input value={f.suburb} onChange={set("suburb")} /></Field>
          <Field label="City / town"><Input value={f.city} onChange={set("city")} /></Field>
        </div>
        <Field label="Street address" hint="Kept private unless you tick the box below."><Input value={f.address} onChange={set("address")} /></Field>
        <label className="vls-check"><input type="checkbox" checked={f.show_address} onChange={set("show_address")} /> Show the address on the website</label>
        <Field label="Location on the map" hint="Used on your map. On the website it only shows when the address is shown.">
          <PinPicker tenantId={tenantId} value={f.pin} onChange={(pin) => setF((s) => ({ ...s, pin }))}
            addressText={[f.address, f.suburb, f.city].filter(Boolean).join(", ")} />
        </Field>
        <div className="vls-3">
          <Field label="Bedrooms"><Input type="number" step="0.5" min="0" value={f.bedrooms} onChange={set("bedrooms")} /></Field>
          <Field label="Bathrooms"><Input type="number" step="0.5" min="0" value={f.bathrooms} onChange={set("bathrooms")} /></Field>
          <Field label="Garages"><Input type="number" min="0" value={f.garages} onChange={set("garages")} /></Field>
          <Field label="Parking"><Input type="number" min="0" value={f.parking} onChange={set("parking")} /></Field>
          <Field label="Floor m²"><Input type="number" min="0" value={f.floor_m2} onChange={set("floor_m2")} /></Field>
          <Field label="Erf m²"><Input type="number" min="0" value={f.erf_m2} onChange={set("erf_m2")} /></Field>
        </div>
        <div className="vls-2">
          <Field label="Levies per month (R)"><Input value={f.levies} onChange={set("levies")} inputMode="decimal" /></Field>
          <Field label="Rates per month (R)"><Input value={f.rates} onChange={set("rates")} inputMode="decimal" /></Field>
        </div>
        {f.listing_type === "rent" && <Field label="Available from"><Input type="date" value={f.available_from} onChange={set("available_from")} /></Field>}
        <Field label="Agent"><Select value={f.agent_id} onChange={set("agent_id")}><option value="">— None —</option>{(x.agents || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Select></Field>
        <Field label="Description"><Textarea value={f.description} onChange={set("description")} rows={5} /></Field>
        <Field label="Features" hint="Comma separated: Pool, Solar, Pet friendly"><Input value={f.features} onChange={set("features")} /></Field>
        <label className="vls-check"><input type="checkbox" checked={f.featured} onChange={set("featured")} /> Feature on the website homepage</label>
        <Field label="Photos" hint="The first photo is the cover.">
          {f.images.length > 0 && (
            <div className="vls-thumbs">
              {f.images.map((u, i) => (
                <span key={u} className="vls-thumb" style={{ backgroundImage: `url("${u}")` }}>
                  {i === 0 && <span className="vls-tag">Cover</span>}
                  <button type="button" aria-label={`Remove photo ${i + 1}`} onClick={() => setF((s) => ({ ...s, images: s.images.filter((x2) => x2 !== u) }))}>✕</button>
                  {i > 0 && <button type="button" className="vls-cover" onClick={() => setF((s) => ({ ...s, images: [u, ...s.images.filter((x2) => x2 !== u)] }))}>Make cover</button>}
                </span>
              ))}
            </div>
          )}
          <VulaImageUpload tenantId={tenantId} maxFiles={20} onUploaded={(urls) => setF((s) => ({ ...s, images: [...s.images, ...urls.filter((u) => !s.images.includes(u))] }))} />
        </Field>
      </div>
    </Sheet>
  );
}

// ── Enquiries ─────────────────────────────────────────────────────────────────
function EnquiriesView({ x }) {
  const [filter, setFilter] = useState("open");
  const titles = Object.fromEntries((x.listings || []).map((p) => [p.id, p.title]));
  const rows = (x.enquiries || []).filter((e) => filter === "all" || (filter === "open" ? e.status !== "closed" : e.status === filter));
  if (x.enquiries === null) return <Skeleton lines={4} />;
  return (
    <>
      <div className="vls-bar">
        <PillTabs label="Filter enquiries" active={filter} onChange={setFilter} tabs={[
          { id: "open", label: "Open" }, { id: "new", label: "New" }, { id: "contacted", label: "Contacted" },
          { id: "viewing", label: "Viewing" }, { id: "closed", label: "Closed" }, { id: "all", label: "All" },
        ]} />
      </div>
      {rows.length === 0 ? (
        <EmptyState Icon={Inbox} title="No enquiries here">People who ask about a listing on your website or on WhatsApp show up here, and the agent is told straight away.</EmptyState>
      ) : (
        <div className="vls-list" role="list">
          {rows.map((e) => {
            const st = ENQUIRY_STATUS[e.status] || { label: e.status, tone: "muted" };
            const wa = e.whatsapp ? `https://wa.me/${e.whatsapp}` : null;
            return (
              <div key={e.id} role="listitem" className="vls-enq">
                <span className="vls-avatar" aria-hidden>{(e.name || "?").slice(0, 2).toUpperCase()}</span>
                <span className="vls-enq-main">
                  <span className="vls-row">
                    <span className="vls-strong">{e.name}</span>
                    <StatusChip tone={st.tone}>{st.label}</StatusChip>
                    {e.wants_viewing && <StatusChip tone="accent">Wants a viewing</StatusChip>}
                  </span>
                  <span className="vls-muted">
                    {[titles[e.property_id] || (e.property_id ? "A listing" : "General enquiry"), e.source === "whatsapp" ? "WhatsApp" : "Website", ago(e.created_at)].join(" · ")}
                  </span>
                  {e.preferred_times && <span className="vls-muted">Prefers: {e.preferred_times}</span>}
                  {e.message && <span className="vls-quote">“{e.message}”</span>}
                  <span className="vls-actions">
                    {wa && <a className="vls-wa" href={wa} target="_blank" rel="noreferrer"><MessageCircle size={14} aria-hidden /> WhatsApp</a>}
                    {e.email && <a className="vls-wa" href={`mailto:${e.email}`}>Email</a>}
                    {x.modules.includes("pipeline") && (
                      <button type="button" className="vls-wa" onClick={async () => {
                        const r = await x.toPipeline(e.id);
                        if (r.error) toast(r.error, "danger");
                        else toast(r.existing ? "Already in the pipeline" : "Added to the pipeline");
                      }}><KanbanSquare size={14} aria-hidden /> Add to pipeline</button>
                    )}
                    <Select aria-label={`Status for ${e.name}`} value={e.status} onChange={async (ev) => {
                      const r = await x.setEnquiryStatus(e.id, ev.target.value);
                      if (r.error) toast(r.error, "danger");
                    }} className="vls-status">
                      {Object.entries(ENQUIRY_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                    </Select>
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

// ── Clients looking for something (wish lists) ───────────────────────────────
function ListingMatches({ x, listing }) {
  const [rows, setRows] = useState(null);
  const [picked, setPicked] = useState([]);
  const [sending, setSending] = useState(false);
  useEffect(() => {
    let live = true;
    x.matchesFor(listing.id).then((r) => { if (live) { setRows(r.matches || []); setPicked((r.matches || []).map((m) => m.phone)); } });
    return () => { live = false; };
  }, [listing.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!rows || rows.length === 0) return null;
  const send = async () => {
    if (!picked.length) return;
    if (!(await confirmDialog(`Send "${listing.title}" to ${picked.length} client${picked.length === 1 ? "" : "s"} on WhatsApp?`, { confirmLabel: "Send" }))) return;
    setSending(true);
    const r = await x.shareListing(listing.id, picked);
    setSending(false);
    if (r.error) { toast(r.error, "danger"); return; }
    const sent = new Set(r.sent || []);
    setRows((all) => all.filter((m) => !sent.has(m.phone)));
    setPicked((p) => p.filter((ph) => !sent.has(ph)));
    if ((r.failed || []).length) toast(`Sent to ${sent.size}. WhatsApp refused ${r.failed.length} — usually because they haven't messaged in the last 24 hours.`, "warn");
    else toast(`Sent to ${sent.size} client${sent.size === 1 ? "" : "s"}`);
  };
  return (
    <div className="vls-matches" role="region" aria-label="Clients looking for this">
      <div className="vls-row"><BellRing size={16} aria-hidden /><span className="vls-strong">{rows.length} client{rows.length === 1 ? " is" : "s are"} looking for something like this</span></div>
      {rows.map((m) => (
        <label key={m.phone} className="vls-check vls-match">
          <input type="checkbox" checked={picked.includes(m.phone)} onChange={(e) => setPicked((p) => e.target.checked ? [...p, m.phone] : p.filter((ph) => ph !== m.phone))} />
          <span><span className="vls-strong">{m.name || m.phone}</span><span className="vls-muted"> · {m.looking_for}</span></span>
        </label>
      ))}
      <Button size="sm" Icon={Send} onClick={send} disabled={sending || !picked.length}>{sending ? "Sending…" : `Send on WhatsApp (${picked.length})`}</Button>
    </div>
  );
}

function ClientsView({ x }) {
  if (x.wishes === null) return <Skeleton lines={3} />;
  if (x.wishes.length === 0) {
    return (
      <EmptyState Icon={Users} title="No clients' wish lists yet">
        When someone searches your listings on WhatsApp — or an agent tells Vula "Thandi wants a 3 bed in Durbanville under R3m" — it's kept here, and you're shown them when a matching listing goes on the market.
      </EmptyState>
    );
  }
  return (
    <div className="vls-list" role="list">
      {x.wishes.map((w) => (
        <div key={w.id} role="listitem" className="vls-enq">
          <span className="vls-avatar" aria-hidden>{(w.name || "?").slice(0, 2).toUpperCase()}</span>
          <span className="vls-enq-main">
            <span className="vls-row"><span className="vls-strong">{w.name || w.phone}</span><StatusChip tone="info">{w.source === "agent" ? "Added by an agent" : "From their search"}</StatusChip></span>
            <span className="vls-quote">Looking for {w.looking_for}</span>
            <span className="vls-actions">
              <a className="vls-wa" href={`https://wa.me/${w.phone}`} target="_blank" rel="noreferrer"><MessageCircle size={14} aria-hidden /> WhatsApp</a>
              <button type="button" className="vls-wa" onClick={async () => {
                if (!(await confirmDialog(`Stop matching listings to ${w.name || w.phone}?`, { confirmLabel: "Stop" }))) return;
                const r = await x.closeWish(w.id);
                if (r.error) toast(r.error, "danger");
              }}>Stop matching</button>
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Agents ────────────────────────────────────────────────────────────────────
function AgentsView({ x }) {
  const [editing, setEditing] = useState(null);
  if (x.agents === null) return <Skeleton lines={3} />;
  return (
    <>
      <div className="vls-bar"><span /><Button Icon={Plus} onClick={() => setEditing({})}>Add agent</Button></div>
      {x.agents.length === 0 ? (
        <EmptyState Icon={Users} title="No agents yet">Add your agents so enquiries reach the right person and they show on your website.</EmptyState>
      ) : (
        <div className="vls-agents">
          {x.agents.map((a) => (
            <div key={a.id} className="vls-agent">
              <span className="vls-agent-photo" style={a.photo_url ? { backgroundImage: `url("${a.photo_url}")` } : undefined}>{!a.photo_url && (a.name || "?").slice(0, 2).toUpperCase()}</span>
              <span className="vls-strong">{a.name}</span>
              <span className="vls-muted">{a.title || "Agent"}{a.active ? "" : " · hidden"}</span>
              <span className="vls-muted">{a.whatsapp || "No WhatsApp number"}</span>
              <Button size="sm" variant="secondary" Icon={Pencil} onClick={() => setEditing(a)}>Edit</Button>
            </div>
          ))}
        </div>
      )}
      {editing && <AgentSheet x={x} agent={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function AgentSheet({ x, agent, onClose }) {
  const isNew = !agent.id;
  const [f, setF] = useState({ name: agent.name || "", title: agent.title || "", whatsapp: agent.whatsapp || "", email: agent.email || "",
    photo_url: agent.photo_url || "", bio: agent.bio || "", ppra_ffc: agent.ppra_ffc || "", active: agent.active ?? true });
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const save = async () => {
    const r = await x.saveAgent(isNew ? f : { ...f, id: agent.id });
    if (r.error) { setErr(r.error); return; }
    toast(isNew ? "Agent added" : "Agent saved");
    onClose();
  };
  const remove = async () => {
    if (!(await confirmDialog(`Remove ${agent.name}? Their listings stay, without an agent.`, { danger: true, confirmLabel: "Remove" }))) return;
    const r = await x.deleteAgent(agent.id);
    if (r.error) { setErr(r.error); return; }
    onClose();
  };
  return (
    <Sheet open title={isNew ? "Add agent" : "Edit agent"} onClose={onClose} footer={
      <div className="vls-sheet-foot">
        {!isNew && <Button variant="danger" Icon={Trash2} onClick={remove}>Remove</Button>}
        <span style={{ flex: 1 }} />
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button onClick={save} disabled={!f.name.trim()}>Save</Button>
      </div>}>
      <div className="vls-form">
        {err && <p className="vls-err" role="alert">{err}</p>}
        <Field label="Name"><Input value={f.name} onChange={set("name")} autoFocus /></Field>
        <Field label="Role"><Input value={f.title} onChange={set("title")} placeholder="Principal agent, Rentals…" /></Field>
        <Field label="WhatsApp number" hint="Enquiries on this agent's listings are sent here."><Input value={f.whatsapp} onChange={set("whatsapp")} inputMode="tel" /></Field>
        <Field label="Email"><Input type="email" value={f.email} onChange={set("email")} /></Field>
        <Field label="Photo link"><Input value={f.photo_url} onChange={set("photo_url")} placeholder="https://…" /></Field>
        <Field label="Short bio"><Textarea value={f.bio} onChange={set("bio")} rows={3} /></Field>
        <Field label="PPRA Fidelity Fund Certificate no."><Input value={f.ppra_ffc} onChange={set("ppra_ffc")} /></Field>
        <label className="vls-check"><input type="checkbox" checked={f.active} onChange={set("active")} /> Show on the website</label>
      </div>
    </Sheet>
  );
}
