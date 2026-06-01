import { useState } from "react";

const API = import.meta.env.VITE_API_URL || "";
const API_KEY = import.meta.env.VITE_API_KEY || "";

export default function VulaAgent() {
  const [tenantId, setTenantId] = useState("digg-demo");
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function run() {
    if (!question.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch(`${API}/v1/agent/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
        body: JSON.stringify({ tenant_id: tenantId, question }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Agent run failed");
      setResult(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ padding: "24px", maxWidth: 900, margin: "0 auto" }}>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Vula Agent</h2>
      <p style={{ color: "#8A8680", marginBottom: 24 }}>
        Multi-agent reasoning — routes your question to the right skill, runs parallel branches, merges the best answer
      </p>

      <div style={{ display: "grid", gap: 16, marginBottom: 24 }}>
        <div>
          <label style={labelStyle}>Tenant ID</label>
          <input style={{ ...inputStyle, maxWidth: 300 }} value={tenantId} onChange={e => setTenantId(e.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Question</label>
          <textarea
            style={{ ...inputStyle, height: 90, resize: "vertical" }}
            placeholder="e.g. What were the agreed PM fees on the Sporty project, and how do they compare to standard SACAP rates?"
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && e.ctrlKey) run(); }}
          />
        </div>
        <button onClick={run} disabled={loading || !question.trim()} style={btnStyle(loading || !question.trim())}>
          {loading ? "Thinking…" : "Run Agent (Ctrl+Enter)"}
        </button>
      </div>

      {error && (
        <div style={{ background: "#FEE", border: "1px solid #FCC", borderRadius: 8, padding: 12, color: "#900", marginBottom: 16 }}>
          {error}
        </div>
      )}

      {result && (
        <div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
            <Chip label="Skill" value={result.skill_used} />
            <Chip label="Complexity" value={`${result.complexity}/3`} />
            <Chip label="Branches" value={result.branch_count} />
            <Chip label="Merge" value={result.merge_strategy} />
            <Chip label="Confidence" value={`${Math.round(result.confidence * 100)}%`} />
            <Chip label="Latency" value={`${(result.latency_ms / 1000).toFixed(1)}s`} />
          </div>

          <div style={{
            background: "#F7F4EE", border: "1px solid #DDD8CE", borderRadius: 8,
            padding: 20, whiteSpace: "pre-wrap", fontSize: 14, lineHeight: 1.6,
            marginBottom: 16,
          }}>
            {result.answer}
          </div>

          {result.sources?.length > 0 && (
            <details style={{ fontSize: 13, color: "#8A8680" }}>
              <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                {result.sources.length} sources
              </summary>
              <ul style={{ marginTop: 8 }}>
                {result.sources.map((s, i) => (
                  <li key={i}>{s.filename || s.url || s.type} {s.score ? `(${s.score})` : ""}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function Chip({ label, value }) {
  return (
    <span style={{
      background: "#FFF", border: "1px solid #DDD8CE", borderRadius: 20,
      padding: "4px 12px", fontSize: 12, color: "#1E1E1E",
    }}>
      <span style={{ color: "#8A8680" }}>{label}:</span> <strong>{value}</strong>
    </span>
  );
}

const labelStyle = { display: "block", fontSize: 12, fontWeight: 600, color: "#1E1E1E", marginBottom: 6 };
const inputStyle = {
  width: "100%", padding: "10px 12px", border: "1px solid #DDD8CE",
  borderRadius: 6, fontSize: 14, fontFamily: "inherit", boxSizing: "border-box",
  background: "#FFF", outline: "none",
};
const btnStyle = (disabled) => ({
  background: disabled ? "#C5C0B8" : "#2C5545",
  color: "#FFF", border: "none", borderRadius: 6,
  padding: "12px 24px", fontSize: 15, fontWeight: 600,
  cursor: disabled ? "not-allowed" : "pointer", width: "100%",
});
