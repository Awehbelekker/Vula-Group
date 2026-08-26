/**
 * VulaRepCallSheet.jsx — view/edit/configure the rep's persistent weekly call sheet
 * (vula/commerce/call_sheet.py). Thin wrapper — reuses the same compose/edit logic the
 * WhatsApp configure_call_sheet/view_call_sheet/update_call_sheet tools already use.
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", text: "#2A2A2A", muted: "#8A8680" };
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export default function VulaRepCallSheet({ tenantId, repPhone }) {
  const [config, setConfig] = useState({});
  const [entries, setEntries] = useState([]);
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState(null);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    if (!tenantId || !repPhone) return;
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/call-sheet?rep_phone=${encodeURIComponent(repPhone)}`);
    const d = await r.json().catch(() => ({}));
    setConfig(d.config || {});
    setEntries(d.entries || []);
  }, [tenantId, repPhone]);
  useEffect(() => { load(); }, [load]);

  const saveConfig = async (patch) => {
    setMsg("");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/call-sheet/configure`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rep_phone: repPhone, ...patch }),
    });
    const d = await r.json().catch(() => ({}));
    if (d.saved) load(); else setMsg(d.detail || "Could not save.");
  };

  const submitInstruction = async (confirm) => {
    setMsg("");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/call-sheet/update`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rep_phone: repPhone, instruction, confirm }),
    });
    const d = await r.json().catch(() => ({}));
    if (d.error) { setMsg(d.error); setPreview(null); return; }
    if (d.preview) { setPreview(d); return; }
    if (d.applied) { setPreview(null); setInstruction(""); load(); }
  };

  if (!repPhone) return <div style={{ padding: 20, color: C.muted, fontSize: 13 }}>Couldn't identify your rep phone number.</div>;

  return (
    <div style={{ maxWidth: 700, margin: "0 auto", padding: 20 }}>
      <h2 style={{ fontSize: 20, fontWeight: 700, color: C.text, margin: "0 0 4px" }}>My Call Sheet</h2>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 16px" }}>
        Every meeting you log via WhatsApp lands here automatically. Correct or add to it below,
        then it's emailed on your schedule.
      </p>

      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, marginBottom: 10 }}>Schedule</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input placeholder="Recipient email" defaultValue={config.call_sheet_recipient_email || ""}
                 onBlur={(e) => saveConfig({ recipient_email: e.target.value })}
                 style={{ padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, minWidth: 220 }} />
          <select defaultValue={config.call_sheet_day_of_week ?? 4}
                  onChange={(e) => saveConfig({ day_of_week: Number(e.target.value) })}
                  style={{ padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }}>
            {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
          </select>
          <input type="time" defaultValue={`${String(config.call_sheet_hour ?? 17).padStart(2, "0")}:${String(config.call_sheet_minute ?? 0).padStart(2, "0")}`}
                 onBlur={(e) => { const [h, m] = e.target.value.split(":"); saveConfig({ hour: Number(h), minute: Number(m) }); }}
                 style={{ padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }} />
        </div>
        {config.call_sheet_last_sent_at && (
          <div style={{ fontSize: 11.5, color: C.muted, marginTop: 8 }}>
            Last sent {new Date(config.call_sheet_last_sent_at).toLocaleString()}
          </div>
        )}
      </div>

      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Fix or add something</div>
        <input value={instruction} onChange={(e) => setInstruction(e.target.value)}
               placeholder='e.g. "the Dick meeting was about self-levelling, not HBC"'
               style={{ width: "100%", padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }} />
        {msg && <div style={{ fontSize: 12.5, color: "#A23B2D", marginTop: 8 }}>{msg}</div>}
        {preview && (
          <div style={{ background: "#FBF7E9", border: "1px solid #E6D9A8", borderRadius: 8, padding: "8px 12px", fontSize: 12.5, marginTop: 8 }}>
            {preview.change}
            <button onClick={() => submitInstruction(true)} style={{ marginLeft: 10, padding: "4px 10px", background: C.green,
              color: "#fff", border: "none", borderRadius: 6, fontSize: 12, cursor: "pointer" }}>Confirm</button>
          </div>
        )}
        {!preview && (
          <button onClick={() => submitInstruction(false)} disabled={!instruction.trim()}
            style={{ marginTop: 8, padding: "7px 14px", background: C.text, color: "#fff", border: "none",
              borderRadius: 8, fontSize: 12.5, cursor: instruction.trim() ? "pointer" : "not-allowed" }}>Preview change</button>
        )}
      </div>

      <div style={{ fontWeight: 700, marginBottom: 8 }}>This week so far ({entries.length})</div>
      {entries.length === 0 && <div style={{ padding: 16, fontSize: 13, color: C.muted, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10 }}>Nothing logged yet.</div>}
      {entries.map((e) => (
        <div key={e.id} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: "10px 14px", marginBottom: 6 }}>
          <div style={{ fontSize: 13, color: C.text }}>{e.text}</div>
          <div style={{ fontSize: 11, color: C.muted }}>{e.source === "manual" ? "Added manually" : "From a logged meeting"} · {new Date(e.created_at).toLocaleDateString()}</div>
        </div>
      ))}
    </div>
  );
}
