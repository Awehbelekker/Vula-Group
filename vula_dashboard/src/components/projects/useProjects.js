/**
 * useProjects.js — the Projects command centre's data and actions (2026-10-19, UX redesign Phase
 * F). Shared by VulaProjects.jsx and projects/ProjectsV2.jsx; moved out unchanged — same
 * endpoints (/v1/projects/{tenant}…), same bodies, same messages.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { VULA_API } from "../../lib/authFetch";

export const PROJECT_TABS = [["overview", "Overview"], ["documents", "Documents"], ["boq", "BOQ"], ["money", "Money"], ["team", "Standards & team"]];

/** A project's BOQ (total + trade sections). */
export const fetchBoq = (tenantId, project) =>
  fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/boq`).then((r) => r.json());
/** Set the contract value; `total` is rand as typed ("1 250 000" → 1250000). Returns false if it isn't a number. */
export async function saveBoqTotal(tenantId, project, total) {
  const v = parseFloat(String(total).replace(/[^\d.]/g, ""));
  if (!v) return false;
  await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/boq`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ total: v }) });
  return true;
}
export const fetchFinancials = (tenantId, project) =>
  fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/financials`).then((r) => r.json());
/** The project tracker (budget vs actual, account, share links) — vula/commerce/project_tracker.py. */
export const fetchTracker = (tenantId, project) =>
  fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/projects/tracker?project=${encodeURIComponent(project)}`).then((r) => r.json());
/** A client link for the project in `view` ("price" | "open_book"); resolves to { url, expires_at } or { error }. */
export async function createShare(tenantId, project, view) {
  const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/projects/share`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project, view }) }).catch(() => null);
  if (!r) return { error: "Couldn't reach Vula — try again." };
  const d = await r.json().catch(() => ({}));
  return r.ok ? d : { error: d.detail || "The link couldn't be made." };
}
/** The online BOQ (vula/commerce/boq_online.py): the bill, the rate library, and its actions. */
const boqUrl = (tenantId, path = "") => `${VULA_API}/v1/commerce/${tenantId}/admin/projects/boq-online${path}`;
export const fetchBoqOnline = (tenantId, project) =>
  fetch(`${boqUrl(tenantId)}?project=${encodeURIComponent(project)}`).then((r) => r.json());
export const fetchBoqLibrary = (tenantId) =>
  fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/boq-library`).then((r) => r.json());
export const fetchBillLines = (tenantId, project) =>
  fetch(`${boqUrl(tenantId, "/from-bill")}?project=${encodeURIComponent(project)}`).then((r) => r.json());
/** POST to the bill; resolves to the server's answer or { error }. */
async function boqPost(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!r) return { error: "Couldn't reach Vula — try again." };
  const d = await r.json().catch(() => ({}));
  return r.ok ? d : { error: d.detail || "That didn't work — try again." };
}
export const saveBoqOnline = (tenantId, project, lines) => boqPost(boqUrl(tenantId), { project, lines });
export const makeBoqTheBudget = (tenantId, project) => boqPost(boqUrl(tenantId, "/use-as-budget"), { project });
/** Open the priced bill in a new window to print or save as PDF. */
export async function printBoq(tenantId, project) {
  const w = window.open("", "_blank");
  const html = await fetch(`${boqUrl(tenantId, "/print")}?project=${encodeURIComponent(project)}`).then((r) => r.text()).catch(() => "");
  if (w) { w.document.write(html || "<p>The bill couldn't be loaded.</p>"); w.document.close(); }
}
/** Save a file the API returns. Resolves to "ok", "forbidden" (no access) or "failed". */
async function saveFile(path, filename) {
  const r = await fetch(`${VULA_API}${path}`).catch(() => null);
  if (!r || !r.ok) return r && r.status === 403 ? "forbidden" : "failed";
  const url = URL.createObjectURL(await r.blob());
  Object.assign(document.createElement("a"), { href: url, download: filename }).click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return "ok";
}
const fileStem = (project) => project.replace(/[^\w -]/g, "").replace(/ /g, "_");
/** The bill as the branded Excel template, pre-filled — the team's working copy (Money access only). */
export const downloadBoq = (tenantId, project) =>
  saveFile(`/v1/commerce/${tenantId}/admin/projects/boq-online.xlsx?project=${encodeURIComponent(project)}`, `${fileStem(project)}_BOQ.xlsx`);
/** The priced bill as a PDF — the copy for clients and anyone outside the team. */
export const downloadBoqPdf = (tenantId, project) =>
  saveFile(`/v1/commerce/${tenantId}/admin/projects/boq-online.pdf?project=${encodeURIComponent(project)}`, `${fileStem(project)}_priced_bill.pdf`);
/** The QS's view of the project (no cost, fee or profit) — open to a login limited to Projects. */
export const fetchQsView = (tenantId, project) =>
  fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/projects/qs-view?project=${encodeURIComponent(project)}`).then((r) => r.json());
/** Record a payment the client made (status "paid") or still has to make ("to_pay") to a supplier directly. */
export async function addDirectPayment(tenantId, body) {
  const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/projects/direct-payments`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!r) return { error: "Couldn't reach Vula — try again." };
  const d = await r.json().catch(() => ({}));
  return r.ok ? d : { error: d.detail || "That couldn't be saved." };
}
export const removeDirectPayment = (tenantId, id) =>
  fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/projects/direct-payments/${id}/remove`, { method: "POST" }).then((r) => r.ok).catch(() => false);
/** Link a client's WhatsApp number to the project (vula/commerce/project_client.py); resolves to the link or { error }. */
export async function addProjectClient(tenantId, body) {
  const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/projects/clients`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!r) return { error: "Couldn't reach Vula — try again." };
  const d = await r.json().catch(() => ({}));
  return r.ok ? d : { error: d.detail || "The client couldn't be added." };
}
export const removeProjectClient = (tenantId, id) =>
  fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/projects/clients/${id}/remove`, { method: "POST" }).then((r) => r.ok).catch(() => false);
/** Yes/no to a slip the client sent on WhatsApp; the client is told either way. */
export const decideClientSlip = (tenantId, id, approve) =>
  fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/projects/client-slips/${id}/decide`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve }) }).then((r) => r.ok).catch(() => false);
export const revokeShare = (tenantId, id) =>
  fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/projects/shares/${id}/revoke`, { method: "POST" }).then((r) => r.ok).catch(() => false);
/** The money rows the screen shows: numbers, and strings that are already rand amounts. */
export const moneyRows = (f) => Object.entries(f || {}).filter(([, v]) => typeof v === "number" || (typeof v === "string" && /^R/.test(v)));

/** Upload files straight to a project (the ingestion pipeline files them under it). Returns how many went. */
export async function uploadToProject(tenantId, project, files) {
  let ok = 0;
  for (const f of files) {
    const fd = new FormData();
    fd.append("tenant_id", tenantId); fd.append("file", f); fd.append("project", project);
    const r = await fetch(`${VULA_API}/ingest`, { method: "POST", body: fd }).catch(() => null);
    if (r && r.ok) ok += 1;
  }
  return ok;
}

export function useProjects(tenantId) {
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
  const closeProject = () => { setSel(null); setOv(null); };

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

  return {
    projects, codes, sel, ov, tab, setTab, query, setQuery, newProj, setNewProj, newCode, setNewCode, newMember, setNewMember,
    busy, msg, groups, allClients, openProject, closeProject, createProject, saveEdit, addAlias, removeAlias, addPhase,
    addCode, linkCode, unlinkCode, addMember, unlinked,
  };
}
