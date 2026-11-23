/**
 * useLabour.js — the casual-labour register and labour-paid report (shared by VulaLabour and
 * LabourV2). Moved verbatim out of VulaLabour.jsx.
 */
import { useState, useEffect, useCallback } from "react";
import { tryApi } from "../../lib/api";
import { confirmDialog, toast } from "../ui/index.jsx";

export const blank = { name: "", id_number: "", bank_account: "", phone: "", rate_rands: "", rate_period: "daily", type: "casual", default_project: "" };

export function useLabour({ tenantId }) {
  const [workers, setWorkers] = useState([]);
  const [report, setReport] = useState(null);
  const [projects, setProjects] = useState([]);
  const [f, setF] = useState(blank);
  const [editing, setEditing] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    // 23 Nov 2026 audit: a failed load showed "No workers yet". Only what loaded replaces the screen.
    const [w, r, p] = await Promise.all([
      tryApi(`/v1/commerce/${tenantId}/admin/workers`),
      tryApi(`/v1/commerce/${tenantId}/admin/reports/labour`),
      tryApi(`/v1/projects/${tenantId}/labels`),
    ]);
    if (w.ok) setWorkers(w.data.workers || []);
    if (r.ok) setReport(r.data);
    if (p.ok) setProjects(p.data.projects || []);
    if (!w.ok) toast(`The worker register couldn't be loaded: ${w.error}`, "danger");
    else if (!r.ok) toast(`The labour report couldn't be loaded: ${r.error}`, "danger");
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 3500); };

  const save = async () => {
    if (!f.name.trim()) return flash("Worker name is required.");
    // 23 Nov 2026 audit: only r.error was checked, so a refusal ({detail}) said "Worker saved ✓".
    const r = await tryApi(`/v1/commerce/${tenantId}/admin/workers`, {
      method: "POST",
      body: { ...f, rate_rands: f.rate_rands ? Number(f.rate_rands) : null },
    });
    if (!r.ok) { toast(`The worker wasn't saved: ${r.error}`, "danger"); return false; }
    setF(blank); setEditing(false); flash("Worker saved ✓"); load();
    return true;   // the redesign closes its sheet on this
  };

  const edit = (w) => { setF({ ...blank, ...w, rate_rands: w.rate_cents ? (w.rate_cents / 100).toString() : "" }); setEditing(true); };
  const remove = async (id) => {
    if (!(await confirmDialog("Remove this worker from the active register?", { danger: true, confirmLabel: 'Yes' }))) return;
    const r = await tryApi(`/v1/commerce/${tenantId}/admin/workers/${id}`, { method: "DELETE" });
    if (!r.ok) toast(`The worker wasn't removed: ${r.error}`, "danger");
    load();
  };
  return { workers, setWorkers, report, setReport, projects, setProjects, f, setF, editing, setEditing, msg, setMsg, load, flash, save, edit, remove };
}
