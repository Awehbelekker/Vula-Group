/**
 * ProjectsV2.jsx — the redesigned Projects command centre (2026-10-19, UX redesign Phase F). Same
 * data and actions as the current screen (projects/useProjects.js): projects grouped by client
 * with their phases, one page per project (Overview / Documents / BOQ / Money / Standards & team),
 * and the master code library when no project is open. Two panes from 1024px; on a phone the list
 * or the open project, with a way back.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, BookOpen, CornerDownRight, FolderKanban, Pencil, Plus, Upload, X } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, EmptyState, Field, FilterBar, IconButton, Input, ListRow, PageHeader, PillTabs, Select, StatusChip, Textarea } from "../ui/index.jsx";
import FiledLibraryV2 from "../documents/FiledLibraryV2";
import { PROJECT_TABS, createShare, fetchBoq, fetchTracker, revokeShare, saveBoqTotal, uploadToProject, useProjects } from "./useProjects";
import "./projects.css";

const STATUS = { active: { label: "Active", tone: "ok" }, on_hold: { label: "On hold", tone: "highlight" }, complete: { label: "Complete", tone: "muted" } };
const statusChip = (s) => { const st = STATUS[s] || { label: (s || "").replace("_", " "), tone: "muted" }; return <StatusChip tone={st.tone}>{st.label}</StatusChip>; };
const money = (c) => formatMoney(c || 0, { cents: "always" });

export default function ProjectsV2({ tenantId }) {
  const x = useProjects(tenantId);
  const open = !!x.sel;
  return (
    <div className="vpj" data-open={open ? "true" : "false"}>
      <PageHeader sub="Your clients' projects and their phases — each with its own documents, BOQ and money." />
      <datalist id="vpj-clients">{x.allClients.map((c) => <option key={c} value={c} />)}</datalist>
      <div className="vpj-panes">
        <section className="vpj-list" aria-label="Projects">
          <FilterBar query={x.query} onQuery={x.setQuery} placeholder="Find a project, client or number" />
          {x.groups.length === 0 ? (
            <EmptyState Icon={FolderKanban} title={x.query ? "No projects match" : "No projects yet"}>
              {x.query ? "Try another name, client or number." : "Create your first project below."}
            </EmptyState>
          ) : x.groups.map(([client, list]) => (
            <div key={client} className="vpj-group">
              <h2 className="vpj-client">{client}</h2>
              <Card pad={4} role="list">
                {list.map((p) => (
                  <div key={p.id} role="listitem">
                    <ProjectRow p={p} active={x.sel?.id === p.id} onClick={() => x.openProject(p.id)} />
                    {p.phases.map((ph) => <ProjectRow key={ph.id} p={ph} phase active={x.sel?.id === ph.id} onClick={() => x.openProject(ph.id)} />)}
                  </div>
                ))}
              </Card>
            </div>
          ))}
          <Card className="vpj-new">
            <h2 className="vpj-h"><Plus size={18} aria-hidden="true" />New project</h2>
            <Field label="Project name"><Input value={x.newProj.name} onChange={(e) => x.setNewProj({ ...x.newProj, name: e.target.value })} /></Field>
            <Field label="Client" hint="Pick from your customers and contacts, or type a new one.">
              <Input list="vpj-clients" value={x.newProj.client} onChange={(e) => x.setNewProj({ ...x.newProj, client: e.target.value })} />
            </Field>
            <Field label="Project number (optional)"><Input value={x.newProj.number} onChange={(e) => x.setNewProj({ ...x.newProj, number: e.target.value })} /></Field>
            <Button Icon={Plus} disabled={x.busy || !x.newProj.name.trim()} onClick={x.createProject}>Create project</Button>
          </Card>
        </section>

        <section className="vpj-page" aria-label={open ? x.sel.name : "Code library"}>
          {x.msg && <p className="vpj-msg" role="status">{x.msg}</p>}
          {!open ? <CodeLibrary x={x} /> : (
            <>
              <Button size="sm" variant="quiet" Icon={ArrowLeft} className="vpj-back" onClick={x.closeProject}>All projects</Button>
              <ProjectHeader key={x.sel.id} x={x} />
              <PillTabs label="Project" active={x.tab} onChange={x.setTab} tabs={PROJECT_TABS.map(([id, label]) => ({ id, label }))} />
              {x.tab === "overview" && <Overview x={x} />}
              {x.tab === "documents" && <ProjectDocuments tenantId={tenantId} project={x.sel.name} />}
              {x.tab === "boq" && <ProjectBoq tenantId={tenantId} project={x.sel.name} />}
              {x.tab === "money" && <ProjectMoney tenantId={tenantId} project={x.sel.name} />}
              {x.tab === "team" && <StandardsTeam x={x} />}
              <Button size="sm" variant="secondary" Icon={BookOpen} className="vpj-lib" onClick={x.closeProject}>Code library</Button>
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function ProjectRow({ p, phase, active, onClick }) {
  return (
    <ListRow Icon={phase ? CornerDownRight : FolderKanban} className={`${phase ? "vpj-phase" : ""}${active ? " vpj-row--on" : ""}`}
      aria-current={active ? "true" : undefined}
      title={phase ? (p.phase || p.name) : p.name}
      detail={[p.number, !phase && p.phases?.length ? `${p.phases.length} phase${p.phases.length === 1 ? "" : "s"}` : null].filter(Boolean).join(" · ") || undefined}
      trailing={statusChip(p.status)} onClick={onClick} />
  );
}

function ProjectHeader({ x }) {
  const { sel, ov } = x;
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ name: sel.name, number: sel.number || "", client: sel.client || "", status: sel.status || "active" });
  const [alias, setAlias] = useState("");
  const [phase, setPhase] = useState("");
  const [moveFrom, setMoveFrom] = useState("");
  return (
    <Card className="vpj-block">
      {ov?.parent && <Button size="sm" variant="quiet" Icon={ArrowLeft} onClick={() => x.openProject(ov.parent.id)}>Phase of {ov.parent.name}</Button>}
      {!editing ? (
        <div className="vpj-head">
          <div className="vpj-head-main">
            <h2 className="vpj-title">{sel.name}</h2>
            <span className="vpj-sub">{sel.client || "No client"}{sel.number ? ` · ${sel.number}` : ""}</span>
          </div>
          {statusChip(sel.status)}
          <Button size="sm" variant="secondary" Icon={Pencil} onClick={() => setEditing(true)}>Edit</Button>
        </div>
      ) : (
        <div className="vpj-form">
          <Field label="Project name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Client"><Input list="vpj-clients" value={form.client} onChange={(e) => setForm({ ...form, client: e.target.value })} /></Field>
          <Field label="Project number"><Input value={form.number} onChange={(e) => setForm({ ...form, number: e.target.value })} /></Field>
          <Field label="Status">
            <Select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
              <option value="active">Active</option><option value="on_hold">On hold</option><option value="complete">Complete</option>
            </Select>
          </Field>
          {form.name.trim() !== sel.name && <p className="vpj-sub vpj-wide">Renaming moves every document, invoice, expense and the BOQ to the new name. The old name keeps working.</p>}
          <div className="vpj-go"><Button onClick={() => { x.saveEdit(form); setEditing(false); }}>Save</Button>
            <Button variant="secondary" onClick={() => setEditing(false)}>Cancel</Button></div>
        </div>
      )}

      <h3 className="vpj-h3">Also known as</h3>
      <div className="vpj-chips">
        {(sel.aliases || []).map((a) => (
          <span key={a} className="vpj-alias">{a}<IconButton Icon={X} quiet label={`Remove the name ${a}`} className="vpj-alias-x" onClick={() => x.removeAlias(a)} /></span>
        ))}
        <Input className="vpj-alias-in" placeholder="Add another name…" aria-label="Add another name for this project" value={alias}
          onChange={(e) => setAlias(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { x.addAlias(alias); setAlias(""); } }} />
      </div>
      <p className="vpj-sub">Documents, invoices and WhatsApp messages using any of these names file under this project.</p>

      {!sel.parent_id && (
        <>
          <h3 className="vpj-h3">Phases</h3>
          {(ov?.phases || []).map((ph) => (
            <ListRow key={ph.id} Icon={CornerDownRight} title={ph.phase || ph.name} onClick={() => x.openProject(ph.id)}
              detail={`${ph.documents} doc${ph.documents === 1 ? "" : "s"}${ph.boq?.total_cents ? ` · BOQ ${money(ph.boq.total_cents)}` : ""}`} />
          ))}
          <div className="vpj-form">
            <Field label="New phase"><Input placeholder="e.g. Phase 2" value={phase} onChange={(e) => setPhase(e.target.value)} /></Field>
            <Field label="Move documents filed as… (optional)"><Input value={moveFrom} onChange={(e) => setMoveFrom(e.target.value)} /></Field>
            <div className="vpj-go"><Button variant="secondary" Icon={Plus} disabled={!phase.trim()} onClick={() => { x.addPhase(phase, moveFrom); setPhase(""); setMoveFrom(""); }}>Add phase</Button></div>
          </div>
          <p className="vpj-sub">A phase has its own BOQ, budget, documents and job costing; this project adds them together.</p>
        </>
      )}
    </Card>
  );
}

function Section({ title, hint, children }) {
  return (
    <Card pad={0} className="vpj-section">
      <div className="vpj-section-head"><h3 className="vpj-strong">{title}</h3>{hint && <p className="vpj-sub">{hint}</p>}</div>
      <div className="vpj-section-body">{children}</div>
    </Card>
  );
}
const Line = ({ label, value, onClick }) => (
  <div className="vpj-line">{onClick ? <button type="button" className="vpj-linkish" onClick={onClick}>{label}</button> : <span>{label}</span>}<span className="vpj-value">{value}</span></div>
);
const Empty = ({ children }) => <p className="vpj-empty">{children}</p>;

function Overview({ x }) {
  const { ov } = x;
  if (!ov) return <Empty>Loading…</Empty>;
  const docs = Object.entries(ov.phases?.length ? ov.documents_with_phases : ov.documents).sort((a, b) => b[1] - a[1]);
  const total = docs.reduce((a, [, n]) => a + n, 0);
  return (
    <>
      <Section title={`Documents · ${total}${ov.phases?.length ? " (with phases)" : ""}`}>
        {docs.length === 0 && <Empty>Nothing filed under this project yet.</Empty>}
        {docs.map(([cat, n]) => <Line key={cat} label={cat} value={n} />)}
      </Section>
      <Section title="BOQ">
        {ov.boq ? <Line label={ov.boq.title || "Contract / BOQ value"} value={<strong className="vula-money">{money(ov.boq.total_cents)}</strong>} />
          : <Empty>No BOQ yet — file a BOQ document under this project, or set the value in the BOQ tab.</Empty>}
        {(ov.phases || []).filter((ph) => ph.boq).map((ph) => (
          <Line key={ph.id} label={`↳ ${ph.phase || ph.name}`} value={<span className="vula-money">{money(ph.boq.total_cents)}</span>} onClick={() => x.openProject(ph.id)} />
        ))}
      </Section>
    </>
  );
}

function ProjectDocuments({ tenantId, project }) {
  const ref = useRef(null);
  const [note, setNote] = useState("");
  const upload = async (files) => {
    const list = Array.from(files || []);
    if (!list.length) return;
    setNote(`Uploading ${list.length} file(s)…`);
    const ok = await uploadToProject(tenantId, project, list);
    setNote(`${ok} of ${list.length} uploaded — Vula reads each one and files it under ${project} in a minute or two.`);
  };
  return (
    <>
      <div className="vpj-go vpj-block">
        <Button Icon={Upload} onClick={() => ref.current?.click()}>Upload to this project</Button>
        <input ref={ref} type="file" multiple hidden tabIndex={-1} aria-hidden="true" onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
        {note && <span className="vpj-sub" role="status">{note}</span>}
      </div>
      <FiledLibraryV2 tenantId={tenantId} project={project} title={project} />
    </>
  );
}

function ProjectBoq({ tenantId, project }) {
  const [boq, setBoq] = useState(null);
  const [total, setTotal] = useState("");
  const load = useCallback(() => {
    fetchBoq(tenantId, project).then((d) => { setBoq(d); setTotal(d.total_cents ? String(d.total_cents / 100) : ""); }).catch(() => setBoq(null));
  }, [tenantId, project]);
  useEffect(() => { load(); }, [load]);
  const sections = boq?.sections || [];
  return (
    <Section title={`BOQ · ${boq?.total_cents ? money(boq.total_cents) : "not set"}`} hint="Filing a BOQ document under this project fills this in, with its trade sections. You can also set the contract value here.">
      {sections.map((s, i) => <Line key={i} label={s.section} value={<span className="vula-money">{money(s.budget_cents)}</span>} />)}
      {sections.length === 0 && <Empty>No trade sections yet.</Empty>}
      <div className="vpj-form vpj-pad">
        <Field label="Contract value (R)"><Input inputMode="decimal" value={total} onChange={(e) => setTotal(e.target.value)} /></Field>
        <div className="vpj-go"><Button onClick={async () => { if (await saveBoqTotal(tenantId, project, total)) load(); }}>Save value</Button></div>
      </div>
    </Section>
  );
}

const VIEWS = { price: "Price only", open_book: "Open book (cost-plus)" };
const VIEW_HINT = {
  price: "The contract, what's been invoiced and paid, and what's still owed. Your own costs and margin stay private.",
  open_book: "Cost-plus: your cost per trade, what the client paid suppliers directly, your fee and the balance.",
};
const cents = (c) => (c == null ? "—" : <span className="vula-money">{money(c)}</span>);

/** One project's money (vula/commerce/project_tracker.py): budget vs actual, the account both ways,
 * cost by trade, and the client links — each made in the view chosen for this project. */
function ProjectMoney({ tenantId, project }) {
  const [t, setT] = useState(null);
  const [view, setView] = useState("price");
  const [made, setMade] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    fetchTracker(tenantId, project).then((d) => {
      setT(d);
      const last = (d.shares || []).find((s) => s.active);
      if (last) setView(last.view);
    }).catch(() => setT({ error: true }));
  }, [tenantId, project]);
  useEffect(() => { setMade(null); load(); }, [load]);
  if (!t) return <Empty>Loading…</Empty>;
  if (t.error || !t.position) return <Empty>The project's money couldn't be read just now.</Empty>;
  const p = t.position;
  const share = async () => {
    setBusy(true);
    const r = await createShare(tenantId, project, view);
    setBusy(false);
    setMade(r);
    if (!r.error) load();
  };
  return (
    <>
      <Section title="Budget vs actual" hint="Spent = what went through your bank on this project, plus what the client paid suppliers directly.">
        <Line label={p.budget_title || "Budget (BOQ)"} value={p.budget_cents ? cents(p.budget_cents) : "not set"} />
        {p.variations_claimed_cents > 0 && <Line label="Variations claimed" value={cents(p.variations_claimed_cents)} />}
        <Line label="Spent through you" value={cents(p.business_cost_cents)} />
        <Line label="Paid by the client directly" value={cents(p.paid_directly_cents)} />
        <Line label={<strong>Total spent{p.spent_pct != null ? ` · ${p.spent_pct}% of budget` : ""}</strong>} value={<strong>{cents(p.spent_cents)}</strong>} />
        {p.budget_left_cents != null && <Line label="Budget left" value={cents(p.budget_left_cents)} />}
      </Section>
      <Section title="Account" hint={`Open book adds your fee (${p.fee_pct}%) to your cost; price only works from your invoices.`}>
        <Line label="Cost through you" value={cents(p.business_cost_cents)} />
        <Line label={`Fee (${p.fee_pct}%)`} value={cents(p.fee_cents)} />
        <Line label="Received from the client" value={cents(p.received_cents)} />
        <Line label={<strong>Open-book balance due</strong>} value={<strong>{cents(p.open_book_balance_cents)}</strong>} />
        <Line label={`Invoiced (${(p.invoices || []).length})`} value={cents(p.invoiced_cents)} />
        <Line label="Invoices paid" value={cents(p.invoices_paid_cents)} />
        <Line label={<strong>Invoices owed</strong>} value={<strong>{cents(p.invoices_owed_cents)}</strong>} />
        {p.to_invoice_cents != null && <Line label="Still to invoice" value={cents(p.to_invoice_cents)} />}
        {p.profit_cents != null && <Line label="Your profit after overheads (never shown to the client)" value={cents(p.profit_cents)} />}
      </Section>
      <Section title="Cost by trade" hint={p.unallocated_trade_cents ? `${money(p.unallocated_trade_cents)} isn't allocated to a trade yet — tag those bank lines in Bank.` : undefined}>
        {(p.trades || []).length === 0 && <Empty>No bank lines on this project yet.</Empty>}
        {(p.trades || []).map((tr) => <Line key={tr.trade} label={tr.trade} value={cents(tr.cents)} />)}
      </Section>
      <Section title="Share with the client" hint="The client opens a link — no login. It always shows the books as they are now.">
        <div className="vpj-form vpj-pad">
          <Field label="What the client sees on this project" hint={VIEW_HINT[view]}>
            <Select value={view} onChange={(e) => setView(e.target.value)}>
              {Object.entries(VIEWS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </Select>
          </Field>
          <div className="vpj-go"><Button disabled={busy} onClick={share}>{busy ? "Making the link…" : "Make a client link"}</Button></div>
          {made?.error && <p className="vpj-sub" role="alert">{made.error}</p>}
          {made?.url && (
            <p className="vpj-sub" role="status">
              Send this to the client: <a href={made.url} target="_blank" rel="noreferrer">{made.url}</a>{" "}
              <Button size="sm" variant="secondary" onClick={() => navigator.clipboard?.writeText(made.url)}>Copy</Button>
            </p>
          )}
        </div>
        {(t.shares || []).map((s) => (
          <Line key={s.id}
            label={`${VIEWS[s.view] || s.view} · made ${String(s.created_at || "").slice(0, 10)} · opened ${s.views || 0}×${s.active ? ` · until ${String(s.expires_at || "").slice(0, 10)}` : " · off"}`}
            value={s.active ? <Button size="sm" variant="secondary" onClick={async () => { if (await revokeShare(tenantId, s.id)) load(); }}>Switch off</Button> : ""} />
        ))}
      </Section>
    </>
  );
}

function StandardsTeam({ x }) {
  const { sel } = x;
  return (
    <>
      <Section title={`Standards & codes · ${(sel.codes || []).length}`}>
        {(sel.codes || []).map((c) => (
          <div key={c.id} className="vpj-line">
            <span><strong>{c.code_ref}</strong> <span className="vpj-sub">— {c.title}{c.version ? ` (${c.version})` : ""}</span></span>
            <Button size="sm" variant="secondary" onClick={() => x.unlinkCode(c.id)}>Unlink</Button>
          </div>
        ))}
        {x.unlinked.length > 0 && (
          <div className="vpj-chips vpj-pad">
            <span className="vpj-sub">Link a code:</span>
            {x.unlinked.map((c) => <Button key={c.id} size="sm" variant="secondary" Icon={Plus} onClick={() => x.linkCode(c.id)}>{c.code_ref}</Button>)}
          </div>
        )}
        {(sel.codes || []).length === 0 && x.unlinked.length === 0 && <Empty>No codes in the library yet — add one from the code library.</Empty>}
      </Section>
      <Section title={`Professional team · ${(sel.team || []).length}`}>
        {(sel.team || []).map((m) => <Line key={m.id} label={<strong>{m.name}</strong>} value={m.role || "—"} />)}
        <div className="vpj-form vpj-pad">
          <Field label="Name"><Input value={x.newMember.name} onChange={(e) => x.setNewMember({ ...x.newMember, name: e.target.value })} /></Field>
          <Field label="Role"><Input value={x.newMember.role} onChange={(e) => x.setNewMember({ ...x.newMember, role: e.target.value })} /></Field>
          <div className="vpj-go"><Button Icon={Plus} disabled={!x.newMember.name.trim()} onClick={x.addMember}>Add</Button></div>
        </div>
      </Section>
    </>
  );
}

function CodeLibrary({ x }) {
  return (
    <Section title={`Master code library · ${x.codes.length}`} hint="Standards uploaded once, then linked to any project. Pasting the standard text makes it searchable and citable in chats.">
      {x.codes.map((c) => (
        <div key={c.id} className="vpj-line">
          <span><strong>{c.code_ref}</strong> <span className="vpj-sub">— {c.title}{c.version ? ` (${c.version})` : ""}</span></span>
          <StatusChip tone={c.status === "current" ? "ok" : "highlight"}>{c.status}{c.doc_id ? " · in KB" : ""}</StatusChip>
        </div>
      ))}
      {x.codes.length === 0 && <Empty>No codes yet. Add your first standard below.</Empty>}
      <div className="vpj-form vpj-pad">
        <Field label="Code ref"><Input placeholder="e.g. STD-SANS10400-A" value={x.newCode.code_ref} onChange={(e) => x.setNewCode({ ...x.newCode, code_ref: e.target.value })} /></Field>
        <Field label="Version"><Input placeholder="e.g. Ed 3 / 2010" value={x.newCode.version} onChange={(e) => x.setNewCode({ ...x.newCode, version: e.target.value })} /></Field>
        <Field label="Title" style={{ gridColumn: "1 / -1" }}><Input placeholder="e.g. SANS 10400 Part A: General Principles" value={x.newCode.title} onChange={(e) => x.setNewCode({ ...x.newCode, title: e.target.value })} /></Field>
        <Field label="Standard's text (optional)" hint="Makes it searchable and citable." style={{ gridColumn: "1 / -1" }}>
          <Textarea value={x.newCode.content} onChange={(e) => x.setNewCode({ ...x.newCode, content: e.target.value })} />
        </Field>
        <div className="vpj-go"><Button Icon={Plus} disabled={x.busy || !x.newCode.code_ref.trim() || !x.newCode.title.trim()} onClick={x.addCode}>Add to library</Button></div>
      </div>
    </Section>
  );
}
