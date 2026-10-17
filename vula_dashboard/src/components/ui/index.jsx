/**
 * ui/index.jsx — Vula design-system primitives. Inline-style, token-driven (var(--*)),
 * accent-aware. Use these instead of ad-hoc per-component styling.
 *
 * 2026-10-17 (UX redesign Phase C): extended in place — new variants and props only, so every
 * existing caller renders as before — plus new primitives in their own files, re-exported at
 * the bottom (ListRow, DataTable, FilterBar, Sheet, CommandPalette, FloatingToolbar). The
 * redesign's sizing (44px targets, pill buttons) lives in ui.css under [data-ui="v2"].
 * Every primitive is on #/ui-kit (master only), which the visual + axe tests screenshot.
 */
import { useEffect, useRef, useState } from "react";
import { T } from "../../theme/tokens";
import "./ui.css";

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
  // the redesign's names: secondary = outlined on the surface, quiet = text only
  secondary: { background: "var(--surface)", color: "var(--ink)", border: "1px solid var(--line)" },
  quiet: { background: "transparent", color: "var(--brand)", border: "1px solid transparent" },
};
export function Button({ children, variant = "primary", size = "md", style, className, Icon, ...rest }) {
  const pad = size === "sm" ? "6px 12px" : size === "lg" ? "12px 22px" : "9px 16px";
  const fs = size === "sm" ? 12.5 : size === "lg" ? 15 : 13.5;
  return (
    <button {...rest} className={`vui-btn${size === "sm" ? " vui-btn--sm" : ""}${className ? ` ${className}` : ""}`} style={{
      ...(Icon ? { display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6 } : {}),
      padding: pad, fontSize: fs, fontWeight: 600, borderRadius: T.rInput, cursor: "pointer",
      fontFamily: T.body, transition: "filter .15s, opacity .15s", ...BTN[variant], ...style,
    }}
      onMouseEnter={(e) => { e.currentTarget.style.filter = "brightness(0.96)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.filter = "none"; }}>
      {Icon && <Icon size={18} strokeWidth={1.75} aria-hidden="true" />}
      {children}
    </button>
  );
}

/** A round icon-only button (44px). `label` is required — it is the button's accessible name.
 * `count` shows an unread/alert number in clay. */
export function IconButton({ Icon, label, count, quiet = false, className, ...rest }) {
  return (
    <button type="button" aria-label={count ? `${label} (${count})` : label} title={label} {...rest}
      className={`vui-icon-btn${quiet ? " vui-icon-btn--quiet" : ""}${className ? ` ${className}` : ""}`}>
      <Icon size={20} strokeWidth={1.75} aria-hidden="true" />
      {count ? <span className="vui-count" aria-hidden="true">{count > 99 ? "99+" : count}</span> : null}
    </button>
  );
}

function TargetRing({ progress = 0, size = 64 }) {
  const r = (size - 8) / 2, c = 2 * Math.PI * r, p = Math.max(0, Math.min(1, progress));
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true" style={{ flex: "none" }}>
      <circle className="vui-ring-track" cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth="6" />
      <circle className="vui-ring-fill" cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth="6" strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - p)} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
    </svg>
  );
}

/**
 * The number tiles. variant "kpi" (default — the existing tile), and the redesign's "hero"
 * (big headline figure, optional target ring + chip, e.g. "won this month" against a target),
 * "tile" (a KPI tile) and "hot" (clay tint for something urgent). `onClick` makes the whole tile a button: every figure links to its list.
 * Values arrive formatted (lib/money.js) — never computed here.
 */
export function StatCard({ label, value, sub, color, accent = false, variant = "kpi", progress, chip, onClick, ariaLabel }) {
  if (variant !== "kpi") {
    const hero = variant === "hero";
    const Tag = onClick ? "button" : "div";
    return (
      <Tag type={onClick ? "button" : undefined} onClick={onClick} aria-label={ariaLabel}
        className={onClick ? "vui-stat--link" : undefined}
        style={{
          display: "flex", alignItems: "center", gap: 16, boxSizing: "border-box",
          padding: hero ? "var(--space-6)" : "var(--space-4)",
          borderRadius: hero ? "var(--radius-xl)" : "var(--radius-lg)",
          border: `1px solid ${variant === "hot" ? "color-mix(in srgb, var(--warm) 30%, var(--line))" : "var(--line)"}`,
          background: variant === "hot" ? "var(--warm-soft)" : "var(--surface)", color: "var(--ink)",
        }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ font: "600 var(--fs-overline)/var(--lh-overline) var(--font-sans)", letterSpacing: ".06em",
            textTransform: "uppercase", color: variant === "hot" ? "var(--warm-text)" : "var(--muted)", marginBottom: 6 }}>{label}</div>
          <div className={hero ? "vula-display" : "vula-money"} style={hero
            ? { fontSize: "var(--fs-display-xl)", lineHeight: "var(--lh-display-xl)", fontWeight: 600, color: color || "var(--ink)" }
            : { fontSize: 22, lineHeight: 1.1, color: color || "var(--ink)" }}>{value}</div>
          {(sub || chip) && (
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, marginTop: 8 }}>
              {sub && <span style={{ fontSize: "var(--fs-caption)", color: "var(--muted)" }}>{sub}</span>}
              {chip && <StatusChip tone="highlight">{chip}</StatusChip>}
            </div>)}
        </div>
        {hero && progress !== undefined && <TargetRing progress={progress} />}
      </Tag>
    );
  }
  return (
    <Card pad="18px 20px">
      <div style={{ fontSize: 11, letterSpacing: ".1em", textTransform: "uppercase", color: T.muted, fontFamily: T.mono, marginBottom: 8 }}>{label}</div>
      <div className="vula-display" style={{ fontSize: 30, fontWeight: 700, lineHeight: 1, color: color || (accent ? T.accent : T.ink) }}>{value}</div>
      {sub && <div style={{ fontSize: 11.5, color: T.muted, marginTop: 6, fontFamily: T.mono }}>{sub}</div>}
    </Card>
  );
}

export function Badge({ children, tone = "muted", style, outline = false, text }) {
  const tones = { accent: T.accent, ok: T.ok, warn: T.warn, danger: T.danger, info: T.info, muted: T.muted,
    brand: "var(--brand)", highlight: "var(--highlight)", warm: "var(--warm)" };
  const c = tones[tone] || T.muted;
  return (
    <span style={{
      padding: "3px 10px", borderRadius: T.rPill, fontSize: 11, fontWeight: 600,
      fontFamily: T.mono, color: text || c, whiteSpace: "nowrap",
      background: outline ? "transparent" : tone === "accent" ? T.accentSoft : `color-mix(in srgb, ${c} 8%, transparent)`,
      border: `1px solid color-mix(in srgb, ${c} ${outline ? 45 : 19}%, transparent)`, ...style,
    }}>{children}</span>
  );
}

/**
 * Status in words, soft tint (the redesign's chip; Badge with sans type). Tones: ok, warn,
 * danger, info (e.g. "Vula answered", "Vula filed"), brand, highlight (money due), warm
 * (overdue), muted. `outline` for labels about where a thing lives, not its state: "Front
 * house", "Back house", "Visible to customers", "Staff only".
 */
export function StatusChip({ children, tone = "muted", outline = false, style }) {
  // text in the tone's text shade (index.css --*-text): 4.5:1 on its own tint in both themes
  const text = tone === "muted" ? "var(--muted)" : tone === "accent" ? "var(--brand-text)" : `var(--${tone}-text)`;
  return (
    <Badge tone={tone} outline={outline} text={text} style={{ fontFamily: "var(--font-sans)", fontSize: "var(--fs-caption)",
      fontWeight: 500, padding: "3px 10px", display: "inline-flex", alignItems: "center", gap: 4, ...style }}>{children}</Badge>
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

/** The one "sub-tab strip" primitive for a section with depth — pair with useSectionTabs.
 * variant "pill" is the redesign's segmented control (PillTabs: section sub-tabs, and Home's
 * Front house / Back house / All). Arrow keys, Home and End move between tabs (2026-10-17). */
export function SectionTabs({ tabs, active, onChange, variant, label }) {
  const ref = useRef(null);
  if (!tabs?.length) return null;
  const onKey = (e) => {
    const i = tabs.findIndex((t) => t.id === active);
    const to = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0
      : e.key === "End" ? tabs.length - 1 : null;
    if (to === null) return;
    e.preventDefault();
    const next = tabs[(to + tabs.length) % tabs.length];
    onChange(next.id);
    ref.current?.querySelector(`[data-tab="${CSS.escape(next.id)}"]`)?.focus();
  };
  const pill = variant === "pill";
  return (
    <div ref={ref} className={`vula-tabs${pill ? " vui-tabs--pill" : ""}`} role="tablist" aria-label={label} onKeyDown={onKey}
      style={{ display: "flex", gap: 4, marginBottom: 14, borderBottom: `1px solid ${T.border}`, paddingBottom: 8, flexWrap: "wrap" }}>
      {tabs.map((t) => (
        <button key={t.id} role="tab" type="button" data-tab={t.id} className="vui-tab" aria-selected={active === t.id}
          tabIndex={active === t.id ? 0 : -1} onClick={() => onChange(t.id)} style={{
          padding: "7px 14px", border: "none", borderRadius: T.rPill, fontSize: 13, fontWeight: 600, whiteSpace: "nowrap",
          cursor: "pointer", fontFamily: T.body,
          background: active === t.id ? T.accentSoft : "transparent",
          color: active === t.id ? T.accent : T.muted,
        }}>
          {t.Icon ? <t.Icon size={16} strokeWidth={1.75} aria-hidden="true" style={{ verticalAlign: "-3px", marginRight: 6 }} />
            : t.icon ? `${t.icon} ` : ""}{t.label}
        </button>
      ))}
    </div>
  );
}
/** The redesign's segmented tabs — SectionTabs as pills. */
export const PillTabs = (props) => <SectionTabs variant="pill" {...props} />;

export function SectionTitle({ children, sub }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <h1 className="vula-display" style={{ fontSize: 28, fontWeight: 700, margin: "0 0 2px" }}>{children}</h1>
      {sub && <p style={{ fontSize: 13, color: T.muted, margin: 0 }}>{sub}</p>}
    </div>
  );
}

/** An empty list says one line and offers one action. Pass a Lucide `Icon` (the redesign)
 * or the older emoji `icon`. */
export function EmptyState({ icon = "✦", Icon, title, children, action }) {
  return (
    <Card pad={40} style={{ textAlign: "center", maxWidth: 460, margin: "32px auto" }}>
      <div style={{ fontSize: 34, marginBottom: 12, display: "flex", justifyContent: "center" }}>
        {Icon ? <span className="vui-disc" style={{ width: 56, height: 56 }}><Icon size={26} strokeWidth={1.75} aria-hidden="true" /></span> : icon}
      </div>
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
export function PageHeader({ title, sub, actions, style, eyebrow, headline = false }) {
  // headline: this screen's ONE display headline (h1, display-lg) — e.g. Home's greeting.
  const H = headline ? "h1" : "h2";
  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: 12, marginBottom: 16, ...style }}>
      <div style={{ flex: "1 1 240px", minWidth: 0 }}>
        {eyebrow && <div style={{ font: "600 var(--fs-overline)/var(--lh-overline) var(--font-sans)", letterSpacing: ".06em",
          textTransform: "uppercase", color: "var(--muted)", marginBottom: 4 }}>{eyebrow}</div>}
        {title && <H className="vula-display" style={{ fontSize: headline ? "var(--fs-display-lg)" : 22,
          lineHeight: headline ? "var(--lh-display-lg)" : undefined, fontWeight: 600, margin: 0, color: T.ink }}>{title}</H>}
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
export function Skeleton({ width = "100%", height = 16, style, lines }) {
  if (lines) {
    return (
      <span aria-hidden="true" style={{ display: "grid", gap: 8, ...style }}>
        {Array.from({ length: lines }, (_, i) => (
          <span key={i} className="vula-skeleton" style={{ display: "block", height, borderRadius: 6, width: i === lines - 1 ? "60%" : width }} />))}
      </span>
    );
  }
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
/** toast("Saved") / toast("Could not send", "danger") — disappears by itself.
 * toast("Archived", "ok", { action: { label: "Undo", onClick } }) offers an undo while it shows. */
export function toast(message, tone = "ok", { action } = {}) { _open({ kind: "toast", message: String(message ?? ""), tone, action }); }

export function UiHost() {
  const [dialog, setDialog] = useState(null);
  const [text, setText] = useState("");
  const [toasts, setToasts] = useState([]);
  useEffect(() => {
    _push = (req) => {
      if (req.kind === "toast") {
        const id = Math.random();
        setToasts((t) => [...t, { ...req, id }]);
        setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), req.tone === "danger" || req.action ? 6000 : 3200);
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
      <div aria-live="polite" className="vui-toasts">
        {toasts.map((t) => (
          <div key={t.id} role="status" className="vula-fade-up" style={{ pointerEvents: "auto", maxWidth: "min(92vw, 460px)", padding: "10px 16px",
            borderRadius: T.rCard, background: T.ink, color: T.bg, boxShadow: T.shadowLg, fontSize: 13.5,
            borderLeft: `4px solid ${tones[t.tone] || T.ok}`, whiteSpace: "pre-wrap" }}>
            {t.message}
            {t.action && (
              <button type="button" className="vui-toast-action" onClick={() => {
                t.action.onClick?.(); setToasts((all) => all.filter((x) => x.id !== t.id));
              }}>{t.action.label}</button>)}
          </div>))}
      </div>
    </>
  );
}

/* ── New in the redesign (2026-10-17, Phase C) — one file each ───────────────────────────── */
export { ListRow } from "./ListRow.jsx";
export { DataTable } from "./DataTable.jsx";
export { FilterBar } from "./FilterBar.jsx";
export { Sheet } from "./Sheet.jsx";
export { CommandPalette, useCommandPaletteHotkey } from "./CommandPalette.jsx";
export { FloatingToolbar } from "./FloatingToolbar.jsx";
