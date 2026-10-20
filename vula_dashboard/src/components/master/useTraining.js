/**
 * useTraining.js — the shared training knowledge bases (shared by VulaTraining and TrainingV2):
 * status, topics and seeding for the SA construction and general SA business KBs. Seeding is
 * master-only on the server. Moved verbatim out of VulaTraining.jsx.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../../lib/authFetch";

// Two shared knowledge bases, same three endpoints each (vula/api/training.py). Seeding is a
// platform operation (master-only on the server), so this tab lives in the Master shell.
export const KBS = {
  construction: { base: "/v1/training", label: "SA construction",
    blurb: "Shared SA construction knowledge — the assistant falls back to this when a construction tenant's own docs have no answer" },
  business: { base: "/v1/training/business", label: "General SA business",
    blurb: "Shared SA small-business knowledge (VAT, POPIA, invoicing, payroll basics) — the fallback for every tenant" },
};

export function useTraining() {
  const [kb, setKb] = useState("construction");
  const [status, setStatus] = useState(null);
  const [topics, setTopics] = useState([]);
  const [loading, setLoading] = useState(true);
  const [seeding, setSeeding] = useState(false);
  const [seedMsg, setSeedMsg] = useState(null);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    try {
      const [s, t] = await Promise.all([
        fetch(`${VULA_API}${KBS[kb].base}/status`).then((r) => r.json()),
        fetch(`${VULA_API}${KBS[kb].base}/topics`).then((r) => r.json()),
      ]);
      setStatus(s);
      setTopics(t.topics || []);
    } catch {
      setStatus({ error: "API unavailable" });
    }
    setLoading(false);
  }, [kb]);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  const triggerSeed = async (force = false) => {
    setSeeding(true);
    setSeedMsg(null);
    try {
      const resp = await fetch(`${VULA_API}${KBS[kb].base}/seed?force=${force}`, { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) { setSeedMsg(data.detail || `Seeding refused (HTTP ${resp.status}).`); setSeeding(false); return; }
      setSeedMsg(data.message || "Seeding started.");
      setTimeout(loadStatus, 8000);
    } catch {
      setSeedMsg("Failed to trigger seeding. Is the API running?");
    }
    setSeeding(false);
  };

  const isSeeded = status?.seeded;
  const chunks = status?.chunks ?? 0;
  return { kb, setKb, status, topics, loading, seeding, seedMsg, setSeedMsg, loadStatus, triggerSeed, isSeeded, chunks };
}
