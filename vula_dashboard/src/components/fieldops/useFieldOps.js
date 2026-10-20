/**
 * useFieldOps.js — Field Operations logic shared by VulaFieldOps and FieldOpsV2 (moved verbatim):
 * the tenant-scoped api() helper, projects, contractors, project status + task detail and
 * sign-off, assigning a task (optionally WhatsApp-ing the contractor), site walkthroughs and the
 * morning briefings.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../../lib/authFetch";
import { toast } from "../ui/index.jsx";

// The tenant this Field Ops view is for (set by VulaFieldOps on render). Every call carries it as
// ?tenant_id= — the API authorizes a signed-in member by it and only returns that tenant's
// tasks, projects and walkthroughs on the id-only routes.
let _fieldTenant = "";
export const setFieldTenant = (t) => { _fieldTenant = t || ""; };

export async function api(method, path, body) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  const url = _fieldTenant
    ? `${VULA_API}${path}${path.includes("?") ? "&" : "?"}tenant_id=${encodeURIComponent(_fieldTenant)}`
    : `${VULA_API}${path}`;
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}


export function useProjectOptions({ tenantId }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!tenantId) return;
    setLoading(true);
    api("GET", `/v1/projects/${tenantId}`)
      .then(d => setProjects(d.projects || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [tenantId]);
  return { projects, setProjects, loading, setLoading };
}

export function useDailyBriefing({ tenantId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    if (!tenantId) return;
    setLoading(true);
    try {
      const d = await api("GET", `/v1/field/daily-tasks/${tenantId}`);
      setData(d);
    } catch {}
    finally { setLoading(false); }
  }, [tenantId]);

  useEffect(() => { load(); }, [load]);

  const dispatch = async () => {
    setDispatching(true); setMsg("");
    try {
      const r = await api("POST", `/v1/field/daily-tasks/${tenantId}/dispatch`);
      setMsg(`Success: ${r.contractors_briefed} briefings sent.`);
    } catch (e) { setMsg(`Error: ${e.message}`); }
    finally { setDispatching(false); }
  };
  return { data, setData, loading, setLoading, dispatching, setDispatching, msg, setMsg, load, dispatch };
}

export function useContractors({ tenantId, onRefresh }) {
  const [contractors, setContractors] = useState([]);
  const [form, setForm] = useState({ name: "", phone: "", trade: "" });
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    try {
      const d = await api("GET", `/v1/field/contractors/${tenantId}`);
      setContractors(d.contractors || []);
    } catch {}
  }, [tenantId]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!form.name || !form.phone || !form.trade) return;
    setSaving(true);
    try {
      await api("POST", "/v1/field/contractors", { tenant_id: tenantId, ...form });
      setForm({ name: "", phone: "", trade: "" });
      setMsg("Contractor saved.");
      load();
      onRefresh?.();
    } catch (e) { setMsg(`Error: ${e.message}`); }
    finally { setSaving(false); }
  };
  return { contractors, setContractors, form, setForm, saving, setSaving, msg, setMsg, load, save };
}

export function useProjectStatus({ tenantId }) {
  const [projectId, setProjectId] = useState("");
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (id) => {
    const targetId = id || projectId;
    if (!targetId.trim()) return;
    setLoading(true); setError("");
    try {
      const d = await api("GET", `/v1/field/project/${targetId.trim()}/status`);
      setStatus(d);
    } catch (e) { setError(e.message); setStatus(null); }
    finally { setLoading(false); }
  }, [projectId]);

  const onProjectChange = (id) => {
    setProjectId(id);
    if (id) load(id);
    else setStatus(null);
  };
  return { projectId, setProjectId, status, setStatus, loading, setLoading, error, setError, load, onProjectChange };
}

export function useTaskRow({ task, projectId, onRefresh, tenantId }) {
  const [requesting, setRequesting] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadDetail = async () => {
    if (expanded) { setExpanded(false); return; }
    setExpanded(true);
    if (detail) return;
    setLoading(true);
    try {
      const d = await api("GET", `/v1/field/task/${task.id}`);
      setDetail(d);
    } catch {}
    finally { setLoading(false); }
  };

  const requestComplete = async (e) => {
    e?.stopPropagation();
    setRequesting(true);
    try {
      await api("POST", `/v1/field/task/${task.id}/complete-request`, {});
    } finally { setRequesting(false); }
  };
  return { requesting, setRequesting, expanded, setExpanded, detail, setDetail, loading, setLoading, loadDetail, requestComplete };
}

export function useSignOff({ taskId, onRefresh }) {
  const [busy, setBusy] = useState(false);
  const [notes, setNotes] = useState("");

  const sign = async (decision) => {
    setBusy(true);
    try {
      // For sign-off we use a contractor phone as approver phone (simulating manager)
      // In a real scenario, this would be the logged-in user's verified phone.
      await api("POST", `/v1/field/walkthrough/${taskId}/approve`, {
        approver_phone: "system-admin", // Placeholder
        decision,
        notes,
      });
      onRefresh();
    } catch (e) { toast(e.message); }
    finally { setBusy(false); }
  };
  return { busy, setBusy, notes, setNotes, sign };
}

export function useAssignTask({ tenantId }) {
  const [contractors, setContractors] = useState([]);
  const [form, setForm] = useState({
    project_id: "", title: "", trade: "",
    contractor_id: "", due_date: "", notes: "",
  });
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!tenantId) return;
    api("GET", `/v1/field/contractors/${tenantId}`)
      .then(d => setContractors(d.contractors || []))
      .catch(() => {});
  }, [tenantId]);

  const assign = async () => {
    if (!form.project_id || !form.title || !form.trade) return;
    setSaving(true); setMsg("");
    try {
      const task = await api("POST", "/v1/field/task", { tenant_id: tenantId, ...form });
      if (form.contractor_id) {
        await api("POST", "/v1/field/task/assign", {
          task_id: task.id,
          contractor_id: form.contractor_id,
          send_whatsapp: true,
        });
        setMsg(`Task created and WhatsApp sent to contractor.`);
      } else {
        setMsg(`Task created (not assigned yet).`);
      }
      setForm(f => ({ ...f, title: "", notes: "", contractor_id: "" }));
    } catch (e) { setMsg(`Error: ${e.message}`); }
    finally { setSaving(false); }
  };
  return { contractors, setContractors, form, setForm, saving, setSaving, msg, setMsg, assign };
}

export function useWalkthrough({ tenantId }) {
  const [contractors, setContractors] = useState([]);
  const [form, setForm] = useState({ project_id: "", title: "", contractor_id: "", items: "" });
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (!tenantId) return;
    api("GET", `/v1/field/contractors/${tenantId}`)
      .then(d => setContractors(d.contractors || []))
      .catch(() => {});
  }, [tenantId]);

  const start = async () => {
    if (!form.project_id || !form.title || !form.contractor_id || !form.items.trim()) return;
    setSaving(true); setMsg(""); setResult(null);
    try {
      const items = form.items.split("\n").map(s => s.trim()).filter(Boolean);
      const d = await api("POST", "/v1/field/walkthrough/start", {
        tenant_id: tenantId,
        project_id: form.project_id,
        title: form.title,
        contractor_id: form.contractor_id,
        items,
      });
      setResult(d);
      setMsg(d.whatsapp_sent ? "Walkthrough started — WhatsApp sent." : "Walkthrough created (WhatsApp not configured).");
    } catch (e) { setMsg(`Error: ${e.message}`); }
    finally { setSaving(false); }
  };
  return { contractors, setContractors, form, setForm, saving, setSaving, msg, setMsg, result, setResult, start };
}
