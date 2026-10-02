/**
 * VulaProjects.jsx — Vula Projects command centre.
 *
 * Projects grouped by client, each with its phases (a phase is its own project with its own
 * BOQ, budget and documents, rolled up under the main project), the other names it's filed
 * under, and one page per project: documents (by type, upload straight to the project), BOQ,
 * money, standards and team. Plus the master code library. 2026-10-02 (Ian: "how can a tenant
 * add projects and aliases, phases, more projects for one client, each with their own BOQ and
 * filed invoices and slips?").
 */
import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { VULA_API } from "../lib/authFetch";
import { FiledLibrary } from "./VulaDocuments";


const C = {
  bg: "var(--bg)", surface: "var(--surface)", border: "var(--border)",
  green: "var(--accent)", amber: "var(--warn)", red: "var(--danger)",
  text: "var(--text)", muted: "var(--muted)", surfaceAlt: "var(--surface-alt)",
};
const STATUS_COLOR = { active: 'var(--ok)', on_hold: C.amber, complete: C.muted };

const inp = { width: "100%", padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, color: C.text, background: C.surface, boxSizing: "border-box" };
const btn = { padding: "8px 14px", background: C.green, color: "var(--on-accent)", border: "none", borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: "pointer" };
const btnGhost = { padding: "6px 12px", background: C.surface, color: C.text, border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12, cursor: "pointer" };

const R = (cents) => `R${(Number(cents || 0) / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const TABS = [["overview", "Overview"], ["documents", "Documents"], ["boq", "BOQ"], ["money", "Money"], ["team", "Standards & team"]];

export default function VulaProjects({ tenantId }) {
  const [projects, setProjects] = useState([]);
  const [codes, setCodes] = useState([]);
  const [clients, setClients] = useState([]);
  const [sel, setSel] = useState(null);          // full selected project
  const [ov, setOv] = useState(null);            // its overview (phases, documents, BOQ)
  const [tab, setTab] = useState("overview");
  const [query, setQuery] = useState("");
  const [newProj, setNewProj] = useState({ name: "", number: "", client: "" });
  const [newCode, setNewCode] = useState({ code_ref: "", title: "", version: "", content: "" });
  const [newMember, setNewMember] = useState({ name: "", role: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const api = useCallback(async (method, path, body) => {
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}${path}`, {
      method, headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const d = await r.json().catch(() => ({}));
    return r.ok ? d : { error: d.detail || d.error || "Something went wrong." };
  }, [tenantId]);

  const loadProjects = useCallback(async () => {
    const d = await api("GET", "");
    setProjects(d.projects || []);
  }, [api]);
  const loadCodes = useCallback(async () => {
    const d = await api("GET", "/codes");
    setCodes(d.codes || []);
  }, [api]);
  const openProject = useCallback(async (pid) => {
    setMsg("");
    const [p, o] = await Promise.all([api("GET", `/p/${pid}`), api("GET", `/p/${pid}/overview`)]);
    setSel(p.error ? null : p);
    setOv(o.error ? null : o);
  }, [api]);

  useEffect(() => { if (tenantId) { loadProjects(); loadCodes(); } }, [tenantId, loadProjects, loadCodes]);
  useEffect(() => {
    // Client picker: the business's customers and contacts, plus clients already on projects.
    if (!tenantId) return;
    Promise.all([
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/customers`).then((r) => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/contacts`).then((r) => r.json()).catch(() => ({})),
    ]).then(([c, k]) => {
      const names = [...(c.customers || []).map((x) => x.name), ...(k.contacts || []).map((x) => x.company || x.name)];
      setClients([...new Set(names.filter(Boolean))].sort());
    });
  }, [tenantId]);

  // Main projects grouped by client, each with its phases underneath.
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const match = (p) => !q || [p.name, p.number, p.client, ...(p.aliases || [])].some((v) => (v || "").toLowerCase().includes(q));
    const byParent = {};
    projects.filter((p) => p.parent_id).forEach((p) => { (byParent[p.parent_id] = byParent[p.parent_id] || []).push(p); });
    const mains = projects.filter((p) => !p.parent_id && (match(p) || (byParent[p.id] || []).some(match)));
    const out = {};
    mains.forEach((p) => { const k = p.client || "No client"; (out[k] = out[k] || []).push({ ...p, phases: byParent[p.id] || [] }); });
    return Object.entries(out).sort(([a], [b]) => (a === "No client") - (b === "No client") || a.localeCompare(b));
  }, [projects, query]);
  const allClients = useMemo(() => [...new Set([...clients, ...projects.map((p) => p.client).filter(Boolean)])].sort(), [clients, projects]);

  const createProject = async () => {
    if (!newProj.name.trim()) return;
    setBusy(true);
    const p = await api("POST", "", newProj);
    setBusy(false);
    if (p.error) { setMsg(p.error); return; }
    setNewProj({ name: "", number: "", client: "" });
    await loadProjects();
    if (p.id) openProject(p.id);
  };
  const saveEdit = async (patch) => {
    if (!sel) return;
    const d = await api("PATCH", `/p/${sel.id}`, patch);
    if (d.error) { setMsg(d.error); return; }
    const moved = Object.values(d.moved || {}).reduce((a, b) => a + b, 0);
    setMsg(moved ? `Saved — ${moved} record(s) moved to the new name; the old name still works.` : "Saved.");
    await loadProjects(); openProject(sel.id);
  };
  const addAlias = async (alias) => {
    if (!sel || !alias.trim()) return;
    const d = await api("POST", `/p/${sel.id}/aliases`, { alias });
    if (d.error) { setMsg(d.error); return; }
    const n = (d.moved || {}).vula_filed_documents || 0;
    setMsg(`“${alias}” now files under ${sel.name}${n ? ` — ${n} document(s) moved` : ""}.`);
    await loadProjects(); openProject(sel.id);
  };
  const removeAlias = async (alias) => saveEdit({ aliases: (sel.aliases || []).filter((a) => a !== alias) });
  const addPhase = async (phase, moveFrom) => {
    if (!sel || !phase.trim()) return;
    const d = await api("POST", `/p/${sel.id}/phases`, { phase, move_from: moveFrom || undefined });
    if (d.error) { setMsg(d.error); return; }
    setMsg(`${d.name} added — its own BOQ, budget and documents.`);
    await loadProjects(); openProject(sel.id);
  };
  const addCode = async () => {
    if (!newCode.code_ref.trim() || !newCode.title.trim()) return;
    setBusy(true);
    await api("POST", "/codes", { ...newCode, category: "Standards", status: "current" });
    setNewCode({ code_ref: "", title: "", version: "", content: "" });
    await loadCodes();
    setBusy(false);
  };
  const linkCode = async (cid) => { if (sel) { await api("POST", `/p/${sel.id}/codes/${cid}`); openProject(sel.id); } };
  const unlinkCode = async (cid) => { if (sel) { await api("DELETE", `/p/${sel.id}/codes/${cid}`); openProject(sel.id); } };
  const addMember = async () => {
    if (!sel || !newMember.name.trim()) return;
    await api("POST", `/p/${sel.id}/team`, newMember);
    setNewMember({ name: "", role: "" });
    openProject(sel.id);
  };

  const linkedIds = new Set((sel?.codes || []).map((c) => c.id));
  const unlinked = codes.filter((c) => !linkedIds.has(c.id));

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "24px" }}>
      <h1 style={{ fontFamily: "var(--font-display)", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 4px" }}>Projects</h1>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 20px" }}>Your clients' projects and their phases — each with its own documents, BOQ and money.</p>
      <datalist id="vp-clients">{allClients.map((c) => <option key={c} value={c} />)}</datalist>

      <div style={{ display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
        {/* ── Left: projects by client + create ── */}
        <div style={{ flex: "0 0 290px", minWidth: 250 }}>
          <input style={{ ...inp, marginBottom: 10 }} placeholder="🔍 Find a project, client or number" value={query} onChange={(e) => setQuery(e.target.value)} />
          <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden", marginBottom: 14 }}>
            {groups.length === 0 && <div style={{ padding: 16, fontSize: 12, color: C.muted }}>No projects yet.</div>}
            {groups.map(([client, list]) => (
              <div key={client}>
                <div style={{ padding: "8px 14px", fontSize: 11, fontWeight: 700, color: C.muted, background: C.surfaceAlt, letterSpacing: 0.3 }}>{client.toUpperCase()}</div>
                {list.map((p) => (
                  <div key={p.id}>
                    <ProjectRow p={p} active={sel?.id === p.id} onClick={() => openProject(p.id)} />
                    {p.phases.map((ph) => <ProjectRow key={ph.id} p={ph} phase active={sel?.id === ph.id} onClick={() => openProject(ph.id)} />)}
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, marginBottom: 8 }}>NEW PROJECT</div>
            <input style={{ ...inp, marginBottom: 6 }} placeholder="Project name" value={newProj.name} onChange={(e) => setNewProj({ ...newProj, name: e.target.value })} />
            <input style={{ ...inp, marginBottom: 6 }} list="vp-clients" placeholder="Client (pick or type)" value={newProj.client} onChange={(e) => setNewProj({ ...newProj, client: e.target.value })} />
            <input style={{ ...inp, marginBottom: 8 }} placeholder="Project number (optional)" value={newProj.number} onChange={(e) => setNewProj({ ...newProj, number: e.target.value })} />
            <button style={{ ...btn, width: "100%", opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={createProject}>Create project</button>
          </div>
        </div>

        {/* ── Right: one project's page, or the code library ── */}
        <div style={{ flex: 1, minWidth: 320 }}>
          {msg && <div style={{ fontSize: 12.5, color: C.text, background: C.surfaceAlt, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px", marginBottom: 12 }}>{msg}</div>}
          {!sel ? (
            <CodeLibrary codes={codes} newCode={newCode} setNewCode={setNewCode} addCode={addCode} busy={busy} />
          ) : (
            <div>
              <ProjectHeader key={sel.id} sel={sel} ov={ov} onSave={saveEdit} onOpen={openProject}
                onAddAlias={addAlias} onRemoveAlias={removeAlias} onAddPhase={addPhase} />
              <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${C.border}`, marginBottom: 14, overflowX: "auto" }}>
                {TABS.map(([k, label]) => (
                  <button key={k} onClick={() => setTab(k)} style={{ padding: "8px 12px", border: "none", background: "transparent", cursor: "pointer", fontSize: 13,
                    color: tab === k ? C.text : C.muted, fontWeight: tab === k ? 700 : 500, borderBottom: tab === k ? `2px solid ${C.green}` : "2px solid transparent", whiteSpace: "nowrap" }}>{label}</button>
                ))}
              </div>
              {tab === "overview" && <Overview ov={ov} onOpen={openProject} />}
              {tab === "documents" && <ProjectDocuments tenantId={tenantId} project={sel.name} />}
              {tab === "boq" && <ProjectBoq tenantId={tenantId} project={sel.name} />}
              {tab === "money" && <ProjectMoney tenantId={tenantId} project={sel.name} />}
              {tab === "team" && (
                <>
                  <Section title={`Standards & codes · ${(sel.codes || []).length}`}>
                    {(sel.codes || []).map((c) => (
                      <Row key={c.id}>
                        <span><strong style={{ color: C.text }}>{c.code_ref}</strong> <span style={{ color: C.muted }}>— {c.title}{c.version ? ` (${c.version})` : ""}</span></span>
                        <button style={btnGhost} onClick={() => unlinkCode(c.id)}>Unlink</button>
                      </Row>
                    ))}
                    {unlinked.length > 0 && (
                      <div style={{ padding: "10px 14px", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                        <span style={{ fontSize: 12, color: C.muted }}>Link a code:</span>
                        {unlinked.map((c) => <button key={c.id} style={btnGhost} onClick={() => linkCode(c.id)}>+ {c.code_ref}</button>)}
                      </div>
                    )}
                    {(sel.codes || []).length === 0 && unlinked.length === 0 && <Empty>No codes in the library yet — add one from the code library.</Empty>}
                  </Section>
                  <Section title={`Professional team · ${(sel.team || []).length}`}>
                    {(sel.team || []).map((m) => (
                      <Row key={m.id}><span><strong style={{ color: C.text }}>{m.name}</strong> <span style={{ color: C.muted }}>— {m.role || "—"}</span></span></Row>
                    ))}
                    <div style={{ padding: "10px 14px", display: "flex", gap: 6 }}>
                      <input style={inp} placeholder="Name" value={newMember.name} onChange={(e) => setNewMember({ ...newMember, name: e.target.value })} />
                      <input style={inp} placeholder="Role" value={newMember.role} onChange={(e) => setNewMember({ ...newMember, role: e.target.value })} />
                      <button style={btn} onClick={addMember}>Add</button>
                    </div>
                  </Section>
                </>
              )}
              <button style={{ ...btnGhost, marginTop: 4 }} onClick={() => { setSel(null); setOv(null); }}>← Code library</button>
            </div>
          )}
        </div>
      </div>
      <p style={{ textAlign: "center", fontSize: 11, color: "var(--faint)", marginTop: 24 }}>Powered by Vula</p>
    </div>
  );
}

function ProjectRow({ p, phase, active, onClick }) {
  return (
    <div onClick={onClick} style={{ padding: phase ? "8px 14px 8px 30px" : "10px 14px", borderBottom: `1px solid ${C.surfaceAlt}`, cursor: "pointer", background: active ? C.surfaceAlt : "transparent" }}>
      <div style={{ fontSize: phase ? 12.5 : 13, fontWeight: 600, color: C.text }}>{phase ? `↳ ${p.phase || p.name}` : p.name}</div>
      <div style={{ fontSize: 11, color: C.muted }}>
        {p.number ? p.number + " · " : ""}<span style={{ color: STATUS_COLOR[p.status] || C.muted }}>● {(p.status || "").replace("_", " ")}</span>
        {!phase && p.phases?.length ? ` · ${p.phases.length} phase${p.phases.length === 1 ? "" : "s"}` : ""}
      </div>
    </div>
  );
}

function ProjectHeader({ sel, ov, onSave, onOpen, onAddAlias, onRemoveAlias, onAddPhase }) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ name: sel.name, number: sel.number || "", client: sel.client || "", status: sel.status || "active" });
  const [alias, setAlias] = useState("");
  const [phase, setPhase] = useState("");
  const [moveFrom, setMoveFrom] = useState("");
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 18, marginBottom: 14 }}>
      {ov?.parent && (
        <div style={{ fontSize: 12, color: C.muted, marginBottom: 4, cursor: "pointer" }} onClick={() => onOpen(ov.parent.id)}>← Phase of {ov.parent.name}</div>
      )}
      {!editing ? (
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, flexWrap: "wrap" }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 20, color: C.text }}>{sel.name}</h2>
            <div style={{ fontSize: 12, color: C.muted }}>{sel.client || "No client"}{sel.number ? ` · ${sel.number}` : ""} · <span style={{ color: STATUS_COLOR[sel.status] || C.muted }}>● {(sel.status || "").replace("_", " ")}</span></div>
          </div>
          <button style={btnGhost} onClick={() => setEditing(true)}>Edit</button>
        </div>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          <input style={inp} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Project name" />
          <input style={inp} list="vp-clients" value={form.client} onChange={(e) => setForm({ ...form, client: e.target.value })} placeholder="Client" />
          <input style={inp} value={form.number} onChange={(e) => setForm({ ...form, number: e.target.value })} placeholder="Project number" />
          <select style={inp} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
            <option value="active">Active</option><option value="on_hold">On hold</option><option value="complete">Complete</option>
          </select>
          {form.name.trim() !== sel.name && <div style={{ fontSize: 12, color: C.muted }}>Renaming moves every document, invoice, expense and the BOQ to the new name. The old name keeps working.</div>}
          <div style={{ display: "flex", gap: 6 }}>
            <button style={btn} onClick={() => { onSave(form); setEditing(false); }}>Save</button>
            <button style={btnGhost} onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      )}

      <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, marginBottom: 4 }}>ALSO KNOWN AS</div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          {(sel.aliases || []).map((a) => (
            <span key={a} style={{ fontSize: 12, background: C.surfaceAlt, border: `1px solid ${C.border}`, borderRadius: 999, padding: "2px 8px" }}>
              {a} <button onClick={() => onRemoveAlias(a)} title="Remove" style={{ border: "none", background: "transparent", cursor: "pointer", color: C.muted }}>×</button>
            </span>
          ))}
          <input style={{ ...inp, width: 200 }} placeholder="Add another name…" value={alias} onChange={(e) => setAlias(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { onAddAlias(alias); setAlias(""); } }} />
        </div>
        <div style={{ fontSize: 11, color: C.muted, marginTop: 4 }}>Documents, invoices and WhatsApp messages using any of these names file under this project.</div>
      </div>

      {!sel.parent_id && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, marginBottom: 4 }}>PHASES</div>
          {(ov?.phases || []).map((ph) => (
            <div key={ph.id} onClick={() => onOpen(ph.id)} style={{ fontSize: 12.5, cursor: "pointer", padding: "3px 0", color: C.text }}>
              ↳ {ph.phase || ph.name} <span style={{ color: C.muted }}>· {ph.documents} doc{ph.documents === 1 ? "" : "s"}{ph.boq?.total_cents ? ` · BOQ ${R(ph.boq.total_cents)}` : ""}</span>
            </div>
          ))}
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
            <input style={{ ...inp, width: 160 }} placeholder="e.g. Phase 2" value={phase} onChange={(e) => setPhase(e.target.value)} />
            <input style={{ ...inp, width: 220 }} placeholder="Move documents filed as… (optional)" value={moveFrom} onChange={(e) => setMoveFrom(e.target.value)} />
            <button style={btnGhost} onClick={() => { onAddPhase(phase, moveFrom); setPhase(""); setMoveFrom(""); }}>+ Add phase</button>
          </div>
          <div style={{ fontSize: 11, color: C.muted, marginTop: 4 }}>A phase has its own BOQ, budget, documents and job costing; this project adds them together.</div>
        </div>
      )}
    </div>
  );
}

function Overview({ ov, onOpen }) {
  if (!ov) return <Empty>Loading…</Empty>;
  const docs = Object.entries(ov.phases?.length ? ov.documents_with_phases : ov.documents).sort((a, b) => b[1] - a[1]);
  const total = docs.reduce((a, [, n]) => a + n, 0);
  return (
    <>
      <Section title={`Documents · ${total}${ov.phases?.length ? " (with phases)" : ""}`}>
        {docs.length === 0 && <Empty>Nothing filed under this project yet.</Empty>}
        {docs.map(([cat, n]) => <Row key={cat}><span>{cat}</span><span style={{ color: C.muted }}>{n}</span></Row>)}
      </Section>
      <Section title="BOQ">
        {ov.boq ? <Row><span>{ov.boq.title || "Contract / BOQ value"}</span><strong>{R(ov.boq.total_cents)}</strong></Row> : <Empty>No BOQ yet — file a BOQ document under this project, or set the value in the BOQ tab.</Empty>}
        {(ov.phases || []).filter((ph) => ph.boq).map((ph) => (
          <Row key={ph.id}><span style={{ cursor: "pointer" }} onClick={() => onOpen(ph.id)}>↳ {ph.phase || ph.name}</span><span>{R(ph.boq.total_cents)}</span></Row>
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
    let ok = 0;
    for (const f of list) {
      const fd = new FormData();
      fd.append("tenant_id", tenantId); fd.append("file", f); fd.append("project", project);
      const r = await fetch(`${VULA_API}/ingest`, { method: "POST", body: fd }).catch(() => null);
      if (r && r.ok) ok += 1;
    }
    setNote(`${ok} of ${list.length} uploaded — Vula reads each one and files it under ${project} in a minute or two.`);
  };
  return (
    <>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
        <button style={btn} onClick={() => ref.current?.click()}>+ Upload to this project</button>
        <input ref={ref} type="file" multiple style={{ display: "none" }} onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
        {note && <span style={{ fontSize: 12, color: C.muted }}>{note}</span>}
      </div>
      <FiledLibrary tenantId={tenantId} project={project} title={`📂 ${project}`} />
    </>
  );
}

function ProjectBoq({ tenantId, project }) {
  const [boq, setBoq] = useState(null);
  const [total, setTotal] = useState("");
  const load = useCallback(() => {
    fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/boq`).then((r) => r.json())
      .then((d) => { setBoq(d); setTotal(d.total_cents ? String(d.total_cents / 100) : ""); }).catch(() => setBoq(null));
  }, [tenantId, project]);
  useEffect(() => { load(); }, [load]);
  const save = async () => {
    const v = parseFloat(String(total).replace(/[^\d.]/g, ""));
    if (!v) return;
    await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/boq`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ total: v }) });
    load();
  };
  const sections = boq?.sections || [];
  return (
    <Section title={`BOQ · ${boq?.total_cents ? R(boq.total_cents) : "not set"}`} hint="Filing a BOQ document under this project fills this in, with its trade sections. You can also set the contract value here.">
      {sections.map((s, i) => (
        <Row key={i}><span>{s.section}</span><span>{R(s.budget_cents)}</span></Row>
      ))}
      {sections.length === 0 && <Empty>No trade sections yet.</Empty>}
      <div style={{ padding: "10px 14px", display: "flex", gap: 6 }}>
        <input style={{ ...inp, maxWidth: 200 }} placeholder="Contract value (R)" value={total} onChange={(e) => setTotal(e.target.value)} />
        <button style={btn} onClick={save}>Save value</button>
      </div>
    </Section>
  );
}

function ProjectMoney({ tenantId, project }) {
  const [f, setF] = useState(null);
  useEffect(() => {
    fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/financials`).then((r) => r.json())
      .then(setF).catch(() => setF(null));
  }, [tenantId, project]);
  if (!f) return <Empty>Loading…</Empty>;
  const rows = Object.entries(f).filter(([, v]) => typeof v === "number" || (typeof v === "string" && /^R/.test(v)));
  return (
    <Section title="Money" hint="Contract, invoiced, paid in and spent for this project. Job costing (cost, fee, profit) is in Finances.">
      {rows.length === 0 && <Empty>No money recorded against this project yet.</Empty>}
      {rows.map(([k, v]) => <Row key={k}><span>{k.replace(/_/g, " ")}</span><span>{typeof v === "number" && /cents/.test(k) ? R(v) : String(v)}</span></Row>)}
    </Section>
  );
}

function CodeLibrary({ codes, newCode, setNewCode, addCode, busy }) {
  return (
    <Section title={`Master code library · ${codes.length}`} hint="Standards uploaded once, then linked to any project. Pasting the standard text makes it searchable & citable in chats.">
      {codes.map((c) => (
        <Row key={c.id}>
          <span><strong style={{ color: C.text }}>{c.code_ref}</strong> <span style={{ color: C.muted }}>— {c.title}{c.version ? ` (${c.version})` : ""}</span></span>
          <span style={{ fontSize: 11, color: c.status === "current" ? C.green : C.amber }}>{c.status}{c.doc_id ? " · in KB" : ""}</span>
        </Row>
      ))}
      {codes.length === 0 && <Empty>No codes yet. Add your first standard below.</Empty>}
      <div style={{ padding: 14, borderTop: `1px solid ${C.surfaceAlt}` }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, marginBottom: 8 }}>ADD A CODE</div>
        <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
          <input style={inp} placeholder="Code ref e.g. STD-SANS10400-A" value={newCode.code_ref} onChange={(e) => setNewCode({ ...newCode, code_ref: e.target.value })} />
          <input style={inp} placeholder="Version e.g. Ed 3 / 2010" value={newCode.version} onChange={(e) => setNewCode({ ...newCode, version: e.target.value })} />
        </div>
        <input style={{ ...inp, marginBottom: 6 }} placeholder="Title e.g. SANS 10400 Part A: General Principles" value={newCode.title} onChange={(e) => setNewCode({ ...newCode, title: e.target.value })} />
        <textarea style={{ ...inp, minHeight: 70, marginBottom: 8, fontFamily: "inherit" }} placeholder="Paste the standard's text (optional) — makes it searchable & citable" value={newCode.content} onChange={(e) => setNewCode({ ...newCode, content: e.target.value })} />
        <button style={{ ...btn, opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={addCode}>Add to library</button>
      </div>
    </Section>
  );
}

function Section({ title, hint, children }) {
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden", marginBottom: 14 }}>
      <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{title}</div>
        {hint && <div style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>{hint}</div>}
      </div>
      {children}
    </div>
  );
}
const Row = ({ children }) => (
  <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.surfaceAlt}`, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, fontSize: 13 }}>{children}</div>
);
const Empty = ({ children }) => (<div style={{ padding: 16, fontSize: 12, color: C.muted }}>{children}</div>);
