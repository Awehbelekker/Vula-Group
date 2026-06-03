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

export default function VulaMerchantAdmin({ tenantId, tenantName, onClose, fullPage = false }) {
  const [tab, setTab] = useState('orders')
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

        {/* Tabs */}
        <div style={styles.tabs}>
          {[
            { id: 'overview',  label: '📊 Overview' },
            { id: 'orders',    label: '📦 Orders' },
            { id: 'products',  label: '🐟 Products' },
            { id: 'scanner',   label: '📷 Scanner' },
            { id: 'invoices',  label: '🧾 Invoices' },
            { id: 'budget',    label: '💰 Budget' },
            { id: 'broadcast', label: '📢 Broadcast' },
          ].map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              style={{ ...styles.tab, ...(tab === t.id ? styles.tabActive : {}) }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div style={styles.content}>
          {tab === 'overview'  && <OverviewTab tenantId={tenantId} />}
          {tab === 'orders'    && <OrdersTab   tenantId={tenantId} />}
          {tab === 'products'  && <ProductsTab tenantId={tenantId} />}
          {tab === 'scanner'   && <VulaSmartScanner tenantId={tenantId} products={products} />}
          {tab === 'invoices'  && <VulaInvoices     tenantId={tenantId} products={products} />}
          {tab === 'budget'    && <VulaBudget        tenantId={tenantId} />}
          {tab === 'broadcast' && <VulaBroadcast     tenantId={tenantId} />}
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

function OverviewTab({ tenantId }) {
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

  const fmt = cents => `R${(cents / 100).toFixed(2)}`

  return (
    <div>
      <div style={styles.statGrid}>
        <StatCard label="Today's revenue"    value={fmt(stats.today_revenue_cents)}  sub={`${stats.today_orders} orders`} accent="#2DAAB5" />
        <StatCard label="Total revenue"      value={fmt(stats.total_revenue_cents)}  sub={`${stats.total_orders} orders`} />
        <StatCard label="To dispatch"        value={stats.to_dispatch}               sub="paid / confirmed / packing" accent="#8b5cf6" />
        <StatCard label="Pending payment"    value={stats.pending_payment}           sub="awaiting checkout" accent="#f59e0b" />
      </div>
    </div>
  )
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

              {nextStatuses.length > 0 && (
                <div style={styles.actions}>
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
              )}
            </div>
          )
        })}
      </div>
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
      {Object.entries(grouped).map(([cat, items]) => (
        <div key={cat} style={{ marginBottom: 24 }}>
          <h3 style={styles.catHeader}>{CATEGORY_LABELS[cat] || cat}</h3>
          <div style={styles.list}>
            {items.map(p => (
              <div key={p.id} style={{ ...styles.productCard, opacity: p.in_stock ? 1 : 0.6 }}>
                <div style={styles.productTop}>
                  <div style={{ flex: 1 }}>
                    <span style={styles.productName}>{p.name}</span>
                    {p.is_weekly_special && (
                      <span style={{ ...styles.badge, color: '#f59e0b', background: 'rgba(245,158,11,0.12)', marginLeft: 6 }}>
                        ⭐ Special
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

                  {/* Weekly special toggle */}
                  <button
                    disabled={saving === p.id}
                    onClick={() => patch(p.id, { is_weekly_special: !p.is_weekly_special })}
                    style={styles.btnGhost}
                  >
                    {p.is_weekly_special ? 'Remove special' : 'Mark special'}
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

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = {
  overlay:      { position:'fixed', inset:0, background:'rgba(0,0,0,0.5)', zIndex:200, display:'flex', justifyContent:'flex-end' },
  panel:        { width:'100%', maxWidth:680, background:'#F7F4EE', overflowY:'auto', display:'flex', flexDirection:'column', boxShadow:'-4px 0 24px rgba(0,0,0,0.15)' },
  fullPage:     { maxWidth:980, margin:'0 auto', background:'#F7F4EE', minHeight:'calc(100vh - 56px)', display:'flex', flexDirection:'column' },
  header:       { display:'flex', alignItems:'flex-start', justifyContent:'space-between', padding:'24px 28px 0', borderBottom:'1px solid #DDD8CE', paddingBottom:16 },
  title:        { fontFamily:"'Cormorant Garamond', serif", fontSize:26, fontWeight:700, color:'#1E1E1E', margin:0 },
  subtitle:     { fontFamily:'system-ui', fontSize:12, color:'#8A8680', margin:'2px 0 0' },
  closeBtn:     { background:'transparent', border:'none', fontSize:28, cursor:'pointer', color:'#8A8680', lineHeight:1 },
  tabs:         { display:'flex', borderBottom:'1px solid #DDD8CE', padding:'0 28px' },
  tab:          { padding:'12px 16px', border:'none', background:'transparent', cursor:'pointer', fontFamily:'system-ui', fontSize:13, color:'#8A8680', borderBottom:'2px solid transparent' },
  tabActive:    { color:'#2C5545', borderBottom:'2px solid #2C5545', fontWeight:600 },
  content:      { padding:'20px 28px', flex:1, overflowY:'auto' },
  loading:      { color:'#8A8680', fontSize:13, fontFamily:'system-ui' },
  empty:        { color:'#8A8680', fontSize:13, fontFamily:'system-ui', padding:'24px 0', textAlign:'center' },
  error:        { color:'#ef4444', fontSize:13, fontFamily:'system-ui' },

  statGrid:     { display:'grid', gridTemplateColumns:'repeat(2, 1fr)', gap:12, marginBottom:20 },
  statCard:     { background:'#fff', border:'1px solid #DDD8CE', borderRadius:8, padding:'16px 18px' },
  statValue:    { fontFamily:"'Cormorant Garamond', serif", fontSize:28, fontWeight:700, margin:'0 0 4px', color:'#2C5545' },
  statLabel:    { fontFamily:'system-ui', fontSize:12, fontWeight:600, color:'#1E1E1E', margin:'0 0 2px' },
  statSub:      { fontFamily:'system-ui', fontSize:11, color:'#8A8680', margin:0 },

  chips:        { display:'flex', gap:6, flexWrap:'wrap', marginBottom:16 },
  chip:         { padding:'5px 12px', borderRadius:20, border:'1px solid #DDD8CE', background:'#fff', cursor:'pointer', fontSize:12, fontFamily:'system-ui', color:'#8A8680' },
  chipActive:   { background:'#2C5545', color:'#fff', border:'1px solid #2C5545' },

  list:         { display:'flex', flexDirection:'column', gap:8 },
  orderCard:    { background:'#fff', border:'1px solid #DDD8CE', borderRadius:8, padding:'14px 16px' },
  orderTop:     { display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:4 },
  orderId:      { fontFamily:"'Source Code Pro', monospace", fontSize:13, fontWeight:600, color:'#1E1E1E', marginRight:8 },
  orderAmount:  { fontFamily:'system-ui', fontSize:15, fontWeight:700, color:'#2C5545' },
  orderMeta:    { fontFamily:'system-ui', fontSize:12, color:'#8A8680', display:'flex', gap:6, marginBottom:4, flexWrap:'wrap' },
  orderDate:    { fontFamily:'system-ui', fontSize:11, color:'#B5B0A8', margin:'2px 0 8px' },
  badge:        { padding:'2px 8px', borderRadius:12, fontSize:11, fontWeight:600 },
  actions:      { display:'flex', gap:6, flexWrap:'wrap' },
  btnAction:    { padding:'5px 12px', background:'#2C5545', color:'#fff', border:'none', borderRadius:6, cursor:'pointer', fontSize:12, fontFamily:'system-ui', fontWeight:600 },
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
}
