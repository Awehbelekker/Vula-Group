/**
 * useClientOnboarding.js — Marketing › Client onboarding, shared by VulaClientOnboarding (current
 * look) and marketing/ClientOnboardingV2 (the redesign, 2026-10-20). Moved verbatim: the opt-in
 * funnel for imported clients, a preview to the owner's own number, and sending the approved
 * intro / opt-in template in small batches (protects the number's quality rating).
 */
import { useState, useEffect } from "react";
import { VULA_API } from "../../lib/authFetch";

export function useClientOnboarding(tenantId) {
  const [stats, setStats] = useState(null);
  const [test, setTest] = useState("");
  const [batch, setBatch] = useState(25);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () =>
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/onboarding/status`)
      .then((r) => r.json()).then((d) => setStats(d.stats || {})).catch(() => setStats({}));
  useEffect(() => { load(); }, [tenantId]);

  const send = async (payload, label) => {
    setBusy(true); setMsg(`${label}…`);
    try {
      const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/onboarding/send-batch`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      }).then((r) => r.json());
      if (d.error) setMsg(`⚠️ ${d.error}`);
      else setMsg(`✓ Sent ${d.sent}${d.failed ? `, ${d.failed} failed` : ""}${d.remaining != null ? ` · ${d.remaining} left` : ""}`);
      load();
    } catch { setMsg("⚠️ Request failed"); }
    setBusy(false);
  };

  return { stats, test, setTest, batch, setBatch, msg, busy, send };
}
