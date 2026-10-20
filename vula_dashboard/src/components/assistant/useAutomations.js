/**
 * useAutomations.js — Assistant › Automations, shared by VulaAutomations (current look) and
 * assistant/AutomationsV2 (the redesign, 2026-10-20). Moved verbatim: trigger → action rules (by
 * form or taught in plain language), on/off and delete, and the staged firings that wait for the
 * owner's approval: nothing sends until they say go.
 */
import { useState, useEffect, useCallback } from 'react'
import { VULA_API } from '../../lib/authFetch'
import { confirmDialog } from '../ui/index.jsx'

export const ORDER_STATUSES = ['paid', 'confirmed', 'packing', 'dispatched', 'delivered', 'cancelled']

export const TRIGGERS = {
  order_status: { label: '📦 Order reaches a status', hint: 'Fires once per order when it reaches the chosen status.' },
  low_stock: { label: '🔔 Product stock is low', hint: 'Fires once per product per day while stock ≤ its reorder threshold (set in Products).' },
  abandoned_cart: { label: '🛒 Cart left without ordering', hint: 'Fires when a WhatsApp customer leaves items in their cart for 2+ hours (carts from the last 3 days only).' },
  reorder_due: { label: '🔁 Time to reorder', hint: "Fires once for a customer whose last order was delivered 7 days ago." },
}

export const ACTIONS = {
  whatsapp_customer: { label: '💬 Message the customer', hint: 'Not available with the low-stock trigger.' },
  whatsapp_team: { label: '🧑 Message the team', hint: 'Sends to whoever is set to receive help requests (Team tab).' },
}

export const PLACEHOLDERS = {
  order_status: '{{order_id}}, {{customer_name}}, {{status}}',
  low_stock: '{{product_name}}, {{stock}}, {{threshold}}',
  abandoned_cart: '{{customer_name}}, {{items}}',
  reorder_due: '{{customer_name}}, {{items}}',
}

export function useAutomations(tenantId) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [showNew, setShowNew] = useState(false)
  const [form, setForm] = useState({ name: '', trigger_type: 'order_status', to_status: 'dispatched', action_type: 'whatsapp_customer', message: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const [firings, setFirings] = useState([])
  const [teachText, setTeachText] = useState('')
  const [teaching, setTeaching] = useState(false)
  const [teachMsg, setTeachMsg] = useState('')
  const [showTeach, setShowTeach] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations`).then(r => r.json()).catch(() => ({}))
    setRows(r.automations || [])
    setLoading(false)
  }, [tenantId])
  useEffect(() => { load() }, [load])

  const loadFirings = useCallback(async () => {
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations/firings`).then(r => r.json()).catch(() => ({}))
    setFirings(r.firings || [])
  }, [tenantId])
  useEffect(() => { loadFirings() }, [loadFirings])

  async function decide(firing, action) {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations/firings/${firing.id}/${action}`, { method: 'POST' })
    loadFirings()
  }

  async function teach() {
    setTeachMsg('')
    if (!teachText.trim()) return
    setTeaching(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations/from-text`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: teachText.trim() }),
    }).then(r => r.json()).catch(() => ({ error: 'network' }))
    setTeaching(false)
    if (r.error) { setTeachMsg(r.error); return }
    setTeachMsg('Rule created ✓'); setTeachText(''); setShowTeach(false); load()
  }

  async function create() {
    setError('')
    if (!form.message.trim()) return setError('Write the message to send.')
    if (form.action_type === 'whatsapp_customer' && form.trigger_type === 'low_stock') {
      return setError("Messaging the customer doesn't work with the low-stock trigger (there's no customer).")
    }
    setSaving(true)
    const body = {
      name: form.name.trim() || undefined,
      trigger_type: form.trigger_type,
      trigger_config: form.trigger_type === 'order_status' ? { to_status: form.to_status } : {},
      action_type: form.action_type,
      action_config: { message: form.message.trim() },
    }
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(r => r.json()).catch(() => ({ error: 'network' }))
    setSaving(false)
    if (r.error) return setError(r.error)
    setShowNew(false)
    setForm({ name: '', trigger_type: 'order_status', to_status: 'dispatched', action_type: 'whatsapp_customer', message: '' })
    load()
  }

  async function toggle(row) {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations/${row.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !row.enabled }),
    })
    load()
  }

  async function remove(row) {
    if (!(await confirmDialog(`Delete automation "${row.name}"?`, { danger: true, confirmLabel: 'Yes' }))) return
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations/${row.id}`, { method: 'DELETE' })
    load()
  }

  return { rows, loading, showNew, setShowNew, form, setForm, saving, error, firings, teachText, setTeachText, teaching, teachMsg, showTeach, setShowTeach, decide, teach, create, toggle, remove }
}
