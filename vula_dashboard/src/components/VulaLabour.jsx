/**
 * VulaLabour.jsx — 👷 casual-labour register + labour-paid report.
 * Load day/week workers (name/ID/bank/rate/project); Vula recognises their payments off the bank
 * statement and files them as casual labour. Backend: /v1/commerce/{tenant}/admin/workers + reports/labour.
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const R = (c) => `R${((c || 0) / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const blank = { name: "", id_number: "", bank_account: "", phone: "", rate_rands: "", rate_period: "daily", type: "casual", default_project: "" };

export default function VulaLabour({ tenantId }) {
  const [workers, setWorkers] = useState([]);
  const [report, setReport] = useState(null);
  const [projects, setProjects] = useState([]);
  const [f, setF] = useState(blank);
  const [editing, setEditing] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    const [w, r, p] = await Promise.all([
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/workers`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports/labour`).then(r => r.json()).catch(() => null),
      fetch(`${VULA_API}/v1/projects/${tenantId}/labels`).then(r => r.json()).catch(() => ({})),
    ]);
    setWorkers(w.workers || []); setReport(r); setProjects(p.projects || []);
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 3500); };

  const save = async () => {
    if (!f.name.trim()) return flash("Worker name is required.");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/workers`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...f, rate_rands: f.rate_rands ? Number(f.rate_rands) : null }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    if (r.error) return flash(r.error);
    setF(blank); setEditing(false); flash("Worker saved ✓"); load();
  };

  const edit = (w) => { setF({ ...blank, ...w, rate_rands: w.rate_cents ? (w.rate_cents / 100).toString() : "" }); setEditing(true); };
  const remove = async (id) => {
    if (!window.confirm("Remove this worker from the active register?")) return;
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/workers/${id}`, { method: "DELETE" }).catch(() => {});
    load();
  };

  return (
    <div style={{ fontFamily: "system-ui", color: C.text, maxWidth: 760 }}>
      <h4 style={{ fontSize: 15, fontWeight: 600, margin: "2px 0 2px" }}>👷 Casual labour</h4>
      <p style={{ color: C.muted, fontSize: 13, marginTop: 0 }}>
        Load a worker once — Vula recognises their payments on the bank statement, files them as casual labour, and costs them to the project. Your accountant gets the record for PAYE/UIF; Vula doesn't file returns.
      </p>

      {/* Add/edit worker */}
      <div style={{ ...card, background: C.alt, marginBottom: 14 }}>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>{editing ? "Edit worker" : "Add a worker"}</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 8 }}>
          <input placeholder="Full name" value={f.name} onChange={e => setF({ ...f, name: e.target.value })} style={input} />
          <input placeholder="ID number" value={f.id_number} onChange={e => setF({ ...f, id_number: e.target.value })} style={input} />
          <input placeholder="Bank account no." value={f.bank_account} onChange={e => setF({ ...f, bank_account: e.target.value })} style={input} />
          <input placeholder="Phone" value={f.phone} onChange={e => setF({ ...f, phone: e.target.value })} style={input} />
          <input type="number" placeholder="Rate R" value={f.rate_rands} onChange={e => setF({ ...f, rate_rands: e.target.value })} style={input} />
          <select value={f.rate_period} onChange={e => setF({ ...f, rate_period: e.target.value })} style={input}>
            <option value="daily">per day</option><option value="weekly">per week</option>
          </select>
          <select value={f.type} onChange={e => setF({ ...f, type: e.target.value })} style={input}>
            <option value="casual">casual</option><option value="subcontractor">subcontractor</option>
          </select>
          <input list="proj-list" placeholder="Default project" value={f.default_project} onChange={e => setF({ ...f, default_project: e.target.value })} style={input} />
          <datalist id="proj-list">{projects.map(p => <option key={p} value={p} />)}</datalist>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}>
          <button style={{ ...btn, ...btnOn }} onClick={save}>{editing ? "Save changes" : "Add worker"}</button>
          {editing && <button style={btn} onClick={() => { setF(blank); setEditing(false); }}>Cancel</button>}
          {msg && <span style={{ fontSize: 12, color: C.green }}>{msg}</span>}
        </div>
      </div>

      {/* Register */}
      <div style={{ fontSize: 11, textTransform: "uppercase", color: C.muted, marginBottom: 6 }}>Register ({workers.length})</div>
      {workers.length === 0 ? <div style={{ color: C.muted, fontSize: 13, marginBottom: 14 }}>No workers yet — add one above.</div>
        : <div style={{ marginBottom: 16 }}>{workers.map(w => (
          <div key={w.id} style={card}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 13 }}>{w.name} <span style={{ color: C.muted, fontWeight: 400, fontSize: 11 }}>· {w.type}{w.rate_cents ? ` · ${R(w.rate_cents)}/${w.rate_period === "weekly" ? "wk" : "day"}` : ""}</span></div>
              <div style={{ fontSize: 12, color: C.muted }}>{w.bank_account ? `acct ${w.bank_account}` : "no bank acct"}{w.id_number ? ` · ID ${w.id_number}` : ""}{w.default_project ? ` · ${w.default_project}` : ""}</div>
            </div>
            <button style={miniBtn} onClick={() => edit(w)}>Edit</button>
            <button style={{ ...miniBtn, color: C.red }} onClick={() => remove(w.id)}>Remove</button>
          </div>
        ))}</div>}

      {/* Labour report */}
      {report && report.payment_count > 0 && (
        <>
          <div style={{ fontSize: 11, textTransform: "uppercase", color: C.muted, marginBottom: 6 }}>
            Labour paid — {R(report.total_cents)} across {report.payment_count} payment(s)
          </div>
          <div style={card}>
            <div style={{ fontSize: 11, color: C.muted, marginBottom: 4 }}>By worker</div>
            {report.by_worker.map((w, i) => (
              <Row key={i} a={`${w.worker}${w.id_number ? ` (ID ${w.id_number})` : ""}`} b={`${R(w.total_cents)} · ${w.payments}×`} />
            ))}
            {report.by_project.length > 0 && <>
              <div style={{ fontSize: 11, color: C.muted, margin: "10px 0 4px" }}>By project</div>
              {report.by_project.map((p, i) => <Row key={i} a={p.project} b={R(p.total_cents)} />)}
            </>}
          </div>
        </>
      )}
    </div>
  );
}

const Row = ({ a, b }) => (
  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, padding: "4px 0", borderBottom: `1px solid ${C.alt}` }}>
    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "65%" }}>{a}</span>
    <span style={{ fontWeight: 600 }}>{b}</span>
  </div>
);

const card = { display: "flex", alignItems: "center", gap: 10, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 12px", marginBottom: 8 };
const btn = { padding: "7px 14px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "#fff", borderColor: C.green };
const miniBtn = { padding: "4px 10px", border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const input = { padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text, fontFamily: "system-ui" };
