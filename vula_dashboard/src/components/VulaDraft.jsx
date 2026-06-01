import { useState } from "react";

const API = import.meta.env.VITE_API_URL || "";
const API_KEY = import.meta.env.VITE_API_KEY || "";

const DOC_TYPES = [
  { id: "fee_proposal", label: "Fee Proposal" },
  { id: "scope_of_works", label: "Scope of Works" },
  { id: "appointment_letter", label: "Letter of Appointment" },
  { id: "site_meeting_minutes", label: "Site Meeting Minutes" },
  { id: "tender_invitation", label: "Tender Invitation" },
  { id: "project_programme", label: "Project Programme" },
  { id: "payment_certificate", label: "Payment Certificate" },
];

export default function VulaDraft() {
  const [tenantId, setTenantId] = useState("digg-demo");
  const [docType, setDocType] = useState("fee_proposal");
  const [brief, setBrief] = useState("");
  const [projectName, setProjectName] = useState("");
  const [clientName, setClientName] = useState("");
  const [projectValue, setProjectValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function generate() {
    if (!brief.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch(`${API}/v1/draft/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
        body: JSON.stringify({
          tenant_id: tenantId,
          document_type: docType,
          brief,
          project_name: projectName || undefined,
          client_name: clientName || undefined,
          project_value: projectValue ? Number(projectValue) : undefined,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Generation failed");
      setResult(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  function copyToClipboard() {
    if (result?.content) navigator.clipboard.writeText(result.content);
  }

  return (
    <div style={{ padding: "24px", maxWidth: 900, margin: "0 auto" }}>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Vula Draft</h2>
      <p style={{ color: "#8A8680", marginBottom: 24 }}>
        Generate professional construction documents in seconds
      </p>

      <div style={{ display: "grid", gap: 16, marginBottom: 24 }}>
        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>Tenant ID</label>
            <input style={inputStyle} value={tenantId} onChange={e => setTenantId(e.target.value)} />
          </div>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>Document Type</label>
            <select style={inputStyle} value={docType} onChange={e => setDocType(e.target.value)}>
              {DOC_TYPES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
            </select>
          </div>
        </div>

        <div>
          <label style={labelStyle}>Project Brief *</label>
          <textarea
            style={{ ...inputStyle, height: 100, resize: "vertical" }}
            placeholder="e.g. Fee proposal for a 450sqm commercial office fitout in Sea Point, full architectural service from concept to construction admin"
            value={brief}
            onChange={e => setBrief(e.target.value)}
          />
        </div>

        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>Project Name</label>
            <input style={inputStyle} placeholder="Sea Point Office Fitout" value={projectName} onChange={e => setProjectName(e.target.value)} />
          </div>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>Client Name</label>
            <input style={inputStyle} placeholder="Acme Holdings (Pty) Ltd" value={clientName} onChange={e => setClientName(e.target.value)} />
          </div>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>Project Value (R)</label>
            <input style={inputStyle} type="number" placeholder="3500000" value={projectValue} onChange={e => setProjectValue(e.target.value)} />
          </div>
        </div>

        <button
          onClick={generate}
          disabled={loading || !brief.trim()}
          style={btnStyle(loading || !brief.trim())}
        >
          {loading ? "Generating…" : "Generate Document"}
        </button>
      </div>

      {error && (
        <div style={{ background: "#FEE", border: "1px solid #FCC", borderRadius: 8, padding: 12, color: "#900", marginBottom: 16 }}>
          {error}
        </div>
      )}

      {result && (
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div style={{ fontSize: 13, color: "#8A8680" }}>
              {result.word_count} words · {result.sources_used} KB sources · {result.document_label}
            </div>
            <button onClick={copyToClipboard} style={{ ...btnStyle(false), padding: "6px 16px", fontSize: 13 }}>
              Copy
            </button>
          </div>
          <pre style={{
            background: "#F7F4EE", border: "1px solid #DDD8CE", borderRadius: 8,
            padding: 20, whiteSpace: "pre-wrap", fontFamily: "inherit",
            fontSize: 14, lineHeight: 1.6, maxHeight: 600, overflowY: "auto"
          }}>
            {result.content}
          </pre>
        </div>
      )}
    </div>
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
