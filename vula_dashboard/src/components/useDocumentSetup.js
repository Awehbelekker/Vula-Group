/**
 * useDocumentSetup.js — upload company documents for Vula to read (POST /admin/setup-from-documents)
 * and follow the reading until it is done. Shared by Home's "Finish setting up" card and the
 * first-run welcome. `onFound` is called when a reading finishes, so the caller can load what
 * was found.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { VULA_API } from "../lib/authFetch";

export const DOC_ACCEPT = ".pdf,.doc,.docx,.txt,.md,.xls,.xlsx,.csv,.png,.jpg,.jpeg,.webp";
const OUTCOME = { waiting: "waiting", reading: "reading…", failed: "couldn't be read", no_text: "no text found in it" };

export function fileLine(f) {
  if (f.status === "done") return f.drafts ? `${f.drafts} ${f.drafts === 1 ? "answer" : "answers"} found` : "filed, no new answers in it";
  return OUTCOME[f.status] || f.status;
}

export function useDocumentSetup(tenantId, onFound) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [st, setSt] = useState(null);
  const url = `${VULA_API}/v1/commerce/${tenantId}/admin/setup-from-documents`;

  const poll = useCallback(() => fetch(url).then((r) => (r.ok ? r.json() : null))
    .then((d) => { if (d?.status) setSt(d.status); return d?.status; }).catch(() => null), [url]);
  // On opening: pick up a reading that is still going (or just finished).
  useEffect(() => { poll(); }, [poll]);
  useEffect(() => {
    if (!st?.running) return undefined;
    const t = setInterval(poll, 4000);
    return () => clearInterval(t);
  }, [st?.running, poll]);
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && st && !st.running) onFound?.();
    wasRunning.current = !!st?.running;
  }, [st, onFound]);

  /** Upload these files; resolves true when the reading has started. */
  const upload = async (files) => {
    if (!files?.length) return false;
    setBusy(true); setErr("");
    try {
      const body = new FormData();
      files.forEach((f) => body.append("files", f));
      const r = await fetch(url, { method: "POST", body });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(d.detail || "Those couldn't be uploaded. Please try again."); return false; }
      if (d.skipped?.length) setErr(`Left out: ${d.skipped.map((s) => `${s.name} (${s.reason})`).join(", ")}.`);
      setSt({ running: true, files: (d.files || []).map((name) => ({ name, status: "waiting", drafts: 0 })) });
      return true;
    } catch { setErr("Couldn't reach Vula. Please try again."); return false; } finally { setBusy(false); }
  };

  return { st, busy, err, reading: !!st?.running, upload };
}
