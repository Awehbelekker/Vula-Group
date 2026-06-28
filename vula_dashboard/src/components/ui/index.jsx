/**
 * ui/index.jsx — Vula design-system primitives. Inline-style, token-driven (var(--*)),
 * accent-aware. Use these instead of ad-hoc per-component styling.
 */
import { T } from "../../theme/tokens";

export function Card({ children, style, pad = 16, hover = false, ...rest }) {
  return (
    <div {...rest} style={{
      background: T.surface, border: `1px solid ${T.border}`, borderRadius: T.rCard,
      boxShadow: T.shadowSm, padding: pad, transition: "box-shadow .15s, transform .15s",
      ...(hover ? { cursor: "pointer" } : {}), ...style,
    }}
      onMouseEnter={hover ? (e) => { e.currentTarget.style.boxShadow = T.shadowMd; } : undefined}
      onMouseLeave={hover ? (e) => { e.currentTarget.style.boxShadow = T.shadowSm; } : undefined}>
      {children}
    </div>
  );
}

const BTN = {
  primary: { background: T.accent, color: T.onAccent, border: "none" },
  soft: { background: T.accentSoft, color: T.accent, border: `1px solid ${T.accent}` },
  ghost: { background: "transparent", color: T.muted, border: `1px solid ${T.border}` },
  danger: { background: "transparent", color: T.danger, border: `1px solid ${T.border}` },
};
export function Button({ children, variant = "primary", size = "md", style, ...rest }) {
  const pad = size === "sm" ? "6px 12px" : size === "lg" ? "12px 22px" : "9px 16px";
  const fs = size === "sm" ? 12.5 : size === "lg" ? 15 : 13.5;
  return (
    <button {...rest} style={{
      padding: pad, fontSize: fs, fontWeight: 600, borderRadius: T.rInput, cursor: "pointer",
      fontFamily: T.body, transition: "filter .15s, opacity .15s", ...BTN[variant], ...style,
    }}
      onMouseEnter={(e) => { e.currentTarget.style.filter = "brightness(0.96)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.filter = "none"; }}>
      {children}
    </button>
  );
}

export function StatCard({ label, value, sub, color, accent = false }) {
  return (
    <Card pad="18px 20px">
      <div style={{ fontSize: 11, letterSpacing: ".1em", textTransform: "uppercase", color: T.muted, fontFamily: T.mono, marginBottom: 8 }}>{label}</div>
      <div className="vula-display" style={{ fontSize: 30, fontWeight: 700, lineHeight: 1, color: color || (accent ? T.accent : T.ink) }}>{value}</div>
      {sub && <div style={{ fontSize: 11.5, color: T.muted, marginTop: 6, fontFamily: T.mono }}>{sub}</div>}
    </Card>
  );
}

export function Badge({ children, tone = "muted", style }) {
  const tones = { accent: T.accent, ok: T.ok, warn: T.warn, danger: T.danger, info: T.info, muted: T.muted };
  const c = tones[tone] || T.muted;
  return (
    <span style={{
      padding: "3px 10px", borderRadius: T.rPill, fontSize: 11, fontWeight: 600,
      fontFamily: T.mono, color: c, background: tone === "accent" ? T.accentSoft : `${c}14`,
      border: `1px solid ${c}30`, ...style,
    }}>{children}</span>
  );
}

export function Field({ label, hint, children, style }) {
  return (
    <label style={{ display: "block", ...style }}>
      {label && <div style={{ fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase", color: T.muted, fontFamily: T.mono, marginBottom: 5 }}>{label}</div>}
      {children}
      {hint && <div style={{ fontSize: 11.5, color: T.muted, marginTop: 4 }}>{hint}</div>}
    </label>
  );
}

export const inputStyle = {
  width: "100%", padding: "9px 11px", border: `1px solid ${T.border}`, borderRadius: T.rInput,
  fontSize: 13.5, fontFamily: T.body, color: T.text, background: T.surface, boxSizing: "border-box",
};

export function SectionTitle({ children, sub }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <h1 className="vula-display" style={{ fontSize: 28, fontWeight: 700, margin: "0 0 2px" }}>{children}</h1>
      {sub && <p style={{ fontSize: 13, color: T.muted, margin: 0 }}>{sub}</p>}
    </div>
  );
}

export function EmptyState({ icon = "✦", title, children, action }) {
  return (
    <Card pad={40} style={{ textAlign: "center", maxWidth: 460, margin: "32px auto" }}>
      <div style={{ fontSize: 34, marginBottom: 12 }}>{icon}</div>
      <div className="vula-display" style={{ fontSize: 21, fontWeight: 700, color: T.ink, marginBottom: 8 }}>{title}</div>
      {children && <p style={{ fontSize: 13.5, color: T.muted, lineHeight: 1.55, margin: "0 0 18px" }}>{children}</p>}
      {action}
    </Card>
  );
}

/** Reusable first-run wizard shell (stepped). steps = [{title, render()}]. */
export function Wizard({ steps = [], step, setStep, onFinish, finishing }) {
  const s = steps[step] || {};
  const last = step >= steps.length - 1;
  return (
    <Card pad={28} style={{ maxWidth: 560, margin: "28px auto" }} className="vula-fade-up">
      <div style={{ display: "flex", gap: 6, marginBottom: 20 }}>
        {steps.map((_, i) => (
          <div key={i} style={{ flex: 1, height: 4, borderRadius: 2, background: i <= step ? T.accent : T.border, transition: "background .2s" }} />
        ))}
      </div>
      <div className="vula-display" style={{ fontSize: 22, fontWeight: 700, color: T.ink, marginBottom: 4 }}>{s.title}</div>
      {s.subtitle && <p style={{ fontSize: 13, color: T.muted, margin: "0 0 18px" }}>{s.subtitle}</p>}
      <div style={{ margin: "14px 0 22px" }}>{s.render && s.render()}</div>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <Button variant="ghost" size="sm" onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0}
          style={step === 0 ? { opacity: 0.4, cursor: "default" } : {}}>Back</Button>
        <Button onClick={() => (last ? onFinish?.() : setStep(step + 1))} disabled={finishing}>
          {finishing ? "Saving…" : last ? "Finish setup" : "Continue"}
        </Button>
      </div>
    </Card>
  );
}
