/**
 * VulaScheduledJobs.jsx — ⏰ Scheduling: per-tenant config for the 5 system WhatsApp jobs
 * (delivery briefing, low-stock alert, sales summary, weekly specials reminder, unpaid-order
 * chase). Times, template and on/off per job — previously hardcoded in the backend.
 * Backend: /v1/commerce/{tenant}/admin/scheduled-jobs (migration 069).
 */
import { useEffect, useState } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const api = (t, p, opts) => fetch(`${VULA_API}/v1/commerce/${t}/admin${p}`, opts).then(r => r.json());

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const pad = (n) => String(n).padStart(2, "0");

export default function VulaScheduledJobs({ tenantId }) {
  const [jobs, setJobs] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 6000); };

  const load = async () => {
    setBusy(true);
    const d = await api(tenantId, "/scheduled-jobs").catch(() => ({ jobs: [], approved_templates: [] }));
    setJobs(d.jobs || []);
    setTemplates(d.approved_templates || []);
    setBusy(false);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [tenantId]);

  const save = async (job_type, patch) => {
    const r = await api(tenantId, "/scheduled-jobs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_type, ...patch }),
    }).catch(() => ({ error: "network" }));
    if (r.detail || r.error) return flash(r.detail || r.error);
    setJobs(js => js.map(j => (j.job_type === job_type ? { ...j, ...r } : j)));
    flash("Saved.");
  };

  return (
    <div style={{ fontFamily: "system-ui", color: C.text, maxWidth: 860 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h4 style={{ fontSize: 15, fontWeight: 600, margin: "2px 0" }}>⏰ Scheduled messages</h4>
        <span style={{ fontSize: 12.5, color: C.muted }}>
          When each automatic WhatsApp goes out, and which approved template it uses. Times are South African (SAST).
        </span>
      </div>
      {msg && <div style={{ fontSize: 12.5, color: C.green, margin: "8px 0" }}>{msg}</div>}
      {busy && <div style={{ fontSize: 12.5, color: C.muted, margin: "8px 0" }}>Loading…</div>}

      {jobs.map(j => (
        <JobCard key={j.job_type} job={j} templates={templates} onSave={(patch) => save(j.job_type, patch)} />
      ))}

      <p style={{ fontSize: 11.5, color: C.muted, marginTop: 8 }}>
        "Default template" uses the standard one for this business. Only Meta-APPROVED templates can be
        selected — create new ones in the 📨 Templates tab first. Changes take effect within ~5 minutes.
      </p>
    </div>
  );
}

function JobCard({ job, templates, onSave }) {
  const [enabled, setEnabled] = useState(job.enabled);
  const [tpl, setTpl] = useState(job.template_name || "");
  const [hour, setHour] = useState(job.hour ?? 8);
  const [minute, setMinute] = useState(job.minute ?? 0);
  const [dow, setDow] = useState(job.day_of_week ?? 4);
  const [interval, setIntervalMin] = useState(job.interval_minutes ?? 30);
  const [saving, setSaving] = useState(false);

  const timed = job.kind === "daily" || job.kind === "weekly";

  const submit = async () => {
    setSaving(true);
    const patch = { enabled, template_name: tpl || null };
    if (timed) { patch.hour = Number(hour); patch.minute = Number(minute); }
    if (job.kind === "weekly") patch.day_of_week = Number(dow);
    if (job.kind === "interval") patch.interval_minutes = Number(interval);
    await onSave(patch);
    setSaving(false);
  };

  return (
    <div style={{ ...card, marginTop: 10, opacity: enabled ? 1 : 0.6 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <label style={{ display: "flex", alignItems: "center", gap: 7, fontWeight: 600, fontSize: 13.5, cursor: "pointer" }}>
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
          {job.label}
        </label>
        <span style={{ fontSize: 12, color: C.muted }}>{job.description}</span>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 10 }}>
        {job.kind === "weekly" && (
          <select value={dow} onChange={e => setDow(e.target.value)} style={input}>
            {DAYS.map((d, i) => <option key={d} value={i}>{d}s</option>)}
          </select>
        )}
        {timed && (
          <span style={{ display: "flex", gap: 4, alignItems: "center", fontSize: 13 }}>
            at
            <select value={hour} onChange={e => setHour(e.target.value)} style={input}>
              {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{pad(h)}</option>)}
            </select>
            :
            <select value={minute} onChange={e => setMinute(e.target.value)} style={input}>
              {[0, 15, 30, 45].map(m => <option key={m} value={m}>{pad(m)}</option>)}
            </select>
          </span>
        )}
        {job.kind === "interval" && (
          <span style={{ display: "flex", gap: 4, alignItems: "center", fontSize: 13 }}>
            every
            <select value={interval} onChange={e => setIntervalMin(e.target.value)} style={input}>
              {[15, 30, 60, 120, 240].map(m => <option key={m} value={m}>{m} min</option>)}
            </select>
          </span>
        )}
        <select value={tpl} onChange={e => setTpl(e.target.value)} style={{ ...input, minWidth: 210, fontFamily: "monospace" }}>
          <option value="">Default template</option>
          {templates.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <button style={{ ...btn, ...btnOn, marginLeft: "auto" }} disabled={saving} onClick={submit}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      {job.last_fired_at && (
        <div style={{ fontSize: 11, color: C.muted, marginTop: 6 }}>
          Last sent: {String(job.last_fired_at).slice(0, 16).replace("T", " ")} UTC
        </div>
      )}
    </div>
  );
}

const card = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 12 };
const btn = { padding: "7px 13px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "#fff", borderColor: C.green };
const input = { padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text, fontFamily: "system-ui" };
