/**
 * useRepCallSheet.js — the rep's weekly call sheet (shared by VulaRepCallSheet and
 * RepCallSheetV2): schedule config, this week's entries, and a plain-language fix that is
 * previewed first and only applied on Confirm. Moved verbatim out of VulaRepCallSheet.jsx.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../../lib/authFetch";

export const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function useRepCallSheet(tenantId, repPhone) {
  const [config, setConfig] = useState({});
  const [entries, setEntries] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState(null);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    if (!tenantId || !repPhone) return;
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/call-sheet?rep_phone=${encodeURIComponent(repPhone)}`);
    const d = await r.json().catch(() => ({}));
    setConfig(d.config || {});
    setEntries(d.entries || []);
    setLoaded(true);
  }, [tenantId, repPhone]);
  useEffect(() => { load(); }, [load]);

  const saveConfig = async (patch) => {
    setMsg("");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/call-sheet/configure`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rep_phone: repPhone, ...patch }),
    });
    const d = await r.json().catch(() => ({}));
    if (d.saved) load(); else setMsg(d.detail || "Could not save.");
  };

  const submitInstruction = async (confirm) => {
    setMsg("");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/call-sheet/update`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rep_phone: repPhone, instruction, confirm }),
    });
    const d = await r.json().catch(() => ({}));
    if (d.error) { setMsg(d.error); setPreview(null); return; }
    if (d.preview) { setPreview(d); return; }
    if (d.applied) { setPreview(null); setInstruction(""); load(); }
  };

  return { config, entries, loaded, instruction, setInstruction, preview, setPreview, msg, saveConfig, submitInstruction };
}
