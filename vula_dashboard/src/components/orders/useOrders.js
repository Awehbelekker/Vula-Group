/**
 * useOrders.js — the Orders list's data and actions (2026-10-18, UX redesign Phase F). Shared by
 * the current list (VulaMerchantAdmin OrdersTab) and orders/OrdersV2.jsx: same endpoints, same
 * status moves, same refund prompt, same deletes. Moved out of OrdersTab unchanged.
 */
import { useCallback, useEffect, useState } from 'react'
import { confirmDialog, toast } from '../ui/index.jsx'
import { downloadCsv } from '../../lib/csv'
import { VULA_API } from '../../lib/authFetch'

export const NEXT_STATUSES = {
  paid:      ['confirmed', 'cancelled'],
  confirmed: ['packing', 'cancelled'],
  packing:   ['dispatched', 'cancelled'],
  dispatched:['delivered'],
  delivered: [],
  cancelled: ['refunded'],
}

export const ORDER_DELETABLE = new Set(['pending_payment', 'cancelled'])

export const ORDER_FILTERS = [
  { id: 'all',             label: 'All' },
  { id: 'paid',            label: 'Paid' },
  { id: 'confirmed',       label: 'Confirmed' },
  { id: 'packing',         label: 'Packing' },
  { id: 'dispatched',      label: 'Dispatched' },
  { id: 'pending_payment', label: 'Unpaid' },
]

export function useOrders(tenantId) {
  const [orders, setOrders] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')
  const [updating, setUpdating] = useState(null)
  const [detailId, setDetailId] = useState(null)
  const [showManual, setShowManual] = useState(false)
  const [selected, setSelected] = useState(new Set())
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    const url = filter === 'all'
      ? `${VULA_API}/v1/commerce/${tenantId}/admin/orders?limit=100`
      : `${VULA_API}/v1/commerce/${tenantId}/admin/orders?status=${filter}&limit=100`
    const r = await fetch(url)
    const d = await r.json()
    setOrders(d.orders || [])
    setSelected(new Set())
    setLoading(false)
  }, [tenantId, filter])

  useEffect(() => { load() }, [load])

  async function advance(orderId, newStatus, extra = {}) {
    setUpdating(orderId)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/orders/${orderId}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus, ...extra }),
    })
    const d = await r.json().catch(() => ({}))
    if (d.refund?.status === 'pending') {
      toast(`Refunded R${(d.refund.amount_cents / 100).toFixed(2)} via Yoco.`)
    } else if (d.refund?.status === 'failed') {
      toast(`Order marked refunded, but the automatic Yoco refund failed: ${d.refund.detail || 'unknown error'}. Please process it manually in Yoco.`, 'danger')
    }
    await load()
    setUpdating(null)
  }

  // "Refunded" is real money leaving the business — for an order that was paid online, offer to
  // actually call Yoco's refund API (opt-in) instead of only recording the status.
  async function refundOrder(o) {
    if (o.yoco_checkout_id) {
      const auto = await confirmDialog(
        `This order was paid online via Yoco (R${(o.total_cents / 100).toFixed(2)}). Refund the customer through Yoco now, or just mark it refunded and refund them yourself?`,
        { title: 'Refund this order', confirmLabel: 'Refund through Yoco', cancelLabel: "Just mark refunded" }
      )
      advance(o.id, 'refunded', auto ? { auto_refund: true } : {})
    } else {
      advance(o.id, 'refunded')
    }
  }

  /** A status button: refunds go through the refund prompt, everything else straight through. */
  const move = (o, ns) => (ns === 'refunded' ? refundOrder(o) : advance(o.id, ns))

  function toggleSelect(orderId) {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(orderId) ? next.delete(orderId) : next.add(orderId)
      return next
    })
  }

  async function deleteOne(order) {
    if (!(await confirmDialog(`Delete order ${order.display_id}? This can't be undone.`, { danger: true, confirmLabel: 'Yes' }))) return
    setUpdating(order.id)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/orders/${order.id}`, { method: 'DELETE' })
    await load()
    setUpdating(null)
  }

  async function deleteSelected() {
    const ids = [...selected]
    if (!ids.length) return
    if (!(await confirmDialog(`Delete ${ids.length} selected order${ids.length === 1 ? '' : 's'}? This can't be undone.`, { danger: true, confirmLabel: 'Yes' }))) return
    setDeleting(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/orders/bulk-delete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order_ids: ids }),
    })
    await load()
    setDeleting(false)
  }

  const exportCsv = () => downloadCsv('orders', orders, [
    { key: 'display_id', label: 'Order' }, { key: 'customer_name', label: 'Customer' },
    { key: 'customer_phone', label: 'Phone' }, { key: 'status', label: 'Status' },
    { label: 'Total (R)', get: o => (o.total_cents / 100).toFixed(2) },
    { key: 'delivery_slot', label: 'Slot' }, { key: 'channel', label: 'Channel' },
    { key: 'created_at', label: 'Created' },
  ])

  return {
    orders, loading, load, filter, setFilter, updating, detailId, setDetailId, showManual, setShowManual,
    selected, setSelected, deleting, advance, refundOrder, move, toggleSelect, deleteOne, deleteSelected, exportCsv,
  }
}
