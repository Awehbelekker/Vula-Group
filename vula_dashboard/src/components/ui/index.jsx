/**
 * ui/index.jsx — Vula design-system primitives. Inline-style, token-driven (var(--*)),
 * accent-aware. Use these instead of ad-hoc per-component styling.
 */
import { useEffect, useState } from "react";
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
      fontFamily: T.mono, color: c, background: tone === "accent" ? T.accentSoft : `color-mix(in srgb, ${c} 8%, transparent)`,
      border: `1px solid color-mix(in srgb, ${c} 19%, transparent)`, ...style,
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

/** The one "sub-tab strip" primitive for a section with depth — pair with useSectionTabs. */
export function SectionTabs({ tabs, active, onChange }) {
  if (!tabs?.length) return null;
  return (
    <div className="vula-tabs" role="tablist" style={{ display: "flex", gap: 4, marginBottom: 14, borderBottom: `1px solid ${T.border}`, paddingBottom: 8, flexWrap: "wrap" }}>
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={active === t.id} onClick={() => onChange(t.id)} style={{
          padding: "7px 14px", border: "none", borderRadius: T.rPill, fontSize: 13, fontWeight: 600, whiteSpace: "nowrap",
          cursor: "pointer", fontFamily: T.body,
          background: active === t.id ? T.accentSoft : "transparent",
          color: active === t.id ? T.accent : T.muted,
        }}>
          {t.icon ? `${t.icon} ` : ""}{t.label}
        </button>
      ))}
    </div>
  );
}

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

/* ── Page structure (2026-09-29: every page the same shape) ──────────────────────────────── */

/** A page's own heading row: title, one line of help, and its actions on the right. The shell
 * already shows the section name, so pages use this for their own title + actions only. */
export function PageHeader({ title, sub, actions, style }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: 12, marginBottom: 16, ...style }}>
      <div style={{ flex: "1 1 240px", minWidth: 0 }}>
        {title && <h2 className="vula-display" style={{ fontSize: 22, fontWeight: 600, margin: 0, color: T.ink }}>{title}</h2>}
        {sub && <p style={{ fontSize: 13, color: T.muted, margin: "4px 0 0", lineHeight: 1.5 }}>{sub}</p>}
      </div>
      {actions && <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{actions}</div>}
    </div>
  );
}

/** Wide content (tables, fixed grids) scrolls sideways on a phone instead of squashing. */
export function ScrollX({ children, style }) {
  return <div className="vula-scroll-x" style={{ overflowX: "auto", WebkitOverflowScrolling: "touch", ...style }}>{children}</div>;
}

/** columns: [{key, label, align?, width?, render?(row)}]; rows: objects; empty/loading states built in. */
export function Table({ columns = [], rows, loading, empty = "Nothing here yet.", onRowClick, rowKey = "id", dense }) {
  const pad = dense ? "7px 10px" : "10px 12px";
  return (
    <ScrollX style={{ border: `1px solid ${T.border}`, borderRadius: T.rCard, background: T.surface }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
        <thead>
          <tr>{columns.map((c) => (
            <th key={c.key} style={{ textAlign: c.align || "left", padding: pad, fontSize: 11, fontWeight: 600, letterSpacing: ".06em",
              textTransform: "uppercase", color: T.muted, borderBottom: `1px solid ${T.border}`, position: "sticky", top: 0,
              background: T.surfaceAlt, whiteSpace: "nowrap", width: c.width }}>{c.label}</th>))}</tr>
        </thead>
        <tbody>
          {loading && [0, 1, 2].map((i) => (
            <tr key={`sk${i}`}><td colSpan={columns.length} style={{ padding: pad }}><Skeleton height={14} /></td></tr>))}
          {!loading && (!rows || rows.length === 0) && (
            <tr><td colSpan={columns.length} style={{ padding: 24, textAlign: "center", color: T.muted }}>{empty}</td></tr>)}
          {!loading && (rows || []).map((r, i) => (
            <tr key={r[rowKey] ?? i} onClick={onRowClick ? () => onRowClick(r) : undefined} className={onRowClick ? "vula-row-click" : undefined}
              style={{ borderBottom: `1px solid ${T.borderSoft}`, cursor: onRowClick ? "pointer" : undefined }}>
              {columns.map((c) => (
                <td key={c.key} style={{ padding: pad, textAlign: c.align || "left", color: T.text, verticalAlign: "top" }}>
                  {c.render ? c.render(r) : r[c.key]}
                </td>))}
            </tr>))}
        </tbody>
      </table>
    </ScrollX>
  );
}

/* ── Form controls: 16px on phones (no iOS zoom-on-focus), tokens everywhere ─────────────── */
export function Input(props) { return <input {...props} className={"vula-input " + (props.className || "")} style={{ ...inputStyle, ...props.style }} />; }
export function Select({ children, ...props }) { return <select {...props} className={"vula-input " + (props.className || "")} style={{ ...inputStyle, ...props.style }}>{children}</select>; }
export function Textarea(props) { return <textarea {...props} className={"vula-input " + (props.className || "")} style={{ ...inputStyle, minHeight: 80, resize: "vertical", ...props.style }} />; }

/* ── Loading / error states ───────────────────────────────────────────────────────────────── */
export function Skeleton({ width = "100%", height = 16, style }) {
  return <span className="vula-skeleton" aria-hidden="true" style={{ display: "block", width, height, borderRadius: 6, ...style }} />;
}
/** A card that failed to load says so and offers Retry — instead of silently vanishing. */
export function ErrorCard({ what = "this", onRetry, style }) {
  return (
    <div role="alert" style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 14px", borderRadius: T.rCard,
      border: `1px solid ${T.border}`, background: T.surface, color: T.muted, fontSize: 13, ...style }}>
      <span aria-hidden="true">⚠︎</span>
      <span style={{ flex: 1 }}>Couldn't load {what}.</span>
      {onRetry && <Button variant="ghost" size="sm" onClick={onRetry}>Retry</Button>}
    </div>
  );
}

/* ── Modal, confirm and toast — replacing window.alert/confirm/prompt ─────────────────────── */
export function Modal({ open, title, children, onClose, actions, width = 440 }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="vula-modal-scrim" onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(18,18,16,.45)",
      display: "grid", placeItems: "center", padding: 16 }}>
      <div role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()} className="vula-fade-up"
        style={{ width: "100%", maxWidth: width, background: T.surface, color: T.text, borderRadius: T.rCard, boxShadow: T.shadowLg,
          border: `1px solid ${T.border}`, padding: 20 }}>
        {title && <h3 className="vula-display" style={{ margin: "0 0 10px", fontSize: 20, color: T.ink }}>{title}</h3>}
        <div style={{ fontSize: 14, lineHeight: 1.55 }}>{children}</div>
        {actions && <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18, flexWrap: "wrap" }}>{actions}</div>}
      </div>
    </div>
  );
}

// One host (mounted once in App) renders every confirm/prompt/toast, so any component can call
// the functions below — the drop-in replacements for window.confirm / prompt / alert.
let _push = null;
const _queue = [];
function _open(req) { if (_push) _push(req); else _queue.push(req); }
/** await confirmDialog("Delete this order?") → true/false. {title, confirmLabel, danger} optional. */
export function confirmDialog(message, opts = {}) {
  return new Promise((resolve) => _open({ kind: "confirm", message, ...opts, resolve }));
}
/** await promptDialog("Correct answer?", {initial}) → string or null. */
export function promptDialog(message, opts = {}) {
  return new Promise((resolve) => _open({ kind: "prompt", message, ...opts, resolve }));
}
/** toast("Saved") / toast("Could not send", "danger") — disappears by itself. */
export function toast(message, tone = "ok") { _open({ kind: "toast", message: String(message ?? ""), tone }); }

export function UiHost() {
  const [dialog, setDialog] = useState(null);
  const [text, setText] = useState("");
  const [toasts, setToasts] = useState([]);
  useEffect(() => {
    _push = (req) => {
      if (req.kind === "toast") {
        const id = Math.random();
        setToasts((t) => [...t, { ...req, id }]);
        setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), req.tone === "danger" ? 6000 : 3200);
      } else { setText(req.initial || ""); setDialog(req); }
    };
    _queue.splice(0).forEach((r) => _push(r));
    return () => { _push = null; };
  }, []);
  const close = (value) => { dialog?.resolve(value); setDialog(null); };
  const tones = { ok: T.ok, danger: T.danger, warn: T.warn, info: T.info };
  return (
    <>
      <Modal open={!!dialog} title={dialog?.title || (dialog?.kind === "prompt" ? "" : "Are you sure?")}
        onClose={() => close(dialog?.kind === "prompt" ? null : false)}
        actions={<>
          <Button variant="ghost" onClick={() => close(dialog?.kind === "prompt" ? null : false)}>{dialog?.cancelLabel || "Cancel"}</Button>
          <Button variant={dialog?.danger ? "danger" : "primary"} autoFocus
            onClick={() => close(dialog?.kind === "prompt" ? text : true)}>{dialog?.confirmLabel || "OK"}</Button>
        </>}>
        <div style={{ whiteSpace: "pre-wrap" }}>{dialog?.message}</div>
        {dialog?.kind === "prompt" && (
          <Textarea value={text} onChange={(e) => setText(e.target.value)} style={{ marginTop: 10 }} autoFocus />)}
      </Modal>
      <div aria-live="polite" style={{ position: "fixed", zIndex: 1100, left: "50%", transform: "translateX(-50%)",
        bottom: "calc(20px + env(safe-area-inset-bottom))", display: "flex", flexDirection: "column", gap: 8, alignItems: "center", pointerEvents: "none" }}>
        {toasts.map((t) => (
          <div key={t.id} role="status" className="vula-fade-up" style={{ pointerEvents: "auto", maxWidth: "min(92vw, 460px)", padding: "10px 16px",
            borderRadius: T.rCard, background: T.ink, color: T.bg, boxShadow: T.shadowLg, fontSize: 13.5,
            borderLeft: `4px solid ${tones[t.tone] || T.ok}`, whiteSpace: "pre-wrap" }}>{t.message}</div>))}
      </div>
    </>
  );
}
