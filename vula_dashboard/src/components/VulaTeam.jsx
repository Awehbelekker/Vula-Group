/**
 * VulaTeam.jsx — tenant team directory. Add members with their own WhatsApp, choose what
 * each can see (access) and which notifications they get by WhatsApp (per event).
 */
import { useState, useEffect, useCallback } from "react";
import { supabase } from "../lib/supabase";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };

// Access modules map 1:1 to dashboard tab ids.
const MODULES = [
  ["overview", "Overview"], ["assistant", "Assistant"], ["orders", "Orders"], ["products", "Products"],
  ["invoices", "Invoices"], ["finances", "Finances"], ["budget", "Budget"], ["customers", "Customers"],
  ["contacts", "Contacts"], ["followups", "Follow-ups"], ["broadcast", "Broadcast"],
  ["projects", "Projects"], ["qsrates", "QS Rates"], ["documents", "Documents"], ["scanner", "Scanner"],
];
const EVENTS = [
  ["which_project", "Which-project?"], ["followup_digest", "Follow-up digest"],
  ["payment_received", "Payments"], ["new_invoice", "New invoices"],
  ["new_order", "New orders"], ["low_stock", "Low stock"],
  ["help_request", "Help requests (agent escalations)"],
  ["procurement_done", "Procurement done"],
];
const ROLES = ["owner", "manager", "bookkeeper", "staff", "sales_rep"];

// Human-readable label per merchant_audit action (vula/api/merchant_audit.py).
const AUDIT_LABELS = {
  member_added: "added team member", member_updated: "updated team member",
  member_removed: "removed team member", login_created: "created a login",
  password_reset: "reset a password", login_role_changed: "changed a login's role",
  login_access_removed: "revoked a login",
};

export default function VulaTeam({ tenantId }) {
  const [members, setMembers] = useState([]);
  const [form, setForm] = useState({ name: "", whatsapp: "", role: "staff" });
  const [logins, setLogins] = useState([]);
  const [loginForm, setLoginForm] = useState({ email: "", role: "staff" });
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
  useEffect(() => { if (showAudit) loadAudit(); }, [showAudit, tenantId]);

  const createLogin = async () => {
    if (!loginForm.email) return;
    setMsg(""); setTempPw("");
    const d = await fetch(`${VULA_API}/v1/users/${tenantId}/users`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(loginForm),
    }).then((x) => x.json());
    if (d.temp_password) { setTempPw(`${d.email} — temporary password: ${d.temp_password}`); setLoginForm({ email: "", role: "staff" }); load(); }
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
  const toggle = (arr, key) => (arr || []).includes(key) ? arr.filter((k) => k !== key) : [...(arr || []), key];

  const Chip = ({ on, label, onClick }) => (
    <button onClick={onClick} style={{ padding: "3px 9px", borderRadius: 14, fontSize: 11, cursor: "pointer",
      border: `1px solid ${on ? C.green : C.border}`, background: on ? C.green : C.surface, color: on ? "#fff" : C.muted }}>{label}</button>
  );

  return (
    <div style={{ maxWidth: 1000, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 2px" }}>Team</h1>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 18px" }}>Add people, give each their own WhatsApp, and choose what they see and get notified about.</p>

      {/* ── Logins & passwords ─────────────────────────────────────────── */}
      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 18 }}>
        <div style={{ fontWeight: 700, color: C.text, marginBottom: 2 }}>Logins & passwords</div>
        <div style={{ fontSize: 12, color: C.muted, marginBottom: 12 }}>Create a dashboard login for a staff member, or reset a password. Share the one-time temporary password — they change it after signing in.</div>

        <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
          <input placeholder="staff@email.com" value={loginForm.email} onChange={(e) => setLoginForm({ ...loginForm, email: e.target.value })} style={{ padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, minWidth: 220 }} />
          <select value={loginForm.role} onChange={(e) => setLoginForm({ ...loginForm, role: e.target.value })} style={{ padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }}>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <button onClick={createLogin} style={{ padding: "8px 16px", background: C.green, color: "#fff", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>+ Create login</button>
        </div>

        {tempPw && <div style={{ background: "#FBF7E9", border: "1px solid #E6D9A8", borderRadius: 8, padding: "8px 12px", fontSize: 12.5, color: "#6B5A12", marginBottom: 10 }}>🔑 {tempPw} <span style={{ color: C.muted }}>(copy now — shown once)</span></div>}
        {msg && <div style={{ fontSize: 12.5, color: msg.includes("✓") ? "#2C7A4B" : "#A23B2D", marginBottom: 10 }}>{msg}</div>}

        {logins.map((u) => (
          <div key={u.user_id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 0", borderTop: `1px solid ${C.alt}`, flexWrap: "wrap" }}>
            <span style={{ fontSize: 13, color: C.text, fontWeight: 600 }}>{u.email || u.user_id.slice(0, 8)}</span>
            <span style={{ fontSize: 11.5, color: C.muted }}>{u.role}</span>
            <span style={{ fontSize: 11, color: C.muted }}>{u.last_sign_in ? `last in ${new Date(u.last_sign_in).toLocaleDateString()}` : "never signed in"}</span>
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <button onClick={() => resetPw(u.user_id, u.email)} style={{ fontSize: 11.5, color: C.muted, background: "none", border: `1px solid ${C.border}`, borderRadius: 6, padding: "3px 9px", cursor: "pointer" }}>Reset password</button>
              <button onClick={() => removeLogin(u.user_id)} style={{ fontSize: 11.5, color: "#A23B2D", background: "none", border: `1px solid ${C.border}`, borderRadius: 6, padding: "3px 9px", cursor: "pointer" }}>Revoke</button>
            </div>
          </div>
        ))}

        <div style={{ borderTop: `1px solid ${C.alt}`, marginTop: 12, paddingTop: 12, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 12.5, color: C.muted }}>Change my password:</span>
          <input type="password" placeholder="New password" value={newPw} onChange={(e) => setNewPw(e.target.value)} style={{ padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }} />
          <button onClick={changeMyPassword} style={{ padding: "7px 14px", background: C.text, color: "#fff", border: "none", borderRadius: 8, fontSize: 12.5, cursor: "pointer" }}>Update</button>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
        <input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={{ padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }} />
        <input placeholder="WhatsApp e.g. 2782…" value={form.whatsapp} onChange={(e) => setForm({ ...form, whatsapp: e.target.value })} style={{ padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }} />
        <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} style={{ padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }}>
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button onClick={add} style={{ padding: "8px 16px", background: C.green, color: "#fff", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>+ Add member</button>
      </div>

      {members.length === 0 && <div style={{ padding: 16, fontSize: 13, color: C.muted, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10 }}>No team members yet — add one above. (Until then, notifications go to the tenant's default number.)</div>}

      {members.map((m) => (
        <div key={m.id} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 12, opacity: m.active ? 1 : 0.55 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 700, color: C.text }}>{m.name || m.whatsapp}</span>
            <span style={{ fontSize: 12, color: C.muted }}>{m.whatsapp}</span>
            <select value={m.role} onChange={(e) => patch(m.id, { role: e.target.value })} style={{ padding: "3px 7px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 11.5 }}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <button onClick={() => patch(m.id, { active: !m.active })} style={{ fontSize: 11.5, color: C.muted, background: "none", border: `1px solid ${C.border}`, borderRadius: 6, padding: "3px 9px", cursor: "pointer" }}>{m.active ? "Disable" : "Enable"}</button>
              <button onClick={() => del(m.id)} style={{ fontSize: 11.5, color: "#A23B2D", background: "none", border: `1px solid ${C.border}`, borderRadius: 6, padding: "3px 9px", cursor: "pointer" }}>Remove</button>
            </div>
          </div>
          {(() => {
            const isOwnerOrManager = m.role === "owner" || m.role === "manager";
            const isFull = isOwnerOrManager || !(m.access || []).length;
            return (
              <>
                <div style={{ fontSize: 11, color: C.muted, marginBottom: 4, textTransform: "uppercase" }}>
                  Can access
                  {isFull && (
                    <span style={{ color: "var(--accent)", textTransform: "none", marginLeft: 6, fontWeight: 600 }}>
                      · Full access{isOwnerOrManager ? " (owner/manager)" : " — click a module below to restrict"}
                    </span>
                  )}
                </div>
                <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginBottom: 12 }}>
                  {MODULES.map(([k, label]) => (
                    <Chip key={k} label={label} on={isFull || (m.access || []).includes(k)}
                          onClick={() => patch(m.id, { access: toggle(m.access, k) })} />
                  ))}
                </div>
              </>
            );
          })()}
          <div style={{ fontSize: 11, color: C.muted, marginBottom: 4, textTransform: "uppercase" }}>Notify by WhatsApp</div>
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
            {EVENTS.map(([k, label]) => <Chip key={k} label={label} on={(m.notify || []).includes(k)} onClick={() => patch(m.id, { notify: toggle(m.notify, k) })} />)}
          </div>
        </div>
      ))}

      {/* ── Audit log (migration 107) ──────────────────────────────────── */}
      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginTop: 18 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }} onClick={() => setShowAudit((s) => !s)}>
          <span style={{ fontWeight: 700, color: C.text }}>Activity log</span>
          <span style={{ fontSize: 11, color: C.muted }}>who changed team access, roles, and logins</span>
          <span style={{ marginLeft: "auto", fontSize: 12, color: C.muted }}>{showAudit ? "▲ hide" : "▼ show"}</span>
        </div>
        {showAudit && (
          auditLog.length === 0
            ? <div style={{ fontSize: 12.5, color: C.muted, marginTop: 10 }}>No activity recorded yet.</div>
            : <div style={{ marginTop: 10 }}>
                {auditLog.map((e) => (
                  <div key={e.id} style={{ display: "flex", gap: 10, padding: "6px 0", borderTop: `1px solid ${C.alt}`, fontSize: 12.5, flexWrap: "wrap" }}>
                    <span style={{ color: C.muted, minWidth: 130 }}>{new Date(e.created_at).toLocaleString()}</span>
                    <span style={{ color: C.text }}>{e.actor_email || "unknown"}</span>
                    <span style={{ color: C.muted }}>{AUDIT_LABELS[e.action] || e.action}</span>
                    {e.detail && (e.detail.email || e.detail.name) && (
                      <span style={{ color: C.muted }}>— {e.detail.email || e.detail.name}</span>
                    )}
                  </div>
                ))}
              </div>
        )}
      </div>

      <p style={{ textAlign: "center", fontSize: 11, color: "#B5B0A8", marginTop: 12 }}>Powered by Vula · each member gets only what you select</p>
    </div>
  );
}
