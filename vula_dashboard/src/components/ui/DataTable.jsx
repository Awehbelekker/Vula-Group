/**
 * DataTable — the redesign's table (2026-10-17, UX redesign Phase C). Sticky header, optional
 * sorting, its own scroll box (a wide table never scrolls the page sideways), and below 768px
 * the same rows as stacked ListRows. It supersedes ui/index.jsx's Table, which keeps its one
 * caller until that screen is migrated (Phase F).
 *
 * columns: [{ key, label, align?, width?, render?(row), sortable?, sortValue?(row) }]
 * stack:   how a row reads on a phone — { title, detail?, trailing?, Icon?, initials? } as
 *          functions of the row; defaults to the first column as title, the second as detail
 *          and the last as trailing.
 */
import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { ListRow } from "./ListRow.jsx";
import { Skeleton } from "./index.jsx";

const cell = (c, row) => (c.render ? c.render(row) : row[c.key]);

export function DataTable({ columns = [], rows, loading, empty = "Nothing here yet.", onRowClick, rowKey = "id",
  stack, caption, initialSort }) {
  const [sort, setSort] = useState(initialSort || null); // { key, dir: "asc"|"desc" }

  const sorted = useMemo(() => {
    if (!rows || !sort) return rows || [];
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return rows;
    const val = col.sortValue || ((r) => r[col.key]);
    const out = [...rows].sort((a, b) => {
      const x = val(a), y = val(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "en-ZA", { numeric: true });
    });
    return sort.dir === "desc" ? out.reverse() : out;
  }, [rows, sort, columns]);

  const toggle = (key) => setSort((s) => (s?.key === key ? (s.dir === "asc" ? { key, dir: "desc" } : null) : { key, dir: "asc" }));

  const first = columns[0], second = columns[1], last = columns[columns.length - 1];
  const asRow = (r) => ({
    title: stack?.title ? stack.title(r) : first && cell(first, r),
    detail: stack?.detail ? stack.detail(r) : second && second !== last ? cell(second, r) : undefined,
    trailing: stack?.trailing ? stack.trailing(r) : last && last !== first ? cell(last, r) : undefined,
    Icon: stack?.Icon?.(r), initials: stack?.initials?.(r),
  });

  const isEmpty = !loading && sorted.length === 0;
  return (
    <div className="vui-table-wrap vui-table-wrap--stack">
      <table className="vui-table">
        {caption && <caption style={{ position: "absolute", left: -9999 }}>{caption}</caption>}
        <thead>
          <tr>
            {columns.map((c) => {
              const dir = sort?.key === c.key ? sort.dir : null;
              const SortIcon = dir === "asc" ? ArrowUp : dir === "desc" ? ArrowDown : ArrowUpDown;
              return (
                <th key={c.key} scope="col" style={{ textAlign: c.align || "left", width: c.width }}
                  aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : c.sortable ? "none" : undefined}>
                  {c.sortable ? (
                    <button type="button" className="vui-sort" onClick={() => toggle(c.key)}>
                      {c.label}<SortIcon size={13} aria-hidden="true" />
                    </button>
                  ) : c.label}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading && [0, 1, 2].map((i) => (
            <tr key={`sk${i}`}><td colSpan={columns.length}><Skeleton height={14} /></td></tr>))}
          {isEmpty && (
            <tr><td colSpan={columns.length} style={{ padding: 24, textAlign: "center", color: "var(--muted)" }}>{empty}</td></tr>)}
          {!loading && sorted.map((r, i) => (
            <tr key={r[rowKey] ?? i} className={onRowClick ? "vui-click" : undefined}
              onClick={onRowClick ? () => onRowClick(r) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(r); } : undefined}>
              {columns.map((c) => <td key={c.key} style={{ textAlign: c.align || "left" }}>{cell(c, r)}</td>)}
            </tr>))}
        </tbody>
      </table>
      <div className="vui-stack" role="list" aria-label={caption}>
        {loading && <div style={{ padding: 12 }}><Skeleton lines={3} /></div>}
        {isEmpty && <div style={{ padding: 24, textAlign: "center", color: "var(--muted)" }}>{empty}</div>}
        {!loading && sorted.map((r, i) => (
          <div role="listitem" key={r[rowKey] ?? i}>
            <ListRow {...asRow(r)} onClick={onRowClick ? () => onRowClick(r) : undefined} />
          </div>))}
      </div>
    </div>
  );
}
