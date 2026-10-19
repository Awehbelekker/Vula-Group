/**
 * VulaPayments.jsx — connect SA payment gateways (onboarding).
 * Pick a gateway → see exactly which keys it needs + where to find them → connect → set default.
 * Pay-links + checkout use the default. Secrets are write-only (never shown back).
 */
import { T } from "../theme/tokens";
import { Card, Button, Badge, SectionTitle, inputStyle } from "./ui";
import { FIELD_LABEL, META, isSecret, usePayments } from "./payments/usePayments";
import PaymentsV2 from "./payments/PaymentsV2";

export default function VulaPayments({ tenantId, v2 = false }) {
  if (v2) return <PaymentsV2 tenantId={tenantId} />;
  return <PaymentsV1 tenantId={tenantId} />;
}

function PaymentsV1({ tenantId }) {
  const { data, connected, openId, form, setForm, mode, setMode, saving, connect, makeDefault, disconnect, open: openGateway, close } = usePayments(tenantId);

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
                    <input key={f} type={isSecret(f) ? "password" : "text"}
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
                    <Button size="sm" variant="ghost" onClick={close}>Cancel</Button>
                  </div>
                </div>
              )}

              {!open && (
                <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                  <Button size="sm" variant={c ? "soft" : "primary"} onClick={() => openGateway(g.id)}>{c ? "Edit keys" : "Connect"}</Button>
                  {c && !c.is_default && <Button size="sm" variant="ghost" onClick={() => makeDefault(g.id)}>Make default</Button>}
                  {c && <Button size="sm" variant="danger" onClick={() => disconnect(g.id)}>Disconnect</Button>}
                </div>
              )}
            </Card>
          );
        })}
      </div>
      <p style={{ textAlign: "center", fontSize: 11, color: "var(--faint)", marginTop: 22 }}><span className="vula-powered">Powered by Vula · </span>pay-links use your default gateway</p>
    </div>
  );
}
