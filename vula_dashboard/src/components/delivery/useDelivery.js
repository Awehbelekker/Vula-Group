/**
 * useDelivery.js — the day's delivery list (grouped by slot), the printable run sheet, and the
 * order-workflow settings (approval, dispatch, collection, payment options) (2026-10-19, UX
 * redesign Phase F). Shared by VulaMerchantAdmin's DeliveryTab, VulaOrderWorkflow.jsx and
 * delivery/DeliveryV2.jsx; the calls and bodies are unchanged.
 */
import { useCallback, useEffect, useState } from 'react'
import { VULA_API } from '../../lib/authFetch'

export const DEL_PAID = new Set(['paid', 'confirmed', 'packing', 'dispatched', 'delivered'])
export const SLOT_NAMES = { morning: 'Morning', afternoon: 'Afternoon', evening: 'Evening' }
export const PAYMENT_OPTIONS = [['online', 'Card / online'], ['cod', 'Pay on delivery'], ['eft', 'EFT / bank transfer']]
const fmt = c => `R${((c || 0) / 100).toFixed(2)}`

export function useDeliveryList(tenantId) {
  const today = new Date().toISOString().slice(0, 10)
  const [date, setDate] = useState(today)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/delivery-list?date=${date}`)
    const d = await r.json().catch(() => null)
    setData(d)
    setLoading(false)
  }, [tenantId, date])

  useEffect(() => { load() }, [load])

  const orders = data?.orders || []
  // Group by delivery slot, preserving the backend's slot ordering.
  const bySlot = {}
  for (const o of orders) {
    const k = o.delivery_slot || 'unscheduled'
    ;(bySlot[k] = bySlot[k] || []).push(o)
  }
  const slots = Object.keys(bySlot)
  return { today, date, setDate, data, loading, orders, bySlot, slots }
}

// Customer names, addresses, notes and product names come from WhatsApp customers, so every one is
// escaped before it goes into the print window (an about:blank window shares the dashboard's origin).
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]))

/** Open the day's run sheet in a new window and print it. `slotLabel(slot)` names each slot. */
export function printRunSheet({ date, slots, bySlot, slotLabel }) {
  const w = window.open('', '_blank')
  if (!w) return
  const blocks = slots.map(slot => {
    const rows = bySlot[slot].map(o => {
      const items = (o.commerce_order_items || [])
        .map(i => `${esc(i.product_name || 'Item')} ×${esc(i.quantity)}`).join(', ')
      return `<tr><td>${esc(o.display_id)}</td><td>${esc(o.customer_name || '')}<br>${esc(o.customer_phone || '')}</td>`
        + `<td>${esc(o.delivery_address || '—')}</td><td>${items}</td>`
        + `<td style="text-align:right">${fmt(o.total_cents)}${DEL_PAID.has(o.status) ? '' : ' (UNPAID)'}</td></tr>`
    }).join('')
    return `<h2>${esc(slotLabel(slot))}</h2><table>`
      + `<tr><th>#</th><th>Customer</th><th>Address</th><th>Items</th><th style="text-align:right">Total</th></tr>${rows}</table>`
  }).join('')
  // The print window has none of the dashboard's CSS variables, so its colours are plain names.
  w.document.write(`<html><head><title>Delivery run — ${esc(date)}</title>
    <style>body{font-family:system-ui;padding:24px;color:black}h1{font-size:20px}h2{font-size:15px;margin-top:18px}
    table{width:100%;border-collapse:collapse;margin-top:6px}td,th{padding:6px 4px;border-bottom:1px solid silver;text-align:left;font-size:12px;vertical-align:top}</style>
    </head><body><h1>Delivery run — ${esc(date)}</h1>${blocks || '<p>No deliveries.</p>'}</body></html>`)
  w.document.close(); w.print()
}

/** Order workflow: approval before fulfilment, where the ticket goes, collection, payment options. */
export function useOrderWorkflow(tenantId) {
  const [s, setS] = useState(null)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/order-settings`)
      .then((r) => r.json()).then((d) => setS(d.settings || {})).catch(() => setS({}))
  }, [tenantId])

  const save = async (patch) => {
    const next = { ...s, ...patch }
    setS(next)
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/order-settings`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(next),
    }).then((r) => r.json()).catch(() => ({}))
    if (d.settings) setS(d.settings)
    setMsg('Saved ✓'); setTimeout(() => setMsg(''), 1500)
  }

  const methods = s && Array.isArray(s.payment_methods) ? s.payment_methods : ['online', 'cod', 'eft']
  const toggleMethod = (m, on) =>
    save({ payment_methods: on ? [...new Set([...methods, m])] : methods.filter((x) => x !== m) })

  return { s, msg, save, methods, toggleMethod }
}
