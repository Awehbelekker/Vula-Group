/**
 * usePayments.js — payment gateways: connect, make default, disconnect (2026-10-19, UX redesign
 * Phase F). Shared by VulaPayments.jsx and payments/PaymentsV2.jsx; moved out unchanged — same
 * /v1/payments/{tenant}/providers endpoints and bodies. Secrets are write-only (never shown back).
 */
import { useCallback, useEffect, useState } from "react";
import { tryApi } from "../../lib/api";
import { toast } from "../ui/index.jsx";

export const META = {
  yoco:     { blurb: "Cards. Popular with SA SMEs.", where: "portal.yoco.com → Sell Online → Payment gateway → API keys" },
  payfast:  { blurb: "Cards + instant EFT + many methods.", where: "PayFast dashboard → Settings → Integration (merchant ID, key, passphrase)" },
  peach:    { blurb: "Enterprise-grade, many methods.", where: "Peach dashboard → Settings → your Entity ID + access token" },
  ikhokha:  { blurb: "SME-focused; cards + pay links.", where: "iKhokha → Developer / API settings (App ID + App Secret)" },
  ozow:     { blurb: "Instant EFT (pay-by-bank). Huge in SA.", where: "Ozow merchant portal → Settings → Site (Site code, Private key, API key)" },
  paystack: { blurb: "Cards + EFT, now in SA.", where: "Paystack dashboard → Settings → API Keys (Secret key)" },
};
export const FIELD_LABEL = {
  secret_key: "Secret key", merchant_id: "Merchant ID", merchant_key: "Merchant key",
  passphrase: "Passphrase", entity_id: "Entity ID", access_token: "Access token",
  webhook_secret: "Webhook secret", app_id: "App ID", app_secret: "App secret",
  site_code: "Site code", private_key: "Private key", api_key: "API key",
};
/** Secret-looking fields are typed as passwords. */
export const isSecret = (f) => f.includes("secret") || f.includes("key") || f === "passphrase" || f === "access_token";

export function usePayments(tenantId) {
  const [data, setData] = useState({ connected: [], available: [] });
  const [openId, setOpenId] = useState(null);
  const [form, setForm] = useState({});
  const [mode, setMode] = useState("live");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const r = await tryApi(`/v1/payments/${tenantId}/providers`);
    if (r.ok) setData(r.data);
    else toast(`Couldn't load your payment gateways: ${r.error}`, "danger");
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const connected = Object.fromEntries((data.connected || []).map((c) => [c.provider, c]));
  const hasDefault = (data.connected || []).some((c) => c.is_default);

  const connect = async (provider) => {
    setSaving(true);
    try {
      const r = await tryApi(`/v1/payments/${tenantId}/providers`, {
        method: "POST", body: { provider, credentials: form, mode, is_default: !hasDefault },
      });
      // refused: the sheet stays open with the keys as typed, and says why
      if (!r.ok) { toast(`Couldn't connect: ${r.error}`, "danger"); return; }
      setOpenId(null); setForm({}); load();
    } finally { setSaving(false); }
  };
  const makeDefault = async (p) => {
    const r = await tryApi(`/v1/payments/${tenantId}/providers/${p}/default`, { method: "POST" });
    if (!r.ok) toast(`Couldn't make it the default: ${r.error}`, "danger");
    load();
  };
  const disconnect = async (p) => {
    const r = await tryApi(`/v1/payments/${tenantId}/providers/${p}`, { method: "DELETE" });
    if (!r.ok) toast(`Couldn't disconnect: ${r.error}`, "danger");
    load();
  };
  const open = (id) => { setOpenId(id); setForm({}); setMode(connected[id]?.mode || "live"); };
  const close = () => { setOpenId(null); setForm({}); };

  return { data, connected, openId, form, setForm, mode, setMode, saving, connect, makeDefault, disconnect, open, close };
}
