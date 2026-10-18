/**
 * useCustomers.js — the Customers screen's data and actions (2026-10-18, UX redesign Phase F).
 * Shared by the current list (VulaCustomers.jsx) and the redesign (customers/CustomersV2.jsx):
 * same endpoints, same debounce, same saves. Moved out of VulaCustomers.jsx unchanged.
 */
import { useCallback, useEffect, useState } from 'react'
import { downloadCsv } from '../../lib/csv'
import { VULA_API } from '../../lib/authFetch'

export const AUDIENCES = [
  { id: 'all',         label: 'All' },
  { id: 'active_30d',  label: 'Active (30d)' },
  { id: 'high_value',  label: 'High value (>R500)' },
]

export const LANG_NAMES = {
  en: 'English', af: 'Afrikaans', zu: 'isiZulu', xh: 'isiXhosa', st: 'Sesotho',
  nso: 'Sepedi', tn: 'Setswana', ts: 'Xitsonga', ve: 'Tshivenda', ss: 'siSwati', nr: 'isiNdebele',
}

/** "today", "yesterday", "3d ago"; "—" when never. */
export function seenAgo(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  const days = Math.floor((Date.now() - d) / 86400000)
  return days === 0 ? 'today' : days === 1 ? 'yesterday' : `${days}d ago`
}

export function useCustomers(tenantId) {
  const [rows, setRows] = useState([])
  const [count, setCount] = useState(0)
  const [totalAll, setTotalAll] = useState(0)
  const [audience, setAudience] = useState('all')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [openPhone, setOpenPhone] = useState(null)
  const [detail, setDetail] = useState(null)
  const [noteDraft, setNoteDraft] = useState('')
  const [savingNote, setSavingNote] = useState(false)
  const [tagsDraft, setTagsDraft] = useState('')
  const [areaDraft, setAreaDraft] = useState('')
  const [savingTags, setSavingTags] = useState(false)

  const openHistory = async (c) => {
    if (openPhone === c.phone) { setOpenPhone(null); return }
    setOpenPhone(c.phone); setDetail(null); setNoteDraft('')
    setTagsDraft((c.tags || []).join(', ')); setAreaDraft(c.area || '')
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/customers/${encodeURIComponent(c.phone)}/detail`)
    const d = await r.json()
    setDetail(d); setNoteDraft(d.note || '')
  }

  const printStatement = async (phone, name) => {
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/customers/${encodeURIComponent(phone)}/statement`)
    const st = await r.json()
    const R = c => `R${((c || 0) / 100).toFixed(2)}`
    const rows = (st.invoices || []).map(inv =>
      `<tr><td>${(inv.created_at || '').slice(0, 10)}</td><td>${inv.invoice_number || ''}</td><td>${inv.doc_type}</td>
       <td>${inv.status}</td><td style="text-align:right">${R(inv.total_cents)}</td></tr>`).join('')
    const w = window.open('', '_blank')
    if (!w) return
    w.document.write(`<html><head><title>Statement — ${name || phone}</title>
      <style>body{font-family:system-ui;padding:24px;color:var(--ink)}table{width:100%;border-collapse:collapse;margin-top:14px}
      th,td{padding:6px 8px;border-bottom:1px solid var(--border);text-align:left;font-size:13px}
      h1{font-size:18px}.tot{font-weight:700}</style></head><body>
      <h1>Statement of account</h1>
      <p>${name || ''} · ${phone}</p>
      <table><tr><th>Date</th><th>No.</th><th>Type</th><th>Status</th><th style="text-align:right">Amount</th></tr>${rows}</table>
      <p class="tot">Total invoiced: ${R(st.total_invoiced_cents)}</p>
      <p class="tot">Total paid: ${R(st.total_paid_cents)}</p>
      ${st.total_credited_cents ? `<p class="tot">Credited: ${R(st.total_credited_cents)}</p>` : ''}
      <p class="tot">Balance due: ${R(st.balance_due_cents)}</p>
      </body></html>`)
    w.document.close(); w.print()
  }

  const saveNote = async (phone) => {
    setSavingNote(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/customers/${encodeURIComponent(phone)}/note`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note: noteDraft }),
    }).catch(() => {})
    setSavingNote(false)
  }

  const load = useCallback(async () => {
    setLoading(true)
    const q = new URLSearchParams({ audience })
    if (search) q.set('search', search)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/customers?${q}`)
    const d = await r.json()
    setRows(d.customers || [])
    setCount(d.count || 0)
    setTotalAll(d.total_all || 0)
    setLoading(false)
  }, [tenantId, audience, search])

  const saveTags = async (phone) => {
    setSavingTags(true)
    const tags = tagsDraft.split(',').map(t => t.trim()).filter(Boolean)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/customers/${encodeURIComponent(phone)}/tags`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags, area: areaDraft }),
    }).catch(() => {})
    setSavingTags(false)
    load()   // refresh so the row + segment counts reflect the change
  }

  useEffect(() => {
    const t = setTimeout(load, search ? 300 : 0)  // debounce search
    return () => clearTimeout(t)
  }, [load, search])

  const exportCsv = () => downloadCsv('customers', rows, [
    { key: 'name', label: 'Name' }, { key: 'phone', label: 'Phone' },
    { key: 'channel', label: 'Channel' }, { key: 'orders', label: 'Orders' },
    { label: 'Total spent (R)', get: c => ((c.total_spent_cents || 0) / 100).toFixed(2) },
    { label: 'Last seen', get: c => c.last_order_at || c.last_seen_at || '' },
    { key: 'area', label: 'Area' },
    { label: 'Tags', get: c => (c.tags || []).join('; ') },
  ])

  return {
    rows, count, totalAll, audience, setAudience, search, setSearch, loading,
    openPhone, closeHistory: () => setOpenPhone(null), detail, openHistory, printStatement,
    noteDraft, setNoteDraft, savingNote, saveNote,
    tagsDraft, setTagsDraft, areaDraft, setAreaDraft, savingTags, saveTags, exportCsv,
  }
}
