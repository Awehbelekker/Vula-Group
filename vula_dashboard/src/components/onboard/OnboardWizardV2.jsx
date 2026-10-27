/**
 * OnboardWizardV2.jsx — the redesigned Master HQ › Onboard Client (2026-10-20, UX redesign
 * Phase F). Same flow as VulaOnboarding (onboard/useOnboardWizard.js): plan, business details,
 * documents to train on, review, then provisioning the 14-day trial workspace. Activating needs
 * the terms ticked, on a review step that shows exactly what will be created.
 */
import { useState } from "react";
import { ArrowLeft, ArrowRight, Check, CheckCircle2, ExternalLink, FileUp, Rocket } from "lucide-react";
import { SA_TZ } from "../../lib/dates";
import { Button, Card, Field, Input, Select, StatusChip } from "../ui/index.jsx";
import { INDUSTRIES, PAIN_POINTS, TIER_INFO, useOnboardWizard } from "./useOnboardWizard";
import "../master/hq.css";
import "./onboard.css";

const STEPS = ["Plan", "Business", "Documents", "Review"];

export default function OnboardWizardV2() {
  const x = useOnboardWizard();
  const s = x.step;
  const nav = (back, next, nextOk = true, nextLabel = "Continue") => (
    <div className="vhq-row">
      {back !== null && <Button variant="quiet" Icon={ArrowLeft} onClick={() => x.setStep(back)}>Back</Button>}
      {next !== null && <Button Icon={ArrowRight} disabled={!nextOk} onClick={() => x.setStep(next)}>{nextLabel}</Button>}
    </div>);
  return (
    <section className="vob" aria-label="Onboard a client">
      {s >= 1 && s <= 4 && (
        <ol className="vob-steps" aria-label="Steps">
          {STEPS.map((label, i) => <li key={label} data-done={i < s - 1} aria-current={i === s - 1 ? "step" : undefined}>{label}</li>)}
        </ol>)}
      <Card className="vob">
        {s === 0 && (
          <div className="vob">
            <h2 className="vob-h">Set a client up on Vula</h2>
            <p className="vhq-sub">Vula learns their business (pricing, clients, voice) and works as an AI team member. 14-day free trial from when their WhatsApp goes live, no card, cancel any time. POPIA-aligned, billed in rand.</p>
            <div><Button Icon={ArrowRight} onClick={() => x.setStep(1)}>Start</Button></div>
          </div>)}
        {s === 1 && (
          <div className="vob">
            <h2 className="vob-h">Choose a plan</h2>
            <p className="vhq-muted">Every plan starts with a 14-day free trial, counted from when the WhatsApp number goes live.</p>
            <div className="vob-tiers" role="radiogroup" aria-label="Plan">
              {TIER_INFO.map((t) => (
                <button key={t.id} type="button" role="radio" aria-checked={x.plan === t.id} className="vob-tier" onClick={() => x.setPlan(t.id)}>
                  <span className="vob-tier-head"><b style={{ fontSize: "var(--fs-title)" }}>{t.name}</b><span>{t.label}</span>{t.highlight && <StatusChip tone="highlight">Most popular</StatusChip>}</span>
                  <span className="vhq-muted">{t.desc}</span>
                  <ul className="vob-features">{t.features.map((f) => <li key={f}><Check size={12} aria-hidden="true" />{f}</li>)}</ul>
                </button>))}
            </div>
            {nav(0, 2, !!x.plan)}
          </div>)}
        {s === 2 && <Business x={x} nav={nav} />}
        {s === 3 && <Documents x={x} nav={nav} />}
        {s === 4 && <Review x={x} />}
        {s === 5 && <Done x={x} />}
      </Card>
    </section>
  );
}

function Business({ x, nav }) {
  const b = x.business;
  const set = (k) => (e) => x.updateBusiness(k, e.target.value);
  const valid = b.companyName && b.industry && b.contactName && b.email;
  return (
    <form className="vob" onSubmit={(e) => { e.preventDefault(); if (valid) x.setStep(3); }}>
      <h2 className="vob-h">About the business</h2>
      <p className="vhq-muted">Used to set up their assistant from day one.</p>
      <Field label="Company name"><Input value={b.companyName} onChange={set("companyName")} autoFocus /></Field>
      <div className="vhq-tiles" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 220px), 1fr))" }}>
        <Field label="Industry"><Select value={b.industry} onChange={set("industry")}><option value="">Choose…</option>{INDUSTRIES.map((o) => <option key={o}>{o}</option>)}</Select></Field>
        <Field label="Staff" hint="Optional"><Input type="number" min="1" value={b.staffCount} onChange={set("staffCount")} /></Field>
        <Field label="Contact's full name"><Input value={b.contactName} onChange={set("contactName")} /></Field>
        <Field label="Work email"><Input type="email" value={b.email} onChange={set("email")} /></Field>
      </div>
      <Field label="WhatsApp" hint="Optional: their login link and setup messages go here"><Input type="tel" value={b.whatsapp} onChange={set("whatsapp")} /></Field>
      {nav(1, 3, !!valid)}
    </form>
  );
}

function Documents({ x, nav }) {
  const [over, setOver] = useState(false);
  const files = x.business.files || [];
  const add = (list) => x.updateBusiness("files", [...files, ...list].slice(0, 10));
  const pains = x.business.painPoints || [];
  return (
    <div className="vob">
      <h2 className="vob-h">Documents to learn from</h2>
      <p className="vhq-muted">Quotes, proposals, invoices, SOPs and price lists, the way you'd brief a new hire. Up to 10 files; they upload once the workspace exists.</p>
      <label className={`vob-drop${over ? " vob-drop--over" : ""}`}
        onDrop={(e) => { e.preventDefault(); setOver(false); add(Array.from(e.dataTransfer.files)); }}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}>
        <FileUp size={24} aria-hidden="true" />
        <b>Choose files or drop them here</b>
        <span className="vhq-muted">PDF, Word or Excel</span>
        <input type="file" multiple className="vob-file" onChange={(e) => add(Array.from(e.target.files))} />
      </label>
      {files.length > 0 && <div className="vhq-row" aria-label="Chosen files">{files.map((f, i) => <StatusChip key={i} outline>{f.name}</StatusChip>)}</div>}
      <fieldset style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
        <legend className="vhq-overline" style={{ marginBottom: "var(--space-2)" }}>Their biggest time-wasters</legend>
        <ul className="vob-checks">
          {PAIN_POINTS.map((p) => (
            <li key={p}><label className="vob-check">
              <input type="checkbox" checked={pains.includes(p)} onChange={() => x.updateBusiness("painPoints", pains.includes(p) ? pains.filter((y) => y !== p) : [...pains, p])} />{p}
            </label></li>))}
        </ul>
      </fieldset>
      {nav(2, 4)}
    </div>
  );
}

function Review({ x }) {
  const [agreed, setAgreed] = useState(false);
  const b = x.business;
  const tier = TIER_INFO.find((t) => t.id === x.plan);
  const ends = new Date(Date.now() + 30 * 86400000).toLocaleDateString("en-ZA", { day: "numeric", month: "long", year: "numeric", timeZone: SA_TZ });
  return (
    <div className="vob">
      <h2 className="vob-h">Review and activate</h2>
      <div className="vhq-row"><span className="vhq-grow"><b>{b.companyName}</b><span className="vhq-muted">{b.industry} · {b.email}</span></span>
        <span className="vhq-grow" style={{ flex: "0 0 auto", alignItems: "flex-end" }}><b>{tier?.label}</b><span className="vhq-muted">{tier?.name} plan</span></span></div>
      <dl className="vhq-detail" style={{ padding: 0 }}>
        <div><dt>Contact</dt><dd>{b.contactName}</dd></div>
        <div><dt>WhatsApp</dt><dd>{b.whatsapp || "Not given"}</dd></div>
        <div><dt>Staff</dt><dd>{b.staffCount || "?"} people</dd></div>
        <div><dt>Trial ends</dt><dd>{ends}</dd></div>
        <div><dt>Documents</dt><dd>{(b.files || []).length}</dd></div>
      </dl>
      <h3 className="vhq-overline">What happens next</h3>
      <ol className="vob-next">
        <li>The Vula workspace is created straight away.</li>
        <li>They get a WhatsApp message with their login link.</li>
        <li>Vula reads their documents (2–4 hours).</li>
        <li>Richard or Judy calls within 24 hours to help them start.</li>
        <li>No billing until day 31; they can cancel before then.</li>
      </ol>
      {x.error && <p className="vhq-error" role="alert">{x.error}</p>}
      <label className="vob-check" style={{ alignItems: "flex-start", paddingTop: "var(--space-2)" }}>
        <input type="checkbox" checked={agreed} onChange={() => setAgreed((a) => !a)} style={{ marginTop: 2 }} />
        <span>They agree to the Vula Group Terms of Service and Privacy Policy, and understand this is a 14-day free trial they can cancel before billing starts.</span>
      </label>
      <div className="vhq-row">
        <Button variant="quiet" Icon={ArrowLeft} onClick={() => x.setStep(3)}>Back</Button>
        <Button Icon={Rocket} disabled={!agreed || x.loading} onClick={x.handleSubmit}>{x.loading ? "Creating the workspace…" : "Activate the free trial"}</Button>
      </div>
    </div>
  );
}

function Done({ x }) {
  const b = x.business;
  const tier = TIER_INFO.find((t) => t.id === x.plan);
  return (
    <div className="vob-done" role="status">
      <span className="vob-done-ic"><CheckCircle2 size={32} aria-hidden="true" /></span>
      <StatusChip tone="ok">Workspace active</StatusChip>
      <h2 className="vob-h">Welcome, {b.contactName?.split(" ")[0]}.</h2>
      <p className="vhq-sub">The workspace for <b>{b.companyName}</b> is being set up on the {tier?.name} plan. The login link is on its way to their WhatsApp.</p>
      {x.workspaceUrl && <span className="vob-url">{x.workspaceUrl}</span>}
      {x.error && <p className="vhq-error" role="alert">{x.error}</p>}
      <dl className="vob-timeline">
        <dt>Right now</dt><dd>Their login link arrives on WhatsApp.</dd>
        <dt>Within 4 hours</dt><dd>Vula finishes reading their documents.</dd>
        <dt>Tomorrow</dt><dd>Richard or Judy runs a first demo with them.</dd>
        <dt>Day 7</dt><dd>Their first AI-drafted quote or proposal, ready to send.</dd>
      </dl>
      {x.paymentUrl && <a className="vob-pay" href={x.paymentUrl} target="_blank" rel="noopener noreferrer">Complete the subscription on PayFast <ExternalLink size={16} aria-hidden="true" /></a>}
      <p className="vhq-muted">Questions? WhatsApp {import.meta.env.VITE_TEAM_WHATSAPP || "+27 82 000 0000"}</p>
    </div>
  );
}
