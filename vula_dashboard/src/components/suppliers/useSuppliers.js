/**
 * useSuppliers.js — Operate › Suppliers and its purchase orders, shared by VulaMerchantAdmin's
 * SuppliersTab (current look) and suppliers/SuppliersV2 (the redesign, 2026-10-20). Moved verbatim:
 * the supplier list and add/edit/delete form (tax number and aliases feed bill auto-matching), and
 * purchase orders with the low-stock reorder suggestions (P3.3).
 */
import { useState, useEffect, useCallback } from 'react'
import { VULA_API } from '../../lib/authFetch'
import { confirmDialog } from '../ui/index.jsx'

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

  const load = useCallback(async () => {
    setLoading(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/suppliers`)
    const d = await r.json()
    setSuppliers(d.suppliers || [])
    setLoading(false)
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
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/suppliers`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    cancel()
    setSaving(false)
    await load()
  }

  async function remove(s) {
    if (!(await confirmDialog(`Delete supplier "${s.name}"?`, { danger: true, confirmLabel: 'Yes' }))) return false
    setSaving(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/suppliers/${s.id}`, { method: 'DELETE' })
    await load()
    setSaving(false)
    return true
  }

  return { suppliers, loading, saving, editing, form, setForm, startEdit, startNew, cancel, save, remove }
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
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reorder-suggestions`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/purchase-orders`).then(r => r.json()).catch(() => ({})),
    ])
    setSuggestions(s.groups || [])
    setPos(p.purchase_orders || [])
    setLoading(false)
  }, [tenantId])
  useEffect(() => { load() }, [load])

  async function createFromSuggestion(group) {
    setBusy(group.supplier_id || 'unassigned')
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/purchase-orders`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        supplier_id: group.supplier_id, supplier_name: group.supplier_name,
        items: group.items.map(it => ({
          product_id: it.product_id, name: it.name, quantity: it.suggested_qty, unit_cost_cents: it.unit_cost_cents,
        })),
      }),
    })
    await load()
    setBusy(null)
  }

  async function advance(po, status) {
    setBusy(po.id)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/purchase-orders/${po.id}/status`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
    })
    await load()
    setBusy(null)
  }

  async function sendPo(po) {
    setBusy(po.id)
    setSendMsg({ ...sendMsg, [po.id]: 'Sending…' })
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/purchase-orders/${po.id}/send`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel: channel[po.id] || 'email' }),
    }).then(r => r.json()).catch(() => ({ error: 'network' }))
    if (r.error) {
      setSendMsg({ ...sendMsg, [po.id]: r.error })
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
