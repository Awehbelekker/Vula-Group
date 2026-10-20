/**
 * VulaScheduledJobs.jsx — ⏰ Scheduling: per-tenant config for the 5 system WhatsApp jobs
 * (delivery briefing, low-stock alert, sales summary, weekly specials reminder, unpaid-order
 * chase). Times, template and on/off per job — previously hardcoded in the backend.
 * Backend: /v1/commerce/{tenant}/admin/scheduled-jobs (migration 069).
 */
import { DAYS, pad, useScheduledJobs, useJobForm } from "./marketing/useScheduledJobs";
import ScheduledJobsV2 from "./marketing/ScheduledJobsV2";

const C = { surface: "var(--surface)", border: "var(--border)", green: "var(--accent)", red: "var(--danger)", text: "var(--text)", muted: "var(--muted)", alt: "var(--surface-alt)" };


export default function VulaScheduledJobs({ v2 = false, ...props }) {
  return v2 ? <ScheduledJobsV2 {...props} /> : <ScheduledJobsV1 {...props} />;
}

function ScheduledJobsV1({ tenantId }) {
  const { jobs, templates, busy, msg, save } = useScheduledJobs(tenantId);

  return (
    <div style={{ color: C.text, maxWidth: 860 }}>
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
  const { enabled, setEnabled, tpl, setTpl, hour, setHour, minute, setMinute, dow, setDow, interval, setIntervalMin, saving, timed, submit } = useJobForm(job, onSave);

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
const btnOn = { background: C.green, color: "var(--on-accent)", borderColor: C.green };
const input = { padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text};
