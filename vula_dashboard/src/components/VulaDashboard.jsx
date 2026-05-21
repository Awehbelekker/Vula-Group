import { useState, useRef, useEffect, useCallback } from "react";

// ── Design Direction ──────────────────────────────────────────────────────────
// Organic/natural meets industrial precision
// Palette: warm cream, deep forest green, amber, charcoal
// Fonts: Cormorant Garamond (display) + Source Code Pro (mono data)
// Feeling: a well-built SA product — confident, warm, locally grounded

const VULA_API = import.meta.env.VITE_API_URL ?? "/api";

const COLORS = {
  bg: "#F7F4EE",
  surface: "#FFFFFF",
  surfaceAlt: "#F0EDE5",
  border: "#DDD8CE",
  green: "#2C5545",
  greenLight: "#3D7260",
  amber: "#C4861A",
  amberLight: "#E8A832",
  charcoal: "#1E1E1E",
  text: "#2A2A2A",
  muted: "#8A8680",
  mutedLight: "#B5B0A8",
};

// ── Utilities ────────────────────────────────────────────────────────────────
function formatTime(ms) {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function fileIcon(type) {
  const icons = { ".pdf": "📄", ".docx": "📝", ".doc": "📝", ".xlsx": "📊", ".xls": "📊", ".csv": "📊", ".txt": "📃", ".png": "🖼️", ".jpg": "🖼️" };
  return icons[type] || "📁";
}

// ── Components ────────────────────────────────────────────────────────────────

function Badge({ children, color = COLORS.green }) {
  return (
    <span style={{
      display: "inline-block", padding: "2px 10px",
      background: `${color}18`, color: color,
      borderRadius: 20, fontSize: 11,
      fontFamily: "'Source Code Pro', monospace",
      letterSpacing: "0.05em", fontWeight: 500,
    }}>{children}</span>
  );
}

function Card({ children, style }) {
  return (
    <div style={{
      background: COLORS.surface,
      border: `1px solid ${COLORS.border}`,
      borderRadius: 12, padding: 24,
      ...style,
    }}>{children}</div>
  );
}

function SectionTitle({ children, sub }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <h2 style={{
        fontFamily: "'Cormorant Garamond', serif",
        fontSize: 26, fontWeight: 600,
        color: COLORS.charcoal, marginBottom: 4,
      }}>{children}</h2>
      {sub && <p style={{ fontSize: 13, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace" }}>{sub}</p>}
    </div>
  );
}

// ── Upload Zone ──────────────────────────────────────────────────────────────

function UploadZone({ tenantId, onUploaded }) {
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [results, setResults] = useState([]);
  const inputRef = useRef();

  const upload = useCallback(async (files) => {
    setUploading(true);
    const newResults = [];
    for (const file of files) {
      const fd = new FormData();
      fd.append("tenant_id", tenantId);
      fd.append("file", file);
      const start = Date.now();
      try {
        const resp = await fetch(`${VULA_API}/ingest`, { method: "POST", body: fd });
        const data = await resp.json();
        newResults.push({ file: file.name, status: "queued", time: Date.now() - start, data });
      } catch (e) {
        newResults.push({ file: file.name, status: "error", error: e.message });
      }
    }
    setResults(prev => [...newResults, ...prev]);
    setUploading(false);
    onUploaded?.();
  }, [tenantId, onUploaded]);

  const handleDrop = (e) => {
    e.preventDefault(); setDragOver(false);
    upload(Array.from(e.dataTransfer.files));
  };

  return (
    <Card>
      <SectionTitle sub="PDF, Word, Excel, CSV, images — up to 50MB each">
        Train your AI
      </SectionTitle>

      <div
        onDrop={handleDrop}
        onDragOver={e => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onClick={() => inputRef.current?.click()}
        style={{
          border: `2px dashed ${dragOver ? COLORS.green : COLORS.border}`,
          borderRadius: 10, padding: "36px 24px",
          textAlign: "center", cursor: "pointer",
          background: dragOver ? `${COLORS.green}08` : COLORS.surfaceAlt,
          transition: "all 0.2s ease",
          marginBottom: results.length ? 20 : 0,
        }}
      >
        <div style={{ fontSize: 36, marginBottom: 10 }}>
          {uploading ? "⏳" : "📂"}
        </div>
        <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 18, color: COLORS.charcoal, marginBottom: 6 }}>
          {uploading ? "Processing..." : "Drop business documents here"}
        </div>
        <div style={{ fontSize: 12, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace" }}>
          Quotes · Invoices · Price lists · SOPs · Contracts · Specs
        </div>
        <input ref={inputRef} type="file" multiple style={{ display: "none" }}
          onChange={e => upload(Array.from(e.target.files))} />
      </div>

      {results.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {results.map((r, i) => (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: 12,
              padding: "10px 14px", borderRadius: 8,
              background: COLORS.surfaceAlt,
              border: `1px solid ${COLORS.border}`,
            }}>
              <span style={{ fontSize: 20 }}>{fileIcon(r.file.slice(r.file.lastIndexOf(".")))}</span>
              <span style={{ flex: 1, fontSize: 13, color: COLORS.text, fontFamily: "system-ui" }}>{r.file}</span>
              <Badge color={r.status === "error" ? "#C0392B" : COLORS.green}>
                {r.status === "queued" ? "Queued ✓" : "Error"}
              </Badge>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ── Query Panel ───────────────────────────────────────────────────────────────

function QueryPanel({ tenantId }) {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState([]);

  const suggested = [
    "What do we charge for architectural drawings?",
    "Summarise our standard terms and conditions",
    "What is our lead time for delivery?",
    "What services do we offer?",
  ];

  const ask = async (q) => {
    if (!q.trim()) return;
    setLoading(true); setResult(null);
    const start = Date.now();
    try {
      const resp = await fetch(`${VULA_API}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tenant_id: tenantId, question: q, top_k: 5 }),
      });
      const data = await resp.json();
      const entry = { question: q, answer: data.answer, sources: data.sources, time: Date.now() - start };
      setResult(entry);
      setHistory(prev => [entry, ...prev.slice(0, 9)]);
    } catch (e) {
      setResult({ question: q, answer: `Error: ${e.message}`, sources: [], time: Date.now() - start });
    }
    setLoading(false);
  };

  return (
    <Card>
      <SectionTitle sub="Ask anything about your business documents">
        Ask your AI
      </SectionTitle>

      {/* Suggested questions */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 16 }}>
        {suggested.map(s => (
          <button key={s} onClick={() => { setQuestion(s); ask(s); }} style={{
            padding: "6px 14px", borderRadius: 20,
            border: `1px solid ${COLORS.border}`,
            background: COLORS.surfaceAlt, color: COLORS.text,
            fontSize: 12, cursor: "pointer",
            fontFamily: "'Source Code Pro', monospace",
            transition: "all 0.15s",
          }}>{s}</button>
        ))}
      </div>

      {/* Input */}
      <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
        <input
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => e.key === "Enter" && ask(question)}
          placeholder="Ask anything about your business..."
          style={{
            flex: 1, padding: "12px 16px",
            border: `1px solid ${COLORS.border}`,
            borderRadius: 8, fontSize: 14,
            fontFamily: "system-ui", color: COLORS.text,
            background: COLORS.surfaceAlt, outline: "none",
          }}
        />
        <button
          onClick={() => ask(question)}
          disabled={loading || !question.trim()}
          style={{
            padding: "12px 24px", borderRadius: 8,
            background: loading ? COLORS.mutedLight : COLORS.green,
            color: "#fff", border: "none", cursor: loading ? "not-allowed" : "pointer",
            fontSize: 14, fontWeight: 600, fontFamily: "system-ui",
            transition: "background 0.2s",
          }}
        >{loading ? "..." : "Ask →"}</button>
      </div>

      {/* Result */}
      {result && (
        <div style={{
          background: `${COLORS.green}08`,
          border: `1px solid ${COLORS.green}30`,
          borderRadius: 10, padding: 20, marginBottom: 16,
        }}>
          <div style={{ fontSize: 12, color: COLORS.green, fontFamily: "'Source Code Pro', monospace", marginBottom: 10 }}>
            ✦ Answer · {formatTime(result.time)}
          </div>
          <div style={{ fontSize: 15, color: COLORS.text, lineHeight: 1.7, marginBottom: 16, fontFamily: "system-ui", whiteSpace: "pre-wrap" }}>
            {result.answer}
          </div>
          {result.sources?.length > 0 && (
            <div>
              <div style={{ fontSize: 11, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace", marginBottom: 8 }}>
                SOURCES
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {result.sources.map((s, i) => (
                  <span key={i} style={{
                    padding: "4px 10px", borderRadius: 6,
                    background: COLORS.surfaceAlt,
                    border: `1px solid ${COLORS.border}`,
                    fontSize: 11, color: COLORS.muted,
                    fontFamily: "'Source Code Pro', monospace",
                  }}>
                    {s.filename} · p{s.page}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* History */}
      {history.length > 1 && (
        <div>
          <div style={{ fontSize: 11, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace", marginBottom: 8 }}>RECENT</div>
          {history.slice(1, 4).map((h, i) => (
            <div key={i} onClick={() => setResult(h)} style={{
              padding: "8px 12px", borderRadius: 6, cursor: "pointer",
              border: `1px solid ${COLORS.border}`, marginBottom: 6,
              fontSize: 13, color: COLORS.muted, fontFamily: "system-ui",
              background: COLORS.surfaceAlt,
            }}>
              {h.question}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ── Web Intelligence Panel ────────────────────────────────────────────────────

function WebIntelPanel({ tenantId }) {
  const [tab, setTab] = useState("company");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [inputs, setInputs] = useState({ url: "", topic: "South African construction industry", keywords: "architecture, construction, fitout" });

  const tabs = [
    { id: "company", label: "Company Research" },
    { id: "digest", label: "News Digest" },
    { id: "tenders", label: "Tenders" },
  ];

  const run = async () => {
    setLoading(true); setResult(null);
    try {
      let endpoint, body;
      if (tab === "company") {
        endpoint = "/scrape/company";
        body = { tenant_id: tenantId, url: inputs.url };
      } else if (tab === "digest") {
        endpoint = "/scrape/digest";
        body = { tenant_id: tenantId, topic: inputs.topic };
      } else {
        endpoint = "/scrape/tenders";
        body = { tenant_id: tenantId, keywords: inputs.keywords.split(",").map(k => k.trim()) };
      }
      const resp = await fetch(`${VULA_API}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setResult(await resp.json());
    } catch (e) {
      setResult({ error: e.message });
    }
    setLoading(false);
  };

  return (
    <Card>
      <SectionTitle sub="Competitive intelligence and market research">
        Web Intelligence
      </SectionTitle>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 4, marginBottom: 20, background: COLORS.surfaceAlt, padding: 4, borderRadius: 8 }}>
        {tabs.map(t => (
          <button key={t.id} onClick={() => { setTab(t.id); setResult(null); }} style={{
            flex: 1, padding: "8px 12px", borderRadius: 6,
            border: "none", cursor: "pointer",
            background: tab === t.id ? COLORS.surface : "transparent",
            color: tab === t.id ? COLORS.green : COLORS.muted,
            fontSize: 13, fontWeight: tab === t.id ? 600 : 400,
            boxShadow: tab === t.id ? "0 1px 4px rgba(0,0,0,0.08)" : "none",
            transition: "all 0.15s", fontFamily: "system-ui",
          }}>{t.label}</button>
        ))}
      </div>

      {/* Input fields */}
      {tab === "company" && (
        <input value={inputs.url} onChange={e => setInputs(p => ({ ...p, url: e.target.value }))}
          placeholder="https://company.co.za" style={inputStyle} />
      )}
      {tab === "digest" && (
        <input value={inputs.topic} onChange={e => setInputs(p => ({ ...p, topic: e.target.value }))}
          placeholder="Industry topic..." style={inputStyle} />
      )}
      {tab === "tenders" && (
        <input value={inputs.keywords} onChange={e => setInputs(p => ({ ...p, keywords: e.target.value }))}
          placeholder="architecture, construction, fitout" style={inputStyle} />
      )}

      <button onClick={run} disabled={loading} style={{
        width: "100%", padding: 12, borderRadius: 8,
        background: loading ? COLORS.mutedLight : COLORS.amber,
        color: "#fff", border: "none", cursor: loading ? "not-allowed" : "pointer",
        fontSize: 14, fontWeight: 600, marginBottom: result ? 20 : 0,
        fontFamily: "system-ui", transition: "background 0.2s",
      }}>
        {loading ? "Researching..." : "Research →"}
      </button>

      {/* Results */}
      {result && !result.error && (
        <div style={{ background: COLORS.surfaceAlt, borderRadius: 10, padding: 16, border: `1px solid ${COLORS.border}` }}>
          {tab === "company" && (
            <>
              <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 22, color: COLORS.charcoal, marginBottom: 4 }}>{result.name}</div>
              <p style={{ fontSize: 13, color: COLORS.muted, marginBottom: 12, lineHeight: 1.6 }}>{result.description}</p>
              {result.services?.length > 0 && (
                <div style={{ marginBottom: 12 }}>
                  <div style={labelStyle}>Services</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {result.services.slice(0, 6).map((s, i) => <Badge key={i}>{s}</Badge>)}
                  </div>
                </div>
              )}
              {result.pitch_angle && (
                <div>
                  <div style={labelStyle}>💡 Pitch Angle</div>
                  <p style={{ fontSize: 13, color: COLORS.text, lineHeight: 1.6 }}>{result.pitch_angle}</p>
                </div>
              )}
            </>
          )}
          {tab === "digest" && (
            <>
              {result.key_trends?.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={labelStyle}>Key Trends</div>
                  {result.key_trends.map((t, i) => (
                    <div key={i} style={{ display: "flex", gap: 8, marginBottom: 6, fontSize: 13, color: COLORS.text }}>
                      <span style={{ color: COLORS.amber }}>→</span>{t}
                    </div>
                  ))}
                </div>
              )}
              {result.articles?.slice(0, 4).map((a, i) => (
                <div key={i} style={{ padding: "10px 0", borderTop: `1px solid ${COLORS.border}` }}>
                  <div style={{ fontSize: 14, fontWeight: 600, color: COLORS.charcoal, marginBottom: 4, fontFamily: "system-ui" }}>{a.title}</div>
                  <div style={{ fontSize: 12, color: COLORS.muted }}>{a.summary}</div>
                </div>
              ))}
            </>
          )}
          {tab === "tenders" && (
            <>
              <div style={labelStyle}>{result.tenders_found || 0} tenders found</div>
              {result.tenders?.slice(0, 5).map((t, i) => (
                <div key={i} style={{ padding: "10px 0", borderTop: `1px solid ${COLORS.border}` }}>
                  <div style={{ fontSize: 14, fontWeight: 600, color: COLORS.charcoal, fontFamily: "system-ui" }}>{t.title}</div>
                  <div style={{ display: "flex", gap: 16, marginTop: 4 }}>
                    {t.reference && <span style={{ fontSize: 11, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace" }}>Ref: {t.reference}</span>}
                    {t.closing_date && <span style={{ fontSize: 11, color: COLORS.amber, fontFamily: "'Source Code Pro', monospace" }}>Closes: {t.closing_date}</span>}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}
      {result?.error && (
        <div style={{ padding: 12, borderRadius: 8, background: "#FEF2F2", border: "1px solid #FECACA", fontSize: 13, color: "#991B1B" }}>
          ⚠️ {result.error} — make sure the Vula API server is running on port 7438.
        </div>
      )}
    </Card>
  );
}

// ── Shared Styles ─────────────────────────────────────────────────────────────
const inputStyle = {
  width: "100%", padding: "12px 16px", marginBottom: 12,
  border: `1px solid ${COLORS.border}`, borderRadius: 8,
  fontSize: 14, fontFamily: "system-ui", color: COLORS.text,
  background: COLORS.surfaceAlt, outline: "none", boxSizing: "border-box",
};

const labelStyle = {
  fontSize: 10, letterSpacing: "0.12em", textTransform: "uppercase",
  color: COLORS.muted, fontFamily: "'Source Code Pro', monospace",
  marginBottom: 8,
};

// ── Main Dashboard ────────────────────────────────────────────────────────────
export default function VulaDashboard() {
  const [tenantId] = useState("demo_tenant");
  const [apiStatus, setApiStatus] = useState("checking");
  const [docCount, setDocCount] = useState(0);

  useEffect(() => {
    fetch(`${VULA_API}/status`)
      .then(r => r.json())
      .then(d => setApiStatus(d.status))
      .catch(() => setApiStatus("offline"));

    fetch(`${VULA_API}/documents/${tenantId}`)
      .then(r => r.json())
      .then(d => setDocCount(d.count || 0))
      .catch(() => {});
  }, [tenantId]);

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600;700&family=Source+Code+Pro:wght@400;500&display=swap');
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: ${COLORS.bg}; }
        button { transition: all 0.15s ease; }
        button:hover:not(:disabled) { opacity: 0.9; transform: translateY(-1px); }
        input:focus { border-color: ${COLORS.green} !important; }
      `}</style>

      <div style={{ minHeight: "100vh", background: COLORS.bg, fontFamily: "system-ui" }}>
        {/* Header */}
        <div style={{
          background: COLORS.surface,
          borderBottom: `1px solid ${COLORS.border}`,
          padding: "16px 24px",
          display: "flex", alignItems: "center", gap: 16,
          position: "sticky", top: 0, zIndex: 100,
        }}>
          <div style={{
            width: 36, height: 36, borderRadius: 10,
            background: COLORS.green,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontFamily: "'Cormorant Garamond', serif",
            fontSize: 20, color: "#fff", fontWeight: 700,
          }}>V</div>

          <div>
            <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: COLORS.charcoal, lineHeight: 1 }}>
              Vula
            </div>
            <div style={{ fontSize: 10, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace", letterSpacing: "0.1em" }}>
              UNLOCK YOUR BUSINESS
            </div>
          </div>

          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{
                width: 8, height: 8, borderRadius: "50%",
                background: apiStatus === "ok" ? "#22C55E" : apiStatus === "offline" ? "#EF4444" : "#F59E0B",
                animation: apiStatus === "checking" ? "pulse 1s infinite" : "none",
              }} />
              <span style={{ fontSize: 11, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace" }}>
                {apiStatus === "ok" ? "Connected" : apiStatus === "offline" ? "Offline" : "Connecting..."}
              </span>
            </div>

            {docCount > 0 && <Badge color={COLORS.green}>{docCount} doc{docCount !== 1 ? "s" : ""} trained</Badge>}

            <div style={{ fontSize: 11, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace" }}>
              {tenantId}
            </div>
          </div>
        </div>

        {/* Offline banner */}
        {apiStatus === "offline" && (
          <div style={{
            background: "#FEF3C7", borderBottom: "1px solid #FDE68A",
            padding: "10px 24px", fontSize: 13, color: "#92400E",
            fontFamily: "'Source Code Pro', monospace",
          }}>
            ⚠️ Vula API is offline — start it with:{" "}
            <code style={{ background: "#FDE68A", padding: "2px 6px", borderRadius: 4 }}>
              uvicorn vula.api.server:app --port 7438
            </code>
          </div>
        )}

        {/* Main content */}
        <div style={{ maxWidth: 1100, margin: "0 auto", padding: "32px 24px" }}>
          {/* Hero */}
          <div style={{ marginBottom: 36, textAlign: "center" }}>
            <h1 style={{
              fontFamily: "'Cormorant Garamond', serif",
              fontSize: "clamp(36px, 5vw, 56px)",
              fontWeight: 700, color: COLORS.charcoal, lineHeight: 1.1,
              marginBottom: 12,
            }}>
              Your business, unlocked.
            </h1>
            <p style={{ fontSize: 15, color: COLORS.muted, maxWidth: 500, margin: "0 auto", lineHeight: 1.7 }}>
              Upload your documents. Ask questions. Research competitors. All running locally on your hardware — private, free, forever.
            </p>
          </div>

          {/* Grid */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
            <UploadZone tenantId={tenantId} onUploaded={() => {
              fetch(`${VULA_API}/documents/${tenantId}`).then(r => r.json()).then(d => setDocCount(d.count || 0)).catch(() => {});
            }} />
            <QueryPanel tenantId={tenantId} />
            <div style={{ gridColumn: "1 / -1" }}>
              <WebIntelPanel tenantId={tenantId} />
            </div>
          </div>

          {/* Footer */}
          <div style={{ textAlign: "center", marginTop: 40, paddingTop: 24, borderTop: `1px solid ${COLORS.border}` }}>
            <p style={{ fontSize: 11, color: COLORS.mutedLight, fontFamily: "'Source Code Pro', monospace" }}>
              Vula AI · by Aweh Be Lekker (Pty) Ltd · Cape Town · POPIA Compliant · Zero Cloud Cost
            </p>
          </div>
        </div>
      </div>

      <style>{`
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
      `}</style>
    </>
  );
}
