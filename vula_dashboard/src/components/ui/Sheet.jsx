/**
 * Sheet — a bottom sheet on a phone, a side panel from 768px (2026-10-17, UX redesign Phase C).
 * For details and quick actions that shouldn't leave the screen: the More menu, the Add menu,
 * an activity item, a contact. Modal: Escape or the scrim closes it, Tab stays inside, and focus
 * goes back to whatever opened it. Rendered into <body> so no card's overflow can clip it.
 */
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Sheet({ open, title, onClose, children, footer, labelledBy }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const opener = document.activeElement;
    const node = ref.current;
    (node?.querySelector("[autofocus]") || node)?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose?.(); return; }
      if (e.key !== "Tab" || !node) return;
      const els = [...node.querySelectorAll(FOCUSABLE)];
      if (!els.length) { e.preventDefault(); return; }
      const [first, last] = [els[0], els[els.length - 1]];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      opener?.focus?.();
    };
  }, [open, onClose]);

  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <>
      <div className="vui-scrim" onClick={onClose} aria-hidden="true" />
      <div ref={ref} className="vui-sheet" role="dialog" aria-modal="true" tabIndex={-1}
        aria-label={labelledBy ? undefined : title} aria-labelledby={labelledBy}>
        <div className="vui-sheet-grip" aria-hidden="true" />
        <div className="vui-sheet-head">
          <h2>{title}</h2>
          <button type="button" className="vui-icon-btn vui-icon-btn--quiet" onClick={onClose} aria-label="Close">
            <X size={20} strokeWidth={1.75} aria-hidden="true" />
          </button>
        </div>
        <div className="vui-sheet-body">{children}</div>
        {footer && <div style={{ padding: "var(--space-3) var(--space-4)", borderTop: "1px solid var(--line)",
          display: "flex", gap: 8, justifyContent: "flex-end" }}>{footer}</div>}
      </div>
    </>,
    document.body,
  );
}
