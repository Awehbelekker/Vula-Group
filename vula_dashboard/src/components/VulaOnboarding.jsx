import { useState, useEffect } from "react";

const TIERS = [
  {
    id: "starter",
    name: "Starter",
    label: "R1,500/mo",
    desc: "1–2 users. Core AI modules. Ideal for sole traders and small practices.",
    features: ["AI quote & proposal builder", "Invoice & billing", "Document intelligence (25 docs)", "Email drafting", "WhatsApp notifications"],
    color: "#8B7355",
    highlight: false,
  },
  {
    id: "growth",
    name: "Growth",
    label: "R3,500/mo",
    desc: "Up to 5 users. Full AI suite. Best for growing SMEs.",
    features: ["Everything in Starter", "Unlimited document intelligence", "CRM & client tracking", "Custom AI persona", "Priority support (24hr)"],
    color: "#C4922A",
    highlight: true,
  },
  {
    id: "business",
    name: "Business",
    label: "R7,500/mo",
    desc: "Unlimited users. White-label ready. For established businesses.",
    features: ["Everything in Growth", "White-label branding", "API access", "Dedicated Soul Box hardware", "Onsite setup & training"],
    color: "var(--accent)",
    highlight: false,
  },
];

const INDUSTRIES = [
  "Architecture & Property",
  "Construction & Engineering",
  "Managed Print & IT Services",
  "Watersports & Lifestyle",
  "Legal & Professional Services",
  "Healthcare & Wellness",
  "Retail & E-commerce",
  "Other",
];

// ─── Utilities ────────────────────────────────────────────────────────────────

function useTypewriter(text, speed = 28, start = true) {
  const [displayed, setDisplayed] = useState("");
  useEffect(() => {
    if (!start) return;
    setDisplayed("");
    let i = 0;
    const timer = setInterval(() => {
      if (i < text.length) { setDisplayed(text.slice(0, ++i)); }
      else clearInterval(timer);
    }, speed);
    return () => clearInterval(timer);
  }, [text, start]);
  return displayed;
}

function ProgressBar({ step, total }) {
  return (
    <div style={{ display: "flex", gap: 6, marginBottom: 40 }}>
      {Array.from({ length: total }).map((_, i) => (
        <div key={i} style={{
          flex: 1, height: 3,
          background: i <= step ? "#C4922A" : "#2A2A2A",
          borderRadius: 2,
          transition: "background 0.4s ease",
        }} />
      ))}
    </div>
  );
}

function Input({ label, value, onChange, placeholder, type = "text", hint }) {
  const [focused, setFocused] = useState(false);
  return (
    <div style={{ marginBottom: 20 }}>
      <label style={{ display: "block", fontSize: 11, letterSpacing: "0.12em", textTransform: "uppercase", color: "#888", marginBottom: 8, fontFamily: "monospace" }}>
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        style={{
          width: "100%", padding: "14px 16px",
          background: "#111", border: `1px solid ${focused ? "#C4922A" : "#2A2A2A"}`,
          borderRadius: 6, color: "#F0EDE8",
          fontSize: 15, outline: "none", boxSizing: "border-box",
          transition: "border-color 0.2s ease",
        }}
      />
      {hint && <p style={{ fontSize: 12, color: "#555", marginTop: 6, fontFamily: "monospace" }}>{hint}</p>}
    </div>
  );
}

function Select({ label, value, onChange, options }) {
  const [focused, setFocused] = useState(false);
  return (
    <div style={{ marginBottom: 20 }}>
      <label style={{ display: "block", fontSize: 11, letterSpacing: "0.12em", textTransform: "uppercase", color: "#888", marginBottom: 8, fontFamily: "monospace" }}>
        {label}
      </label>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        style={{
          width: "100%", padding: "14px 16px",
          background: "#111", border: `1px solid ${focused ? "#C4922A" : "#2A2A2A"}`,
          borderRadius: 6, color: value ? "#F0EDE8" : "#555",
          fontSize: 15, outline: "none", boxSizing: "border-box",
          appearance: "none", cursor: "pointer",
          transition: "border-color 0.2s ease",
        }}
      >
        <option value="" disabled>Select industry</option>
        {options.map(o => <option key={o} value={o} style={{ background: "#111" }}>{o}</option>)}
      </select>
    </div>
  );
}

function Btn({ children, onClick, disabled, ghost, style: extra }) {
  const [hovered, setHovered] = useState(false);
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        padding: "14px 28px", borderRadius: 6,
        border: ghost ? "1px solid #2A2A2A" : "none",
        fontSize: 14, fontWeight: 600, letterSpacing: "0.04em",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.4 : 1,
        transition: "background 0.2s, transform 0.1s",
        transform: hovered && !disabled ? "translateY(-1px)" : "none",
        background: ghost ? "transparent" : (hovered ? "#D4A23A" : "#C4922A"),
        color: ghost ? "#888" : "#0A0A0A",
        ...extra,
      }}
    >{children}</button>
  );
}

// ─── Steps ────────────────────────────────────────────────────────────────────

function StepIntro({ onNext }) {
  const headline = useTypewriter("AI that knows your business.", 35);
  return (
    <div style={{ textAlign: "center", padding: "40px 0" }}>
      <div style={{ display: "inline-block", padding: "6px 14px", border: "1px solid #C4922A33", borderRadius: 20, fontSize: 11, letterSpacing: "0.15em", color: "#C4922A", textTransform: "uppercase", marginBottom: 32, fontFamily: "monospace" }}>
        Vula — SA Business Intelligence
      </div>
      <h1 style={{ fontSize: "clamp(32px, 5vw, 52px)", fontWeight: 700, color: "#F0EDE8", lineHeight: 1.15, marginBottom: 20, minHeight: 66 }}>
        {headline}<span style={{ color: "#C4922A", opacity: headline.length > 0 ? 1 : 0 }}>_</span>
      </h1>
      <p style={{ color: "#888", fontSize: 16, lineHeight: 1.7, maxWidth: 480, margin: "0 auto 40px" }}>
        Vula learns your business — your pricing, your clients, your voice — and works as your AI team member. POPIA-compliant. ZAR-native. Built in Cape Town.
      </p>
      <div style={{ display: "flex", gap: 16, justifyContent: "center", flexWrap: "wrap", marginBottom: 48 }}>
        {["30-day free trial", "No credit card", "Cancel anytime"].map(t => (
          <div key={t} style={{ display: "flex", alignItems: "center", gap: 8, color: "#666", fontSize: 13, fontFamily: "monospace" }}>
            <span style={{ color: "#C4922A" }}>✓</span> {t}
          </div>
        ))}
      </div>
      <Btn onClick={onNext} style={{ padding: "16px 40px", fontSize: 15 }}>
        Get Started — Free →
      </Btn>
    </div>
  );
}

function StepPlan({ selected, onSelect, onNext, onBack }) {
  return (
    <div>
      <h2 style={{ fontSize: 28, color: "#F0EDE8", marginBottom: 8 }}>Choose your plan</h2>
      <p style={{ color: "#666", fontSize: 14, marginBottom: 32, fontFamily: "monospace" }}>All plans include a 30-day free trial. No card required.</p>
      <div style={{ display: "grid", gap: 16, marginBottom: 32 }}>
        {TIERS.map(tier => (
          <div
            key={tier.id}
            onClick={() => onSelect(tier.id)}
            style={{
              padding: "20px 24px", borderRadius: 8, cursor: "pointer",
              border: `1px solid ${selected === tier.id ? tier.color : "#2A2A2A"}`,
              background: selected === tier.id ? `${tier.color}11` : "#0D0D0D",
              transition: "all 0.2s ease", position: "relative", overflow: "hidden",
            }}
          >
            {tier.highlight && (
              <div style={{ position: "absolute", top: 0, right: 0, padding: "4px 12px", background: "#C4922A", fontSize: 10, letterSpacing: "0.1em", color: "#0A0A0A", fontWeight: 700, fontFamily: "monospace" }}>
                MOST POPULAR
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 16, color: "#F0EDE8" }}>{tier.name}</div>
                <div style={{ fontSize: 12, color: "#666", marginTop: 2, fontFamily: "monospace" }}>{tier.desc}</div>
              </div>
              <div style={{ fontSize: 20, color: tier.color, fontWeight: 700 }}>{tier.label}</div>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px", marginTop: 12 }}>
              {tier.features.map(f => (
                <span key={f} style={{ fontSize: 12, color: "#777", fontFamily: "monospace" }}>
                  <span style={{ color: tier.color }}>→</span> {f}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 12 }}>
        <Btn ghost onClick={onBack}>← Back</Btn>
        <Btn onClick={onNext} disabled={!selected}>Continue →</Btn>
      </div>
    </div>
  );
}

function StepBusiness({ data, onChange, onNext, onBack }) {
  const valid = data.companyName && data.industry && data.contactName && data.email;
  return (
    <div>
      <h2 style={{ fontSize: 28, color: "#F0EDE8", marginBottom: 8 }}>Tell us about your business</h2>
      <p style={{ color: "#666", fontSize: 14, marginBottom: 32, fontFamily: "monospace" }}>We use this to configure your AI from day one.</p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 20px" }}>
        <div style={{ gridColumn: "1 / -1" }}>
          <Input label="Company Name" value={data.companyName} onChange={v => onChange("companyName", v)} placeholder="e.g. Smith & Associates Architecture" />
        </div>
        <Select label="Industry" value={data.industry} onChange={v => onChange("industry", v)} options={INDUSTRIES} />
        <Input label="Number of Staff" value={data.staffCount} onChange={v => onChange("staffCount", v)} placeholder="e.g. 4" type="number" />
        <Input label="Your Full Name" value={data.contactName} onChange={v => onChange("contactName", v)} placeholder="e.g. Themba Dlamini" />
        <Input label="Work Email" value={data.email} onChange={v => onChange("email", v)} placeholder="you@company.co.za" type="email" />
        <div style={{ gridColumn: "1 / -1" }}>
          <Input label="WhatsApp (for notifications)" value={data.whatsapp} onChange={v => onChange("whatsapp", v)} placeholder="+27 82 000 0000" hint="We'll send you setup links and AI summaries via WhatsApp." />
        </div>
      </div>
      <div style={{ display: "flex", gap: 12, marginTop: 8 }}>
        <Btn ghost onClick={onBack}>← Back</Btn>
        <Btn onClick={onNext} disabled={!valid}>Continue →</Btn>
      </div>
    </div>
  );
}

function StepTraining({ data, onChange, onNext, onBack }) {
  const [dragOver, setDragOver] = useState(false);
  const [files, setFiles] = useState([]);

  const addFiles = (newFiles) => setFiles(prev => [...prev, ...newFiles].slice(0, 10));

  const painPoints = [
    "Writing proposals & quotes takes too long",
    "Staff spend hours on emails and follow-ups",
    "Hard to stay on top of client communications",
    "Invoicing and cashflow is manual and slow",
    "Can't find documents when I need them",
    "No time to train new staff properly",
  ];

  return (
    <div>
      <h2 style={{ fontSize: 28, color: "#F0EDE8", marginBottom: 8 }}>Train your AI</h2>
      <p style={{ color: "#666", fontSize: 14, marginBottom: 28, fontFamily: "monospace" }}>Upload documents so your AI learns how your business works.</p>

      <div
        onDrop={e => { e.preventDefault(); setDragOver(false); addFiles(Array.from(e.dataTransfer.files)); }}
        onDragOver={e => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onClick={() => document.getElementById("ob-file-input").click()}
        style={{
          border: `2px dashed ${dragOver ? "#C4922A" : "#2A2A2A"}`,
          borderRadius: 8, padding: "32px 24px", textAlign: "center",
          background: dragOver ? "#C4922A08" : "#0D0D0D",
          cursor: "pointer", marginBottom: 24, transition: "all 0.2s ease",
        }}
      >
        <div style={{ fontSize: 28, marginBottom: 8 }}>📄</div>
        <div style={{ color: "#F0EDE8", fontSize: 14, marginBottom: 4 }}>Drop your business documents here</div>
        <div style={{ color: "#555", fontSize: 12, fontFamily: "monospace" }}>Quotes, proposals, invoices, SOPs, price lists — PDF, Word, Excel</div>
        {files.length > 0 && (
          <div style={{ marginTop: 16, display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center" }}>
            {files.map((f, i) => (
              <span key={i} style={{ padding: "4px 10px", background: "#C4922A22", borderRadius: 4, fontSize: 11, color: "#C4922A", fontFamily: "monospace" }}>{f.name}</span>
            ))}
          </div>
        )}
        <input id="ob-file-input" type="file" multiple style={{ display: "none" }} onChange={e => addFiles(Array.from(e.target.files))} />
      </div>

      <div style={{ marginBottom: 28 }}>
        <label style={{ display: "block", fontSize: 11, letterSpacing: "0.12em", textTransform: "uppercase", color: "#888", marginBottom: 16, fontFamily: "monospace" }}>
          What are your biggest time-wasters?
        </label>
        <div style={{ display: "grid", gap: 8 }}>
          {painPoints.map(p => {
            const selected = data.painPoints?.includes(p);
            return (
              <div
                key={p}
                onClick={() => {
                  const cur = data.painPoints || [];
                  onChange("painPoints", selected ? cur.filter(x => x !== p) : [...cur, p]);
                }}
                style={{
                  padding: "12px 16px", borderRadius: 6, cursor: "pointer",
                  border: `1px solid ${selected ? "#C4922A" : "#1E1E1E"}`,
                  background: selected ? "#C4922A11" : "#0D0D0D",
                  color: selected ? "#F0EDE8" : "#666",
                  fontSize: 13, transition: "all 0.15s ease",
                  display: "flex", alignItems: "center", gap: 10,
                }}
              >
                <span style={{ color: selected ? "#C4922A" : "#333", fontSize: 16 }}>{selected ? "✓" : "○"}</span>
                {p}
              </div>
            );
          })}
        </div>
      </div>

      <div style={{ display: "flex", gap: 12 }}>
        <Btn ghost onClick={onBack}>← Back</Btn>
        <Btn onClick={onNext}>Continue →</Btn>
      </div>
    </div>
  );
}

function StepConfirm({ data, plan, onSubmit, onBack, loading, error }) {
  const tier = TIERS.find(t => t.id === plan);
  const [agreed, setAgreed] = useState(false);
  return (
    <div>
      <h2 style={{ fontSize: 28, color: "#F0EDE8", marginBottom: 8 }}>Review & activate</h2>
      <p style={{ color: "#666", fontSize: 14, marginBottom: 28, fontFamily: "monospace" }}>Your 30-day free trial starts today.</p>

      <div style={{ background: "#0D0D0D", border: "1px solid #2A2A2A", borderRadius: 8, padding: 24, marginBottom: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20, paddingBottom: 20, borderBottom: "1px solid #1E1E1E" }}>
          <div>
            <div style={{ fontSize: 20, color: "#F0EDE8", fontWeight: 700 }}>{data.companyName}</div>
            <div style={{ fontSize: 12, color: "#555", fontFamily: "monospace", marginTop: 4 }}>{data.industry} · {data.email}</div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 20, color: tier?.color, fontWeight: 700 }}>{tier?.label}</div>
            <div style={{ fontSize: 11, color: "#555", fontFamily: "monospace" }}>{tier?.name} plan</div>
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          {[
            { label: "Contact", value: data.contactName },
            { label: "WhatsApp", value: data.whatsapp || "Not provided" },
            { label: "Staff", value: `${data.staffCount || "?"} people` },
            { label: "Trial ends", value: new Date(Date.now() + 30 * 86400000).toLocaleDateString("en-ZA", { day: "numeric", month: "long", year: "numeric" }) },
          ].map(({ label, value }) => (
            <div key={label}>
              <div style={{ fontSize: 10, color: "#555", textTransform: "uppercase", letterSpacing: "0.1em", fontFamily: "monospace", marginBottom: 4 }}>{label}</div>
              <div style={{ fontSize: 14, color: "#CCC" }}>{value}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ background: "#0A0F0A", border: "1px solid #1A2A1A", borderRadius: 8, padding: 20, marginBottom: 24 }}>
        <div style={{ fontSize: 11, color: "#4A8A4A", textTransform: "uppercase", letterSpacing: "0.12em", fontFamily: "monospace", marginBottom: 12 }}>What happens next</div>
        {[
          "Your Vula workspace is provisioned instantly",
          "You'll receive a WhatsApp message with your login link",
          "Your AI begins processing your uploaded documents (2–4 hours)",
          "Richard or Judy will call within 24 hours to help you get started",
          "No billing until day 31 — cancel anytime before",
        ].map((step, i) => (
          <div key={i} style={{ display: "flex", gap: 12, marginBottom: 10 }}>
            <span style={{ color: "#4A8A4A", fontSize: 12, fontFamily: "monospace", minWidth: 20 }}>0{i + 1}</span>
            <span style={{ fontSize: 13, color: "#8A8A8A", lineHeight: 1.5 }}>{step}</span>
          </div>
        ))}
      </div>

      {error && (
        <div style={{ background: "#3A0A0A", border: "1px solid #8A2A2A", borderRadius: 6, padding: "12px 16px", marginBottom: 20, color: "#FF6B6B", fontSize: 13, fontFamily: "monospace" }}>
          {error}
        </div>
      )}

      <div onClick={() => setAgreed(!agreed)} style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 28, cursor: "pointer" }}>
        <div style={{ width: 18, height: 18, borderRadius: 4, border: `1px solid ${agreed ? "#C4922A" : "#2A2A2A"}`, background: agreed ? "#C4922A" : "transparent", flexShrink: 0, marginTop: 2, display: "flex", alignItems: "center", justifyContent: "center", transition: "all 0.15s ease" }}>
          {agreed && <span style={{ color: "#0A0A0A", fontSize: 11 }}>✓</span>}
        </div>
        <span style={{ fontSize: 12, color: "#666", fontFamily: "monospace", lineHeight: 1.6 }}>
          I agree to the Vula Group Terms of Service and Privacy Policy. I understand this is a 30-day free trial and I can cancel before billing begins.
        </span>
      </div>

      <div style={{ display: "flex", gap: 12 }}>
        <Btn ghost onClick={onBack}>← Back</Btn>
        <Btn onClick={onSubmit} disabled={!agreed || loading} style={{ flex: 1, justifyContent: "center" }}>
          {loading ? "Activating your workspace..." : "Activate Free Trial →"}
        </Btn>
      </div>
    </div>
  );
}

function StepSuccess({ data, plan, workspaceUrl, paymentUrl }) {
  const tier = TIERS.find(t => t.id === plan);
  const [count, setCount] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setCount(c => c < 100 ? c + 2 : 100), 20);
    return () => clearInterval(t);
  }, []);

  return (
    <div style={{ textAlign: "center", padding: "20px 0" }}>
      <div style={{ width: 80, height: 80, borderRadius: "50%", border: `3px solid ${count === 100 ? "#4A8A4A" : "#2A2A2A"}`, display: "flex", alignItems: "center", justifyContent: "center", margin: "0 auto 28px", background: count === 100 ? "#4A8A4A11" : "transparent", transition: "all 0.6s ease", fontSize: 32 }}>
        {count === 100 ? "✓" : <span style={{ fontSize: 16, color: "#555", fontFamily: "monospace" }}>{count}%</span>}
      </div>
      <div style={{ display: "inline-block", padding: "6px 14px", border: "1px solid #4A8A4A33", borderRadius: 20, fontSize: 11, letterSpacing: "0.15em", color: "#4A8A4A", textTransform: "uppercase", marginBottom: 20, fontFamily: "monospace" }}>
        Workspace Active
      </div>
      <h2 style={{ fontSize: 32, color: "#F0EDE8", marginBottom: 12, fontWeight: 700 }}>
        Welcome, {data.contactName?.split(" ")[0]}.
      </h2>
      <p style={{ color: "#666", fontSize: 15, lineHeight: 1.7, maxWidth: 440, margin: "0 auto 36px" }}>
        Your Vula workspace for <strong style={{ color: "#F0EDE8" }}>{data.companyName}</strong> is being configured on the {tier?.name} plan. Check your WhatsApp for the login link.
      </p>
      {workspaceUrl && (
        <div style={{ background: "#0D0D0D", border: "1px solid #2A2A2A", borderRadius: 8, padding: 16, marginBottom: 24, fontFamily: "monospace", fontSize: 13, color: "#C4922A" }}>
          {workspaceUrl}
        </div>
      )}
      <div style={{ background: "#0D0D0D", border: "1px solid #2A2A2A", borderRadius: 8, padding: 24, marginBottom: 32, textAlign: "left" }}>
        <div style={{ fontSize: 11, color: "#555", textTransform: "uppercase", letterSpacing: "0.12em", fontFamily: "monospace", marginBottom: 16 }}>Your next 24 hours</div>
        {[
          { time: "Right now", action: "Check WhatsApp — your login link is on its way" },
          { time: "Within 4 hours", action: "Your AI finishes learning your documents" },
          { time: "Tomorrow", action: "Richard or Judy calls to do your first AI demo with you" },
          { time: "Day 7", action: "Your first AI-generated quote or proposal — ready to send" },
        ].map(({ time, action }) => (
          <div key={time} style={{ display: "flex", gap: 16, marginBottom: 14 }}>
            <span style={{ fontSize: 10, color: "#C4922A", fontFamily: "monospace", minWidth: 90, paddingTop: 3 }}>{time}</span>
            <span style={{ fontSize: 13, color: "#AAA", lineHeight: 1.5 }}>{action}</span>
          </div>
        ))}
      </div>
      {paymentUrl && (
        <div style={{ marginBottom: 28 }}>
          <a
            href={paymentUrl}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: "block", padding: "16px 32px",
              background: "#C4922A", color: "#fff",
              borderRadius: 10, textDecoration: "none",
              fontSize: 15, fontWeight: 700, letterSpacing: "0.03em",
              textAlign: "center", transition: "opacity 0.15s",
            }}
          >
            Complete Subscription via PayFast →
          </a>
          <p style={{ fontSize: 11, color: "#444", fontFamily: "monospace", marginTop: 8 }}>
            Secure monthly billing · Cancel anytime · No lock-in
          </p>
        </div>
      )}

      <p style={{ fontSize: 12, color: "#444", fontFamily: "monospace" }}>
        Questions? WhatsApp us: <span style={{ color: "#C4922A" }}>{import.meta.env.VITE_TEAM_WHATSAPP || "+27 82 000 0000"}</span>
      </p>
    </div>
  );
}

// ─── Main ─────────────────────────────────────────────────────────────────────

export default function VulaOnboarding() {
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
      const resp = await fetch("/api/v1/onboard", {
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
      setWorkspaceUrl(data.workspace_url);
      if (data.payment_url) setPaymentUrl(data.payment_url);
      setStep(5);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=DM+Sans:wght@400;500;600;700&display=swap');
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        ::selection { background: #C4922A33; color: #F0EDE8; }
      `}</style>

      <div style={{ minHeight: "100vh", background: "#080808", color: "#F0EDE8", fontFamily: "'DM Sans', system-ui, sans-serif", display: "flex", alignItems: "center", justifyContent: "center", padding: "24px 16px" }}>
        <div style={{ position: "fixed", inset: 0, zIndex: 0, pointerEvents: "none", backgroundImage: "radial-gradient(ellipse at 20% 20%, #C4922A08 0%, transparent 60%), radial-gradient(ellipse at 80% 80%, var(--accent)06 0%, transparent 60%)" }} />

        <div style={{ position: "relative", zIndex: 1, width: "100%", maxWidth: 580 }}>
          {/* Logo */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 40 }}>
            <div style={{ width: 32, height: 32, borderRadius: 8, background: "var(--accent)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, fontWeight: 700, color: "#fff" }}>V</div>
            <span style={{ fontSize: 15, fontWeight: 600, color: "#F0EDE8" }}>Vula</span>
            <span style={{ marginLeft: "auto", fontSize: 11, color: "#444", fontFamily: "monospace" }}>by Vula Group</span>
          </div>

          {step > 0 && step < 5 && <ProgressBar step={step - 1} total={4} />}

          <div style={{ background: "#0E0E0E", border: "1px solid #1E1E1E", borderRadius: 12, padding: "clamp(24px, 5vw, 44px)", boxShadow: "0 24px 80px rgba(0,0,0,0.6)" }}>
            {step === 0 && <StepIntro onNext={() => setStep(1)} />}
            {step === 1 && <StepPlan selected={plan} onSelect={setPlan} onNext={() => setStep(2)} onBack={() => setStep(0)} />}
            {step === 2 && <StepBusiness data={business} onChange={updateBusiness} onNext={() => setStep(3)} onBack={() => setStep(1)} />}
            {step === 3 && <StepTraining data={business} onChange={updateBusiness} onNext={() => setStep(4)} onBack={() => setStep(2)} />}
            {step === 4 && <StepConfirm data={business} plan={plan} onSubmit={handleSubmit} onBack={() => setStep(3)} loading={loading} error={error} />}
            {step === 5 && <StepSuccess data={business} plan={plan} workspaceUrl={workspaceUrl} paymentUrl={paymentUrl} />}
          </div>

          {step < 5 && (
            <div style={{ display: "flex", justifyContent: "center", gap: 24, marginTop: 24, flexWrap: "wrap" }}>
              {["🔒 POPIA Compliant", "🇿🇦 ZAR Billing via PayFast", "📍 Cape Town Built"].map(t => (
                <span key={t} style={{ fontSize: 11, color: "#3A3A3A", fontFamily: "monospace" }}>{t}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
