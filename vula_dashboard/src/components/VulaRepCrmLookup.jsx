/**
 * VulaRepCrmLookup.jsx — search Dynamics 365 accounts/contacts/opportunities from the dashboard.
 * Shows the connect flow if not yet connected; a search box + results once it is.
 */
import VulaDynamics365Connect from "./VulaDynamics365Connect";
import { KINDS, useRepCrmLookup } from "./rep/useRepCrmLookup";
import RepCrmLookupV2 from "./rep/RepCrmLookupV2";

const C = { surface: "var(--surface)", border: "var(--border)", green: "var(--accent)", text: "var(--text)", muted: "var(--muted)" };

function VulaRepCrmLookupV1({ tenantId }) {
  const { connected, kind, setKind, query, setQuery, results, error, search } = useRepCrmLookup(tenantId);

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
        <button onClick={search} style={{ padding: "8px 16px", background: C.green, color: "var(--on-accent)", border: "none",
          borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>Search</button>
      </div>
      {error && <div style={{ fontSize: 12.5, color: "var(--danger)", marginBottom: 10 }}>{error}</div>}
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

export default function VulaRepCrmLookup({ v2 = false, ...props }) {
  return v2 ? <RepCrmLookupV2 {...props} /> : <VulaRepCrmLookupV1 {...props} />;
}
