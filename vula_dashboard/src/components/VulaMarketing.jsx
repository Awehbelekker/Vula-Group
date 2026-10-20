/**
 * VulaMarketing.jsx — AI marketing copy generator.
 * "Write today's specials post / a product description / promo copy / a broadcast blast."
 * Grounded in the tenant's real catalogue. Backend: /v1/commerce/{tenant}/admin/marketing/generate
 */
import { KINDS, TONES, useMarketingCopy } from "./marketing/useMarketingCopy";
import MarketingCopyV2 from "./marketing/MarketingCopyV2";

const C = { surface: "var(--surface)", border: "var(--border)", green: "var(--accent)", text: "var(--text)", muted: "var(--muted)", alt: "var(--surface-alt)" };

export default function VulaMarketing({ v2 = false, ...props }) {
  return v2 ? <MarketingCopyV2 {...props} /> : <MarketingV1 {...props} />;
}

function MarketingV1({ tenantId, onSendAsBroadcast }) {
  const { kind, setKind, topic, setTopic, tone, setTone, variants, loading, err, copied, saved, savedIdx, active, needsTopic, generate, copy, saveVariant, deleteSaved } = useMarketingCopy(tenantId);

  return (
    <div style={{ color: C.text, maxWidth: 720 }}>
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

      {err && <div style={{ color: "var(--danger)", fontSize: 13, marginTop: 12 }}>{err}</div>}

      <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 12 }}>
        {variants.map((v, i) => (
          <div key={i} style={card}>
            <div style={{ whiteSpace: "pre-wrap", fontSize: 14, lineHeight: 1.5 }}>{v}</div>
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button style={miniBtn} onClick={() => copy(v, i)}>{copied === i ? "Copied ✓" : "Copy"}</button>
              <button style={miniBtn} onClick={() => saveVariant(v, i)}>{savedIdx === i ? "Saved ✓" : "💾 Save"}</button>
              {onSendAsBroadcast && (
                <button style={{ ...miniBtn, ...btnOn }} onClick={() => onSendAsBroadcast(v)}>📢 Send as broadcast</button>
              )}
            </div>
          </div>
        ))}
      </div>

      {saved.length > 0 && (
        <div style={{ marginTop: 28 }}>
          <h4 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 10px" }}>📚 Saved copy</h4>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {saved.map(s => (
              <div key={s.id} style={{ ...card, background: C.alt }}>
                <div style={{ fontSize: 11, color: C.muted, marginBottom: 6, textTransform: "uppercase" }}>
                  {KINDS.find(k => k.id === s.kind)?.label || s.kind}{s.topic ? ` · ${s.topic}` : ""}
                </div>
                <div style={{ whiteSpace: "pre-wrap", fontSize: 13.5, lineHeight: 1.5 }}>{s.body}</div>
                <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                  <button style={miniBtn} onClick={() => copy(s.body, `s${s.id}`)}>{copied === `s${s.id}` ? "Copied ✓" : "Copy"}</button>
                  {onSendAsBroadcast && (
                    <button style={{ ...miniBtn, ...btnOn }} onClick={() => onSendAsBroadcast(s.body)}>📢 Send as broadcast</button>
                  )}
                  <button style={{ ...miniBtn, marginLeft: "auto", color: "var(--danger)" }} onClick={() => deleteSaved(s.id)}>Delete</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const chip = { padding: "8px 14px", border: `1px solid ${C.border}`, borderRadius: 20, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const chipOn = { background: C.green, color: "var(--on-accent)", borderColor: C.green };
const btn = { padding: "8px 16px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "var(--on-accent)", borderColor: C.green };
const miniBtn = { padding: "5px 12px", border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const input = { padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text };
const card = { padding: "14px 16px", border: `1px solid ${C.border}`, borderRadius: 8, background: C.surface };
