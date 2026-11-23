/**
 * useMarketingCopy.js — Marketing › Campaigns (AI marketing copy), shared by VulaMarketing (current
 * look) and marketing/MarketingCopyV2 (the redesign, 2026-10-20). Moved verbatim: generate two
 * variants of today's specials / a product description / promo copy / a broadcast blast from the
 * live catalogue, copy or save one, and hand one to Broadcast.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../../lib/authFetch";
import { errorText, tryApi } from "../../lib/api";
import { toast } from "../ui/index.jsx";

export const KINDS = [
  { id: "specials", label: "📣 Today's specials", hint: "A fresh-today post for WhatsApp status or social." },
  { id: "product",  label: "🏷️ Product description", hint: "Describe one product — type its name below." },
  { id: "promo",    label: "🎉 Promo copy", hint: "Copy for an offer — describe it below." },
  { id: "broadcast",label: "📢 Broadcast blast", hint: "A WhatsApp message to existing customers." },
];
export const TONES = ["warm & friendly", "playful", "premium", "urgent / limited-time", "professional"];

export function useMarketingCopy(tenantId) {
  const [kind, setKind] = useState("specials");
  const [topic, setTopic] = useState("");
  const [tone, setTone] = useState("warm & friendly");
  const [variants, setVariants] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(-1);
  const [saved, setSaved] = useState([]);
  const [savedIdx, setSavedIdx] = useState(-1);

  const active = KINDS.find(k => k.id === kind);
  const needsTopic = kind === "product" || kind === "promo";

  const loadSaved = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/marketing/saved`);
      const d = await r.json().catch(() => null);
      if (!r.ok) { toast(`Couldn't load your saved copy: ${errorText(d)}`, "danger"); return; }
      setSaved((d && d.saved) || []);
    } catch { toast("Couldn't load your saved copy. Check your connection and try again.", "danger"); }
  }, [tenantId]);
  useEffect(() => { loadSaved(); }, [loadSaved]);

  const generate = async () => {
    setErr(""); setVariants([]); setLoading(true);
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/marketing/generate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, topic, tone, variants: 2 }),
      });
      const d = await r.json().catch(() => null);
      if (!r.ok || d?.error) setErr(errorText(d));
      else setVariants(d?.variants || []);
    } catch (e) { setErr(String(e.message || e)); }
    setLoading(false);
  };

  const copy = (text, i) => {
    navigator.clipboard?.writeText(text);
    setCopied(i); setTimeout(() => setCopied(-1), 1500);
  };

  const saveVariant = async (text, i) => {
    const res = await tryApi(`/v1/commerce/${tenantId}/admin/marketing/saved`, {
      method: "POST",
      body: { kind, topic, tone, body: text },
    });
    if (!res.ok) { toast(`That copy wasn't saved: ${res.error}`, "danger"); return; }
    setSavedIdx(i);
    loadSaved();
    setTimeout(() => setSavedIdx(-1), 1200);
  };

  const deleteSaved = async (id) => {
    setSaved(s => s.filter(x => x.id !== id));
    const res = await tryApi(`/v1/commerce/${tenantId}/admin/marketing/saved/${id}`, { method: "DELETE" });
    if (!res.ok) { toast(`That copy wasn't deleted: ${res.error}`, "danger"); loadSaved(); }
  };

  return { kind, setKind, topic, setTopic, tone, setTone, variants, loading, err, copied, saved, savedIdx, active, needsTopic, generate, copy, saveVariant, deleteSaved };
}
