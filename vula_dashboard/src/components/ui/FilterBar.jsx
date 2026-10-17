/**
 * FilterBar — search plus filter chips, sticky at the top of a list (2026-10-17, UX redesign
 * Phase C). Chips are toggle buttons (aria-pressed). Controlled: the screen owns the query and
 * the selected chip ids and does the filtering (on the server where the list is paged).
 *
 * chips: [{ id, label, count? }]; selected: array of chip ids; onToggle(id)
 */
import { Search } from "lucide-react";

export function FilterBar({ query = "", onQuery, placeholder = "Search", chips = [], selected = [], onToggle, label = "Filters", children }) {
  return (
    <div className="vui-filters" role="search">
      {onQuery && (
        <label className="vui-search">
          <Search size={18} strokeWidth={1.75} aria-hidden="true" />
          <input type="search" value={query} onChange={(e) => onQuery(e.target.value)} placeholder={placeholder} aria-label={placeholder} />
        </label>)}
      {chips.length > 0 && (
        <div className="vui-chips" role="group" aria-label={label}>
          {chips.map((c) => (
            <button key={c.id} type="button" className="vui-chip" aria-pressed={selected.includes(c.id)} onClick={() => onToggle?.(c.id)}>
              {c.label}{c.count != null && <span style={{ color: "var(--muted)" }}>{c.count}</span>}
            </button>))}
        </div>)}
      {children}
    </div>
  );
}
