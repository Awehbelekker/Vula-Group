/**
 * VulaRepCrmLookup.jsx — search Dynamics 365 accounts/contacts/opportunities from the dashboard.
 * Shows the connect flow if not yet connected; a search box + results once it is.
 */
import { useState, useEffect, useCallback } from "react";
import VulaDynamics365Connect from "./VulaDynamics365Connect";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", text: "#2A2A2A", muted: "#8A8680" };
const KINDS = [["contact", "Contacts"], ["account", "Accounts"], ["opportunity", "Opportunities"]];

export default function VulaRepCrmLookup({ tenantId }) {
  const [connected, setConnected] = useState(null);
  const [kind, setKind] = useState("contact");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [error, setError] = useState("");

  const checkStatus = useCallback(async () => {
    const r = await fetch(`${VULA_API}/v1/dynamics365/status/${tenantId}`);
    const d = await r.json().catch(() => ({}));
    setConnected(d.status === "connected");
  }, [tenantId]);
  useEffect(() => { checkStatus(); }, [checkStatus]);

  const search = async () => {
    setError("");
    const params = new URLSearchParams({ query, kind });
    const r = await fetch(`${VULA_API}/v1/dynamics365/${tenantId}/search?${params}`);
    const d = await r.json().catch(() => ({}));
    if (d.error) { setError(d.error); setResults([]); }
    else setResults(d.results || []);
  };

  if (connected === null) return null;
  if (!connected) {
    return (
      <div style={{ maxWidth: 700, margin: "0 auto", padding: 20 }}>
        <h2 style={{ fontSize: 20, fontWeight: 700, color: C.text, margin: "0 0 12px" }}>Dynamics 365</h2>
        <VulaDynamics365Connect tenantId={tenantId} />
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 700, margin: "0 auto", padding: 20 }}>
      <h2 style={{ fontSize: 20, fontWeight: 700, color: C.text, margin: "0 0 4px" }}>Dynamics 365</h2>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 16px" }}>Search your real CRM data.</p>

      <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
        {KINDS.map(([k, label]) => (
          <button key={k} onClick={() => setKind(k)}
            style={{ padding: "5px 12px", borderRadius: 14, fontSize: 12, cursor: "pointer",
              border: `1px solid ${kind === k ? C.green : C.border}`,
              background: kind === k ? C.green : C.surface, color: kind === k ? "#fff" : C.muted }}>{label}</button>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()}
               placeholder="Search…" style={{ flex: 1, padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }} />
        <button onClick={search} style={{ padding: "8px 16px", background: C.green, color: "#fff", border: "none",
          borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>Search</button>
      </div>
      {error && <div style={{ fontSize: 12.5, color: "#A23B2D", marginBottom: 10 }}>{error}</div>}
      {results.map((r, i) => (
        <div key={r.id || i} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: "10px 14px", marginBottom: 6 }}>
          <div style={{ fontWeight: 700, color: C.text, fontSize: 13.5 }}>{r.name || r.fullname || r.subject || "—"}</div>
          <div style={{ fontSize: 12, color: C.muted }}>
            {[r.emailaddress1, r.telephone1, r.jobtitle].filter(Boolean).join(" · ")}
          </div>
        </div>
      ))}
      {results.length === 0 && !error && <div style={{ fontSize: 13, color: C.muted }}>No results yet — try a search.</div>}
    </div>
  );
}
