/**
 * VulaAgentActivity.jsx — a window into the AI agent: what it did (tools it called) and how it
 * thought (local vs cloud routing), plus a "teach it" box that stores a correction as KB the
 * assistants ground on. Backend: /admin/agent-activity + /admin/agent-teach (reuses telemetry 045).
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5", blue: "#2B5797" };

const when = (ts) => {
  if (!ts) return "";
  const d = new Date(ts), s = Math.floor((Date.now() - d) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return d.toLocaleDateString("en-ZA");
};

export default function VulaAgentActivity({ tenantId }) {
  const [events, setEvents] = useState(null);
  const [q, setQ] = useState("");
  const [a, setA] = useState("");
  const [msg, setMsg] = useState("");

  const [persona, setPersona] = useState("");
  const [suggested, setSuggested] = useState("");
  const [voiceMsg, setVoiceMsg] = useState("");
  const [analyzing, setAnalyzing] = useState(false);

  const load = useCallback(async () => {
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/agent-activity?limit=80`)
      .then(r => r.json()).catch(() => ({ events: [] }));
    setEvents(d.events || []);
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const loadPersona = useCallback(async () => {
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/persona`)
      .then(r => r.json()).catch(() => ({}));
    setPersona(d.persona_prompt || "");
    setSuggested(d.persona_prompt_suggested || "");
  }, [tenantId]);
  useEffect(() => { loadPersona(); }, [loadPersona]);

  const savePersona = async (text) => {
    setVoiceMsg("Saving…");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/persona`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona_prompt: text }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    if (r.error) setVoiceMsg(r.error);
    else { setPersona(text); setSuggested(""); setVoiceMsg("Saved ✓"); }
    setTimeout(() => setVoiceMsg(""), 4000);
  };

  const analyzeVoice = async () => {
    setAnalyzing(true);
    setVoiceMsg("Reading your recent replies…");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/persona/analyze`, { method: "POST" })
      .then(r => r.json()).catch(() => ({ error: "network" }));
    setAnalyzing(false);
    if (r.error) setVoiceMsg(r.error);
    else { setSuggested(r.suggested); setVoiceMsg(`Learned from ${r.sample_count} of your replies ✓`); }
  };

  const teach = async () => {
    if (!q.trim() || !a.trim()) { setMsg("Fill in both the question and the correct answer."); return; }
    setMsg("Teaching…");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/agent-teach`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, answer: a }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    if (r.error) setMsg(r.error);
    else { setMsg("Learned ✓ — the assistant will use this from now on."); setQ(""); setA(""); }
    setTimeout(() => setMsg(""), 4000);
  };

  return (
    <div style={{ fontFamily: "system-ui", color: C.text, maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <h4 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>🧠 Agent activity</h4>
        <button onClick={load} style={{ ...btn, marginLeft: "auto" }}>Refresh</button>
      </div>
      <p style={{ color: C.muted, fontSize: 13, marginTop: 0 }}>
        See what Vula's AI did — the tools it used and whether it ran on your local GPU or escalated to the cloud.
      </p>

      {/* Voice card */}
      <div style={{ ...card, background: C.alt, marginBottom: 16 }}>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>🗣️ How Vula sounds</div>
        <div style={{ fontSize: 12, color: C.muted, marginBottom: 8 }}>
          Set it yourself, or let Vula learn it from your own words — WhatsApp replies you've
          sent, meeting notes, and emails you've sent.
        </div>
        <textarea value={persona} onChange={e => setPersona(e.target.value)}
          placeholder="e.g. Warm, casual, quick replies — small family shop, not corporate."
          style={{ ...input, width: "100%", minHeight: 54, resize: "vertical", boxSizing: "border-box" }} />
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6, flexWrap: "wrap" }}>
          <button onClick={() => savePersona(persona)} style={{ ...btn, ...btnOn }}>Save</button>
          <button onClick={analyzeVoice} disabled={analyzing} style={btn}>
            {analyzing ? "Analyzing…" : "Analyze my voice"}
          </button>
          {voiceMsg && <span style={{ fontSize: 12, color: C.green }}>{voiceMsg}</span>}
        </div>
        {suggested && suggested !== persona && (
          <div style={{ ...card, marginTop: 10, background: C.surface }}>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 4 }}>Vula suggests:</div>
            <div style={{ fontSize: 13, marginBottom: 8 }}>{suggested}</div>
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={() => savePersona(suggested)} style={{ ...btn, ...btnOn }}>Accept</button>
              <button onClick={() => savePersona(persona)} style={btn}>Dismiss</button>
            </div>
          </div>
        )}
      </div>

      {/* Teach box */}
      <div style={{ ...card, background: C.alt, marginBottom: 16 }}>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>✏️ Teach the assistant</div>
        <div style={{ fontSize: 12, color: C.muted, marginBottom: 8 }}>
          Got a wrong or missing answer? Give the correct one — Vula stores it and uses it in future replies.
        </div>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Question / topic (e.g. 'Do you deliver to Stellenbosch?')" style={{ ...input, width: "100%", marginBottom: 6 }} />
        <textarea value={a} onChange={e => setA(e.target.value)} placeholder="The correct answer Vula should give" style={{ ...input, width: "100%", minHeight: 54, resize: "vertical", boxSizing: "border-box" }} />
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6 }}>
          <button onClick={teach} style={{ ...btn, ...btnOn }}>Teach it</button>
          {msg && <span style={{ fontSize: 12, color: C.green }}>{msg}</span>}
        </div>
      </div>

      {/* Timeline */}
      <div style={{ fontSize: 11, textTransform: "uppercase", color: C.muted, marginBottom: 8 }}>Recent activity</div>
      {events === null ? <div style={{ color: C.muted, fontSize: 13 }}>Loading…</div>
        : events.length === 0 ? <div style={{ color: C.muted, fontSize: 13 }}>No activity yet. It appears here as customers and you chat with the assistant.</div>
          : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {events.map((e, i) => (
                <div key={i} style={{ ...card, display: "flex", alignItems: "center", gap: 10, padding: "9px 12px" }}>
                  {e.kind === "tool" ? (
                    <>
                      <span style={{ fontSize: 15 }} title={e.consequential ? "Consequential action (money, stock, or a customer-facing change)" : undefined}>
                        {e.consequential ? "⚠️" : "🔧"}
                      </span>
                      <span style={{ flex: 1, fontSize: 13 }}>
                        {e.narrated ? (
                          <>
                            {e.narrated}
                            <span style={{ color: C.muted }}> · {e.who === "admin" ? "you (owner)" : "customer"}</span>
                          </>
                        ) : (
                          <>
                            <b>{e.tool}</b>
                            <span style={{ color: C.muted }}> · {e.who === "admin" ? "you (owner)" : "customer"}</span>
                            {e.args && Object.keys(e.args).length > 0 &&
                              <span style={{ color: C.muted, fontSize: 12 }}> · {Object.entries(e.args).map(([k, v]) => `${k}: ${v}`).join(", ").slice(0, 80)}</span>}
                          </>
                        )}
                      </span>
                    </>
                  ) : (
                    <>
                      <span style={{ fontSize: 15 }}>{e.escalated ? "☁️" : "⚡"}</span>
                      <span style={{ flex: 1, fontSize: 13 }}>
                        <b>{e.escalated ? "Cloud" : "Local GPU"}</b>
                        <span style={{ color: C.muted }}> · {e.task}{e.reason ? ` · ${e.reason}` : ""}</span>
                      </span>
                    </>
                  )}
                  <span style={{ fontSize: 11, color: C.muted, flexShrink: 0 }}>{when(e.at)}</span>
                </div>
              ))}
            </div>
          )}
    </div>
  );
}

const btn = { padding: "6px 12px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const btnOn = { background: C.green, color: "#fff", borderColor: C.green };
const input = { padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text, fontFamily: "system-ui" };
const card = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: "12px 14px" };
