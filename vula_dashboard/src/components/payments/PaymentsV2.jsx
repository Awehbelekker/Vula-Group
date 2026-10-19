/**
 * PaymentsV2.jsx — the redesigned Payments gateways (2026-10-19, UX redesign Phase F). Same data
 * and actions as the current screen (payments/usePayments.js): one card per gateway with its
 * status, and Connect / Edit keys opening a Sheet with each key labelled and where to find it.
 */
import { CreditCard, KeyRound, Star, Unplug } from "lucide-react";
import { Button, Card, Field, Input, PageHeader, Select, Sheet, StatusChip } from "../ui/index.jsx";
import { FIELD_LABEL, META, isSecret, usePayments } from "./usePayments";
import "./payments.css";

export default function PaymentsV2({ tenantId }) {
  const p = usePayments(tenantId);
  const gateway = (p.data.available || []).find((g) => g.id === p.openId);
  return (
    <div className="vpy">
      <PageHeader sub="Connect the gateways you use — pay links and checkout use your default. Your secret keys are stored securely and never shown back." />
      <ul className="vpy-grid" aria-label="Payment gateways">
        {(p.data.available || []).map((g) => {
          const c = p.connected[g.id];
          return (
            <li key={g.id}>
              <Card className="vpy-card">
                <div className="vpy-head">
                  <span className="vpy-ic" aria-hidden="true"><CreditCard size={18} strokeWidth={1.75} /></span>
                  <h3 className="vpy-name">{g.label}</h3>
                  {c && <StatusChip tone={c.is_default ? "accent" : "ok"}>{c.is_default ? "Default" : "Connected"}</StatusChip>}
                  {c && <StatusChip>{c.mode === "test" ? "Test" : "Live"}</StatusChip>}
                </div>
                <p className="vpy-sub">{(META[g.id] || {}).blurb}</p>
                <div className="vpy-actions">
                  <Button size="sm" variant={c ? "secondary" : "primary"} Icon={KeyRound} onClick={() => p.open(g.id)}>{c ? "Edit keys" : "Connect"}</Button>
                  {c && !c.is_default && <Button size="sm" variant="secondary" Icon={Star} onClick={() => p.makeDefault(g.id)}>Make default</Button>}
                  {c && <Button size="sm" variant="quiet" Icon={Unplug} onClick={() => p.disconnect(g.id)}>Disconnect</Button>}
                </div>
              </Card>
            </li>
          );
        })}
      </ul>
      <p className="vpy-sub vpy-foot">Pay links use your default gateway.</p>

      <Sheet open={!!gateway} title={gateway ? `${p.connected[gateway.id] ? "Update" : "Connect"} ${gateway.label}` : ""} onClose={p.close}
        footer={gateway && <>
          <Button variant="secondary" onClick={p.close}>Cancel</Button>
          <Button onClick={() => p.connect(gateway.id)} disabled={p.saving}>{p.saving ? "Connecting…" : (p.connected[gateway.id] ? "Update" : "Connect")}</Button>
        </>}>
        {gateway && (
          <div className="vpy-form">
            <p className="vpy-where"><KeyRound size={16} aria-hidden="true" /><span><strong>Where to find these:</strong> {(META[gateway.id] || {}).where}</span></p>
            {gateway.fields.map((f) => (
              <Field key={f} label={FIELD_LABEL[f] || f}>
                <Input type={isSecret(f) ? "password" : "text"} autoComplete="off" value={p.form[f] || ""} onChange={(e) => p.setForm({ ...p.form, [f]: e.target.value })} />
              </Field>
            ))}
            <Field label="Mode">
              <Select value={p.mode} onChange={(e) => p.setMode(e.target.value)}>
                <option value="live">Live</option><option value="test">Test / sandbox</option>
              </Select>
            </Field>
          </div>
        )}
      </Sheet>
    </div>
  );
}
