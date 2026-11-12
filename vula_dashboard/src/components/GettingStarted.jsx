/**
 * GettingStarted.jsx — Home's "Finish setting up" card (12 Nov 2026).
 *
 * A business can sign up by itself, but nothing then told the owner what was left to do: the
 * go-live checklist was only on the operator's screen. This shows the owner's own steps, in plain
 * words, each with the page that does it (GET /admin/setup/owner). WhatsApp is one of the
 * OPTIONAL steps: a business that only wants invoicing can finish without connecting a number.
 *
 * Under it, the plan: which plan, how long the trial has left, and — for the owner or a manager,
 * while nothing has been paid — the link to pay (GET /admin/plan). Self-serve signups had no way
 * to see or pay for their plan.
 *
 * In between: "Let Vula fill it in" — upload the company's own documents and Vula drafts the
 * profile answers from them — and the answers Vula found (from those or from the website) for
 * the owner to keep or dismiss (ProfileDrafts.jsx).
 *
 * Owners and managers only. "Hide" puts it away on this device once every required step is done.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, CheckCircle2, Circle, CreditCard, ListChecks, Upload } from "lucide-react";
import { VULA_API } from "../lib/authFetch";
import { useAuthStore } from "../store/auth";
import { Button, Card } from "./ui/index.jsx";
import ProfileDrafts from "./ProfileDrafts.jsx";
import "./gettingStarted.css";

const LEADS = ["owner", "manager", "admin", "master"];

// Where each step is done: [section, sub-tab]. A sub-tab alone finds its own section.
const WHERE = {
  branding: ["settings", "brand"],
  payments: [null, "invoices"],
  vat: [null, "invoices"],
  first_invoice: [null, "invoices"],
  profile: ["settings", "profile"],
  team: ["team", null],
  whatsapp: ["settings", "whatsapp"],
};

const ACCEPT = ".pdf,.doc,.docx,.txt,.md,.xls,.xlsx,.csv,.png,.jpg,.jpeg,.webp";
const OUTCOME = {
  waiting: "waiting", reading: "reading…", failed: "couldn't be read", no_text: "no text found in it",
};

/**
 * "Let Vula fill it in": the owner uploads the documents they already have — company profile,
 * terms, price list, menu, FAQ — and Vula files each one and drafts the business profile answers
 * it finds in them (POST /admin/setup-from-documents). Progress is read back from the server
 * until the reading is done; what was found appears in the list below for the owner to check.
 */
function FillFromDocuments({ tenantId, onFound }) {
  const input = useRef(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [st, setSt] = useState(null);
  const url = `${VULA_API}/v1/commerce/${tenantId}/admin/setup-from-documents`;

  const poll = useCallback(() => fetch(url).then((r) => (r.ok ? r.json() : null))
    .then((d) => { if (d?.status) setSt(d.status); return d?.status; }).catch(() => null), [url]);
  // On opening Home: pick up a reading that is still going (or just finished).
  useEffect(() => { poll(); }, [poll]);
  useEffect(() => {
    if (!st?.running) return undefined;
    const t = setInterval(poll, 4000);
    return () => clearInterval(t);
  }, [st?.running, poll]);
  // When a reading finishes, the list of what was found is loaded again.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && st && !st.running) onFound?.();
    wasRunning.current = !!st?.running;
  }, [st, onFound]);

  const upload = async (e) => {
    const files = [...(e.target.files || [])];
    e.target.value = "";
    if (!files.length) return;
    setBusy(true); setErr("");
    try {
      const body = new FormData();
      files.forEach((f) => body.append("files", f));
      const r = await fetch(url, { method: "POST", body });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(d.detail || "Those couldn't be uploaded. Please try again."); return; }
      if (d.skipped?.length) setErr(`Left out: ${d.skipped.map((s) => `${s.name} (${s.reason})`).join(", ")}.`);
      setSt({ running: true, files: (d.files || []).map((name) => ({ name, status: "waiting", drafts: 0 })) });
    } catch { setErr("Couldn't reach Vula. Please try again."); } finally { setBusy(false); }
  };

  const reading = !!st?.running;
  return (
    <div className="vgs-fill" role="group" aria-label="Let Vula fill it in from your documents">
      <div className="vgs-fill-row">
        <p className="vgs-fill-text"><strong>Let Vula fill it in.</strong> Upload what you already have: your company profile, terms, price list, menu or FAQ. Vula files them and pulls out your hours, areas, policies and more for you to check.</p>
        <input ref={input} type="file" multiple accept={ACCEPT} hidden onChange={upload} aria-label="Choose company documents to upload" />
        <Button size="sm" Icon={Upload} disabled={busy || reading} onClick={() => input.current?.click()}>
          {busy ? "Uploading…" : reading ? "Reading your documents…" : "Upload documents"}
        </Button>
      </div>
      {st?.files?.length > 0 && (
        <ul className="vgs-files" aria-live="polite" aria-label="Documents being read">
          {st.files.map((f) => (
            <li key={f.name}>{f.name}: {f.status === "done"
              ? (f.drafts ? `${f.drafts} ${f.drafts === 1 ? "answer" : "answers"} found` : "filed, no new answers in it")
              : (OUTCOME[f.status] || f.status)}</li>))}
        </ul>)}
      {err && <p role="alert" className="vgs-warn">{err}</p>}
    </div>
  );
}

function PlanLine({ plan }) {
  if (!plan) return null;
  const users = plan.users?.limit ? `${plan.users.used ?? "–"} of ${plan.users.limit} users` : null;
  const docs = plan.documents?.limit ? `${plan.documents.used ?? "–"} of ${plan.documents.limit} documents` : null;
  const trial = plan.trial_over ? "Your free trial has ended."
    : plan.on_trial && plan.trial_days_left != null
      ? `Free trial: ${plan.trial_days_left} ${plan.trial_days_left === 1 ? "day" : "days"} left.`
      : null;
  return (
    <div className="vgs-plan" role="group" aria-label="Your plan">
      <p className="vgs-plan-text">
        <strong>{plan.label || plan.plan}</strong>
        {plan.paid ? " · paid" : ""}
        {trial ? <span className={plan.trial_over ? "vgs-warn" : undefined}> · {trial}</span> : null}
        {[users, docs].filter(Boolean).length > 0 && <span className="vgs-muted"> · {[users, docs].filter(Boolean).join(", ")}</span>}
      </p>
      {plan.pay_url && (
        <Button size="sm" Icon={CreditCard} onClick={() => window.open(plan.pay_url, "_blank", "noopener,noreferrer")}>
          Choose how to pay
        </Button>)}
    </div>
  );
}

export default function GettingStarted({ tenantId, onNavigate }) {
  const role = useAuthStore((s) => s.role);
  const key = `vula-setup-hidden-${tenantId}`;
  const [setup, setSetup] = useState(null);
  const [plan, setPlan] = useState(null);
  const [hidden, setHidden] = useState(() => { try { return localStorage.getItem(key) === "1"; } catch { return false; } });
  const [found, setFound] = useState(0);          // bumped when a reading finishes: reload what was found

  const load = useCallback(() => {
    if (!LEADS.includes(role)) return;
    const get = (path) => fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/${path}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    get("setup/owner").then((d) => setSetup(d && Array.isArray(d.steps) ? d : null));
    get("plan").then((d) => setPlan(d && d.plan ? d : null));
  }, [tenantId, role]);
  useEffect(() => { load(); }, [load]);

  if (!LEADS.includes(role) || !setup) return null;
  // Nothing left to say: every required step done, and no trial to pay for.
  const planNeedsThem = plan && !plan.paid && (plan.on_trial || plan.trial_over);
  if (setup.complete && (hidden || !planNeedsThem)) return null;

  const hide = () => { try { localStorage.setItem(key, "1"); } catch { /* private mode */ } setHidden(true); };
  const open = (step) => () => {
    const [section, tab] = WHERE[step.id] || [null, step.tab];
    onNavigate?.(section || tab, section ? tab : (tab || undefined));
  };

  return (
    <Card role="region" aria-label="Finish setting up" className="vgs">
      <div className="vgs-head">
        <strong className="vgs-title"><ListChecks size={16} aria-hidden="true" />
          {setup.complete ? "You're set up" : "Finish setting up"}</strong>
        <span className="vgs-muted">{setup.done} of {setup.total} done</span>
        {setup.complete && <Button size="sm" variant="quiet" className="vgs-hide" onClick={hide}>Hide</Button>}
      </div>
      <progress className="vgs-bar" max={setup.total || 1} value={setup.done} aria-label="Setup progress" />
      <ul className="vgs-steps">
        {setup.steps.map((s) => (
          <li key={s.id} className={`vgs-step${s.done ? " vgs-step--done" : ""}`}>
            {s.done ? <CheckCircle2 size={18} aria-hidden="true" className="vgs-tick" /> : <Circle size={18} aria-hidden="true" className="vgs-todo" />}
            <span className="vgs-step-text">
              <span className="vgs-step-label">{s.label}{s.optional && <span className="vgs-opt"> Optional</span>}
                <span className="vgs-sr">{s.done ? " (done)" : " (to do)"}</span></span>
              {!s.done && <span className="vgs-muted">{s.why}</span>}
            </span>
            {!s.done && <Button size="sm" variant="secondary" Icon={ArrowRight} onClick={open(s)}
              aria-label={`${s.label}: open`}>Open</Button>}
          </li>))}
      </ul>
      {!setup.complete && <FillFromDocuments tenantId={tenantId} onFound={() => { setFound((n) => n + 1); load(); }} />}
      <ProfileDrafts tenantId={tenantId} refresh={found} onChanged={load} />
      <PlanLine plan={plan} />
    </Card>
  );
}
