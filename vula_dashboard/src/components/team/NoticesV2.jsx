/**
 * NoticesV2.jsx — Team › Notices (2026-10-20, Ian: "broadcasting to the staff, like for birthdays,
 * public holidays, any notifications that they need to know about, maybe a newsletter").
 *
 * The owner or a manager writes a notice (optionally with a picture, optionally "must read"),
 * sees exactly who it reaches and how it reads, sends a test to themselves, then sends it now or
 * at a set time. Each notice shows delivered / read / "Got it", and who hasn't confirmed yet.
 * Birthdays and public holidays are switched on here. Anyone else on the team sees the notices
 * sent to them, with a Got it button. Backend: vula/api/staff_notices.py.
 */
import { useCallback, useEffect, useState } from "react";
import { BellRing, CalendarClock, Megaphone, Send, TestTube2, XCircle } from "lucide-react";
import { VULA_API } from "../../lib/authFetch";
import { Button, Card, EmptyState, Field, Input, Select, StatusChip, Textarea, toast } from "../ui/index.jsx";
import { ROLE_LABELS, ROLES } from "./useTeam";

const KIND_LABEL = { notice: "Notice", newsletter: "Newsletter", birthday: "Birthday", holiday: "Public holiday" };
const STATUS_TONE = { sent: "ok", scheduled: "info", sending: "info", draft: "muted", cancelled: "muted" };
const EMPTY = { title: "", body: "", image_url: "", must_read: false, who: "all", roles: [], send_at: "" };

async function call(path, opts = {}) {
  const r = await fetch(`${VULA_API}${path}`, { headers: { "Content-Type": "application/json" }, ...opts });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || d.error || `Request failed (${r.status})`);
  return d;
}

export default function NoticesV2({ tenantId }) {
  const [data, setData] = useState({ notices: [], full: false });
  const [settings, setSettings] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const base = `/v1/team/${tenantId}/notices`;

  const load = useCallback(async () => {
    if (!tenantId) return;
    try {
      setData(await call(base));
      const s = await call(`${base}/settings`);
      setSettings(s.settings);
    } catch (e) { toast(e.message, "danger"); }
  }, [tenantId, base]);
  useEffect(() => { load(); }, [load]);

  const audience = form.who === "roles" && form.roles.length ? { roles: form.roles } : { all: true };
  const body = () => ({ title: form.title, body: form.body, image_url: form.image_url || null,
    must_read: form.must_read, audience });

  const doPreview = async () => {
    try { setPreview(await call(`${base}/preview`, { method: "POST", body: JSON.stringify(body()) })); }
    catch (e) { toast(e.message, "danger"); }
  };
  const save = async (mode) => {
    if (!form.title.trim() || !form.body.trim()) { toast("Give the notice a title and a message.", "danger"); return; }
    if (mode === "schedule" && !form.send_at) { toast("Pick when it should go out.", "danger"); return; }
    setBusy(true);
    try {
      const d = await call(base, { method: "POST", body: JSON.stringify({ ...body(), send_now: mode === "now",
        send_at: mode === "schedule" ? new Date(form.send_at).toISOString() : null }) });
      if (mode === "test") {
        const t = await call(`${base}/${d.notice.id}/test`, { method: "POST" });
        toast(t.error ? `Test not sent: ${t.error}` : `Test sent to ${t.sent_to} — it's saved as a draft.`, t.error ? "danger" : "ok");
      } else if (mode === "now") {
        const r = d.result || {};
        toast(r.status === "sending" ? `Sending to ${r.total} people…` : `Sent to ${r.sent || 0} of ${r.total || 0}.`
          + (r.failed ? ` ${r.failed} couldn't be reached.` : ""), r.failed ? "warn" : "ok");
        setForm(EMPTY); setPreview(null);
      } else {
        toast(`Scheduled for ${new Date(form.send_at).toLocaleString("en-ZA")}.`);
        setForm(EMPTY); setPreview(null);
      }
      load();
    } catch (e) { toast(e.message, "danger"); }
    setBusy(false);
  };
  const act = async (id, what) => {
    try {
      const d = await call(`${base}/${id}/${what}`, { method: "POST" });
      if (what === "send") toast(d.error || (d.status === "sending" ? "Sending…" : `Sent to ${d.sent || 0} of ${d.total || 0}.`), d.error ? "danger" : "ok");
      load();
    } catch (e) { toast(e.message, "danger"); }
  };
  const saveSettings = async (patch) => {
    try { setSettings((await call(`${base}/settings`, { method: "PUT", body: JSON.stringify(patch) })).settings); }
    catch (e) { toast(e.message, "danger"); }
  };

  if (!data.full) {
    // A team member's own noticeboard.
    return (
      <div>
        {data.notices.length === 0 && <EmptyState Icon={Megaphone} title="No notices yet">Notices from the business show here and on your WhatsApp.</EmptyState>}
        {data.notices.map((n) => (
          <Card key={n.id} className="vtm-block">
            <h2 className="vtm-h"><Megaphone size={18} aria-hidden="true" />{n.title}</h2>
            <p className="vtm-sub">{n.sent_at ? new Date(n.sent_at).toLocaleString("en-ZA") : ""}</p>
            {n.image_url && <img src={n.image_url} alt="" className="vtm-notice-img" />}
            <p className="vtm-notice-body">{n.body}</p>
            {n.must_read && (n.acknowledged_at
              ? <StatusChip tone="ok">You confirmed {new Date(n.acknowledged_at).toLocaleDateString("en-ZA")}</StatusChip>
              : <Button size="sm" onClick={() => act(n.id, "ack")}>Got it ✅</Button>)}
          </Card>
        ))}
      </div>
    );
  }

  return (
    <div>
      <Card className="vtm-block">
        <h2 className="vtm-h"><Megaphone size={18} aria-hidden="true" />New notice</h2>
        <p className="vtm-sub">Goes to your team on WhatsApp. People who haven't messaged in the last day get it as your approved staff notice template (Settings › WhatsApp templates › Submit standard templates).</p>
        <div className="vtm-form">
          <Field label="Title"><Input value={form.title} maxLength={120} placeholder="Office closed Friday" onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          <Field label="Who gets it">
            <Select value={form.who} onChange={(e) => setForm({ ...form, who: e.target.value })}>
              <option value="all">Everyone on the team</option>
              <option value="roles">Only some roles…</option>
            </Select>
          </Field>
          <Field label="Picture link (optional)" hint="A poster or this month's newsletter page"><Input type="url" value={form.image_url} placeholder="https://…" onChange={(e) => setForm({ ...form, image_url: e.target.value })} /></Field>
        </div>
        {form.who === "roles" && (
          <div className="vtm-chips" role="group" aria-label="Roles">
            {ROLES.map((r) => (
              <button key={r} type="button" className="vui-chip vtm-chip" aria-pressed={form.roles.includes(r)}
                onClick={() => setForm({ ...form, roles: form.roles.includes(r) ? form.roles.filter((x) => x !== r) : [...form.roles, r] })}>
                {ROLE_LABELS[r] || r}
              </button>
            ))}
          </div>
        )}
        <Field label="Message"><Textarea value={form.body} maxLength={900} placeholder="The office is closed on Friday for the year-end function. Site teams carry on as normal." onChange={(e) => setForm({ ...form, body: e.target.value })} /></Field>
        <label className="vtm-check"><input type="checkbox" checked={form.must_read} onChange={(e) => setForm({ ...form, must_read: e.target.checked })} />
          Must read — everyone taps <b>Got it</b>; I remind anyone who hasn't after a day and tell you who.</label>
        <div className="vtm-notice-actions">
          <Button variant="secondary" onClick={doPreview}>Preview</Button>
          <Button variant="secondary" Icon={TestTube2} disabled={busy} onClick={() => save("test")}>Send test to me</Button>
          <Button Icon={Send} disabled={busy} onClick={() => save("now")}>Send now</Button>
          <span className="vtm-schedule">
            <Input type="datetime-local" value={form.send_at} onChange={(e) => setForm({ ...form, send_at: e.target.value })} aria-label="Send at" />
            <Button variant="secondary" Icon={CalendarClock} disabled={busy} onClick={() => save("schedule")}>Schedule</Button>
          </span>
        </div>
        {preview && (
          <div className="vtm-preview" role="status">
            <p className="vtm-strong">{preview.count} {preview.count === 1 ? "person" : "people"}: {preview.names.join(", ")}</p>
            <pre className="vtm-preview-text">{preview.text}</pre>
            <p className="vtm-sub">{preview.note}</p>
          </div>
        )}
      </Card>

      {settings && (
        <Card className="vtm-block">
          <h2 className="vtm-h"><BellRing size={18} aria-hidden="true" />Birthdays and public holidays</h2>
          <p className="vtm-sub">Add birthdays (day and month only) on each person's card under People. Anyone can reply STOP to skip these; work notices still reach them.</p>
          <label className="vtm-check"><input type="checkbox" checked={!!settings.birthdays} onChange={(e) => saveSettings({ birthdays: e.target.checked })} />
            Birthdays — a wish to the person, and a note to the team (unless they'd rather not)</label>
          <label className="vtm-check"><input type="checkbox" checked={!!settings.holidays} onChange={(e) => saveSettings({ holidays: e.target.checked })} />
            Public holidays — a reminder the day before each South African public holiday</label>
          <div className="vtm-form">
            <Field label="Send at">
              <Select value={settings.send_hour} onChange={(e) => saveSettings({ send_hour: Number(e.target.value) })}>
                {[6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17].map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
              </Select>
            </Field>
            <Field label="Add to holiday reminders (optional)">
              <Input defaultValue={settings.holiday_note} placeholder="The office is closed." onBlur={(e) => e.target.value !== settings.holiday_note && saveSettings({ holiday_note: e.target.value })} />
            </Field>
          </div>
        </Card>
      )}

      {data.notices.length === 0 && <EmptyState Icon={Megaphone} title="No notices sent yet">Your first notice will show here with who has read it.</EmptyState>}
      {data.notices.map((n) => {
        const c = n.counts || {};
        return (
          <Card key={n.id} className="vtm-block">
            <div className="vtm-member-head">
              <span className="vtm-login-who">
                <span className="vtm-strong">{n.title}</span>
                <span className="vtm-sub">{KIND_LABEL[n.kind] || n.kind} · {n.sent_at ? `sent ${new Date(n.sent_at).toLocaleString("en-ZA")}`
                  : n.send_at ? `goes out ${new Date(n.send_at).toLocaleString("en-ZA")}` : new Date(n.created_at).toLocaleString("en-ZA")}</span>
              </span>
              <StatusChip tone={STATUS_TONE[n.status] || "muted"}>{n.status}</StatusChip>
              {n.must_read && <StatusChip tone="warn">Must read</StatusChip>}
              <span className="vtm-actions">
                {(n.status === "draft" || n.status === "scheduled") && <Button size="sm" Icon={Send} onClick={() => act(n.id, "send")}>Send now</Button>}
                {(n.status === "draft" || n.status === "scheduled") && <Button size="sm" variant="secondary" Icon={XCircle} onClick={() => act(n.id, "cancel")}>Cancel</Button>}
              </span>
            </div>
            <p className="vtm-notice-body">{n.body}</p>
            {n.status === "sent" && (
              <p className="vtm-sub">
                Sent to {c.sent || 0}{c.failed ? ` (${c.failed} couldn't be reached)` : ""} · delivered {c.delivered || 0} · read {c.read || 0}
                {n.must_read ? ` · confirmed ${c.acknowledged || 0}` : ""}
                {n.must_read && (c.not_acknowledged || []).length ? ` — not yet: ${c.not_acknowledged.join(", ")}` : ""}
              </p>
            )}
          </Card>
        );
      })}
    </div>
  );
}
