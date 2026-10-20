/**
 * useScheduledJobs.js — Marketing › Scheduling: the automatic WhatsApp jobs (delivery briefing,
 * low-stock alert, sales summary, weekly specials reminder, unpaid-order chase), shared by
 * VulaScheduledJobs (current look) and marketing/ScheduledJobsV2 (the redesign, 2026-10-20). Moved
 * verbatim: each job's on/off, time (SAST) or interval, and which approved template it uses.
 */
import { useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";

const api = (t, p, opts) => fetch(`${VULA_API}/v1/commerce/${t}/admin${p}`, opts).then(r => r.json());
export const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const pad = (n) => String(n).padStart(2, "0");

export function useScheduledJobs(tenantId) {
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

  return { jobs, templates, busy, msg, save };
}

export function useJobForm(job, onSave) {
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

  return { enabled, setEnabled, tpl, setTpl, hour, setHour, minute, setMinute, dow, setDow, interval, setIntervalMin, saving, timed, submit };
}
