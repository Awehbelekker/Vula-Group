/**
 * VulaProjects.jsx — Vula Projects command centre.
 *
 * Projects (Level 1) + the master code library (standards uploaded once, linked to
 * many projects) + the professional-team directory. Mirrors the practice hierarchy.
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", border: "#DDD8CE",
  green: "var(--accent)", amber: "#C4861A", red: "#C0392B",
  text: "#2A2A2A", muted: "#8A8680", surfaceAlt: "#F0EDE5",
};
const STATUS_COLOR = { active: C.green, on_hold: C.amber, complete: C.muted };

const inp = { width: "100%", padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, color: C.text, background: C.surface, boxSizing: "border-box" };
const btn = { padding: "8px 14px", background: C.green, color: "#fff", border: "none", borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: "pointer" };
const btnGhost = { padding: "6px 12px", background: C.surface, color: C.text, border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12, cursor: "pointer" };

export default function VulaProjects({ tenantId }) {
  const [projects, setProjects] = useState([]);
  const [codes, setCodes] = useState([]);
  const [sel, setSel] = useState(null);          // full selected project
  const [newProj, setNewProj] = useState({ name: "", number: "", client: "" });
  const [newCode, setNewCode] = useState({ code_ref: "", title: "", version: "", content: "" });
  const [newMember, setNewMember] = useState({ name: "", role: "" });
  const [busy, setBusy] = useState(false);

  const api = useCallback(async (method, path, body) => {
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}${path}`, {
      method, headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    return r.json();
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
    setSel(await api("GET", `/p/${pid}`));
  }, [api]);

  useEffect(() => { if (tenantId) { loadProjects(); loadCodes(); } }, [tenantId, loadProjects, loadCodes]);

  const createProject = async () => {
    if (!newProj.name.trim()) return;
    setBusy(true);
    const p = await api("POST", "", newProj);
    setNewProj({ name: "", number: "", client: "" });
    await loadProjects();
    if (p.id) openProject(p.id);
    setBusy(false);
  };
  const setStatus = async (status) => {
    if (!sel) return;
    await api("PATCH", `/p/${sel.id}`, { status });
    openProject(sel.id); loadProjects();
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
    <div style={{ maxWidth: 1000, margin: "0 auto", padding: "24px" }}>
      <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 4px" }}>Projects</h1>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 20px" }}>Projects, the master code library, and your professional team — one command centre.</p>

      <div style={{ display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
        {/* ── Left: project list + create ── */}
        <div style={{ flex: "0 0 270px", minWidth: 240 }}>
          <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden", marginBottom: 14 }}>
            <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.border}`, fontSize: 13, fontWeight: 600 }}>
              Projects <span style={{ color: C.muted }}>· {projects.length}</span>
            </div>
            {projects.length === 0 && <div style={{ padding: 16, fontSize: 12, color: C.muted }}>No projects yet.</div>}
            {projects.map((p) => (
              <div key={p.id} onClick={() => openProject(p.id)}
                style={{ padding: "10px 14px", borderBottom: `1px solid ${C.surfaceAlt}`, cursor: "pointer",
                  background: sel?.id === p.id ? C.surfaceAlt : "transparent" }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{p.name}</div>
                <div style={{ fontSize: 11, color: C.muted }}>
                  {p.number ? p.number + " · " : ""}{p.client || "—"}
                  <span style={{ color: STATUS_COLOR[p.status] || C.muted, marginLeft: 6 }}>● {p.status}</span>
                </div>
              </div>
            ))}
          </div>
          <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.muted, marginBottom: 8 }}>NEW PROJECT</div>
            <input style={{ ...inp, marginBottom: 6 }} placeholder="Project name" value={newProj.name} onChange={(e) => setNewProj({ ...newProj, name: e.target.value })} />
            <input style={{ ...inp, marginBottom: 6 }} placeholder="Number (optional)" value={newProj.number} onChange={(e) => setNewProj({ ...newProj, number: e.target.value })} />
            <input style={{ ...inp, marginBottom: 8 }} placeholder="Client (optional)" value={newProj.client} onChange={(e) => setNewProj({ ...newProj, client: e.target.value })} />
            <button style={{ ...btn, width: "100%", opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={createProject}>Create project</button>
          </div>
        </div>

        {/* ── Right: command centre or code library ── */}
        <div style={{ flex: 1, minWidth: 320 }}>
          {!sel ? (
            <CodeLibrary codes={codes} newCode={newCode} setNewCode={setNewCode} addCode={addCode} busy={busy} />
          ) : (
            <div>
              <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 18, marginBottom: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <h2 style={{ margin: 0, fontSize: 20, color: C.text }}>{sel.name}</h2>
                    <div style={{ fontSize: 12, color: C.muted }}>{sel.number ? sel.number + " · " : ""}{sel.client || "—"}</div>
                  </div>
                  <select value={sel.status} onChange={(e) => setStatus(e.target.value)} style={{ ...inp, width: "auto" }}>
                    <option value="active">active</option><option value="on_hold">on hold</option><option value="complete">complete</option>
                  </select>
                </div>
              </div>

              {/* Linked codes */}
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
                    {unlinked.map((c) => (
                      <button key={c.id} style={btnGhost} onClick={() => linkCode(c.id)}>+ {c.code_ref}</button>
                    ))}
                  </div>
                )}
                {(sel.codes || []).length === 0 && unlinked.length === 0 && (
                  <Empty>No codes in the library yet — add one below (deselect the project).</Empty>
                )}
              </Section>

              {/* Team */}
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

              <button style={{ ...btnGhost, marginTop: 4 }} onClick={() => setSel(null)}>← Code library</button>
            </div>
          )}
        </div>
      </div>
      <p style={{ textAlign: "center", fontSize: 11, color: "#B5B0A8", marginTop: 24 }}>Powered by Vula</p>
    </div>
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
