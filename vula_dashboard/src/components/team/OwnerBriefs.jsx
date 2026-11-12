/**
 * OwnerBriefs.jsx — Team › Notices: the owner's own WhatsApp briefs (11–12 Nov 2026).
 *
 * Off unless the owner asks for them (here, in onboarding, or on WhatsApp: "send me a morning
 * brief at 7 on weekdays"), and only at the time and on the days the owner picks.
 * Morning brief: what needs a person today — customers waiting, follow-ups, emails, quotes Vula
 * drafted, documents to check, today's bookings — plus money paid, owed and due.
 * Weekly advisor: money owed, unusual spending, low stock, unanswered questions.
 * "See today's" shows exactly what would be sent. Owners and managers only.
 * Backend: vula/api/owner_briefs.py, vula/owner_briefs.py.
 */
import { useCallback, useEffect, useState } from "react";
import { Eye, Sunrise } from "lucide-react";
import { VULA_API } from "../../lib/authFetch";
import { Button, Card, Field, Input, Select, toast } from "../ui/index.jsx";

async function call(path, opts = {}) {
  const r = await fetch(`${VULA_API}${path}`, { headers: { "Content-Type": "application/json" }, ...opts });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || d.error || `Request failed (${r.status})`);
  return d;
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const BRIEFS = [
  { id: "morning", label: "Morning brief",
    help: "What needs you today (waiting customers, follow-ups, emails, quotes to approve, documents, bookings) and your money: paid yesterday, overdue, due this week." },
  { id: "weekly", label: "Weekly advisor",
    help: "Money owed, spending that's higher than usual, low stock and questions Vula couldn't answer." },
];
const pad = (n) => String(n ?? 0).padStart(2, "0");

export default function OwnerBriefs({ tenantId }) {
  const [settings, setSettings] = useState(null);
  const [preview, setPreview] = useState({});
  const base = `/v1/team/${tenantId}/briefs`;

  const load = useCallback(async () => {
    if (!tenantId) return;
    try { setSettings((await call(base)).settings || null); }
    catch { setSettings(null); }        // migration not applied yet → the card stays hidden
  }, [tenantId, base]);
  useEffect(() => { load(); }, [load]);

  const save = async (kind, patch) => {
    try { setSettings((await call(base, { method: "PUT", body: JSON.stringify({ [kind]: patch }) })).settings); }
    catch (e) { toast(e.message, "danger"); }
  };
  const setTime = (kind, value) => {
    const [h, m] = (value || "").split(":").map(Number);
    if (Number.isInteger(h) && Number.isInteger(m)) save(kind, { hour: h, minute: m });
  };
  const toggleDay = (cfg, d) => {
    const days = cfg.days.includes(d) ? cfg.days.filter((x) => x !== d) : [...cfg.days, d].sort();
    if (days.length) save("morning", { days });
  };
  const show = async (kind) => {
    try {
      const d = await call(`${base}/preview?kind=${kind}`);
      setPreview({ ...preview, [kind]: d.empty ? "Nothing to send today, so nothing would go out." : d.text });
    } catch (e) { toast(e.message, "danger"); }
  };

  if (!settings) return null;
  return (
    <Card className="vtm-block">
      <h2 className="vtm-h"><Sunrise size={18} aria-hidden="true" />Your briefs on WhatsApp</h2>
      <p className="vtm-sub">Off unless you switch them on, and sent only at the time you pick. You can also just tell Vula on WhatsApp, e.g. "send me a morning brief at 7 on weekdays" or "stop my morning brief". Nothing is sent on a day with nothing to say.</p>
      {BRIEFS.map((b) => {
        const cfg = settings[b.id] || {};
        return (
          <div key={b.id}>
            <label className="vtm-check">
              <input type="checkbox" checked={!!cfg.on} onChange={(e) => save(b.id, { on: e.target.checked })} />
              <span>{b.label}{cfg.on ? ` — ${cfg.when}` : ""}<br /><span className="vtm-sub">{b.help}</span></span>
            </label>
            {cfg.on && (
              <div className="vtm-form">
                <Field label="Time">
                  <Input type="time" min="05:00" max="21:59" defaultValue={`${pad(cfg.hour)}:${pad(cfg.minute)}`}
                    onBlur={(e) => setTime(b.id, e.target.value)} />
                </Field>
                {b.id === "weekly" ? (
                  <Field label="Day">
                    <Select value={cfg.day} onChange={(e) => save("weekly", { day: Number(e.target.value) })}>
                      {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
                    </Select>
                  </Field>
                ) : (
                  <Field label="Days">
                    <div className="vtm-brief-days">
                      {DAYS.map((d, i) => (
                        <label key={d} className="vtm-check"><input type="checkbox" checked={(cfg.days || []).includes(i)} onChange={() => toggleDay(cfg, i)} />{d}</label>
                      ))}
                    </div>
                  </Field>
                )}
              </div>
            )}
            <Button size="sm" variant="secondary" Icon={Eye} onClick={() => show(b.id)}>See today's</Button>
            {preview[b.id] && <pre className="vtm-brief-preview">{preview[b.id]}</pre>}
          </div>
        );
      })}
    </Card>
  );
}
