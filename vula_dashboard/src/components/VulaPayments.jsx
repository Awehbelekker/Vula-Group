/**
 * VulaPayments.jsx — connect SA payment gateways (onboarding).
 * Pick a gateway → see exactly which keys it needs + where to find them → connect → set default.
 * Pay-links + checkout use the default. Secrets are write-only (never shown back).
 */
import { useState, useEffect, useCallback } from "react";
import { T } from "../theme/tokens";
import { Card, Button, Badge, SectionTitle, inputStyle } from "./ui";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";

const META = {
  yoco:     { blurb: "Cards. Popular with SA SMEs.", where: "portal.yoco.com → Sell Online → Payment gateway → API keys" },
  payfast:  { blurb: "Cards + instant EFT + many methods.", where: "PayFast dashboard → Settings → Integration (merchant ID, key, passphrase)" },
  peach:    { blurb: "Enterprise-grade, many methods.", where: "Peach dashboard → Settings → your Entity ID + access token" },
  ikhokha:  { blurb: "SME-focused; cards + pay links.", where: "iKhokha → Developer / API settings (App ID + App Secret)" },
  ozow:     { blurb: "Instant EFT (pay-by-bank). Huge in SA.", where: "Ozow merchant portal → Settings → Site (Site code, Private key, API key)" },
  paystack: { blurb: "Cards + EFT, now in SA.", where: "Paystack dashboard → Settings → API Keys (Secret key)" },
};
const FIELD_LABEL = {
  secret_key: "Secret key", merchant_id: "Merchant ID", merchant_key: "Merchant key",
  passphrase: "Passphrase", entity_id: "Entity ID", access_token: "Access token",
  webhook_secret: "Webhook secret", app_id: "App ID", app_secret: "App secret",
  site_code: "Site code", private_key: "Private key", api_key: "API key",
};

export default function VulaPayments({ tenantId }) {
  const [data, setData] = useState({ connected: [], available: [] });
  const [openId, setOpenId] = useState(null);
  const [form, setForm] = useState({});
  const [mode, setMode] = useState("live");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const r = await fetch(`${VULA_API}/v1/payments/${tenantId}/providers`);
    setData(await r.json());
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const connected = Object.fromEntries((data.connected || []).map((c) => [c.provider, c]));
  const hasDefault = (data.connected || []).some((c) => c.is_default);

  const connect = async (provider) => {
    setSaving(true);
    try {
      await fetch(`${VULA_API}/v1/payments/${tenantId}/providers`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, credentials: form, mode, is_default: !hasDefault }),
      });
      setOpenId(null); setForm({}); load();
    } finally { setSaving(false); }
  };
  const makeDefault = async (p) => { await fetch(`${VULA_API}/v1/payments/${tenantId}/providers/${p}/default`, { method: "POST" }); load(); };
  const disconnect = async (p) => { await fetch(`${VULA_API}/v1/payments/${tenantId}/providers/${p}`, { method: "DELETE" }); load(); };

  return (
    <div style={{ maxWidth: 880, margin: "0 auto", padding: 24 }}>
      <SectionTitle sub="Connect the gateways you use — pay-links & checkout use your default. Your secret keys are stored securely and never shown back.">Payments</SectionTitle>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(380px,1fr))", gap: 14 }}>
        {(data.available || []).map((g) => {
          const c = connected[g.id];
          const open = openId === g.id;
          const m = META[g.id] || {};
          return (
            <Card key={g.id} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontWeight: 700, color: T.ink, fontSize: 15 }}>{g.label}</span>
                {c && <Badge tone={c.is_default ? "accent" : "ok"}>{c.is_default ? "Default" : "Connected"}</Badge>}
                {c && <span style={{ fontSize: 11, color: T.muted }}>{c.mode}</span>}
              </div>
              <div style={{ fontSize: 12.5, color: T.muted }}>{m.blurb}</div>

              {open && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 6 }}>
                  <div style={{ fontSize: 11.5, color: T.muted, background: T.surfaceAlt, borderRadius: 8, padding: "8px 10px" }}>🔑 Where to find these: {m.where}</div>
                  {g.fields.map((f) => (
                    <input key={f} type={f.includes("secret") || f.includes("key") || f === "passphrase" || f === "access_token" ? "password" : "text"}
                      placeholder={FIELD_LABEL[f] || f} value={form[f] || ""}
                      onChange={(e) => setForm({ ...form, [f]: e.target.value })} style={inputStyle} />
                  ))}
                  <label style={{ fontSize: 12.5, color: T.muted, display: "flex", gap: 8, alignItems: "center" }}>
                    <select value={mode} onChange={(e) => setMode(e.target.value)} style={{ ...inputStyle, width: "auto", padding: "5px 8px" }}>
                      <option value="live">Live</option><option value="test">Test / sandbox</option>
                    </select>
                  </label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <Button size="sm" onClick={() => connect(g.id)} disabled={saving}>{saving ? "Connecting…" : (c ? "Update" : "Connect")}</Button>
                    <Button size="sm" variant="ghost" onClick={() => { setOpenId(null); setForm({}); }}>Cancel</Button>
                  </div>
                </div>
              )}

              {!open && (
                <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                  <Button size="sm" variant={c ? "soft" : "primary"} onClick={() => { setOpenId(g.id); setForm({}); setMode(c?.mode || "live"); }}>{c ? "Edit keys" : "Connect"}</Button>
                  {c && !c.is_default && <Button size="sm" variant="ghost" onClick={() => makeDefault(g.id)}>Make default</Button>}
                  {c && <Button size="sm" variant="danger" onClick={() => disconnect(g.id)}>Disconnect</Button>}
                </div>
              )}
            </Card>
          );
        })}
      </div>
      <p style={{ textAlign: "center", fontSize: 11, color: "#B5B0A8", marginTop: 22 }}>Powered by Vula · pay-links use your default gateway</p>
    </div>
  );
}
