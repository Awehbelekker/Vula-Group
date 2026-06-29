/**
 * VulaTenants.jsx — master Tenant Control Plane.
 * Provision a tenant, pick a business type (auto-enables the right modules), toggle modules,
 * set theme accent + store URL + default gateway. No code change to onboard a store.
 */
import { useState, useEffect, useCallback } from "react";
import { T } from "../theme/tokens";
import { Card, Button, Badge, SectionTitle, inputStyle } from "./ui";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";

export default function VulaTenants() {
  const [tenants, setTenants] = useState([]);
  const [registry, setRegistry] = useState({ modules: [], business_types: [] });
  const [openId, setOpenId] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ tenant_id: "", display_name: "", business_type: "food" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [t, r] = await Promise.all([
      fetch(`${VULA_API}/v1/tenants`).then((x) => x.json()),
      fetch(`${VULA_API}/v1/tenants/registry`).then((x) => x.json()),
    ]);
    setTenants(t.tenants || []);
    setRegistry(r);
  }, []);
  useEffect(() => { load(); }, [load]);

  const patch = async (id, body) => {
    setBusy(true);
    try {
      await fetch(`${VULA_API}/v1/tenants/${id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      await load();
    } finally { setBusy(false); }
  };

  const create = async () => {
    if (!form.tenant_id.trim()) return;
    setBusy(true);
    try {
      await fetch(`${VULA_API}/v1/tenants`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, tenant_id: form.tenant_id.trim().toLowerCase().replace(/\s+/g, "-") }),
      });
      setCreating(false); setForm({ tenant_id: "", display_name: "", business_type: "food" });
      await load();
    } finally { setBusy(false); }
  };

  const toggleModule = (t, key) => {
    const has = (t.modules || []).includes(key);
    const next = has ? t.modules.filter((m) => m !== key) : [...(t.modules || []), key];
    patch(t.tenant_id, { modules: next });
  };

  const applyPreset = (t, bt) => {
    const preset = registry.business_types.find((b) => b.id === bt);
    patch(t.tenant_id, { business_type: bt, modules: preset ? preset.modules : t.modules });
  };

  return (
    <div style={{ maxWidth: 920, margin: "0 auto", padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <SectionTitle sub="Provision and configure every tenant here. Pick a business type and the right modules switch on — no code change to onboard a store.">Tenants</SectionTitle>
        <Button size="sm" onClick={() => setCreating((v) => !v)}>{creating ? "Close" : "+ New tenant"}</Button>
      </div>

      {creating && (
        <Card style={{ marginBottom: 16, display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ fontWeight: 700, color: T.ink }}>New tenant</div>
          <input placeholder="slug (e.g. kelp-boardbags)" value={form.tenant_id}
            onChange={(e) => setForm({ ...form, tenant_id: e.target.value })} style={inputStyle} />
          <input placeholder="Display name (e.g. Kelp Board Bags)" value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })} style={inputStyle} />
          <select value={form.business_type} onChange={(e) => setForm({ ...form, business_type: e.target.value })} style={inputStyle}>
            {registry.business_types.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
          </select>
          <Button size="sm" onClick={create} disabled={busy}>{busy ? "Creating…" : "Create tenant"}</Button>
        </Card>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {tenants.map((t) => {
          const open = openId === t.tenant_id;
          const accent = t.theme?.accent || "#888";
          return (
            <Card key={t.tenant_id}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}
                   onClick={() => setOpenId(open ? null : t.tenant_id)}>
                <span style={{ width: 14, height: 14, borderRadius: 4, background: accent, border: "1px solid #0001" }} />
                <span style={{ fontWeight: 700, color: T.ink, fontSize: 15 }}>{t.display_name || t.tenant_id}</span>
                <span style={{ fontSize: 11.5, color: T.muted }}>{t.tenant_id}</span>
                <Badge tone="ok">{(t.modules || []).length} modules</Badge>
                {t.business_type && <Badge>{t.business_type}</Badge>}
                <span style={{ marginLeft: "auto", color: T.muted }}>{open ? "▲" : "▼"}</span>
              </div>

              {open && (
                <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 14 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 10 }}>
                    <Labeled label="Business type">
                      <select value={t.business_type || "other"} onChange={(e) => applyPreset(t, e.target.value)} style={inputStyle}>
                        {registry.business_types.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
                      </select>
                    </Labeled>
                    <Labeled label="Store URL">
                      <input defaultValue={t.store_url || ""} placeholder="https://…" style={inputStyle}
                        onBlur={(e) => e.target.value !== (t.store_url || "") && patch(t.tenant_id, { store_url: e.target.value })} />
                    </Labeled>
                    <Labeled label="Accent colour">
                      <input type="color" defaultValue={accent} style={{ ...inputStyle, height: 38, padding: 2 }}
                        onBlur={(e) => patch(t.tenant_id, { theme: { ...(t.theme || {}), accent: e.target.value } })} />
                    </Labeled>
                    <Labeled label="Default gateway">
                      <input defaultValue={t.default_payment_provider || ""} placeholder="yoco / payfast / …" style={inputStyle}
                        onBlur={(e) => e.target.value !== (t.default_payment_provider || "") && patch(t.tenant_id, { default_payment_provider: e.target.value })} />
                    </Labeled>
                  </div>

                  <div>
                    <div style={{ fontSize: 11, color: T.muted, textTransform: "uppercase", marginBottom: 6 }}>Modules this tenant sees</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {registry.modules.map((m) => {
                        const on = (t.modules || []).includes(m.id);
                        return (
                          <button key={m.id} onClick={() => toggleModule(t, m.id)} disabled={busy}
                            style={{ padding: "5px 11px", borderRadius: 18, fontSize: 12.5, cursor: "pointer",
                              border: `1px solid ${on ? "var(--accent)" : T.border}`,
                              background: on ? "var(--accent)" : T.surface, color: on ? "#fff" : T.muted }}>
                            {m.label}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )}
            </Card>
          );
        })}
        {!tenants.length && <Card><span style={{ color: T.muted }}>No tenants yet — run migration 040, then “+ New tenant”.</span></Card>}
      </div>
    </div>
  );
}

function Labeled({ label, children }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ fontSize: 11, color: T.muted, textTransform: "uppercase" }}>{label}</span>
      {children}
    </label>
  );
}
