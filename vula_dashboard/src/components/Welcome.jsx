/**
 * Welcome.jsx — the first-run welcome on Home (12 Nov 2026).
 *
 * Ian: Vula should "showcase its capabilities and strength from the beginning" — but "not all
 * clients would want to do this in trial", so the first thing is a choice, not a form:
 *   - "Just my website" (the default): one address, one button.
 *   - "Website and documents": also the company profile, terms, price list or menu.
 *   - "Skip for now": closed for the whole business; set-up stays on "Finish setting up".
 * Then Vula reads (live progress), shows what it found — brand, products and photos, which
 * policies and contact details it has, the profile answers to keep — and lets the owner ask it
 * a customer's question and see the answer with where it came from.
 *
 * Calls: GET/POST /admin/setup/first-run…, POST /admin/setup-from-website (as "Make it yours"),
 * useDocumentSetup.js, ProfileDrafts.jsx, POST /admin/setup/preview-answer.
 * Owners and managers only, for a business's first 14 days (the server decides `show`).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Check, CircleDashed, FileText, Globe, MessageCircleQuestion, Send, Sparkles, Upload, X } from "lucide-react";
import { VULA_API } from "../lib/authFetch";
import { useAuthStore } from "../store/auth";
import { Button, Card, Field, Input } from "./ui/index.jsx";
import ProfileDrafts from "./ProfileDrafts.jsx";
import { DOC_ACCEPT, fileLine, useDocumentSetup } from "./useDocumentSetup.js";
import "./gettingStarted.css";

const LEADS = ["owner", "manager", "admin", "master"];
const POLICY_LABEL = { terms: "Terms", privacy: "Privacy policy", returns: "Returns policy", shipping: "Delivery policy" };
const CONTACT_LABEL = { phone: "Phone", email: "Email", address: "Address", hours: "Opening hours", socials: "Social pages" };

function Found({ ok, children }) {
  return (
    <li className={`vwl-found${ok ? "" : " vwl-found--no"}`}>
      {ok ? <Check size={16} aria-hidden="true" /> : <CircleDashed size={16} aria-hidden="true" />}
      <span>{children}</span>
    </li>
  );
}

/** What Vula found, as ticks and honest gaps. */
function WhatWasFound({ s }) {
  const w = s.website || {};
  const cov = w.coverage || {};
  const pr = cov.products || {};
  const items = [];
  if (s.brand?.set) items.push(<Found key="brand" ok>Your logo and colours</Found>);
  if (w.url) {
    if (pr.found) {
      items.push(<Found key="products" ok>{pr.found} products{pr.with_image ? `, ${pr.with_image} with photos` : ""}{pr.with_description ? `, ${pr.with_description} with descriptions` : ""}</Found>);
    }
    if (cov.pages) items.push(<Found key="pages" ok={cov.pages.used > 0}>{cov.pages.used} pages of your website, for Vula to answer from</Found>);
    for (const [k, label] of Object.entries(POLICY_LABEL)) {
      if (k in (cov.policies || {})) items.push(<Found key={k} ok={!!cov.policies[k]}>{cov.policies[k] ? label : `${label}: not found on the website`}</Found>);
    }
    const contact = Object.entries(cov.contact || {});
    const have = contact.filter(([, v]) => v).map(([k]) => CONTACT_LABEL[k]);
    const missing = contact.filter(([, v]) => !v).map(([k]) => CONTACT_LABEL[k]);
    if (have.length) items.push(<Found key="contact" ok>{have.join(", ")}</Found>);
    if (missing.length) items.push(<Found key="nocontact" ok={false}>Not found: {missing.join(", ").toLowerCase()}</Found>);
    if (cov.pdfs?.length) items.push(<Found key="pdfs" ok>Read from the website's PDFs: {cov.pdfs.join(", ")}</Found>);
  }
  for (const f of s.documents?.files || []) {
    items.push(<Found key={`doc-${f.name}`} ok={f.status === "done"}>{f.name}: {fileLine(f)}</Found>);
  }
  if (!items.length) return null;
  return (
    <section aria-label="What Vula found">
      <ul className="vwl-list">{items}</ul>
      {w.note && <p className="vgs-muted">{w.note}</p>}
      {w.error && <p className="vgs-warn" role="alert">The website couldn't be read completely: {w.error}</p>}
    </section>
  );
}

/** "Ask Vula like a customer would": the answer, and where it came from. */
function TryIt({ tenantId, suggestions }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const ask = async (text) => {
    const question = (text ?? q).trim();
    if (!question) return;
    setQ(question); setBusy(true); setErr(""); setRes(null);
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/setup/preview-answer`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(d.detail || "Vula couldn't answer just now."); return; }
      setRes(d);
    } catch { setErr("Couldn't reach Vula. Please try again."); } finally { setBusy(false); }
  };
  const notKept = suggestions.some((t) => !t.ready);
  return (
    <section className="vwl-try" aria-label="Ask Vula like a customer would">
      <strong className="vgs-title"><MessageCircleQuestion size={16} aria-hidden="true" />Ask Vula like a customer would</strong>
      {notKept && <p className="vgs-muted">Keep the answers above first: Vula only tells customers what you've kept.</p>}
      {suggestions.length > 0 && (
        <div className="vgs-fill-row">
          {suggestions.map((t) => <Button key={t.key} size="sm" variant="secondary" disabled={busy} onClick={() => ask(t.question)}>{t.question}</Button>)}
        </div>)}
      <form className="vgs-fill-row" onSubmit={(e) => { e.preventDefault(); ask(); }}>
        <Input className="vwl-q" value={q} maxLength={300} onChange={(e) => setQ(e.target.value)}
          aria-label="A question a customer might ask" placeholder="e.g. Do you deliver on Saturdays?" />
        <Button type="submit" Icon={Send} disabled={busy || !q.trim()}>{busy ? "Asking…" : "Ask"}</Button>
      </form>
      {res && (
        <div className="vwl-answer" role="status">
          <p className="vpd-answer">{res.answer}</p>
          <span className="vpd-source">
            {res.sources ? <><FileText size={14} aria-hidden="true" />From: {res.sources}</>
              : "Vula didn't find this in anything you've given it, so it says so instead of guessing. Add it under Settings › Business profile."}
          </span>
        </div>)}
      {err && <p role="alert" className="vgs-warn">{err}</p>}
    </section>
  );
}

export default function Welcome({ tenantId, onOpenChange }) {
  const role = useAuthStore((s) => s.role);
  const [s, setS] = useState(null);
  const [mode, setMode] = useState("website");          // "website" | "both"
  const [url, setUrl] = useState("");
  const [files, setFiles] = useState([]);
  const [starting, setStarting] = useState(false);
  const [started, setStarted] = useState(false);
  const [err, setErr] = useState("");
  const [found, setFound] = useState(0);
  const input = useRef(null);
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin/setup`;

  const load = useCallback(() => fetch(`${base}/first-run`).then((r) => (r.ok ? r.json() : null))
    .then((d) => setS(d)).catch(() => setS(null)), [base]);
  useEffect(() => { if (LEADS.includes(role)) load(); }, [load, role]);
  const docs = useDocumentSetup(tenantId, () => { setFound((n) => n + 1); load(); });

  const open = !!(s && s.show && LEADS.includes(role));
  useEffect(() => { onOpenChange?.(open); }, [open, onOpenChange]);
  // While anything is being read, follow it.
  const running = !!(s?.running || docs.reading);
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [running, load]);

  if (!open) return null;

  const close = async (reason) => {
    try {
      await fetch(`${base}/first-run/close`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason }) });
    } catch { /* it shows again next time; nothing lost */ }
    setS((x) => ({ ...x, show: false, closed: reason }));
  };

  const start = async (e) => {
    e.preventDefault();
    if (!url.trim() && !(mode === "both" && files.length)) { setErr(mode === "both" ? "Add your website, a document, or both." : "Add your website address."); return; }
    setStarting(true); setErr("");
    try {
      if (url.trim()) {
        const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/setup-from-website`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: url.trim() }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { setErr(d.detail || "Couldn't read that address."); return; }
        if (d.brand?.applied) window.dispatchEvent(new Event("vula-brand-changed"));
      }
      if (mode === "both" && files.length) await docs.upload(files);
      setStarted(true);
      load();
    } catch { setErr("Couldn't reach Vula. Please try again."); } finally { setStarting(false); }
  };

  const hasResults = !!(s.website?.url || s.documents?.files?.length);
  const showForm = !started && !running && !hasResults;
  const w = s.website || {};

  return (
    <Card role="region" aria-label="Let Vula set you up" className="vgs vwl">
      <div className="vgs-head">
        <strong className="vgs-title"><Sparkles size={16} aria-hidden="true" />Let Vula set you up</strong>
        {showForm && <Button size="sm" variant="quiet" Icon={X} className="vgs-hide" onClick={() => close("skipped")}>Skip for now</Button>}
      </div>

      {showForm && (
        <form className="vwl-form" onSubmit={start}>
          <p className="vgs-muted">Give Vula your website and it brings in your products, photos, policies, hours and contact details, so you don't type them. It takes a minute or two. You can skip this and do it any time from Finish setting up.</p>
          <div className="vwl-modes" role="radiogroup" aria-label="What to give Vula">
            {[["website", "Just my website"], ["both", "Website and documents"]].map(([id, label]) => (
              <button key={id} type="button" role="radio" aria-checked={mode === id}
                className={`vwl-mode${mode === id ? " vwl-mode--on" : ""}`} onClick={() => setMode(id)}>{label}</button>))}
          </div>
          <Field label="Your website">
            <Input type="text" inputMode="url" autoComplete="url" placeholder="yourbusiness.co.za" value={url} onChange={(e) => setUrl(e.target.value)} />
          </Field>
          {mode === "both" && (
            <div className="vgs-fill-row">
              <input ref={input} type="file" multiple accept={DOC_ACCEPT} hidden aria-label="Choose company documents"
                onChange={(e) => { setFiles([...(e.target.files || [])]); e.target.value = ""; }} />
              <Button type="button" size="sm" variant="secondary" Icon={Upload} onClick={() => input.current?.click()}>Add documents</Button>
              <span className="vgs-muted">{files.length ? files.map((f) => f.name).join(", ") : "Company profile, terms, price list, menu or FAQ"}</span>
            </div>)}
          <div className="vgs-fill-row">
            <Button type="submit" Icon={Globe} disabled={starting}>{starting ? "Starting…" : "Set me up"}</Button>
            <Button type="button" variant="quiet" onClick={() => close("skipped")}>I'll do it myself</Button>
          </div>
          {(err || docs.err) && <p role="alert" className="vgs-warn">{err || docs.err}</p>}
        </form>)}

      {!showForm && (
        <>
          {running && (
            <p className="vgs-muted" aria-live="polite">
              Reading{w.running && w.step ? `: ${w.step}` : " your documents"}
              {w.products_found ? ` · ${w.products_found} products so far` : ""}
              {w.pages_read ? ` · ${w.pages_read} pages read` : ""}…
            </p>)}
          <WhatWasFound s={s} />
          <ProfileDrafts tenantId={tenantId} refresh={found + (running ? 0 : 1)} onChanged={load} />
          {!running && <TryIt tenantId={tenantId} suggestions={s.try_asking || []} />}
          <div className="vgs-fill-row">
            <Button onClick={() => close("done")} disabled={running}>{running ? "Still reading…" : "Done — take me to my dashboard"}</Button>
          </div>
        </>)}
    </Card>
  );
}
