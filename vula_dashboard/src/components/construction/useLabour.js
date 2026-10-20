/**
 * useLabour.js — the casual-labour register and labour-paid report (shared by VulaLabour and
 * LabourV2). Moved verbatim out of VulaLabour.jsx.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../../lib/authFetch";
import { confirmDialog } from "../ui/index.jsx";

export const blank = { name: "", id_number: "", bank_account: "", phone: "", rate_rands: "", rate_period: "daily", type: "casual", default_project: "" };

export function useLabour({ tenantId }) {
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
    return true;   // the redesign closes its sheet on this
  };

  const edit = (w) => { setF({ ...blank, ...w, rate_rands: w.rate_cents ? (w.rate_cents / 100).toString() : "" }); setEditing(true); };
  const remove = async (id) => {
    if (!(await confirmDialog("Remove this worker from the active register?", { danger: true, confirmLabel: 'Yes' }))) return;
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/workers/${id}`, { method: "DELETE" }).catch(() => {});
    load();
  };
  return { workers, setWorkers, report, setReport, projects, setProjects, f, setF, editing, setEditing, msg, setMsg, load, flash, save, edit, remove };
}
