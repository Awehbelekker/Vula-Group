/**
 * VulaMerchantAdmin.jsx
 *
 * Merchant-facing admin for a single Vula Commerce tenant.
 * Opened from VulaCommerce when clicking "Manage tenant →".
 *
 * Tabs:
 *   📊 Overview — daily revenue, orders to dispatch, pending payments
 *   📦 Orders   — list, filter by status, update fulfilment status
 *   🐟 Products — toggle stock on/off, edit price, mark weekly special
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import VulaImageUpload from './VulaImageUpload'
import VulaSmartScanner from './VulaSmartScanner'
import VulaInvoices from './VulaInvoices'
import VulaBudget from './VulaBudget'
import VulaBroadcast from './VulaBroadcast'
import VulaCustomers from './VulaCustomers'
import VulaAssistant from './VulaAssistant'
import VulaSettings from './VulaSettings'
import VulaDocuments from './VulaDocuments'
import VulaProjects from './VulaProjects'
import VulaQSRates from './VulaQSRates'
import VulaContacts from './VulaContacts'
import VulaFinances from './VulaFinances'
import VulaFollowups from './VulaFollowups'
import VulaTeam from './VulaTeam'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'

const STATUS_LABELS = {
  pending_payment: { label: 'Awaiting payment', color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
  paid:            { label: 'Paid',              color: '#22c55e', bg: 'rgba(34,197,94,0.12)' },
  confirmed:       { label: 'Confirmed',         color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' },
  packing:         { label: 'Packing',            color: '#8b5cf6', bg: 'rgba(139,92,246,0.12)' },
  dispatched:      { label: 'Dispatched',         color: '#0ea5e9', bg: 'rgba(14,165,233,0.12)' },
  delivered:       { label: 'Delivered',          color: '#10b981', bg: 'rgba(16,185,129,0.12)' },
  cancelled:       { label: 'Cancelled',          color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
  refunded:        { label: 'Refunded',           color: '#6b7280', bg: 'rgba(107,114,128,0.12)' },
}

const NEXT_STATUSES = {
  paid:      ['confirmed', 'cancelled'],
  confirmed: ['packing', 'cancelled'],
  packing:   ['dispatched', 'cancelled'],
  dispatched:['delivered'],
  delivered: [],
  cancelled: ['refunded'],
}

const CATEGORY_LABELS = {
  fresh_fish:     'Fresh Fish',
  fresh_chicken:  'Fresh Chicken',
  frozen_chicken: 'Frozen Chicken',
  frozen_seafood: 'Frozen Seafood',
  extras:         'Extras',
}

export default function VulaMerchantAdmin({ tenantId, tenantName, onClose, fullPage = false, access = [], full = true }) {
  const [tab, setTab] = useState('orders')
  // A member with a defined access list sees only those modules (+ overview). Owners/
  // managers (full) see everything including Team/Settings.
  const canSee = (id) => full || id === 'overview' || (access || []).includes(id)
  // If the current tab isn't visible to this member, fall back to a safe default.
  useEffect(() => { if (!canSee(tab)) setTab('overview') }, [access, full])  // eslint-disable-line
  const [products, setProducts] = useState([])

  // Load products once — shared by scanner + invoices
  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products`)
      .then(r => r.json()).then(d => setProducts(d.products || [])).catch(() => {})
  }, [tenantId])

  // Inner content shared by modal + full-page modes.
  const inner = (
    <>
        {/* Header */}
        <div style={styles.header}>
          <div>
            <h2 style={styles.title}>{tenantName}</h2>
            <p style={styles.subtitle}>Merchant admin</p>
          </div>
          {!fullPage && <button onClick={onClose} style={styles.closeBtn}>×</button>}
        </div>

        {/* Tabs — grouped for scannability, horizontally scrollable on mobile */}
        <div style={styles.tabs}>
          {(() => {
            const GROUPS = [
              [{ id: 'overview', label: '📊 Overview' }, { id: 'assistant', label: '💬 Assistant' }],
              [{ id: 'orders', label: '📦 Orders' }, { id: 'delivery', label: '🛵 Delivery' }, { id: 'products', label: '🐟 Products' }, { id: 'suppliers', label: '🚚 Suppliers' }],
              [{ id: 'invoices', label: '🧾 Invoices' }, { id: 'budget', label: '💰 Budget' }, { id: 'scanner', label: '📷 Scanner' }],
              [{ id: 'customers', label: '👥 Customers' }, { id: 'contacts', label: '📇 Contacts' }, { id: 'followups', label: '📬 Follow-ups' }, { id: 'broadcast', label: '📢 Broadcast' }],
              [{ id: 'projects', label: '🏗️ Projects' }, { id: 'qsrates', label: '📐 QS Rates' }, { id: 'finances', label: '💵 Finances' }, { id: 'documents', label: '📂 Documents' }],
              [...(full ? [{ id: 'team', label: '👥 Team' }, { id: 'settings', label: '⚙️ Settings' }] : [])],
            ]
            const items = []
            GROUPS.forEach((g) => {
              const visible = g.filter(t => canSee(t.id))
              if (!visible.length) return
              if (items.length) items.push({ divider: true, key: `d${items.length}` })
              visible.forEach(t => items.push(t))
            })
            return items.map(t => t.divider
              ? <span key={t.key} style={styles.tabDivider} />
              : (
                <button key={t.id} onClick={() => setTab(t.id)}
                  style={{ ...styles.tab, ...(tab === t.id ? styles.tabActive : {}) }}>
                  {t.label}
                </button>
              ))
          })()}
        </div>

        {/* Content */}
        <div style={styles.content}>
          {tab === 'overview'  && <OverviewTab tenantId={tenantId} setTab={setTab} />}
          {tab === 'assistant' && <VulaAssistant    tenantId={tenantId} />}
          {tab === 'orders'    && <OrdersTab   tenantId={tenantId} />}
          {tab === 'delivery'  && <DeliveryTab tenantId={tenantId} />}
          {tab === 'products'  && <ProductsTab tenantId={tenantId} />}
          {tab === 'suppliers' && <SuppliersTab tenantId={tenantId} />}
          {tab === 'scanner'   && <VulaSmartScanner tenantId={tenantId} products={products} />}
          {tab === 'invoices'  && <VulaInvoices     tenantId={tenantId} products={products} />}
          {tab === 'budget'    && <VulaBudget        tenantId={tenantId} />}
          {tab === 'customers' && <VulaCustomers     tenantId={tenantId} />}
          {tab === 'contacts'  && <VulaContacts      tenantId={tenantId} />}
          {tab === 'finances'  && <VulaFinances      tenantId={tenantId} />}
          {tab === 'followups' && <VulaFollowups     tenantId={tenantId} />}
          {tab === 'broadcast' && <VulaBroadcast     tenantId={tenantId} />}
          {tab === 'projects'  && <VulaProjects      tenantId={tenantId} />}
          {tab === 'qsrates'   && <VulaQSRates       tenantId={tenantId} />}
          {tab === 'documents' && <VulaDocuments     tenantId={tenantId} />}
          {tab === 'team'      && <VulaTeam          tenantId={tenantId} />}
          {tab === 'settings'  && <VulaSettings      tenantId={tenantId} tenantName={tenantName} adminEmail="" />}
        </div>
    </>
  )

  // Full-page mode (owner/staff dedicated admin) — no modal overlay.
  if (fullPage) {
    return <div style={styles.fullPage}>{inner}</div>
  }

  // Modal mode (master clicking "Manage tenant" from the Commerce list).
  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={styles.panel} onClick={e => e.stopPropagation()}>
        {inner}
      </div>
    </div>
  )
}

// ── Overview ─────────────────────────────────────────────────────────────────

function OverviewTab({ tenantId, setTab }) {
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/stats`)
      .then(r => r.json())
      .then(setStats)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [tenantId])

  if (loading) return <p style={styles.loading}>Loading…</p>
  if (!stats) return <p style={styles.error}>Could not load stats.</p>

  const fmt = cents => `R${(Number(cents || 0) / 100).toLocaleString('en-ZA', { maximumFractionDigits: 0 })}`
  const series = stats.daily_revenue || []
  const weekOrders = series.reduce((s, d) => s + (d.orders || 0), 0)
  const aov = stats.total_orders ? stats.total_revenue_cents / stats.total_orders : 0
  const alerts = [
    { show: stats.to_dispatch > 0,          label: 'To dispatch',      value: stats.to_dispatch,                hint: 'orders ready to send', tab: 'orders',   color: '#8b5cf6' },
    { show: stats.pending_payment > 0,      label: 'Awaiting payment', value: stats.pending_payment,            hint: 'unpaid orders',        tab: 'orders',   color: '#f59e0b' },
    { show: stats.invoice_overdue_cents > 0,label: 'Invoices overdue', value: fmt(stats.invoice_overdue_cents), hint: 'chase these',          tab: 'invoices', color: '#C0392B' },
    { show: stats.low_stock_count > 0,      label: 'Low stock',        value: stats.low_stock_count,            hint: 'items running out',    tab: 'products', color: '#C0392B' },
  ].filter(a => a.show)

  return (
    <div>
      <div style={styles.statGrid}>
        <StatCard label="Today's revenue" value={fmt(stats.today_revenue_cents)} sub={`${stats.today_orders} orders today`} accent="var(--accent, #2C5545)" />
        <StatCard label="Total revenue"   value={fmt(stats.total_revenue_cents)} sub={`${stats.total_orders} orders`} />
        <StatCard label="Avg order value" value={fmt(aov)}                        sub="per paid order" accent="#2B5797" />
        <StatCard label="This week"       value={weekOrders}                      sub="orders (7 days)" accent="#8b5cf6" />
      </div>

      <TrendChart series={series} fmt={fmt} />

      {alerts.length > 0 ? (
        <div style={{ marginTop: 18 }}>
          <p style={ovS.sectionLabel}>Needs attention</p>
          <div style={ovS.alertRow}>
            {alerts.map(a => (
              <button key={a.label} onClick={() => setTab && setTab(a.tab)} style={ovS.alertCard}>
                <span style={{ ...ovS.alertValue, color: a.color }}>{a.value}</span>
                <span style={ovS.alertLabel}>{a.label}</span>
                <span style={ovS.alertHint}>{a.hint} →</span>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <p style={{ ...ovS.sectionLabel, marginTop: 18 }}>✓ All caught up — nothing needs attention right now.</p>
      )}
    </div>
  )
}

function TrendChart({ series, fmt }) {
  if (!series.length) return null
  const max = Math.max(1, ...series.map(d => d.revenue_cents))
  const dayName = iso => new Date(iso).toLocaleDateString('en-ZA', { weekday: 'short' })
  return (
    <div style={ovS.chartCard}>
      <p style={ovS.sectionLabel}>Revenue — last 7 days</p>
      <div style={ovS.bars}>
        {series.map(d => (
          <div key={d.date} style={ovS.barCol} title={`${d.date}: ${fmt(d.revenue_cents)} · ${d.orders} orders`}>
            <div style={ovS.barWrap}>
              <div style={{ ...ovS.bar, height: `${Math.max(2, Math.round((d.revenue_cents / max) * 100))}%` }} />
            </div>
            <span style={ovS.barLabel}>{dayName(d.date)}</span>
          </div>
        ))}
      </div>
      <p style={ovS.chartFoot}>Peak day: {fmt(max)}</p>
    </div>
  )
}

const ovS = {
  sectionLabel: { fontSize: 12, fontWeight: 700, color: '#8A8680', textTransform: 'uppercase', letterSpacing: '0.4px', margin: '0 0 8px' },
  chartCard: { background: '#fff', border: '1px solid #DDD8CE', borderRadius: 12, padding: 18, marginTop: 16 },
  bars: { display: 'flex', alignItems: 'flex-end', gap: 10, height: 120 },
  barCol: { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, height: '100%' },
  barWrap: { flex: 1, width: '100%', display: 'flex', alignItems: 'flex-end' },
  bar: { width: '100%', background: 'linear-gradient(180deg, #2C5545, #3d7a5f)', borderRadius: '4px 4px 0 0', minHeight: 2 },
  barLabel: { fontSize: 11, color: '#8A8680' },
  chartFoot: { fontSize: 11, color: '#B5B0A8', margin: '10px 0 0', textAlign: 'right' },
  alertRow: { display: 'flex', gap: 10, flexWrap: 'wrap' },
  alertCard: { flex: '1 1 150px', minWidth: 140, background: '#fff', border: '1px solid #DDD8CE', borderRadius: 12, padding: '14px 16px', textAlign: 'left', cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 2 },
  alertValue: { fontSize: 22, fontWeight: 700 },
  alertLabel: { fontSize: 13, fontWeight: 600, color: '#2A2A2A' },
  alertHint: { fontSize: 11, color: '#8A8680' },
}

function StatCard({ label, value, sub, accent = '#6b7280' }) {
  return (
    <div style={styles.statCard}>
      <p style={{ ...styles.statValue, color: accent }}>{value}</p>
      <p style={styles.statLabel}>{label}</p>
      <p style={styles.statSub}>{sub}</p>
    </div>
  )
}

// ── Orders ────────────────────────────────────────────────────────────────────

function OrdersTab({ tenantId }) {
  const [orders, setOrders] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')
  const [updating, setUpdating] = useState(null)
  const [detailId, setDetailId] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    const url = filter === 'all'
      ? `${VULA_API}/v1/commerce/${tenantId}/admin/orders?limit=100`
      : `${VULA_API}/v1/commerce/${tenantId}/admin/orders?status=${filter}&limit=100`
    const r = await fetch(url)
    const d = await r.json()
    setOrders(d.orders || [])
    setLoading(false)
  }, [tenantId, filter])

  useEffect(() => { load() }, [load])

  async function advance(orderId, newStatus) {
    setUpdating(orderId)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/orders/${orderId}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus }),
    })
    await load()
    setUpdating(null)
  }

  const filters = [
    { id: 'all',             label: 'All' },
    { id: 'paid',            label: 'Paid' },
    { id: 'confirmed',       label: 'Confirmed' },
    { id: 'packing',         label: 'Packing' },
    { id: 'dispatched',      label: 'Dispatched' },
    { id: 'pending_payment', label: 'Unpaid' },
  ]

  return (
    <div>
      {/* Filter chips */}
      <div style={styles.chips}>
        {filters.map(f => (
          <button
            key={f.id}
            onClick={() => setFilter(f.id)}
            style={{ ...styles.chip, ...(filter === f.id ? styles.chipActive : {}) }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {loading && <p style={styles.loading}>Loading orders…</p>}
      {!loading && orders.length === 0 && <p style={styles.empty}>No orders found.</p>}

      <div style={styles.list}>
        {orders.map(o => {
          const s = STATUS_LABELS[o.status] || STATUS_LABELS.pending_payment
          const nextStatuses = NEXT_STATUSES[o.status] || []
          const fmt = cents => `R${(cents / 100).toFixed(2)}`
          return (
            <div key={o.id} style={styles.orderCard}>
              <div style={styles.orderTop}>
                <div>
                  <span style={styles.orderId}>{o.display_id}</span>
                  <span style={{ ...styles.badge, color: s.color, background: s.bg }}>
                    {s.label}
                  </span>
                </div>
                <span style={styles.orderAmount}>{fmt(o.total_cents)}</span>
              </div>
              <div style={styles.orderMeta}>
                <span>{o.customer_name}</span>
                <span>·</span>
                <span>{o.customer_phone}</span>
                <span>·</span>
                <span>{o.delivery_slot || '—'}</span>
                <span>·</span>
                <span>{o.channel === 'whatsapp' ? '💬 WhatsApp' : '🌐 Web'}</span>
              </div>
              <p style={styles.orderDate}>{new Date(o.created_at).toLocaleString('en-ZA')}</p>

              <div style={styles.actions}>
                <button onClick={() => setDetailId(o.id)} style={styles.btnGhost}>📋 Details / pack</button>
                {nextStatuses.map(ns => (
                  <button
                    key={ns}
                    disabled={updating === o.id}
                    onClick={() => advance(o.id, ns)}
                    style={ns === 'cancelled' ? styles.btnDanger : styles.btnAction}
                  >
                    {updating === o.id ? '…' : `→ ${STATUS_LABELS[ns]?.label || ns}`}
                  </button>
                ))}
              </div>
            </div>
          )
        })}
      </div>

      {detailId && (
        <OrderDetailDrawer tenantId={tenantId} orderId={detailId} onClose={() => setDetailId(null)} />
      )}
    </div>
  )
}

// ── Order detail + packing slip ─────────────────────────────────────────────

function OrderDetailDrawer({ tenantId, orderId, onClose }) {
  const [order, setOrder] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/orders/${orderId}`)
      .then(r => r.json()).then(setOrder).catch(() => {}).finally(() => setLoading(false))
  }, [tenantId, orderId])

  const fmt = c => `R${((c || 0) / 100).toFixed(2)}`
  const items = order?.commerce_order_items || []

  function printSlip() {
    const w = window.open('', '_blank')
    if (!w) return
    const rows = items.map(i =>
      `<tr><td>${i.product_name || i.name || 'Item'}</td><td style="text-align:right">${i.quantity}</td><td style="text-align:right">${fmt(i.unit_price_cents || i.price_cents)}</td></tr>`
    ).join('')
    w.document.write(`
      <html><head><title>Packing slip ${order.display_id}</title>
      <style>body{font-family:system-ui;padding:24px;color:#1E1E1E}h1{font-size:20px}
      table{width:100%;border-collapse:collapse;margin-top:12px}td,th{padding:6px 4px;border-bottom:1px solid #ddd;text-align:left}</style>
      </head><body>
      <h1>Packing slip — ${order.display_id}</h1>
      <p><strong>${order.customer_name || ''}</strong> · ${order.customer_phone || ''}</p>
      <p>${order.delivery_address || 'No address'} · ${order.delivery_slot || ''}</p>
      ${order.delivery_notes ? `<p>Notes: ${order.delivery_notes}</p>` : ''}
      <table><tr><th>Item</th><th style="text-align:right">Qty</th><th style="text-align:right">Price</th></tr>${rows}</table>
      <h3 style="text-align:right">Total: ${fmt(order.total_cents)}</h3>
      </body></html>`)
    w.document.close(); w.print()
  }

  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={{ ...styles.panel, maxWidth: 480 }} onClick={e => e.stopPropagation()}>
        <div style={styles.header}>
          <div>
            <h2 style={styles.title}>{order?.display_id || 'Order'}</h2>
            <p style={styles.subtitle}>Order detail & packing</p>
          </div>
          <button onClick={onClose} style={styles.closeBtn}>×</button>
        </div>
        <div style={styles.content}>
          {loading ? <p style={styles.loading}>Loading…</p> : !order ? (
            <p style={styles.empty}>Could not load order.</p>
          ) : (
            <>
              <div style={styles.detailBlock}>
                <p style={styles.detailName}>{order.customer_name}</p>
                <p style={styles.detailMeta}>{order.customer_phone}{order.customer_email ? ` · ${order.customer_email}` : ''}</p>
                <p style={styles.detailMeta}>📍 {order.delivery_address || 'No delivery address'}</p>
                <p style={styles.detailMeta}>🕐 {order.delivery_slot || '—'}{order.channel ? ` · ${order.channel}` : ''}</p>
                {order.delivery_notes && <p style={styles.detailNotes}>Note: {order.delivery_notes}</p>}
              </div>

              <p style={styles.detailSection}>Items to pack</p>
              <div style={styles.list}>
                {items.length === 0 ? <p style={styles.detailMeta}>No line items recorded.</p> : items.map((it, i) => (
                  <div key={i} style={styles.packRow}>
                    <span style={{ flex: 1 }}>{it.product_name || it.name || 'Item'}</span>
                    <span style={styles.packQty}>×{it.quantity}</span>
                    <span style={styles.packPrice}>{fmt(it.unit_price_cents || it.price_cents)}</span>
                  </div>
                ))}
              </div>

              <div style={styles.detailTotal}>
                <span>Total</span><span>{fmt(order.total_cents)}</span>
              </div>

              <button onClick={printSlip} style={{ ...styles.btnAction, width: '100%', marginTop: 14, padding: '12px' }}>
                🖨 Print packing slip
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Delivery list ───────────────────────────────────────────────────────────

const SLOT_LABELS = {
  morning:   '🌅 Morning',
  afternoon: '☀️ Afternoon',
  evening:   '🌆 Evening',
}
const DEL_PAID = new Set(['paid', 'confirmed', 'packing', 'dispatched', 'delivered'])

function DeliveryTab({ tenantId }) {
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

  const fmt = c => `R${((c || 0) / 100).toFixed(2)}`
  const orders = data?.orders || []

  // Group by delivery slot, preserving the backend's slot ordering.
  const bySlot = {}
  for (const o of orders) {
    const k = o.delivery_slot || 'unscheduled'
    ;(bySlot[k] = bySlot[k] || []).push(o)
  }
  const slots = Object.keys(bySlot)

  function printRun() {
    const w = window.open('', '_blank')
    if (!w) return
    const blocks = slots.map(slot => {
      const rows = bySlot[slot].map(o => {
        const items = (o.commerce_order_items || [])
          .map(i => `${i.product_name || 'Item'} ×${i.quantity}`).join(', ')
        return `<tr><td>${o.display_id}</td><td>${o.customer_name || ''}<br>${o.customer_phone || ''}</td>`
          + `<td>${o.delivery_address || '—'}</td><td>${items}</td>`
          + `<td style="text-align:right">${fmt(o.total_cents)}${DEL_PAID.has(o.status) ? '' : ' (UNPAID)'}</td></tr>`
      }).join('')
      return `<h2>${SLOT_LABELS[slot] || slot}</h2><table>`
        + `<tr><th>#</th><th>Customer</th><th>Address</th><th>Items</th><th style="text-align:right">Total</th></tr>${rows}</table>`
    }).join('')
    w.document.write(`<html><head><title>Delivery run — ${date}</title>
      <style>body{font-family:system-ui;padding:24px;color:#1E1E1E}h1{font-size:20px}h2{font-size:15px;margin-top:18px}
      table{width:100%;border-collapse:collapse;margin-top:6px}td,th{padding:6px 4px;border-bottom:1px solid #ddd;text-align:left;font-size:12px;vertical-align:top}</style>
      </head><body><h1>Delivery run — ${date}</h1>${blocks || '<p>No deliveries.</p>'}</body></html>`)
    w.document.close(); w.print()
  }

  return (
    <div>
      <div style={styles.delBar}>
        <input type="date" value={date} onChange={e => setDate(e.target.value)} style={styles.dateInput} />
        <button onClick={() => setDate(today)} style={styles.btnGhost}>Today</button>
        {orders.length > 0 && (
          <button onClick={printRun} style={{ ...styles.btnAction, marginLeft: 'auto' }}>🖨 Print run sheet</button>
        )}
      </div>

      {!loading && data && (
        <div style={styles.statGrid}>
          <StatCard label="Deliveries" value={data.total} sub={`${slots.length} slot${slots.length !== 1 ? 's' : ''}`} accent="var(--accent, #2C5545)" />
          <StatCard label="Paid"   value={data.paid_count}   sub={fmt(data.paid_revenue_cents)}   accent="#16a34a" />
          <StatCard label="Unpaid" value={data.unpaid_count} sub={fmt(data.unpaid_revenue_cents)} accent="#f59e0b" />
          <StatCard label="To collect" value={fmt((data.paid_revenue_cents || 0) + (data.unpaid_revenue_cents || 0))} sub="total value" />
        </div>
      )}

      {loading && <p style={styles.loading}>Loading delivery list…</p>}
      {!loading && orders.length === 0 && <p style={styles.empty}>No deliveries scheduled for {date}.</p>}

      {slots.map(slot => (
        <div key={slot} style={{ marginBottom: 18 }}>
          <p style={styles.slotHeader}>
            {SLOT_LABELS[slot] || slot}<span style={styles.slotCount}> · {bySlot[slot].length}</span>
          </p>
          <div style={styles.list}>
            {bySlot[slot].map(o => {
              const paid = DEL_PAID.has(o.status)
              const items = o.commerce_order_items || []
              return (
                <div key={o.id} style={styles.orderCard}>
                  <div style={styles.orderTop}>
                    <div>
                      <span style={styles.orderId}>{o.display_id}</span>
                      <span style={{ ...styles.badge, color: paid ? '#16a34a' : '#f59e0b', background: paid ? 'rgba(34,197,94,0.12)' : 'rgba(245,158,11,0.12)' }}>
                        {paid ? 'Paid' : 'Unpaid'}
                      </span>
                    </div>
                    <span style={styles.orderAmount}>{fmt(o.total_cents)}</span>
                  </div>
                  <div style={styles.orderMeta}>
                    <span>{o.customer_name}</span><span>·</span><span>{o.customer_phone}</span>
                  </div>
                  <p style={styles.delAddress}>📍 {o.delivery_address || 'No address'}</p>
                  {o.delivery_notes && <p style={styles.detailNotes}>Note: {o.delivery_notes}</p>}
                  {items.length > 0 && (
                    <p style={styles.delItems}>
                      {items.map((i, idx) => `${i.product_name || 'Item'} ×${i.quantity}`).join(' · ')}
                    </p>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Products ──────────────────────────────────────────────────────────────────

function ProductsTab({ tenantId }) {
  const [products, setProducts] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(null)
  const [editPrice, setEditPrice] = useState({}) // id → string
  const [expandedId, setExpandedId] = useState(null) // id of expanded product card
  const [showAdd, setShowAdd] = useState(false)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ name: '', price: '', category: 'extras', sold_by: 'pack', description: '' })

  const load = useCallback(async () => {
    setLoading(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products`)
    const d = await r.json()
    setProducts(d.products || [])
    setLoading(false)
  }, [tenantId])

  useEffect(() => { load() }, [load])

  async function patch(productId, data) {
    setSaving(productId)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/${productId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    await load()
    setSaving(null)
  }

  async function createProduct(e) {
    e.preventDefault()
    const cents = Math.round(parseFloat(form.price) * 100)
    if (!form.name.trim() || isNaN(cents) || cents <= 0) return
    setAdding(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: form.name.trim(), price_cents: cents, category: form.category,
        sold_by: form.sold_by, description: form.description.trim(), in_stock: true,
      }),
    })
    setForm({ name: '', price: '', category: 'extras', sold_by: 'pack', description: '' })
    setShowAdd(false)
    setAdding(false)
    await load()
  }

  async function deleteProduct(p) {
    if (!confirm(`Delete "${p.name}"? This cannot be undone.`)) return
    setSaving(p.id)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/${p.id}`, { method: 'DELETE' })
    await load()
    setSaving(null)
  }

  async function savePrice(p) {
    const raw = editPrice[p.id]
    if (!raw) return
    const cents = Math.round(parseFloat(raw) * 100)
    if (isNaN(cents) || cents <= 0) return
    setEditPrice(prev => { const n = { ...prev }; delete n[p.id]; return n })
    await patch(p.id, { price_cents: cents })
  }

  // Group by category
  const grouped = {}
  products.forEach(p => {
    const cat = p.category || 'extras'
    if (!grouped[cat]) grouped[cat] = []
    grouped[cat].push(p)
  })

  if (loading) return <p style={styles.loading}>Loading products…</p>

  return (
    <div>
      {/* Add product */}
      <div style={{ marginBottom: 16 }}>
        {!showAdd ? (
          <button onClick={() => setShowAdd(true)} style={styles.btnAction}>+ Add product</button>
        ) : (
          <form onSubmit={createProduct} style={styles.addProductForm}>
            <input placeholder="Product name" value={form.name} required
                   onChange={e => setForm({ ...form, name: e.target.value })} style={styles.apInput} />
            <div style={{ display: 'flex', gap: 8 }}>
              <input placeholder="Price (R)" type="number" step="0.01" value={form.price} required
                     onChange={e => setForm({ ...form, price: e.target.value })} style={styles.apInput} />
              <select value={form.sold_by} onChange={e => setForm({ ...form, sold_by: e.target.value })} style={styles.apInput}>
                <option value="pack">per pack / item</option>
                <option value="kg">per kg</option>
              </select>
              <select value={form.category} onChange={e => setForm({ ...form, category: e.target.value })} style={styles.apInput}>
                {Object.keys(CATEGORY_LABELS).map(c => <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>)}
              </select>
            </div>
            <textarea placeholder="Description (optional)" rows={2} value={form.description}
                      onChange={e => setForm({ ...form, description: e.target.value })} style={styles.apInput} />
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="submit" disabled={adding} style={styles.btnAction}>{adding ? 'Adding…' : 'Add product'}</button>
              <button type="button" onClick={() => setShowAdd(false)} style={styles.btnGhost}>Cancel</button>
            </div>
            <p style={{ fontSize: 11, color: '#8A8680', fontFamily: 'system-ui', margin: 0 }}>
              You can add a photo after creating, via the 📷 Photos button.
            </p>
          </form>
        )}
      </div>

      {Object.entries(grouped).map(([cat, items]) => (
        <div key={cat} style={{ marginBottom: 24 }}>
          <h3 style={styles.catHeader}>{CATEGORY_LABELS[cat] || cat}</h3>
          <div style={styles.list}>
            {items.map(p => (
              <div key={p.id} style={{ ...styles.productCard, opacity: p.in_stock ? 1 : 0.6 }}>
                <div style={styles.productTop}>
                  <div style={{ flex: 1 }}>
                    <span style={styles.productName}>{p.name}</span>
                    {p.is_daily_catch && (
                      <span style={{ ...styles.badge, color: '#f59e0b', background: 'rgba(245,158,11,0.12)', marginLeft: 6 }}>
                        ⭐ Catch of the day
                      </span>
                    )}
                  </div>

                  {/* Stock toggle */}
                  <button
                    disabled={saving === p.id}
                    onClick={() => patch(p.id, { in_stock: !p.in_stock })}
                    style={p.in_stock ? styles.btnStock : styles.btnStockOff}
                  >
                    {saving === p.id ? '…' : p.in_stock ? '✓ In stock' : '✗ Out of stock'}
                  </button>
                </div>

                <div style={styles.productMeta}>
                  {/* Inline price edit */}
                  <div style={styles.priceRow}>
                    <span style={styles.priceLabel}>R</span>
                    <input
                      style={styles.priceInput}
                      value={editPrice[p.id] !== undefined ? editPrice[p.id] : (p.price_cents / 100).toFixed(2)}
                      onChange={e => setEditPrice(prev => ({ ...prev, [p.id]: e.target.value }))}
                      onBlur={() => savePrice(p)}
                      onKeyDown={e => e.key === 'Enter' && savePrice(p)}
                    />
                    <span style={styles.priceUnit}>/{p.sold_by === 'kg' ? 'kg' : 'pack'}</span>
                  </div>

                  {/* Catch of the day toggle */}
                  <button
                    disabled={saving === p.id}
                    onClick={() => patch(p.id, { is_daily_catch: !p.is_daily_catch })}
                    style={styles.btnGhost}
                  >
                    {p.is_daily_catch ? 'Remove catch' : 'Mark catch of day'}
                  </button>

                  {/* Expand for image upload */}
                  <button
                    onClick={() => setExpandedId(expandedId === p.id ? null : p.id)}
                    style={styles.btnGhost}
                  >
                    {expandedId === p.id ? '▲ Less' : '📷 Photos'}
                  </button>
                </div>

                {/* Expanded: image upload + description */}
                {expandedId === p.id && (
                  <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid #EDE9DF' }}>
                    <p style={{ fontSize: 12, fontFamily: 'system-ui', fontWeight: 600, color: '#1E1E1E', margin: '0 0 8px' }}>
                      Product photos
                    </p>
                    <VulaImageUpload
                      tenantId={tenantId}
                      existingUrls={p.image_url ? [p.image_url] : []}
                      maxFiles={3}
                      onUploaded={(urls) => {
                        if (urls.length > 0) patch(p.id, { image_url: urls[0] })
                      }}
                    />
                    <div style={{ marginTop: 12 }}>
                      <p style={{ fontSize: 12, fontFamily: 'system-ui', fontWeight: 600, color: '#1E1E1E', margin: '0 0 6px' }}>
                        Description / notes
                      </p>
                      <textarea
                        defaultValue={p.description || p.notes || ''}
                        rows={3}
                        onBlur={e => {
                          const val = e.target.value.trim()
                          if (val !== (p.description || p.notes || '')) {
                            patch(p.id, { description: val })
                          }
                        }}
                        style={{
                          width: '100%', padding: '8px 10px',
                          border: '1px solid #DDD8CE', borderRadius: 6,
                          fontFamily: 'system-ui', fontSize: 13, color: '#1E1E1E',
                          resize: 'vertical', boxSizing: 'border-box',
                        }}
                        placeholder="e.g. Skin-on, boneless, great for braaing"
                      />
                    </div>
                    <button
                      onClick={() => deleteProduct(p)}
                      disabled={saving === p.id}
                      style={styles.btnDeleteProduct}
                    >
                      🗑 Delete product
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Suppliers ───────────────────────────────────────────────────────────────────

const BLANK_SUPPLIER = {
  name: '', aliases: '', payment_terms_days: 30, category: 'general',
  contact_phone: '', contact_email: '', account_number: '', tax_id: '', notes: '',
}

function SuppliersTab({ tenantId }) {
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
    if (!confirm(`Delete supplier "${s.name}"?`)) return
    setSaving(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/suppliers/${s.id}`, { method: 'DELETE' })
    await load()
    setSaving(false)
  }

  if (loading) return <p style={styles.loading}>Loading suppliers…</p>

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        {!editing ? (
          <button onClick={startNew} style={styles.btnAction}>+ Add supplier</button>
        ) : (
          <form onSubmit={save} style={styles.addProductForm}>
            <input placeholder="Supplier name" value={form.name} required
                   onChange={e => setForm({ ...form, name: e.target.value })} style={styles.apInput} />
            <div style={{ display: 'flex', gap: 8 }}>
              <input placeholder="Payment terms (days)" type="number" value={form.payment_terms_days}
                     onChange={e => setForm({ ...form, payment_terms_days: e.target.value })} style={styles.apInput} />
              <input placeholder="Tax / VAT no." value={form.tax_id || ''}
                     onChange={e => setForm({ ...form, tax_id: e.target.value })} style={styles.apInput} />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input placeholder="Phone" value={form.contact_phone || ''}
                     onChange={e => setForm({ ...form, contact_phone: e.target.value })} style={styles.apInput} />
              <input placeholder="Email" value={form.contact_email || ''}
                     onChange={e => setForm({ ...form, contact_email: e.target.value })} style={styles.apInput} />
            </div>
            <input placeholder="Aliases (comma-separated)" value={form.aliases}
                   onChange={e => setForm({ ...form, aliases: e.target.value })} style={styles.apInput} />
            <textarea placeholder="Notes (optional)" rows={2} value={form.notes || ''}
                      onChange={e => setForm({ ...form, notes: e.target.value })} style={styles.apInput} />
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="submit" disabled={saving} style={styles.btnAction}>
                {saving ? 'Saving…' : (editing.id ? 'Save changes' : 'Add supplier')}
              </button>
              <button type="button" onClick={cancel} style={styles.btnGhost}>Cancel</button>
            </div>
            <p style={{ fontSize: 11, color: '#8A8680', fontFamily: 'system-ui', margin: 0 }}>
              Tax number & aliases improve auto-matching when you scan this supplier's bills.
            </p>
          </form>
        )}
      </div>

      {suppliers.length === 0 ? (
        <p style={styles.empty}>No suppliers yet. Add one so scanned bills auto-fill payment terms.</p>
      ) : (
        <div style={styles.list}>
          {suppliers.map(s => (
            <div key={s.id} style={styles.productCard}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                <div>
                  <span style={styles.productName}>{s.name}</span>
                  <span style={{ ...styles.statSub, marginLeft: 8 }}>{s.payment_terms_days ?? 30} day terms</span>
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button onClick={() => startEdit(s)} style={styles.btnGhost}>Edit</button>
                  <button onClick={() => remove(s)} disabled={saving} style={styles.btnDanger}>Delete</button>
                </div>
              </div>
              <div style={{ ...styles.statSub, marginTop: 4 }}>
                {[
                  s.tax_id ? `VAT ${s.tax_id}` : null,
                  s.contact_phone || null,
                  s.contact_email || null,
                  (s.aliases && s.aliases.length)
                    ? `aka ${Array.isArray(s.aliases) ? s.aliases.join(', ') : s.aliases}` : null,
                ].filter(Boolean).join(' · ') || '—'}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = {
  overlay:      { position:'fixed', inset:0, background:'rgba(0,0,0,0.5)', zIndex:200, display:'flex', justifyContent:'flex-end' },
  panel:        { width:'100%', maxWidth:680, background:'#F7F4EE', overflowY:'auto', display:'flex', flexDirection:'column', boxShadow:'-4px 0 24px rgba(0,0,0,0.15)' },
  fullPage:     { maxWidth:980, margin:'0 auto', background:'#F7F4EE', minHeight:'calc(100vh - 56px)', display:'flex', flexDirection:'column' },
  header:       { display:'flex', alignItems:'flex-start', justifyContent:'space-between', padding:'24px 28px 0', borderBottom:'1px solid #DDD8CE', paddingBottom:16 },
  title:        { fontFamily:"'Cormorant Garamond', serif", fontSize:26, fontWeight:700, color:'#1E1E1E', margin:0 },
  subtitle:     { fontFamily:'system-ui', fontSize:12, color:'#8A8680', margin:'2px 0 0' },
  closeBtn:     { background:'transparent', border:'none', fontSize:28, cursor:'pointer', color:'#8A8680', lineHeight:1 },
  tabs:         { display:'flex', alignItems:'center', borderBottom:'1px solid #DDD8CE', padding:'0 28px', overflowX:'auto', whiteSpace:'nowrap' },
  tab:          { padding:'12px 14px', border:'none', background:'transparent', cursor:'pointer', fontFamily:'system-ui', fontSize:13, color:'#8A8680', borderBottom:'2px solid transparent', flex:'0 0 auto' },
  tabActive:    { color:'var(--accent, #2C5545)', borderBottom:'2px solid var(--accent, #2C5545)', fontWeight:600 },
  tabDivider:   { width:1, height:18, background:'#DDD8CE', margin:'0 6px', flex:'0 0 auto' },
  content:      { padding:'20px 28px', flex:1, overflowY:'auto' },
  loading:      { color:'#8A8680', fontSize:13, fontFamily:'system-ui' },
  empty:        { color:'#8A8680', fontSize:13, fontFamily:'system-ui', padding:'24px 0', textAlign:'center' },
  error:        { color:'#ef4444', fontSize:13, fontFamily:'system-ui' },

  statGrid:     { display:'grid', gridTemplateColumns:'repeat(2, 1fr)', gap:12, marginBottom:20 },
  statCard:     { background:'#fff', border:'1px solid #DDD8CE', borderRadius:8, padding:'16px 18px' },
  statValue:    { fontFamily:"'Cormorant Garamond', serif", fontSize:28, fontWeight:700, margin:'0 0 4px', color:'var(--accent, #2C5545)' },
  statLabel:    { fontFamily:'system-ui', fontSize:12, fontWeight:600, color:'#1E1E1E', margin:'0 0 2px' },
  statSub:      { fontFamily:'system-ui', fontSize:11, color:'#8A8680', margin:0 },

  delBar:       { display:'flex', alignItems:'center', gap:8, marginBottom:16 },
  dateInput:    { padding:'7px 10px', border:'1px solid #DDD8CE', borderRadius:6, fontFamily:'system-ui', fontSize:13, color:'#1E1E1E' },
  slotHeader:   { fontFamily:"'Cormorant Garamond', serif", fontSize:18, fontWeight:700, color:'#1E1E1E', margin:'0 0 8px' },
  slotCount:    { fontFamily:'system-ui', fontSize:12, fontWeight:400, color:'#8A8680' },
  delAddress:   { fontFamily:'system-ui', fontSize:12, color:'#6B7280', margin:'2px 0' },
  delItems:     { fontFamily:'system-ui', fontSize:12, color:'#1E1E1E', margin:'6px 0 0' },

  chips:        { display:'flex', gap:6, flexWrap:'wrap', marginBottom:16 },
  chip:         { padding:'5px 12px', borderRadius:20, border:'1px solid #DDD8CE', background:'#fff', cursor:'pointer', fontSize:12, fontFamily:'system-ui', color:'#8A8680' },
  chipActive:   { background:'var(--accent, #2C5545)', color:'#fff', border:'1px solid var(--accent, #2C5545)' },

  list:         { display:'flex', flexDirection:'column', gap:8 },
  orderCard:    { background:'#fff', border:'1px solid #DDD8CE', borderRadius:8, padding:'14px 16px' },
  orderTop:     { display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:4 },
  orderId:      { fontFamily:"'Source Code Pro', monospace", fontSize:13, fontWeight:600, color:'#1E1E1E', marginRight:8 },
  orderAmount:  { fontFamily:'system-ui', fontSize:15, fontWeight:700, color:'var(--accent, #2C5545)' },
  orderMeta:    { fontFamily:'system-ui', fontSize:12, color:'#8A8680', display:'flex', gap:6, marginBottom:4, flexWrap:'wrap' },
  orderDate:    { fontFamily:'system-ui', fontSize:11, color:'#B5B0A8', margin:'2px 0 8px' },
  badge:        { padding:'2px 8px', borderRadius:12, fontSize:11, fontWeight:600 },
  actions:      { display:'flex', gap:6, flexWrap:'wrap' },
  btnAction:    { padding:'5px 12px', background:'var(--accent, #2C5545)', color:'#fff', border:'none', borderRadius:6, cursor:'pointer', fontSize:12, fontFamily:'system-ui', fontWeight:600 },
  btnDanger:    { padding:'5px 12px', background:'transparent', color:'#ef4444', border:'1px solid rgba(239,68,68,0.3)', borderRadius:6, cursor:'pointer', fontSize:12, fontFamily:'system-ui' },

  catHeader:    { fontFamily:"'Cormorant Garamond', serif", fontSize:18, fontWeight:700, color:'#1E1E1E', margin:'0 0 8px' },
  productCard:  { background:'#fff', border:'1px solid #DDD8CE', borderRadius:8, padding:'12px 14px' },
  productTop:   { display:'flex', alignItems:'center', gap:8, marginBottom:8 },
  productName:  { fontFamily:'system-ui', fontSize:13, fontWeight:600, color:'#1E1E1E' },
  productMeta:  { display:'flex', alignItems:'center', gap:10 },
  priceRow:     { display:'flex', alignItems:'center', gap:2 },
  priceLabel:   { fontFamily:'system-ui', fontSize:13, color:'#1E1E1E', fontWeight:600 },
  priceInput:   { width:60, padding:'3px 6px', border:'1px solid #DDD8CE', borderRadius:4, fontFamily:'system-ui', fontSize:13, color:'#1E1E1E', textAlign:'right' },
  priceUnit:    { fontFamily:'system-ui', fontSize:12, color:'#8A8680' },
  btnStock:     { padding:'4px 10px', background:'rgba(34,197,94,0.12)', color:'#16a34a', border:'1px solid rgba(34,197,94,0.3)', borderRadius:20, cursor:'pointer', fontSize:12, fontFamily:'system-ui', fontWeight:600, whiteSpace:'nowrap' },
  btnStockOff:  { padding:'4px 10px', background:'rgba(239,68,68,0.1)', color:'#ef4444', border:'1px solid rgba(239,68,68,0.3)', borderRadius:20, cursor:'pointer', fontSize:12, fontFamily:'system-ui', fontWeight:600, whiteSpace:'nowrap' },
  btnGhost:     { padding:'4px 10px', background:'transparent', color:'#8A8680', border:'1px solid #DDD8CE', borderRadius:20, cursor:'pointer', fontSize:11, fontFamily:'system-ui' },
  addProductForm:{ display:'flex', flexDirection:'column', gap:8, background:'#fff', border:'1px solid #DDD8CE', borderRadius:10, padding:14 },
  apInput:      { flex:1, padding:'9px 11px', border:'1px solid #DDD8CE', borderRadius:6, fontFamily:'system-ui', fontSize:13, boxSizing:'border-box' },
  btnDeleteProduct:{ marginTop:12, padding:'7px 12px', background:'transparent', color:'#ef4444', border:'1px solid rgba(239,68,68,0.3)', borderRadius:6, cursor:'pointer', fontSize:12, fontFamily:'system-ui' },
  detailBlock:  { background:'#fff', border:'1px solid #DDD8CE', borderRadius:8, padding:14, marginBottom:14 },
  detailName:   { fontFamily:'system-ui', fontSize:15, fontWeight:700, color:'#1E1E1E', margin:'0 0 4px' },
  detailMeta:   { fontFamily:'system-ui', fontSize:13, color:'#6B7280', margin:'2px 0' },
  detailNotes:  { fontFamily:'system-ui', fontSize:13, color:'#1E1E1E', margin:'6px 0 0', fontStyle:'italic' },
  detailSection:{ fontFamily:'system-ui', fontSize:12, fontWeight:600, color:'#1E1E1E', margin:'0 0 8px' },
  packRow:      { display:'flex', alignItems:'center', gap:10, background:'#fff', border:'1px solid #DDD8CE', borderRadius:8, padding:'10px 12px', fontFamily:'system-ui', fontSize:14, color:'#1E1E1E' },
  packQty:      { fontWeight:700, color:'var(--accent, #2C5545)' },
  packPrice:    { color:'#6B7280', fontSize:13, minWidth:70, textAlign:'right' },
  detailTotal:  { display:'flex', justifyContent:'space-between', fontFamily:'system-ui', fontWeight:700, fontSize:16, color:'var(--accent, #2C5545)', marginTop:12, paddingTop:10, borderTop:'1px solid #DDD8CE' },
}
