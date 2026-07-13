/**
 * VulaMarketing.jsx — AI marketing copy generator.
 * "Write today's specials post / a product description / promo copy / a broadcast blast."
 * Grounded in the tenant's real catalogue. Backend: /v1/commerce/{tenant}/admin/marketing/generate
 */
import { useState } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };

const KINDS = [
  { id: "specials", label: "📣 Today's specials", hint: "A fresh-today post for WhatsApp status or social." },
  { id: "product",  label: "🏷️ Product description", hint: "Describe one product — type its name below." },
  { id: "promo",    label: "🎉 Promo copy", hint: "Copy for an offer — describe it below." },
  { id: "broadcast",label: "📢 Broadcast blast", hint: "A WhatsApp message to existing customers." },
];
const TONES = ["warm & friendly", "playful", "premium", "urgent / limited-time", "professional"];

export default function VulaMarketing({ tenantId }) {
  const [kind, setKind] = useState("specials");
  const [topic, setTopic] = useState("");
  const [tone, setTone] = useState("warm & friendly");
  const [variants, setVariants] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(-1);

  const active = KINDS.find(k => k.id === kind);
  const needsTopic = kind === "product" || kind === "promo";

  const generate = async () => {
    setErr(""); setVariants([]); setLoading(true);
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/marketing/generate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, topic, tone, variants: 2 }),
      });
      const d = await r.json();
      if (d.error) setErr(d.error);
      else setVariants(d.variants || []);
    } catch (e) { setErr(String(e.message || e)); }
    setLoading(false);
  };

  const copy = (text, i) => {
    navigator.clipboard?.writeText(text);
    setCopied(i); setTimeout(() => setCopied(-1), 1500);
  };

  return (
    <div style={{ fontFamily: "system-ui", color: C.text, maxWidth: 720 }}>
      <h4 style={{ fontSize: 15, fontWeight: 600, margin: "4px 0 4px" }}>✨ Marketing copy</h4>
      <p style={{ color: C.muted, fontSize: 13, marginTop: 0 }}>
        Vula writes copy from your live products & prices — nothing invented. Pick a type, generate, tweak, use.
      </p>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "14px 0" }}>
        {KINDS.map(k => (
          <button key={k.id} onClick={() => setKind(k.id)}
            style={{ ...chip, ...(kind === k.id ? chipOn : {}) }}>{k.label}</button>
        ))}
      </div>
      <div style={{ color: C.muted, fontSize: 12, marginBottom: 10 }}>{active?.hint}</div>

      {needsTopic && (
        <input
          placeholder={kind === "product" ? "Product name (e.g. Yellowtail)" : "The offer (e.g. 20% off crayfish this weekend)"}
          value={topic} onChange={e => setTopic(e.target.value)} style={{ ...input, width: "100%", marginBottom: 10 }} />
      )}

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, color: C.muted }}>Tone</span>
        <select value={tone} onChange={e => setTone(e.target.value)} style={input}>
          {TONES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <button onClick={generate} disabled={loading || (needsTopic && !topic.trim())}
          style={{ ...btn, ...btnOn, opacity: loading || (needsTopic && !topic.trim()) ? 0.6 : 1 }}>
          {loading ? "Writing…" : "Generate"}
        </button>
      </div>

      {err && <div style={{ color: "#C0392B", fontSize: 13, marginTop: 12 }}>{err}</div>}

      <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 12 }}>
        {variants.map((v, i) => (
          <div key={i} style={card}>
            <div style={{ whiteSpace: "pre-wrap", fontSize: 14, lineHeight: 1.5 }}>{v}</div>
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button style={miniBtn} onClick={() => copy(v, i)}>{copied === i ? "Copied ✓" : "Copy"}</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const chip = { padding: "8px 14px", border: `1px solid ${C.border}`, borderRadius: 20, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const chipOn = { background: C.green, color: "#fff", borderColor: C.green };
const btn = { padding: "8px 16px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "#fff", borderColor: C.green };
const miniBtn = { padding: "5px 12px", border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const input = { padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text };
const card = { padding: "14px 16px", border: `1px solid ${C.border}`, borderRadius: 8, background: C.surface };
