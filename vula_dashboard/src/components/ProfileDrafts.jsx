/**
 * ProfileDrafts.jsx — "Vula found these — check them" (12 Nov 2026).
 *
 * When Vula reads a business's website or the documents it uploads, it DRAFTS the business
 * profile answers it can find (hours, areas, payment terms, returns, FAQ…), each one only when
 * the page or document really says it. A draft is never used to answer anyone until the owner
 * keeps it. The only place to keep one used to be the WhatsApp interview, so a business with no
 * WhatsApp number typed every answer by hand.
 *
 * This lists the drafts with where each was found, and keeps or dismisses them
 * (POST /admin/business-profile/drafts). Used on Home's setup card and in Settings › Business
 * profile. `refresh` (a number that changes) makes it load again; `onChanged` tells the parent
 * the profile changed.
 */
import { useCallback, useEffect, useState } from "react";
import { Check, FileText, Globe, X } from "lucide-react";
import { VULA_API } from "../lib/authFetch";
import { Button } from "./ui/index.jsx";
import "./gettingStarted.css";

export function useProfileDrafts(tenantId, refresh = 0, onChanged) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const url = `${VULA_API}/v1/commerce/${tenantId}/admin/business-profile`;

  const load = useCallback(() => {
    if (!tenantId) return;
    fetch(url).then((r) => (r.ok ? r.json() : null)).then((d) => setData(d)).catch(() => setData(null));
  }, [tenantId, url]);
  useEffect(() => { load(); }, [load, refresh]);

  const review = async (body) => {
    setBusy(true); setErr("");
    try {
      const r = await fetch(`${url}/drafts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(d.detail || "That didn't save. Please try again."); return; }
      setData(d);                         // the profile as the server now has it
      onChanged?.(d);
    } catch { setErr("Couldn't reach Vula. Please try again."); } finally { setBusy(false); }
  };

  const drafts = data?.drafts || {};
  const questions = data?.questions || [];
  const items = questions.filter((q) => drafts[q.key]).map((q) => ({ key: q.key, question: q.q, ...drafts[q.key] }));
  return {
    items, busy, err, answered: data?.answered, total: data?.total,
    keep: (key) => review({ accept: [key] }),
    dismiss: (key) => review({ dismiss: [key] }),
    keepAll: () => review({ accept: items.map((i) => i.key) }),
  };
}

export default function ProfileDrafts({ tenantId, refresh = 0, onChanged }) {
  const x = useProfileDrafts(tenantId, refresh, onChanged);
  if (!x.items.length) return null;
  return (
    <section className="vpd" aria-label="Answers Vula found for you to check">
      <div className="vgs-head">
        <strong className="vgs-title">Vula found {x.items.length} {x.items.length === 1 ? "answer" : "answers"} for your business profile</strong>
        {x.items.length > 1 && <Button size="sm" Icon={Check} disabled={x.busy} className="vgs-hide" onClick={x.keepAll}>Keep all</Button>}
      </div>
      <p className="vgs-muted">Each one is quoted from your own website or document. Nothing is used to answer your customers until you keep it.</p>
      <ul className="vpd-list">
        {x.items.map((d) => (
          <li key={d.key} className="vpd-item">
            <span className="vgs-muted">{d.question}</span>
            <span className="vpd-answer">{d.answer}</span>
            <span className="vpd-source">
              {d.source?.kind === "document" ? <FileText size={14} aria-hidden="true" /> : <Globe size={14} aria-hidden="true" />}
              {d.source?.kind === "document" ? `From your document ${d.source.label}` : `From your website${d.source?.label ? ` (${d.source.label})` : ""}`}
            </span>
            <span className="vpd-actions">
              <Button size="sm" Icon={Check} disabled={x.busy} onClick={() => x.keep(d.key)} aria-label={`Keep this answer: ${d.question}`}>Keep</Button>
              <Button size="sm" variant="secondary" Icon={X} disabled={x.busy} onClick={() => x.dismiss(d.key)} aria-label={`Dismiss this answer: ${d.question}`}>Dismiss</Button>
            </span>
          </li>))}
      </ul>
      {x.err && <p role="alert" className="vgs-warn">{x.err}</p>}
    </section>
  );
}
