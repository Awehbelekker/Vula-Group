/**
 * useRepCallSheet.js — the rep's weekly call sheet (shared by VulaRepCallSheet and
 * RepCallSheetV2): schedule config, this week's entries, and a plain-language fix that is
 * previewed first and only applied on Confirm. Moved verbatim out of VulaRepCallSheet.jsx.
 */
import { useState, useEffect, useCallback } from "react";
import { tryApi } from "../../lib/api";

export const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function useRepCallSheet(tenantId, repPhone) {
  const [config, setConfig] = useState({});
  const [entries, setEntries] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState(null);
  const [msg, setMsg] = useState("");
  const [loadError, setLoadError] = useState(null);   // the sheet didn't load (not "nothing logged")

  // 23 Nov 2026 audit: a failed load showed "Nothing logged yet" (or spun for ever on a network
  // error), and a fix the server refused with {detail} showed nothing at all.
  const load = useCallback(async () => {
    if (!tenantId || !repPhone) return;
    const { ok, data: d, error } = await tryApi(`/v1/commerce/${tenantId}/admin/call-sheet?rep_phone=${encodeURIComponent(repPhone)}`);
    if (ok) {
      setConfig(d.config || {});
      setEntries(d.entries || []);
      setLoadError(null);
    } else {
      setLoadError(error);
    }
    setLoaded(true);
  }, [tenantId, repPhone]);
  useEffect(() => { load(); }, [load]);

  const saveConfig = async (patch) => {
    setMsg("");
    const { ok, data: d, error } = await tryApi(`/v1/commerce/${tenantId}/admin/call-sheet/configure`, {
      method: "POST", body: { rep_phone: repPhone, ...patch },
    });
    if (ok && d.saved) load(); else setMsg(ok ? (d.detail || "Could not save.") : `Could not save: ${error}`);
  };

  const submitInstruction = async (confirm) => {
    setMsg("");
    const { ok, data, error } = await tryApi(`/v1/commerce/${tenantId}/admin/call-sheet/update`, {
      method: "POST", body: { rep_phone: repPhone, instruction, confirm },
    });
    // what was typed stays in the box on a refusal
    if (!ok) { setMsg(error); setPreview(null); return; }
    const d = data || {};
    if (d.preview) { setPreview(d); return; }
    if (d.applied) { setPreview(null); setInstruction(""); load(); }
  };

  return { config, entries, loaded, loadError, load, instruction, setInstruction, preview, setPreview, msg, saveConfig, submitInstruction };
}
