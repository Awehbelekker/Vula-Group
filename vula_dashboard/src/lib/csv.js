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

/** Minimal RFC4180-ish CSV parser (quoted fields, escaped "", commas/newlines inside quotes).
 * Returns an array of objects keyed by the header row. */
export function parseCsv(text) {
  const rows = []
  let row = [], cell = '', inQuotes = false
  const pushCell = () => { row.push(cell); cell = '' }
  const pushRow = () => { pushCell(); rows.push(row); row = [] }
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++ } else { inQuotes = false }
      } else cell += c
    } else if (c === '"') inQuotes = true
    else if (c === ',') pushCell()
    else if (c === '\n') pushRow()
    else if (c === '\r') { /* skip, \n handles the line break */ }
    else cell += c
  }
  if (cell !== '' || row.length) pushRow()
  if (!rows.length) return []
  const header = rows[0].map(h => h.trim())
  return rows.slice(1).filter(r => r.some(v => v !== '')).map(r => {
    const obj = {}
    header.forEach((h, i) => { obj[h] = r[i] !== undefined ? r[i] : '' })
    return obj
  })
}
