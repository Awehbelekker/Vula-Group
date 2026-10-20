/**
 * useOnboardWizard.js — Master HQ's "Onboard Client" wizard (shared by VulaOnboarding and
 * OnboardWizardV2): plan, business details, documents to train on, then provisioning the trial
 * workspace (POST /v1/onboard) and uploading the documents to it. Moved verbatim out of
 * VulaOnboarding.jsx; plan prices are the published list prices.
 */
import { useState } from "react";
import { VULA_API } from "../../lib/authFetch";

export const TIER_INFO = [
  {
    id: "starter",
    name: "Starter",
    label: "R1,500/mo",
    desc: "1–2 users. Core AI modules. Ideal for sole traders and small practices.",
    features: ["AI quote & proposal builder", "Invoice & billing", "Document intelligence (25 docs)", "Email drafting", "WhatsApp notifications"],
    highlight: false,
  },
  {
    id: "growth",
    name: "Growth",
    label: "R3,500/mo",
    desc: "Up to 5 users. Full AI suite. Best for growing SMEs.",
    features: ["Everything in Starter", "Unlimited document intelligence", "CRM & client tracking", "Custom AI persona", "Priority support (24hr)"],
    highlight: true,
  },
  {
    id: "business",
    name: "Business",
    label: "R7,500/mo",
    desc: "Unlimited users. White-label ready. For established businesses.",
    features: ["Everything in Growth", "White-label branding", "API access", "Dedicated Soul Box hardware", "Onsite setup & training"],
    highlight: false,
  },
];

export const INDUSTRIES = [
  "Architecture & Property",
  "Construction & Engineering",
  "Managed Print & IT Services",
  "Watersports & Lifestyle",
  "Legal & Professional Services",
  "Healthcare & Wellness",
  "Retail & E-commerce",
  "Other",
];

export const PAIN_POINTS = [
  "Writing proposals & quotes takes too long",
  "Staff spend hours on emails and follow-ups",
  "Hard to stay on top of client communications",
  "Invoicing and cashflow is manual and slow",
  "Can't find documents when I need them",
  "No time to train new staff properly",
];

export function useOnboardWizard() {
  const [step, setStep] = useState(0);
  const [plan, setPlan] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [workspaceUrl, setWorkspaceUrl] = useState("");
  const [paymentUrl, setPaymentUrl] = useState("");
  const [business, setBusiness] = useState({
    companyName: "", industry: "", staffCount: "",
    contactName: "", email: "", whatsapp: "", painPoints: [],
  });

  const updateBusiness = (key, val) => setBusiness(prev => ({ ...prev, [key]: val }));

  const handleSubmit = async () => {
    setLoading(true);
    setError("");
    try {
      // Absolute API URL so the global auth wrapper (lib/authFetch.js) attaches the master's
      // token — the relative /api path skipped it and the backend answered 401.
      const resp = await fetch(`${VULA_API}/v1/onboard`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          company_name: business.companyName,
          industry: business.industry,
          staff_count: business.staffCount ? parseInt(business.staffCount, 10) : null,
          contact_name: business.contactName,
          email: business.email,
          whatsapp: business.whatsapp || null,
          pain_points: business.painPoints,
          plan,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || `Server error ${resp.status}`);
      }
      const data = await resp.json();
      const docs = business.files || [];
      if (docs.length && data.workspace_slug) {
        const fd = new FormData();
        fd.append("tenant_id", data.workspace_slug);
        docs.forEach(f => fd.append("files", f));
        try {
          const up = await fetch(`${VULA_API}/v1/onboard/documents`, { method: "POST", body: fd });
          if (!up.ok) setError("Workspace created, but the documents didn't upload — add them from the Documents tab.");
        } catch {
          setError("Workspace created, but the documents didn't upload — add them from the Documents tab.");
        }
      }
      setWorkspaceUrl(data.workspace_url);
      if (data.payment_url) setPaymentUrl(data.payment_url);
      setStep(5);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };
  return { step, setStep, plan, setPlan, loading, setLoading, error, setError, workspaceUrl, setWorkspaceUrl, paymentUrl, setPaymentUrl, business, setBusiness, updateBusiness, handleSubmit };
}
