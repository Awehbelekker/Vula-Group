/** lib/csv.js — client-side CSV export. No backend round trip: build from data already in state. */

function csvCell(v) {
  const s = v == null ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/** rows: array of objects. columns: [{key, label}] or omitted to use Object.keys(rows[0]). */
export function downloadCsv(filename, rows, columns) {
  const cols = columns || Object.keys(rows[0] || {}).map(key => ({ key, label: key }))
  const header = cols.map(c => csvCell(c.label)).join(',')
  const body = rows.map(r => cols.map(c => csvCell(typeof c.get === 'function' ? c.get(r) : r[c.key])).join(',')).join('\n')
  const blob = new Blob([header + '\n' + body], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename.endsWith('.csv') ? filename : `${filename}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
