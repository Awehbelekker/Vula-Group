/**
 * FloatingToolbar — the phone's main controls, floating above the home bar (2026-10-17, UX
 * redesign Phase C). The redesigned shell (Phase D) puts Search, Add, Today, Inbox and More
 * here; Add is the `primary` item (amber). While it shows, toasts sit above it
 * (--vui-toolbar-h) and screens should end with <div className="vui-toolbar-pad" /> so their
 * last row isn't hidden behind it.
 *
 * items: [{ id, label, Icon, onClick, count?, primary?, current? }]
 */
import { useEffect } from "react";

export function FloatingToolbar({ items = [], label = "Quick actions" }) {
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty("--vui-toolbar-h", "72px");
    return () => root.style.removeProperty("--vui-toolbar-h");
  }, []);
  return (
    <nav className="vui-toolbar" aria-label={label}>
      {items.map(({ id, label: l, Icon, onClick, count, primary, current }) => (
        <button key={id} type="button" className={`vui-tool${primary ? " vui-tool--primary" : ""}`} onClick={onClick}
          aria-label={count ? `${l} (${count})` : l} aria-current={current ? "page" : undefined}>
          <Icon size={primary ? 22 : 20} strokeWidth={1.75} aria-hidden="true" />
          {!primary && <span aria-hidden="true">{l}</span>}
          {count ? <span className="vui-count" aria-hidden="true">{count > 99 ? "99+" : count}</span> : null}
        </button>))}
    </nav>
  );
}
