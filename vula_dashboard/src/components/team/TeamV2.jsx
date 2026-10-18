/**
 * TeamV2.jsx — the redesigned Team (2026-10-18, UX redesign Phase F). Same data and actions as
 * the current screen (team/useTeam.js), same rules: an owner or manager, or a member with no
 * access list, has full access; ticking a module restricts them to what's ticked. The access
 * chips are grouped by menu section — the redesign's menu, so Stock and Suppliers sit under
 * Operate — and each is a toggle button (aria-pressed). Ids are the same as before.
 */
import { useMemo } from "react";
import { History, KeyRound, Plus, RotateCcw, ShieldCheck, Trash2, UserPlus, UserX, Users } from "lucide-react";
import { MERCHANT_GROUPS, withV2Moves } from "../../navConfig.jsx";
import { Button, Card, EmptyState, Field, Input, PageHeader, Select, StatusChip } from "../ui/index.jsx";
import { AUDIT_LABELS, EVENTS, MODULES, REP_MODULES, ROLE_LABELS, ROLES, toggle, useTeam } from "./useTeam";
import "./team.css";

/** MODULES grouped as the redesigned menu shows them; anything not in a section goes last. */
function useModuleGroups() {
  return useMemo(() => {
    const seen = new Set();
    const groups = [];
    for (const g of withV2Moves(MERCHANT_GROUPS)) {
      for (const it of g.items) {
        const ids = it.subtabs ? it.subtabs.map((st) => [st.id, st.label]) : [[it.id, it.label]];
        const label = it.subtabs ? it.label : (g.label || "General");
        let grp = groups.find((x) => x.label === label);
        if (!grp) groups.push((grp = { label, items: [] }));
        ids.forEach(([id, l]) => { seen.add(id); grp.items.push([id, l]); });
      }
    }
    groups.push({ label: "My Work (sales reps)", items: REP_MODULES.filter(([id]) => !seen.has(id)) });
    const rest = MODULES.filter(([id]) => !seen.has(id) && !REP_MODULES.some(([r]) => r === id));
    if (rest.length) groups.push({ label: "Other", items: rest });
    return groups.filter((g) => g.items.length);
  }, []);
}

function Chip({ on, label, onClick }) {
  return <button type="button" className="vui-chip vtm-chip" aria-pressed={on} onClick={onClick}>{label}</button>;
}

export default function TeamV2({ tenantId }) {
  const t = useTeam(tenantId);
  const groups = useModuleGroups();
  const ok = t.msg && t.msg.includes("✓");

  return (
    <div>
      <PageHeader sub="Add people, give each their own WhatsApp, and choose what they see and get notified about. Each member gets only what you select." />

      {/* ── Logins & passwords ── */}
      <Card className="vtm-block">
        <h2 className="vtm-h"><KeyRound size={18} aria-hidden="true" />Logins &amp; passwords</h2>
        <p className="vtm-sub">Create a dashboard login for a staff member, or reset a password. Share the one-time temporary password — they change it after signing in.</p>
        <div className="vtm-form">
          <Field label="Email"><Input type="email" placeholder="staff@email.com" value={t.loginForm.email} onChange={(e) => t.setLoginForm({ ...t.loginForm, email: e.target.value })} /></Field>
          <Field label={t.loginForm.role === "sales_rep" ? "WhatsApp (required)" : "WhatsApp (optional)"}>
            <Input inputMode="tel" placeholder="e.g. 2782…" value={t.loginForm.whatsapp} onChange={(e) => t.setLoginForm({ ...t.loginForm, whatsapp: e.target.value })} />
          </Field>
          <Field label="Role">
            <Select value={t.loginForm.role} onChange={(e) => t.setLoginForm({ ...t.loginForm, role: e.target.value })}>
              {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r] || r}</option>)}
            </Select>
          </Field>
          <div className="vtm-go"><Button Icon={Plus} onClick={t.createLogin}>Create login</Button></div>
        </div>
        {t.loginForm.role === "sales_rep" && (
          <p className="vtm-sub">A Sales Rep login needs a WhatsApp number — it's how My Work scopes contacts, call sheet, reminders and expenses to them.</p>
        )}
        {t.tempPw && <p className="vtm-pw" role="status"><KeyRound size={16} aria-hidden="true" />{t.tempPw} <span className="vtm-sub">(copy now — shown once)</span></p>}
        {t.msg && <p className={`vtm-msg${ok ? " vtm-msg--ok" : ""}`} role={ok ? "status" : "alert"}>{t.msg}</p>}

        {t.logins.length > 0 && (
          <div className="vtm-logins" role="list" aria-label="Dashboard logins">
            {t.logins.map((u) => (
              <div key={u.user_id} role="listitem" className="vtm-login">
                <span className="vtm-login-who">
                  <span className="vtm-strong">{u.email || u.user_id.slice(0, 8)}</span>
                  <span className="vtm-sub">{ROLE_LABELS[u.role] || u.role} · {u.last_sign_in ? `last in ${new Date(u.last_sign_in).toLocaleDateString("en-ZA")}` : "never signed in"}</span>
                </span>
                <span className="vtm-actions">
                  <Button size="sm" variant="secondary" Icon={RotateCcw} onClick={() => t.resetPw(u.user_id, u.email)}>Reset password</Button>
                  <Button size="sm" variant="danger" Icon={UserX} onClick={() => t.removeLogin(u.user_id)}>Revoke</Button>
                </span>
              </div>
            ))}
          </div>
        )}

        <div className="vtm-mypw">
          <Field label="Change my password">
            <Input type="password" autoComplete="new-password" placeholder="New password (8+ characters)" value={t.newPw} onChange={(e) => t.setNewPw(e.target.value)} />
          </Field>
          <Button variant="secondary" onClick={t.changeMyPassword}>Update</Button>
        </div>
      </Card>

      {/* ── Add a member ── */}
      <Card className="vtm-block">
        <h2 className="vtm-h"><UserPlus size={18} aria-hidden="true" />Add a team member</h2>
        <div className="vtm-form">
          <Field label="Name"><Input value={t.form.name} onChange={(e) => t.setForm({ ...t.form, name: e.target.value })} /></Field>
          <Field label="WhatsApp"><Input inputMode="tel" placeholder="e.g. 2782…" value={t.form.whatsapp} onChange={(e) => t.setForm({ ...t.form, whatsapp: e.target.value })} /></Field>
          <Field label="Role">
            <Select value={t.form.role} onChange={(e) => t.setForm({ ...t.form, role: e.target.value })}>
              {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r] || r}</option>)}
            </Select>
          </Field>
          <div className="vtm-go"><Button Icon={Plus} onClick={t.add}>Add member</Button></div>
        </div>
      </Card>

      {t.members.length === 0 && (
        <EmptyState Icon={Users} title="No team members yet">Add one above. Until then, notifications go to the business's default number.</EmptyState>
      )}

      {t.members.map((m) => {
        const isOwnerOrManager = m.role === "owner" || m.role === "manager";
        const isFull = isOwnerOrManager || !(m.access || []).length;
        return (
          <Card key={m.id} className={`vtm-member${m.active ? "" : " vtm-member--off"}`}>
            <div className="vtm-member-head">
              <span className="vui-disc" aria-hidden="true">{(m.name || m.whatsapp || "?").charAt(0).toUpperCase()}</span>
              <span className="vtm-login-who">
                <span className="vtm-strong">{m.name || m.whatsapp}</span>
                <span className="vtm-sub">{m.whatsapp}</span>
              </span>
              {!m.active && <StatusChip>Disabled</StatusChip>}
              <Select value={m.role} onChange={(e) => t.patch(m.id, { role: e.target.value })} aria-label={`Role for ${m.name || m.whatsapp}`} className="vtm-role">
                {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r] || r}</option>)}
              </Select>
              <span className="vtm-actions">
                <Button size="sm" variant="secondary" onClick={() => t.patch(m.id, { active: !m.active })}>{m.active ? "Disable" : "Enable"}</Button>
                <Button size="sm" variant="danger" Icon={Trash2} onClick={() => t.del(m.id)}>Remove</Button>
              </span>
            </div>

            <h3 className="vtm-h3">Can access
              {isFull && <StatusChip tone="ok"><ShieldCheck size={13} aria-hidden="true" />Full access{isOwnerOrManager ? " (owner / manager)" : " — tick a module to restrict"}</StatusChip>}
            </h3>
            {groups.map((g) => (
              <div key={g.label} className="vtm-group" role="group" aria-label={`${g.label} access for ${m.name || m.whatsapp}`}>
                <span className="vtm-group-label">{g.label}</span>
                <span className="vtm-chips">
                  {g.items.map(([k, label]) => (
                    <Chip key={k} label={label} on={isFull || (m.access || []).includes(k)} onClick={() => t.patch(m.id, { access: toggle(m.access, k) })} />
                  ))}
                </span>
              </div>
            ))}

            <h3 className="vtm-h3">Notify by WhatsApp</h3>
            <div className="vtm-chips" role="group" aria-label={`Notifications for ${m.name || m.whatsapp}`}>
              {EVENTS.map(([k, label]) => <Chip key={k} label={label} on={(m.notify || []).includes(k)} onClick={() => t.patch(m.id, { notify: toggle(m.notify, k) })} />)}
            </div>
          </Card>
        );
      })}

      {/* ── Audit log (migration 107) ── */}
      <Card className="vtm-block">
        <details open={t.showAudit} onToggle={(e) => t.setShowAudit(e.currentTarget.open)}>
          <summary className="vtm-summary"><History size={18} aria-hidden="true" /><span className="vtm-strong">Activity log</span>
            <span className="vtm-sub">who changed team access, roles and logins</span></summary>
          {t.auditLog.length === 0
            ? <p className="vtm-sub">No activity recorded yet.</p>
            : <div className="vtm-audit">
                {t.auditLog.map((e) => (
                  <div key={e.id} className="vtm-audit-row">
                    <span className="vtm-sub">{new Date(e.created_at).toLocaleString("en-ZA")}</span>
                    <span>{e.actor_email || "unknown"} {AUDIT_LABELS[e.action] || e.action}
                      {e.detail && (e.detail.email || e.detail.name) ? <span className="vtm-sub"> — {e.detail.email || e.detail.name}</span> : null}</span>
                  </div>
                ))}
              </div>}
        </details>
      </Card>
    </div>
  );
}
