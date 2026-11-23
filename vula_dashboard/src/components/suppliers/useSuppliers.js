/**
 * useSuppliers.js — Operate › Suppliers and its purchase orders, shared by VulaMerchantAdmin's
 * SuppliersTab (current look) and suppliers/SuppliersV2 (the redesign, 2026-10-20). Moved verbatim:
 * the supplier list and add/edit/delete form (tax number and aliases feed bill auto-matching), and
 * purchase orders with the low-stock reorder suggestions (P3.3).
 */
import { useState, useEffect, useCallback } from 'react'
import { VULA_API } from '../../lib/authFetch'
import { api, tryApi } from '../../lib/api'
import { confirmDialog, toast } from '../ui/index.jsx'

export const BLANK_SUPPLIER = {
  name: '', aliases: '', payment_terms_days: 30, category: 'general',
  contact_phone: '', contact_email: '', account_number: '', tax_id: '', notes: '',
}

export function useSuppliers(tenantId) {
  const [suppliers, setSuppliers] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState(null) // null | {} (new) | supplier
  const [form, setForm] = useState(BLANK_SUPPLIER)
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api(`/v1/commerce/${tenantId}/admin/suppliers`)
      setSuppliers(d.suppliers || [])
    } catch (e) {
      toast(`Couldn't load your suppliers: ${e.message}`, 'danger')
    } finally {
      setLoading(false)
    }
  }, [tenantId])

  useEffect(() => { load() }, [load])

  function startEdit(s) {
    setForm({
      ...BLANK_SUPPLIER, ...s,
      aliases: Array.isArray(s.aliases) ? s.aliases.join(', ') : (s.aliases || ''),
      payment_terms_days: s.payment_terms_days ?? 30,
    })
    setEditing(s)
  }
  function startNew() { setForm(BLANK_SUPPLIER); setEditing({}) }
  function cancel() { setEditing(null); setForm(BLANK_SUPPLIER) }

  async function save(e) {
    e.preventDefault()
    if (!form.name.trim()) return
    setSaving(true)
    const payload = {
      ...form,
      name: form.name.trim(),
      payment_terms_days: parseInt(form.payment_terms_days, 10) || 30,
      aliases: String(form.aliases).split(',').map(a => a.trim()).filter(Boolean),
    }
    if (editing && editing.id) payload.id = editing.id
    const res = await tryApi(`/v1/commerce/${tenantId}/admin/suppliers`, {
      method: 'POST', body: payload,
    })
    if (!res.ok) {
      // keep the form open with what was typed
      toast(`The supplier wasn't saved: ${res.error}`, 'danger')
      setSaving(false)
      return
    }
    const saved = res.data
    // a new supplier that is a business already on file is not added twice (supplier_identity.py)
    setNotice(saved && saved.matched_existing
      ? `${payload.name} is already on your list as ${saved.name}, so it was added as another name for it.`
      : '')
    cancel()
    setSaving(false)
    await load()
  }

  async function remove(s) {
    if (!(await confirmDialog(`Delete supplier "${s.name}"?`, { danger: true, confirmLabel: 'Yes' }))) return false
    setSaving(true)
    const res = await tryApi(`/v1/commerce/${tenantId}/admin/suppliers/${s.id}`, { method: 'DELETE' })
    if (!res.ok) toast(`"${s.name}" wasn't deleted: ${res.error}`, 'danger')
    await load()
    setSaving(false)
    return res.ok
  }

  return { suppliers, loading, saving, editing, form, setForm, startEdit, startNew, cancel, save, remove, notice, reload: load }
}

/** Supplier records that look like one business (vula/commerce/supplier_identity.py) and merging
 * them: bills, expenses and documents move to the kept record, the other names become its aliases. */
export function useSupplierDuplicates(tenantId, onMerged) {
  const [groups, setGroups] = useState([])
  const [busy, setBusy] = useState(null)
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/suppliers/duplicates`)
      .then(r => (r.ok ? r.json() : {})).catch(() => ({}))
    setGroups(d.groups || [])
  }, [tenantId])
  useEffect(() => { load() }, [load])

  async function merge(group) {
    const names = group.duplicates.map(d => d.name).join(', ')
    if (!(await confirmDialog(`Merge ${names} into ${group.keep.name}? Their bills and documents move to ${group.keep.name}.`,
      { confirmLabel: 'Merge' }))) return
    setBusy(group.keep.id)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/suppliers/merge`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keep_id: group.keep.id, duplicate_ids: group.duplicates.map(d => d.id) }),
    })
    const d = await r.json().catch(() => ({}))
    setMessage(r.status === 403 ? 'Only the owner and managers can merge suppliers.'
      : d.message || d.detail || (r.ok ? 'Merged.' : "That didn't work — please try again."))
    setBusy(null)
    await load()
    if (r.ok && onMerged) onMerged()
  }

  return { groups, busy, message, merge }
}

export function usePurchaseOrders(tenantId) {
  const [suggestions, setSuggestions] = useState([])
  const [pos, setPos] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(null)
  const [channel, setChannel] = useState({})
  const [sendMsg, setSendMsg] = useState({})

  const load = useCallback(async () => {
    setLoading(true)
    const [s, p] = await Promise.all([
      tryApi(`/v1/commerce/${tenantId}/admin/reorder-suggestions`),
      tryApi(`/v1/commerce/${tenantId}/admin/purchase-orders`),
    ])
    if (s.ok) setSuggestions(s.data.groups || [])
    if (p.ok) setPos(p.data.purchase_orders || [])
    if (!s.ok || !p.ok) toast(`Couldn't load your purchase orders: ${(!p.ok ? p : s).error}`, 'danger')
    setLoading(false)
  }, [tenantId])
  useEffect(() => { load() }, [load])

  async function createFromSuggestion(group) {
    setBusy(group.supplier_id || 'unassigned')
    const res = await tryApi(`/v1/commerce/${tenantId}/admin/purchase-orders`, {
      method: 'POST',
      body: {
        supplier_id: group.supplier_id, supplier_name: group.supplier_name,
        items: group.items.map(it => ({
          product_id: it.product_id, name: it.name, quantity: it.suggested_qty, unit_cost_cents: it.unit_cost_cents,
        })),
      },
    })
    if (!res.ok) toast(`The purchase order wasn't created: ${res.error}`, 'danger')
    await load()
    setBusy(null)
  }

  async function advance(po, status) {
    setBusy(po.id)
    const res = await tryApi(`/v1/commerce/${tenantId}/admin/purchase-orders/${po.id}/status`, {
      method: 'PATCH', body: { status },
    })
    if (!res.ok) toast(`The purchase order wasn't changed: ${res.error}`, 'danger')
    await load()
    setBusy(null)
  }

  async function sendPo(po) {
    setBusy(po.id)
    setSendMsg({ ...sendMsg, [po.id]: 'Sending…' })
    const res = await tryApi(`/v1/commerce/${tenantId}/admin/purchase-orders/${po.id}/send`, {
      method: 'POST', body: { channel: channel[po.id] || 'email' },
    })
    const r = res.data || {}
    if (!res.ok) {
      setSendMsg({ ...sendMsg, [po.id]: res.error })
    } else {
      setSendMsg({ ...sendMsg, [po.id]: r.warnings ? `Sent via ${r.sent_via.join(', ')} (${r.warnings[0]})` : `Sent via ${r.sent_via.join(', ')} ✓` })
      await load()
    }
    setBusy(null)
  }

  const fmt = c => `R${((c || 0) / 100).toFixed(2)}`
  const PO_STATUS = { draft: '📝 Draft', sent: '📤 Sent', received: '✅ Received', cancelled: '✕ Cancelled' }

  return { suggestions, pos, loading, busy, channel, setChannel, sendMsg, createFromSuggestion, advance, sendPo, fmt, PO_STATUS }
}
