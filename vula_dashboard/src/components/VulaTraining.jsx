import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", border: "#DDD8CE",
  green: "#2C5545", amber: "#D97706", red: "#DC2626",
  charcoal: "#1E1E1E", text: "#2A2A2A", muted: "#8A8680",
};

function TopicCard({ doc }) {
  const kb = Math.round(doc.chars / 1000);
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: "16px 20px",
      display: "flex", alignItems: "flex-start", gap: 16,
    }}>
      <div style={{
        width: 36, height: 36, borderRadius: 8,
        background: `${C.green}14`, display: "flex",
        alignItems: "center", justifyContent: "center",
        flexShrink: 0, fontSize: 18,
      }}>
        📚
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 16, fontWeight: 600, color: C.charcoal }}>
          {doc.topic}
        </div>
        <div style={{ fontSize: 11, color: C.muted, fontFamily: "'Source Code Pro', monospace", marginTop: 4 }}>
          {doc.filename} · {kb}k chars
        </div>
      </div>
      <div style={{
        padding: "3px 10px", borderRadius: 6,
        background: `${C.green}12`, color: C.green,
        border: `1px solid ${C.green}30`,
        fontSize: 10, fontWeight: 600,
        fontFamily: "'Source Code Pro', monospace",
        flexShrink: 0,
      }}>
        seeded
      </div>
    </div>
  );
}

export default function VulaTraining() {
  const [status, setStatus] = useState(null);
  const [topics, setTopics] = useState([]);
  const [loading, setLoading] = useState(true);
  const [seeding, setSeeding] = useState(false);
  const [seedMsg, setSeedMsg] = useState(null);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    try {
      const [s, t] = await Promise.all([
        fetch(`${VULA_API}/v1/training/status`).then((r) => r.json()),
        fetch(`${VULA_API}/v1/training/topics`).then((r) => r.json()),
      ]);
      setStatus(s);
      setTopics(t.topics || []);
    } catch {
      setStatus({ error: "API unavailable" });
    }
    setLoading(false);
  }, []);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  const triggerSeed = async (force = false) => {
    setSeeding(true);
    setSeedMsg(null);
    try {
      const resp = await fetch(`${VULA_API}/v1/training/seed?force=${force}`, { method: "POST" });
      const data = await resp.json();
      setSeedMsg(data.message || "Seeding started.");
      setTimeout(loadStatus, 8000);
    } catch {
      setSeedMsg("Failed to trigger seeding. Is the API running?");
    }
    setSeeding(false);
  };

  const isSeeded = status?.seeded;
  const chunks = status?.chunks ?? 0;

  return (
    <>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600;700&family=Source+Code+Pro:wght@400;500&display=swap');`}</style>
      <div style={{ maxWidth: 900, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui" }}>

        {/* Header */}
        <div style={{ marginBottom: 28 }}>
          <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 32, fontWeight: 700, color: C.charcoal, marginBottom: 4 }}>
            Training Knowledge Base
          </h1>
          <p style={{ fontSize: 13, color: C.muted, fontFamily: "'Source Code Pro', monospace" }}>
            Shared SA construction knowledge — WhatsApp AI and client portal fall back to this when tenant docs have no answer
          </p>
        </div>

        {/* Status card */}
        <div style={{
          background: C.surface, border: `1px solid ${isSeeded ? C.green + "40" : C.amber + "40"}`,
          borderRadius: 12, padding: "20px 24px", marginBottom: 24,
          display: "flex", alignItems: "center", gap: 20, flexWrap: "wrap",
        }}>
          <div style={{
            width: 12, height: 12, borderRadius: "50%",
            background: isSeeded ? "#22C55E" : C.amber,
            flexShrink: 0,
          }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: C.charcoal }}>
              {loading ? "Checking…" : isSeeded ? "Knowledge base is active" : "Not yet seeded"}
            </div>
            <div style={{ fontSize: 12, color: C.muted, marginTop: 2, fontFamily: "'Source Code Pro', monospace" }}>
              {loading ? "" : isSeeded
                ? `${chunks.toLocaleString()} chunks · ${topics.length} topic documents`
                : "Run seed to populate the shared construction KB"}
            </div>
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <button
              onClick={() => triggerSeed(false)}
              disabled={seeding || loading}
              style={{
                padding: "9px 20px", borderRadius: 8,
                background: isSeeded ? C.surface : C.green,
                color: isSeeded ? C.green : "#fff",
                border: `1px solid ${C.green}`,
                fontSize: 13, fontWeight: 600, cursor: seeding ? "not-allowed" : "pointer",
                fontFamily: "'Source Code Pro', monospace",
              }}
            >
              {seeding ? "Seeding…" : isSeeded ? "Re-seed" : "Seed now"}
            </button>
            {isSeeded && (
              <button
                onClick={() => triggerSeed(true)}
                disabled={seeding}
                style={{
                  padding: "9px 20px", borderRadius: 8,
                  background: C.surface, color: C.muted,
                  border: `1px solid ${C.border}`,
                  fontSize: 13, cursor: seeding ? "not-allowed" : "pointer",
                  fontFamily: "'Source Code Pro', monospace",
                }}
              >
                Force re-seed
              </button>
            )}
            <button
              onClick={loadStatus}
              disabled={loading}
              style={{
                padding: "9px 20px", borderRadius: 8,
                background: C.surface, color: C.muted,
                border: `1px solid ${C.border}`,
                fontSize: 13, cursor: "pointer",
                fontFamily: "'Source Code Pro', monospace",
              }}
            >
              ↺
            </button>
          </div>
        </div>

        {seedMsg && (
          <div style={{ padding: "12px 16px", borderRadius: 8, background: "#F0FDF4", border: "1px solid #BBF7D0", fontSize: 13, color: "#166534", marginBottom: 20 }}>
            {seedMsg}
          </div>
        )}

        {/* How it works */}
        <div style={{
          background: `${C.green}06`, border: `1px solid ${C.green}20`,
          borderRadius: 10, padding: "16px 20px", marginBottom: 28,
        }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: C.green, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 8, fontFamily: "'Source Code Pro', monospace" }}>
            How the fallback works
          </div>
          <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, color: C.text, lineHeight: 1.8 }}>
            <li>Client asks a question via WhatsApp or the portal</li>
            <li>AI searches the client's own ingested documents first</li>
            <li>If no relevant answer found → falls back to this shared construction KB</li>
            <li>Client gets an answer about QS terms, contracts, materials, drawing reading, etc.</li>
          </ol>
        </div>

        {/* Topics */}
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: C.text, textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "'Source Code Pro', monospace", marginBottom: 16 }}>
            Topics in the knowledge base ({topics.length})
          </div>
          {loading ? (
            <div style={{ textAlign: "center", padding: 40, color: C.muted, fontFamily: "'Source Code Pro', monospace", fontSize: 13 }}>
              Loading topics…
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {topics.map((doc) => (
                <TopicCard key={doc.filename} doc={doc} />
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ textAlign: "center", marginTop: 40, paddingTop: 24, borderTop: `1px solid ${C.border}` }}>
          <p style={{ fontSize: 11, color: C.muted, fontFamily: "'Source Code Pro', monospace" }}>
            Vula Training KB · {chunks.toLocaleString()} chunks · Add content by editing vula/training/content.py and re-seeding
          </p>
        </div>
      </div>
    </>
  );
}
