/**
 * CommandPalette — Cmd/Ctrl+K and the toolbar's Search (2026-10-17, UX redesign Phase C).
 * Jump to a page, find a contact, chat, invoice or document, or "Ask Vula" with what was typed.
 *
 * sources:  [{ group, items: [{ id, label, detail?, Icon?, keywords?, onSelect }] }] — known now
 *           (pages, quick actions); filtered as the person types.
 * search:   optional async (query) => same shape — server-side lookups (contacts, invoices…),
 *           already scoped to what this login may see; called after a short pause.
 * onAsk:    (query) => void — the last row, always there once something is typed.
 * Keyboard: ↑/↓ move, Enter runs, Escape closes (a combobox with aria-activedescendant).
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Search, Sparkles } from "lucide-react";
import { ListRow } from "./ListRow.jsx";

/** Open on Cmd/Ctrl+K anywhere (outside text fields that use K themselves, which none do). */
export function useCommandPaletteHotkey(setOpen) {
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setOpen]);
}

const matches = (item, q) => !q || `${item.label} ${item.detail || ""} ${(item.keywords || []).join(" ")}`.toLowerCase().includes(q);

export function CommandPalette({ open, onClose, sources = [], search, onAsk, placeholder = "Search or ask Vula…" }) {
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState([]);
  const [active, setActive] = useState(0);
  const inputRef = useRef(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return undefined;
    setQuery(""); setRemote([]); setActive(0);
    const opener = document.activeElement;
    setTimeout(() => inputRef.current?.focus(), 0);
    return () => opener?.focus?.();
  }, [open]);

  useEffect(() => {
    if (!open || !search || query.trim().length < 2) { setRemote([]); return undefined; }
    let alive = true;
    const t = setTimeout(() => {
      Promise.resolve(search(query.trim())).then((r) => { if (alive) setRemote(r || []); }).catch(() => {});
    }, 200);
    return () => { alive = false; clearTimeout(t); };
  }, [open, query, search]);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const local = sources.map((g) => ({ ...g, items: g.items.filter((it) => matches(it, q)) })).filter((g) => g.items.length);
    const out = [...local, ...remote.filter((g) => g.items?.length)];
    if (onAsk && q) out.push({ group: "Ask Vula", items: [{ id: "__ask", label: `Ask Vula: “${query.trim()}”`, Icon: Sparkles, onSelect: () => onAsk(query.trim()) }] });
    return out;
  }, [sources, remote, query, onAsk]);
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);
  useEffect(() => { setActive((a) => Math.min(a, Math.max(0, flat.length - 1))); }, [flat.length]);

  if (!open || typeof document === "undefined") return null;
  const run = (item) => { if (!item) return; onClose?.(); item.onSelect?.(); };
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); onClose?.(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, flat.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); run(flat[active]); }
  };
  const optId = (i) => `${listId}-opt-${i}`;
  let i = -1;
  return createPortal(
    <>
      <div className="vui-scrim" onClick={onClose} aria-hidden="true" />
      <div className="vui-palette" role="dialog" aria-modal="true" aria-label="Search">
        <div className="vui-palette-input">
          <Search size={20} strokeWidth={1.75} aria-hidden="true" />
          <input ref={inputRef} value={query} onChange={(e) => { setQuery(e.target.value); setActive(0); }} onKeyDown={onKey}
            placeholder={placeholder} role="combobox" aria-expanded="true" aria-controls={listId} aria-autocomplete="list"
            aria-activedescendant={flat.length ? optId(active) : undefined} aria-label={placeholder} />
          <span className="vui-kbd" aria-hidden="true">Esc</span>
        </div>
        <div className="vui-palette-list" id={listId} role="listbox" aria-label="Results">
          {flat.length === 0 && <div style={{ padding: 16, color: "var(--muted)", fontSize: "var(--fs-body)" }}>Nothing matches. Try other words.</div>}
          {groups.map((g) => (
            <div key={g.group} role="group" aria-label={g.group}>
              <div className="vui-palette-group" aria-hidden="true">{g.group}</div>
              {g.items.map((it) => {
                i += 1;
                const idx = i;
                return (
                  <ListRow key={`${g.group}:${it.id}`} id={optId(idx)} role="option" aria-selected={idx === active}
                    Icon={it.Icon} title={it.label} detail={it.detail}
                    onMouseEnter={() => setActive(idx)} onMouseDown={(e) => e.preventDefault()} onClick={() => run(it)} />
                );
              })}
            </div>))}
        </div>
      </div>
    </>,
    document.body,
  );
}
