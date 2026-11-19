/**
 * AccountantCard.jsx — Team › People › Accountant (19 Nov 2026, Phase 3 of Xero-quality books).
 * The owner or a manager gives the business's accountant a view-only login: they see the books,
 * reports, bank lines, invoices and the year's documents, change nothing, take no paid seat and
 * must use two-step sign-in. Access can end on a date and be removed any time. Every view, open and
 * download is listed here (vula_mind/vula/accountant_activity.py).
 */
import { useCallback, useEffect, useState } from "react";
import { BookOpenCheck, History, UserMinus } from "lucide-react";
import { VULA_API } from "../../lib/authFetch";
import { Button, Card, Field, Input, StatusChip, confirmDialog, toast } from "../ui/index.jsx";

const users = (t) => `${VULA_API}/v1/users/${t}/accountants`;
const when = (v) => (v ? new Date(v).toLocaleString("en-ZA", { dateStyle: "medium", timeStyle: "short" }) : "—");
const ACTION = { accountant_viewed: "looked at", accountant_opened: "opened", accountant_downloaded: "downloaded" };

export function useAccountants(tenantId) {
  const [list, setList] = useState([]);
  const [events, setEvents] = useState([]);
  const [form, setForm] = useState({ email: "", name: "", expires_on: "" });
  const [issued, setIssued] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    if (!tenantId) return;
    const [a, e] = await Promise.all([
      fetch(users(tenantId)).then((r) => r.json()).catch(() => ({})),
      fetch(`${users(tenantId)}/activity?limit=50`).then((r) => r.json()).catch(() => ({})),
    ]);
    setList(a.accountants || []);
    setEvents(e.events || []);
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);
  const invite = async () => {
    if (!form.email.trim()) return;
    setBusy(true); setIssued("");
    const body = { email: form.email.trim(), name: form.name.trim() || undefined, expires_on: form.expires_on || undefined };
    const d = await fetch(users(tenantId), { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) }).then((r) => r.json()).catch(() => ({ error: "Couldn't reach Vula. Try again." }));
    setBusy(false);
    if (d.error || d.detail) { toast(String(d.error || d.detail), "danger"); return; }
    setIssued(d.temp_password ? `${d.email} — one-time password: ${d.temp_password}. ${d.note}` : `${d.email} added. ${d.note}`);
    setForm({ email: "", name: "", expires_on: "" });
    load();
  };
  const revoke = async (a) => {
    if (!(await confirmDialog(`Remove ${a.name || a.email}'s access to the books?`, { confirmLabel: "Remove access" }))) return;
    const r = await fetch(`${users(tenantId)}/${a.id}`, { method: "DELETE" }).catch(() => null);
    if (!r || !r.ok) { toast("Couldn't remove the access. Try again.", "danger"); return; }
    toast(`${a.name || a.email} can no longer open the books.`);
    load();
  };
  return { list, events, form, setForm, issued, busy, invite, revoke };
}

export default function AccountantCard({ tenantId }) {
  const x = useAccountants(tenantId);
  const active = x.list.filter((a) => a.active !== false);
  return (
    <Card className="vtm-block">
      <h2 className="vtm-h"><BookOpenCheck size={18} aria-hidden="true" />Accountant</h2>
      <p className="vtm-sub">Give your accountant a view-only login: they see the books, reports, bank lines, invoices and the year's
        documents, and can download them — they can't change anything. It doesn't use one of your team seats. They'll set up
        two-step sign-in, and everything they open is listed below.</p>
      <div className="vtm-form">
        <Field label="Accountant's email"><Input type="email" value={x.form.email} placeholder="accounts@firm.co.za"
          onChange={(e) => x.setForm({ ...x.form, email: e.target.value })} /></Field>
        <Field label="Name (optional)"><Input value={x.form.name} onChange={(e) => x.setForm({ ...x.form, name: e.target.value })} /></Field>
        <Field label="Access ends (optional)"><Input type="date" value={x.form.expires_on}
          onChange={(e) => x.setForm({ ...x.form, expires_on: e.target.value })} /></Field>
        <Button onClick={x.invite} disabled={x.busy || !x.form.email.trim()}>{x.busy ? "Inviting…" : "Invite the accountant"}</Button>
      </div>
      {x.issued && <p className="vtm-msg vtm-msg--ok" role="status">{x.issued}</p>}
      {active.length > 0 && (
        <ul className="vtm-acc-list">
          {active.map((a) => (
            <li key={a.id} className="vtm-acc">
              <span className="vtm-acc-who"><span className="vtm-strong">{a.name || a.email}</span>
                <span className="vtm-sub"> · {a.email}{a.expires_on ? ` · until ${a.expires_on}` : ""}</span>
                <span className="vtm-sub"> · last opened the books {a.last_seen ? when(a.last_seen) : "never"}</span></span>
              <StatusChip tone="info">View only</StatusChip>
              <Button size="sm" variant="secondary" Icon={UserMinus} onClick={() => x.revoke(a)}
                aria-label={`Remove ${a.name || a.email}'s access`}>Remove access</Button>
            </li>))}
        </ul>)}
      <details className="vtm-acc-activity">
        <summary className="vtm-summary"><History size={18} aria-hidden="true" /><span className="vtm-strong">Accountant activity</span>
          <span className="vtm-sub">what your accountant looked at, opened and downloaded</span></summary>
        {x.events.length === 0
          ? <p className="vtm-sub">Nothing yet.</p>
          : <div className="vtm-audit">
              {x.events.map((e) => (
                <div key={e.id} className="vtm-audit-row">
                  <span className="vtm-sub">{when(e.created_at)}</span>
                  <span>{e.actor_email || "Accountant"} {ACTION[e.action] || e.action} {e.detail?.what || e.detail?.page}
                    {e.detail?.document ? <span className="vtm-sub"> — {e.detail.document}</span> : null}</span>
                </div>))}
            </div>}
      </details>
    </Card>
  );
}
