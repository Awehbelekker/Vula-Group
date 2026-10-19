/**
 * useTeam.js — the Team screen's data and actions (2026-10-18, UX redesign Phase F): members,
 * their access and notifications, dashboard logins and passwords, and the audit log. Shared by
 * VulaTeam.jsx and team/TeamV2.jsx; moved out of VulaTeam.jsx unchanged — same endpoints (all
 * owner/manager-only on the server, tenant_auth.require_tenant_admin), same bodies.
 */
import { useCallback, useEffect, useState } from "react";
import { supabase } from "../../lib/supabase";
import { REP_DEFAULT_ACCESS, MERCHANT_GROUPS } from "../../navConfig.jsx";
import { VULA_API } from "../../lib/authFetch";

// Sales rep ("My Work") tabs — a sales_rep login gets these by default (see createLogin).
export const REP_MODULES = [["rep-contacts", "My Contacts"], ["rep-callsheet", "My Call Sheet"], ["rep-bookings", "My Bookings"],
  ["rep-documents", "My Documents"], ["rep-reminders", "My Reminders"], ["rep-expenses", "My Expenses"], ["rep-crm", "My Dynamics 365"]];

// Access modules map 1:1 to dashboard tab ids — generated from the sidebar (navConfig) so a
// new tab can always be granted. The hand-kept list had drifted: ~28 tabs (Inbox, Expenses,
// Bank, Payments, Bookings, Pages, Templates, QS, Takeoff...) couldn't be given to a
// restricted member at all. Section ids with subtabs are groupings, not permissions — only
// their children are listed.
export const MODULES = (() => {
  const out = [];
  for (const g of MERCHANT_GROUPS) {
    for (const it of g.items) {
      if (it.subtabs) it.subtabs.forEach(st => out.push([st.id, st.label]));
      else out.push([it.id, it.label]);
    }
  }
  for (const r of REP_MODULES) {
    if (!out.some(([k]) => k === r[0])) out.push(r);
  }
  return out;
})();
export const EVENTS = [
  ["which_project", "Which-project?"], ["followup_digest", "Follow-up digest"],
  ["payment_received", "Payments"], ["new_invoice", "New invoices"],
  ["new_order", "New orders"], ["low_stock", "Low stock"],
  ["help_request", "Help requests (agent escalations)"],
  ["procurement_done", "Procurement done"],
  ["invoice_overdue_escalated", "Overdue invoice escalations (14+ days)"],
  ["project_margin", "Project check (weekly: jobs below their fee)"],
];
export const ROLES = ["owner", "manager", "bookkeeper", "staff", "sales_rep"];
// Friendly display label per role — the stored value stays the plain enum (used by the
// backend's role checks), only the text shown to a tenant is reframed. sales_rep gets an
// "AI-assisted" note since that role's WhatsApp number is backed by a scoped AI assistant
// (contacts, meetings, proposals), same "AI team member" framing used elsewhere in the app.
export const ROLE_LABELS = { owner: "Owner", manager: "Manager", bookkeeper: "Bookkeeper",
  staff: "Staff", sales_rep: "Sales Rep (AI-assisted)" };

// Human-readable label per merchant_audit action (vula/api/merchant_audit.py).
export const AUDIT_LABELS = {
  member_added: "added team member", member_updated: "updated team member",
  member_removed: "removed team member", login_created: "created a login",
  password_reset: "reset a password", login_role_changed: "changed a login's role",
  login_access_removed: "revoked a login",
};

export const toggle = (arr, key) => (arr || []).includes(key) ? arr.filter((k) => k !== key) : [...(arr || []), key];

export function useTeam(tenantId) {
  const [members, setMembers] = useState([]);
  const [form, setForm] = useState({ name: "", whatsapp: "", role: "staff" });
  const [logins, setLogins] = useState([]);
  const [loginForm, setLoginForm] = useState({ email: "", role: "staff", whatsapp: "" });
  const [tempPw, setTempPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [msg, setMsg] = useState("");
  const [auditLog, setAuditLog] = useState([]);
  const [showAudit, setShowAudit] = useState(false);

  const load = useCallback(async () => {
    if (!tenantId) return;
    const r = await fetch(`${VULA_API}/v1/team/${tenantId}`);
    const d = await r.json();
    setMembers(d.members || []);
    const lu = await fetch(`${VULA_API}/v1/users/${tenantId}/users`).then((x) => x.json()).catch(() => ({}));
    setLogins(lu.users || []);
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const loadAudit = async () => {
    if (!tenantId) return;
    const d = await fetch(`${VULA_API}/v1/team/${tenantId}/audit`).then((x) => x.json()).catch(() => ({}));
    setAuditLog(d.events || []);
  };
  useEffect(() => { if (showAudit) loadAudit(); }, [showAudit, tenantId]);  // eslint-disable-line react-hooks/exhaustive-deps

  const createLogin = async () => {
    if (!loginForm.email) return;
    if (loginForm.role === "sales_rep" && !loginForm.whatsapp.trim()) {
      setMsg("A Sales Rep login needs a WhatsApp number — it's how My Work scopes their data.");
      return;
    }
    setMsg(""); setTempPw("");
    // A sales_rep login is meant to be a genuinely separate, restricted "My Work" view — default
    // its access to exactly those tabs (still editable afterward via the team-directory chips
    // below) rather than leaving it empty, which today's `full = !access.length` rule would
    // otherwise treat as full owner-equivalent access.
    const body = loginForm.role === "sales_rep"
      ? { ...loginForm, access: REP_DEFAULT_ACCESS } : loginForm;
    const d = await fetch(`${VULA_API}/v1/users/${tenantId}/users`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then((x) => x.json());
    if (d.temp_password) { setTempPw(`${d.email} — temporary password: ${d.temp_password}`); setLoginForm({ email: "", role: "staff", whatsapp: "" }); load(); }
    else setMsg(d.error || "Could not create login");
  };
  const resetPw = async (uid, email) => {
    setMsg(""); setTempPw("");
    const d = await fetch(`${VULA_API}/v1/users/${tenantId}/users/${uid}/reset`, { method: "POST" }).then((x) => x.json());
    if (d.temp_password) setTempPw(`${email || ""} — temporary password: ${d.temp_password}`);
    else setMsg(d.error || "Reset failed");
  };
  const removeLogin = async (uid) => {
    await fetch(`${VULA_API}/v1/users/${tenantId}/users/${uid}`, { method: "DELETE" }); load();
  };
  const changeMyPassword = async () => {
    setMsg("");
    if ((newPw || "").length < 8) { setMsg("Password must be at least 8 characters."); return; }
    const { error } = await supabase.auth.updateUser({ password: newPw });
    setMsg(error ? error.message : "Your password was changed ✓"); setNewPw("");
  };

  const add = async () => {
    if (!form.name && !form.whatsapp) return;
    await fetch(`${VULA_API}/v1/team/${tenantId}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...form, access: [], notify: ["which_project", "followup_digest"] }),
    });
    setForm({ name: "", whatsapp: "", role: "staff" }); load();
  };
  const patch = async (id, body) => {
    const d = await fetch(`${VULA_API}/v1/team/${tenantId}/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then((x) => x.json()).catch(() => ({}));
    if (d.login_revoked) setMsg("Disabled — their dashboard login was revoked too.");
    load();
  };
  const del = async (id) => { await fetch(`${VULA_API}/v1/team/${tenantId}/${id}`, { method: "DELETE" }); load(); };

  return {
    members, form, setForm, logins, loginForm, setLoginForm, tempPw, newPw, setNewPw, msg, auditLog, showAudit, setShowAudit,
    createLogin, resetPw, removeLogin, changeMyPassword, add, patch, del,
  };
}
