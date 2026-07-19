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

import { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react'
import VulaImageUpload from './VulaImageUpload'
import { downloadCsv } from '../lib/csv'
import VulaSmartScanner from './VulaSmartScanner'
import VulaInvoices from './VulaInvoices'
import VulaBookings from './VulaBookings'
import VulaMarketing from './VulaMarketing'
import VulaFinanceInsights from './VulaFinanceInsights'
import VulaBankRec from './VulaBankRec'
import VulaAccounting from './VulaAccounting'
import VulaLabour from './VulaLabour'
import VulaExpenses from './VulaExpenses'
import VulaImport from './VulaImport'
import VulaRecurringOrders from './VulaRecurringOrders'
import VulaAgentActivity from './VulaAgentActivity'
import VulaBudget from './VulaBudget'
import VulaBroadcast from './VulaBroadcast'
import VulaWATemplates from './VulaWATemplates'
import VulaScheduledJobs from './VulaScheduledJobs'
import VulaCustomers from './VulaCustomers'
import VulaAssistant from './VulaAssistant'
import VulaInbox from './VulaInbox'
import VulaSettings from './VulaSettings'
import VulaDocuments from './VulaDocuments'
import VulaProjects from './VulaProjects'
import VulaQSRates from './VulaQSRates'
import VulaContacts from './VulaContacts'
import VulaFinances from './VulaFinances'
import VulaFollowups from './VulaFollowups'
import VulaTeam from './VulaTeam'
import VulaProjectWorkspace from './VulaProjectWorkspace'
import VulaFieldOps from './VulaFieldOps'
import VulaReports from './VulaReports'
import VulaOrderWorkflow from './VulaOrderWorkflow'
import VulaClientOnboarding from './VulaClientOnboarding'
import VulaCSMetrics from './VulaCSMetrics'
import VulaQS from './VulaQS'
import VulaQSPro from './VulaQSPro'
import VulaTakeoff from './VulaTakeoff'
import VulaDraft from './VulaDraft'
import VulaTraining from './VulaTraining'
import VulaAutomations from './VulaAutomations'
// Lazy-loaded: the Puck page builder is ~1 MB — keep it out of the main bundle until the Pages tab opens.
const VulaPages = lazy(() => import('./VulaPages'))
import VulaPayments from './VulaPayments'

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

export default function VulaMerchantAdmin({ tenantId, tenantName, onClose, fullPage = false, access = [], full = true, activeTab, onTabChange }) {
  // Controlled mode (UI overhaul shell): the sidebar (VulaShell via App.jsx) owns navigation and
  // passes activeTab/onTabChange — we skip our own header + tab strip. Uncontrolled mode keeps
  // the original internal tabs (still used by the master "Manage tenant" modal).
  const controlled = activeTab !== undefined
  const [tabState, setTabState] = useState('orders')
  const tab = controlled ? activeTab : tabState
  const setTab = controlled ? (onTabChange || (() => {})) : setTabState
  // A member with a defined access list sees only those modules (+ overview). Owners/
  // managers (full) see everything including Team/Settings.
  const canSee = (id) => full || id === 'overview' || (access || []).includes(id)
  // If the current tab isn't visible to this member, fall back to a safe default.
  useEffect(() => { if (!canSee(tab)) setTab('overview') }, [access, full])  // eslint-disable-line
  const [products, setProducts] = useState([])
  const [modules, setModules] = useState(null)   // tenant's enabled capability keys (control plane)
  const [broadcastDraft, setBroadcastDraft] = useState(null)   // Marketing → "Send as broadcast" handoff (P2.1)

  // Tenant-level module gating (business-type driven). Always show core tabs; map a few
  // tab ids to their module key. null/empty modules = show everything (no config yet).
  const CORE = new Set(['overview', 'assistant', 'agentlog', 'inbox', 'settings', 'suppliers', 'qsrates', 'pages', 'marketing', 'bank', 'books', 'labour', 'expenses', 'import', 'wa-templates', 'scheduling'])
  const MODMAP = {
    customers: 'crm', contacts: 'crm', broadcast: 'broadcasts', subscriptions: 'orders',
    qs: 'estimating', qspro: 'estimating', takeoff: 'estimating', draft: 'ai_draft', training: 'training',
  }
  const tenantHas = (id) => modules === null || !modules.length || CORE.has(id)
    || (modules || []).includes(MODMAP[id] || id)

  // Load products + tenant modules
  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products`)
      .then(r => r.json()).then(d => setProducts(d.products || [])).catch(() => {})
    fetch(`${VULA_API}/v1/tenants/${tenantId}`)
      .then(r => r.json()).then(d => setModules(d.modules || [])).catch(() => setModules([]))
  }, [tenantId])

  // Inner content shared by modal + full-page modes.
  const inner = (
    <>
        {/* Header + tab strip — hidden in controlled mode (the shell provides both) */}
        {!controlled && <div style={styles.header}>
          <div>
            <h2 style={styles.title}>{tenantName}</h2>
            <p style={styles.subtitle}>Merchant admin</p>
          </div>
          {!fullPage && <button onClick={onClose} style={styles.closeBtn}>×</button>}
        </div>}

        {!controlled && <div style={styles.tabs}>
          {(() => {
            const GROUPS = [
              [{ id: 'overview', label: '📊 Overview' }, { id: 'reports', label: '📈 Reports' }, { id: 'assistant', label: '💬 Assistant' }, { id: 'agentlog', label: '🧠 Agent' }, { id: 'inbox', label: '📥 Inbox' }, { id: 'automations', label: '⚡ Automations' }],
              [{ id: 'orders', label: '📦 Orders' }, { id: 'subscriptions', label: '🔁 Subscriptions' }, { id: 'bookings', label: '📅 Bookings' }, { id: 'delivery', label: '🛵 Delivery' }, { id: 'products', label: '🐟 Products' }, { id: 'suppliers', label: '🚚 Suppliers' }],
              [{ id: 'invoices', label: '🧾 Invoices' }, { id: 'bank', label: '🏦 Bank' }, { id: 'books', label: '📒 Books' }, { id: 'labour', label: '👷 Labour' }, { id: 'expenses', label: '💸 Expenses' }, { id: 'payments', label: '💳 Payments' }, { id: 'budget', label: '💰 Budget' }, { id: 'scanner', label: '📷 Scanner' }],
              [{ id: 'customers', label: '👥 Customers' }, { id: 'contacts', label: '📇 Contacts' }, { id: 'import', label: '📥 Import' }, { id: 'followups', label: '📬 Follow-ups' }, { id: 'broadcast', label: '📢 Broadcast' }, { id: 'wa-templates', label: '📨 Templates' }, { id: 'scheduling', label: '⏰ Scheduling' }, { id: 'marketing', label: '✨ Marketing' }, { id: 'pages', label: '🎨 Pages' }],
              [{ id: 'workspace', label: '🗂️ Workspace' }, { id: 'projects', label: '🏗️ Projects' }, { id: 'fieldops', label: '👷 Field Ops' }, { id: 'qsrates', label: '📐 QS Rates' }, { id: 'finances', label: '💵 Finances' }, { id: 'documents', label: '📂 Documents' }],
              [{ id: 'qs', label: '🧮 Quick Cost' }, { id: 'qspro', label: '📐 QS Pro' }, { id: 'takeoff', label: '📏 Takeoff' }, { id: 'draft', label: '✍️ AI Draft' }, { id: 'training', label: '📚 Training KB' }],
              [...(full ? [{ id: 'team', label: '👥 Team' }, { id: 'settings', label: '⚙️ Settings' }] : [])],
            ]
            const items = []
            GROUPS.forEach((g) => {
              const visible = g.filter(t => canSee(t.id) && tenantHas(t.id))
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
        </div>}

        {/* Content */}
        <div style={controlled ? styles.contentBare : styles.content}>
          {tab === 'overview'  && <OverviewTab tenantId={tenantId} setTab={setTab} />}
          {tab === 'assistant' && <VulaAssistant    tenantId={tenantId} />}
          {tab === 'agentlog'  && <VulaAgentActivity tenantId={tenantId} />}
          {tab === 'inbox'     && <VulaInbox        tenantId={tenantId} />}
          {tab === 'orders'    && <OrdersTab   tenantId={tenantId} />}
          {tab === 'bookings'  && <VulaBookings tenantId={tenantId} />}
          {tab === 'subscriptions' && <VulaRecurringOrders tenantId={tenantId} />}
          {tab === 'delivery'  && <><VulaOrderWorkflow tenantId={tenantId} /><DeliveryTab tenantId={tenantId} /></>}
          {tab === 'products'  && <ProductsTab tenantId={tenantId} />}
          {tab === 'suppliers' && <SuppliersTab tenantId={tenantId} />}
          {tab === 'scanner'   && <VulaSmartScanner tenantId={tenantId} products={products} />}
          {tab === 'invoices'  && <VulaInvoices     tenantId={tenantId} products={products} />}
          {tab === 'bank'      && <VulaBankRec      tenantId={tenantId} />}
          {tab === 'books'     && <VulaAccounting  tenantId={tenantId} />}
          {tab === 'labour'    && <VulaLabour      tenantId={tenantId} />}
          {tab === 'expenses'  && <VulaExpenses    tenantId={tenantId} />}
          {tab === 'import'    && <VulaImport      tenantId={tenantId} />}
          {tab === 'budget'    && <VulaBudget        tenantId={tenantId} />}
          {tab === 'customers' && <VulaCustomers     tenantId={tenantId} />}
          {tab === 'contacts'  && <VulaContacts      tenantId={tenantId} />}
          {tab === 'finances'  && <><VulaFinanceInsights tenantId={tenantId} /><VulaFinances tenantId={tenantId} /></>}
          {tab === 'followups' && <VulaFollowups     tenantId={tenantId} />}
          {tab === 'broadcast' && <><VulaClientOnboarding tenantId={tenantId} /><VulaBroadcast tenantId={tenantId} draftBody={broadcastDraft} onConsumeDraft={() => setBroadcastDraft(null)} /></>}
          {tab === 'wa-templates' && <VulaWATemplates tenantId={tenantId} />}
          {tab === 'scheduling' && <VulaScheduledJobs tenantId={tenantId} />}
          {tab === 'marketing' && <VulaMarketing tenantId={tenantId} onSendAsBroadcast={(text) => { setBroadcastDraft(text); setTab('broadcast') }} />}
          {tab === 'qs'       && <VulaQS />}
          {tab === 'qspro'    && <VulaQSPro />}
          {tab === 'takeoff'  && <VulaTakeoff />}
          {tab === 'draft'    && <VulaDraft tenantId={tenantId} />}
          {tab === 'training' && <VulaTraining />}
          {tab === 'automations' && <VulaAutomations tenantId={tenantId} />}
          {tab === 'pages'     && <Suspense fallback={<div style={{ padding: 20, color: '#8A8680' }}>Loading page builder…</div>}><VulaPages tenantId={tenantId} /></Suspense>}
          {tab === 'projects'  && <VulaProjects      tenantId={tenantId} />}
          {tab === 'fieldops'  && <VulaFieldOps     tenantId={tenantId} />}
          {tab === 'reports'   && <><VulaFinanceInsights tenantId={tenantId} /><VulaReports tenantId={tenantId} /></>}
          {tab === 'payments'  && <VulaPayments     tenantId={tenantId} />}
          {tab === 'qsrates'   && <VulaQSRates       tenantId={tenantId} />}
          {tab === 'documents' && <VulaDocuments     tenantId={tenantId} />}
          {tab === 'workspace' && <VulaProjectWorkspace tenantId={tenantId} />}
          {tab === 'team'      && <VulaTeam          tenantId={tenantId} />}
          {tab === 'settings'  && <VulaSettings      tenantId={tenantId} tenantName={tenantName} adminEmail="" />}
        </div>
    </>
  )

  // Full-page mode (owner/staff dedicated admin) — no modal overlay. In controlled/shell mode
  // the shell already provides width + chrome, so no constraining wrapper — and the Puck page
  // editor gets the FULL viewport (a 1100px clamp cripples a visual canvas).
  if (fullPage) {
    return <div style={controlled ? (tab === 'pages' ? undefined : { maxWidth: 1100 }) : styles.fullPage}>{inner}</div>
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
    { show: stats.open_escalations > 0,     label: 'Customer waiting on you', value: stats.open_escalations,   hint: (stats.oldest_escalation?.question || 'answer on WhatsApp').slice(0, 46), tab: 'inbox', color: '#C0392B' },
    { show: stats.to_dispatch > 0,          label: 'To dispatch',      value: stats.to_dispatch,                hint: 'orders ready to send', tab: 'orders',   color: '#8b5cf6' },
    { show: stats.pending_payment > 0,      label: 'Awaiting payment', value: stats.pending_payment,            hint: 'unpaid orders',        tab: 'orders',   color: '#f59e0b' },
    { show: stats.invoice_overdue_cents > 0,label: 'Invoices overdue', value: fmt(stats.invoice_overdue_cents), hint: 'chase these',          tab: 'invoices', color: '#C0392B' },
    { show: stats.low_stock_count > 0,      label: 'Low stock',        value: stats.low_stock_count,            hint: 'items running out',    tab: 'products', color: '#C0392B' },
  ].filter(a => a.show)

  return (
    <div>
      <div style={styles.statGrid}>
        <StatCard label="Today's revenue" value={fmt(stats.today_revenue_cents)} sub={`${stats.today_orders} orders today`} accent="var(--accent, var(--accent))" />
        <StatCard label="Total revenue"   value={fmt(stats.total_revenue_cents)} sub={`${stats.total_orders} orders`} />
        <StatCard label="Avg order value" value={fmt(aov)}                        sub="per paid order" accent="#2B5797" />
        <StatCard label="This week"       value={weekOrders}                      sub="orders (7 days)" accent="#8b5cf6" />
      </div>

      <TrendChart series={series} fmt={fmt} />

      <VulaCSMetrics tenantId={tenantId} />

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

      {/* Glass-box AI + knowledge — what the assistant did and knows (UI overhaul P3) */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: 14, marginTop: 18 }}>
        {(stats.agent_recent || []).length > 0 && (
          <div style={ovS.chartCard}>
            <p style={ovS.sectionLabel}>🧠 Your assistant, recently <button onClick={() => setTab && setTab('agentlog')} style={{ float: 'right', border: 'none', background: 'none', color: 'var(--accent)', fontSize: 12, cursor: 'pointer', fontWeight: 600 }}>Watch it work →</button></p>
            {(stats.agent_recent || []).map((a, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, fontSize: 12.5, padding: '5px 0', borderBottom: '1px solid #ECE8DF', alignItems: 'center' }}>
                <span style={{ fontFamily: 'monospace', fontSize: 11.5, color: 'var(--accent)' }}>{a.tool}</span>
                <span style={{ marginLeft: 'auto', color: '#8A8680', fontSize: 11 }}>{String(a.at || '').slice(11, 16)}</span>
              </div>
            ))}
          </div>
        )}
        {stats.knowledge && (stats.knowledge.learned_answers > 0 || stats.knowledge.taught > 0) && (
          <div style={ovS.chartCard}>
            <p style={ovS.sectionLabel}>📚 Knowledge pulling through</p>
            <div style={{ display: 'flex', gap: 18, fontSize: 13 }}>
              <div><b style={{ fontSize: 20, fontFamily: 'monospace' }}>{stats.knowledge.learned_answers}</b><div style={{ fontSize: 11.5, color: '#8A8680' }}>learned answers</div></div>
              <div><b style={{ fontSize: 20, fontFamily: 'monospace' }}>{stats.knowledge.taught}</b><div style={{ fontSize: 11.5, color: '#8A8680' }}>taught by you</div></div>
            </div>
            <p style={{ fontSize: 11.5, color: '#8A8680', margin: '10px 0 0' }}>Everything the assistant knows is visible and editable in the <button onClick={() => setTab && setTab('agentlog')} style={{ border: 'none', background: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: 11.5, padding: 0, fontWeight: 600 }}>Agent tab</button>.</p>
          </div>
        )}
      </div>
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
  bar: { width: '100%', background: 'linear-gradient(180deg, var(--accent), #3d7a5f)', borderRadius: '4px 4px 0 0', minHeight: 2 },
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
  const [showManual, setShowManual] = useState(false)

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
      <div style={{ ...styles.chips, justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
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
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => downloadCsv('orders', orders, [
            { key: 'display_id', label: 'Order' }, { key: 'customer_name', label: 'Customer' },
            { key: 'customer_phone', label: 'Phone' }, { key: 'status', label: 'Status' },
            { label: 'Total (R)', get: o => (o.total_cents / 100).toFixed(2) },
            { key: 'delivery_slot', label: 'Slot' }, { key: 'channel', label: 'Channel' },
            { key: 'created_at', label: 'Created' },
          ])} style={styles.btnGhost} disabled={!orders.length}>⬇ Export CSV</button>
          <button onClick={() => setShowManual(true)} style={styles.btnAction}>+ New order</button>
        </div>
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

      {showManual && (
        <ManualOrderModal tenantId={tenantId} onClose={() => setShowManual(false)}
          onCreated={() => { setShowManual(false); load() }} />
      )}
    </div>
  )
}

// ── Manual order creation (P1.3) — phone/walk-in orders the admin captures directly ─────────

function ManualOrderModal({ tenantId, onClose, onCreated }) {
  const [products, setProducts] = useState([])
  const [lines, setLines] = useState([])          // [{product_id, quantity}]
  const [customerName, setCustomerName] = useState('')
  const [customerPhone, setCustomerPhone] = useState('')
  const [address, setAddress] = useState('')
  const [slot, setSlot] = useState('morning')
  const [paymentMethod, setPaymentMethod] = useState('cod')
  const [markPaid, setMarkPaid] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products`).then(r => r.json())
      .then(d => setProducts((d.products || []).filter(p => !p.archived)))
      .catch(() => {})
  }, [tenantId])

  const fmt = c => `R${((c || 0) / 100).toFixed(2)}`
  const priceOf = p => (p.sale_price_cents != null ? p.sale_price_cents : p.price_cents)

  function addLine(productId) {
    if (!productId) return
    setLines(ls => {
      const existing = ls.find(l => l.product_id === productId)
      if (existing) return ls.map(l => l.product_id === productId ? { ...l, quantity: l.quantity + 1 } : l)
      return [...ls, { product_id: productId, quantity: 1 }]
    })
  }
  function setQty(productId, qty) {
    const q = Math.max(0, parseFloat(qty) || 0)
    setLines(ls => q === 0 ? ls.filter(l => l.product_id !== productId) : ls.map(l => l.product_id === productId ? { ...l, quantity: q } : l))
  }
  function removeLine(productId) { setLines(ls => ls.filter(l => l.product_id !== productId)) }

  const total = lines.reduce((sum, l) => {
    const p = products.find(pp => String(pp.id) === String(l.product_id))
    return sum + (p ? priceOf(p) * l.quantity : 0)
  }, 0)

  async function submit() {
    setError('')
    if (!lines.length) return setError('Add at least one product.')
    if (!customerName.trim() || !customerPhone.trim()) return setError('Customer name and phone are required.')
    setSaving(true)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/orders/manual`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items: lines, customer_name: customerName.trim(), customer_phone: customerPhone.trim(),
          delivery_address: address.trim() || undefined, delivery_slot: slot,
          payment_method: paymentMethod, mark_paid: markPaid,
        }),
      })
      if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || 'Could not create order') }
      onCreated()
    } catch (e) {
      setError(e.message || 'Could not create order')
    }
    setSaving(false)
  }

  const field = { padding: '9px 11px', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 13, fontFamily: 'system-ui', boxSizing: 'border-box', width: '100%' }

  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={{ ...styles.panel, maxWidth: 520 }} onClick={e => e.stopPropagation()}>
        <div style={styles.header}>
          <div>
            <h2 style={styles.title}>+ New order</h2>
            <p style={styles.subtitle}>Phone / walk-in — captured the same way as a storefront order</p>
          </div>
          <button onClick={onClose} style={styles.closeBtn}>×</button>
        </div>

        <div style={{ ...styles.content, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <select onChange={e => { addLine(e.target.value); e.target.value = '' }} defaultValue="" style={field}>
            <option value="" disabled>+ Add a product…</option>
            {products.map(p => (
              <option key={p.id} value={p.id}>{p.name} — {fmt(priceOf(p))}{p.stock_quantity != null ? ` (${p.stock_quantity} in stock)` : ''}</option>
            ))}
          </select>

          {lines.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {lines.map(l => {
                const p = products.find(pp => String(pp.id) === String(l.product_id))
                if (!p) return null
                return (
                  <div key={l.product_id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ flex: 1, fontSize: 13 }}>{p.name}</span>
                    <input type="number" min="0" step="0.5" value={l.quantity}
                      onChange={e => setQty(l.product_id, e.target.value)}
                      style={{ ...field, width: 64, padding: '6px 8px' }} />
                    <span style={{ fontSize: 13, width: 80, textAlign: 'right' }}>{fmt(priceOf(p) * l.quantity)}</span>
                    <button onClick={() => removeLine(l.product_id)} style={styles.btnDanger}>✕</button>
                  </div>
                )
              })}
              <div style={{ textAlign: 'right', fontWeight: 700, fontSize: 14, paddingTop: 4, borderTop: '1px solid #EDE9DF' }}>
                Total: {fmt(total)}
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 8 }}>
            <input placeholder="Customer name" value={customerName} onChange={e => setCustomerName(e.target.value)} style={field} />
            <input placeholder="Phone (WhatsApp)" value={customerPhone} onChange={e => setCustomerPhone(e.target.value)} style={field} />
          </div>
          <input placeholder="Delivery address (blank = collection)" value={address} onChange={e => setAddress(e.target.value)} style={field} />
          <div style={{ display: 'flex', gap: 8 }}>
            <select value={slot} onChange={e => setSlot(e.target.value)} style={field}>
              <option value="morning">Morning</option>
              <option value="afternoon">Afternoon</option>
              <option value="evening">Evening</option>
            </select>
            <select value={paymentMethod} onChange={e => setPaymentMethod(e.target.value)} style={field}>
              <option value="cod">Cash on delivery</option>
              <option value="eft">EFT</option>
              <option value="card">Card (in person)</option>
            </select>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
            <input type="checkbox" checked={markPaid} onChange={e => setMarkPaid(e.target.checked)} />
            Already paid — mark as paid now
          </label>

          {error && <p style={{ color: '#C0392B', fontSize: 13, margin: 0 }}>{error}</p>}

          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', paddingTop: 6 }}>
            <button onClick={onClose} style={styles.btnGhost}>Cancel</button>
            <button onClick={submit} disabled={saving} style={styles.btnAction}>{saving ? 'Creating…' : 'Create order'}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Order detail + packing slip ─────────────────────────────────────────────

function OrderDetailDrawer({ tenantId, orderId, onClose }) {
  const [order, setOrder] = useState(null)
  const [depth, setDepth] = useState(null)   // timeline + WhatsApp exchange (UI overhaul P3)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/orders/${orderId}`)
      .then(r => r.json()).then(setOrder).catch(() => {}).finally(() => setLoading(false))
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/orders/${orderId}/detail`)
      .then(r => r.json()).then(setDepth).catch(() => {})
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

              {/* Timeline — how this order actually happened (UI overhaul P3) */}
              {(depth?.timeline || []).length > 0 && (
                <>
                  <p style={styles.detailSection}>Timeline</p>
                  <div style={{ borderLeft: '2px solid #ECE8DF', paddingLeft: 12, marginLeft: 4 }}>
                    {depth.timeline.map((t, i) => (
                      <div key={i} style={{ padding: '4px 0', fontSize: 12.5 }}>
                        <b style={{ color: '#1E1E1E' }}>{t.label}</b>
                        <span style={{ display: 'block', fontSize: 11, color: '#8A8680' }}>
                          {String(t.at || '').slice(0, 16).replace('T', ' ')}
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {/* The WhatsApp exchange that produced it */}
              {(depth?.conversation || []).length > 0 && (
                <>
                  <p style={styles.detailSection}>From the conversation</p>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                    {depth.conversation.map((m, i) => (
                      <div key={i} style={{
                        alignSelf: m.role === 'user' ? 'flex-start' : 'flex-end',
                        background: m.role === 'user' ? '#F0EDE5' : 'var(--accent-soft, rgba(44,85,69,.10))',
                        borderRadius: 10, padding: '6px 10px', fontSize: 12, maxWidth: '88%',
                      }}>
                        {m.text}
                      </div>
                    ))}
                  </div>
                </>
              )}

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
          <StatCard label="Deliveries" value={data.total} sub={`${slots.length} slot${slots.length !== 1 ? 's' : ''}`} accent="var(--accent, var(--accent))" />
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
  const [categories, setCategories] = useState([])   // per-tenant (migration 073); fallback = legacy labels
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(null)
  const [editPrice, setEditPrice] = useState({}) // id → string
  const [expandedId, setExpandedId] = useState(null) // id of expanded product card
  const [showAdd, setShowAdd] = useState(false)
  const [showCats, setShowCats] = useState(false)
  const [newCat, setNewCat] = useState('')
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ name: '', price: '', category: 'extras', sold_by: 'pack', description: '' })

  const load = useCallback(async () => {
    setLoading(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products`)
    const d = await r.json()
    setProducts(d.products || [])
    const rc = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/categories`).then(x => x.json()).catch(() => ({}))
    setCategories(rc.categories || [])
    setLoading(false)
  }, [tenantId])

  useEffect(() => { load() }, [load])

  // Per-tenant category labels with legacy fallback (pre-migration-073 tenants).
  const catLabel = (key) => categories.find(c => c.key === key)?.label || CATEGORY_LABELS[key] || key
  const catKeys = categories.length ? categories.map(c => c.key) : Object.keys(CATEGORY_LABELS)

  async function addCategory() {
    const label = newCat.trim()
    if (!label) return
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/categories`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label, sort_order: categories.length + 1 }),
    })
    setNewCat(''); await load()
  }

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
    if (!confirm(`Remove "${p.name}" from the shop? (Products with order history are archived and can be restored.)`)) return
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
      {/* Add product + category manager */}
      <div style={{ marginBottom: 16 }}>
        {!showAdd ? (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button onClick={() => setShowAdd(true)} style={styles.btnAction}>+ Add product</button>
            <button onClick={() => setShowCats(s => !s)} style={styles.btnGhost}>🏷 Categories</button>
          </div>
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
                {catKeys.map(c => <option key={c} value={c}>{catLabel(c)}</option>)}
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

      {showCats && (
        <div style={{ background: '#fff', border: '1px solid #DDD8CE', borderRadius: 10, padding: 14, marginBottom: 16 }}>
          <p style={{ fontSize: 12.5, fontWeight: 600, fontFamily: 'system-ui', margin: '0 0 8px' }}>Your categories</p>
          {(categories.length ? categories : Object.keys(CATEGORY_LABELS).map(k => ({ key: k, label: CATEGORY_LABELS[k] }))).map(c => (
            <span key={c.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontFamily: 'system-ui', background: '#F0EDE5', borderRadius: 999, padding: '4px 12px', margin: '0 6px 6px 0' }}>
              {c.label}
            </span>
          ))}
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <input placeholder="New category (e.g. Smoked Fish)" value={newCat} onChange={e => setNewCat(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && addCategory()}
              style={{ flex: 1, padding: '7px 10px', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 13, fontFamily: 'system-ui' }} />
            <button onClick={addCategory} style={styles.btnAction}>Add</button>
          </div>
          {!categories.length && <p style={{ fontSize: 11, color: '#8A8680', fontFamily: 'system-ui', margin: '8px 0 0' }}>Adding your first category needs migration 073.</p>}
        </div>
      )}

      {Object.entries(grouped).map(([cat, items]) => (
        <div key={cat} style={{ marginBottom: 24 }}>
          <h3 style={styles.catHeader}>{catLabel(cat)}</h3>
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

                {/* Expanded: full edit panel (migration 073 depth) */}
                {expandedId === p.id && (
                  <ProductEditPanel tenantId={tenantId} product={p} patch={patch} saving={saving}
                    deleteProduct={deleteProduct} catKeys={catKeys} catLabel={catLabel} />
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// Deep product editor (UI e-commerce depth, 2026-07-17): name/category/sold-by editable
// post-create, numeric stock, multi-image gallery (persists ALL urls to `images`, first =
// cover), sale price with end date, weight/pack/serves, archive-aware delete.
function ProductEditPanel({ tenantId, product: p, patch, saving, deleteProduct, catKeys, catLabel }) {
  const [f, setF] = useState({
    name: p.name || '', category: p.category || 'extras', sold_by: p.sold_by || 'pack',
    stock: p.stock_quantity ?? '', sale: p.sale_price_cents != null ? (p.sale_price_cents / 100).toFixed(2) : '',
    saleEnds: (p.sale_ends_at || '').slice(0, 10), weight: p.weight_grams ?? '', packSize: p.pack_size ?? '',
    serves: p.serves ?? '',
    reorderThreshold: p.reorder_threshold ?? '', reorderQty: p.reorder_qty ?? '',
    defaultSupplierId: p.default_supplier_id || '',
  })
  const [suppliers, setSuppliers] = useState([])
  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/suppliers`).then(r => r.json())
      .then(d => setSuppliers(d.suppliers || [])).catch(() => {})
  }, [tenantId])
  const set = (k, v) => setF(prev => ({ ...prev, [k]: v }))
  const inp = { padding: '7px 10px', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 13, fontFamily: 'system-ui', boxSizing: 'border-box' }
  const lbl = { fontSize: 11, color: '#8A8680', fontFamily: 'system-ui', display: 'block', marginBottom: 3 }

  function saveDetails() {
    const upd = { name: f.name.trim() || p.name, category: f.category, sold_by: f.sold_by }
    if (f.stock !== '' && !isNaN(parseInt(f.stock))) upd.stock_quantity = parseInt(f.stock)
    const saleC = f.sale === '' ? null : Math.round(parseFloat(f.sale) * 100)
    upd.sale_price_cents = (saleC && saleC > 0) ? saleC : null
    upd.sale_ends_at = f.saleEnds ? `${f.saleEnds}T23:59:59+02:00` : null
    if (f.weight !== '') upd.weight_grams = parseInt(f.weight) || null
    if (f.packSize !== '') upd.pack_size = f.packSize
    if (f.serves !== '') upd.serves = parseInt(f.serves) || null
    upd.reorder_threshold = f.reorderThreshold === '' ? null : parseInt(f.reorderThreshold) || null
    upd.reorder_qty = f.reorderQty === '' ? null : parseInt(f.reorderQty) || null
    upd.default_supplier_id = f.defaultSupplierId || null
    patch(p.id, upd)
  }

  const gallery = (p.images && p.images.length) ? p.images : (p.image_url ? [p.image_url] : [])

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid #EDE9DF', display: 'flex', flexDirection: 'column', gap: 12 }}>
      {p.archived && (
        <div style={{ fontSize: 12.5, fontFamily: 'system-ui', color: '#A23B2D' }}>
          📦 Archived — hidden from the shop. <button onClick={() => patch(p.id, { archived: false })} style={{ ...styles.btnGhost, color: 'var(--accent)' }}>Restore</button>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
        <div><span style={lbl}>Name</span>
          <input value={f.name} onChange={e => set('name', e.target.value)} style={{ ...inp, width: '100%' }} /></div>
        <div><span style={lbl}>Category</span>
          <select value={f.category} onChange={e => set('category', e.target.value)} style={{ ...inp, width: '100%' }}>
            {[...new Set([...catKeys, f.category])].map(c => <option key={c} value={c}>{catLabel(c)}</option>)}
          </select></div>
        <div><span style={lbl}>Sold by</span>
          <select value={f.sold_by} onChange={e => set('sold_by', e.target.value)} style={{ ...inp, width: '100%' }}>
            <option value="pack">per pack / item</option><option value="kg">per kg</option>
          </select></div>
        <div><span style={lbl}>Stock on hand (blank = untracked)</span>
          <input type="number" value={f.stock} onChange={e => set('stock', e.target.value)} placeholder="e.g. 12" style={{ ...inp, width: '100%' }} /></div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
        <div><span style={lbl}>🔥 Sale price (R, blank = no sale)</span>
          <input type="number" step="0.01" value={f.sale} onChange={e => set('sale', e.target.value)} placeholder="e.g. 169.00" style={{ ...inp, width: '100%' }} /></div>
        <div><span style={lbl}>Sale ends</span>
          <input type="date" value={f.saleEnds} onChange={e => set('saleEnds', e.target.value)} style={{ ...inp, width: '100%' }} /></div>
        <div><span style={lbl}>Weight (g)</span>
          <input type="number" value={f.weight} onChange={e => set('weight', e.target.value)} style={{ ...inp, width: '100%' }} /></div>
        <div><span style={lbl}>Pack size / serves</span>
          <div style={{ display: 'flex', gap: 6 }}>
            <input value={f.packSize} onChange={e => set('packSize', e.target.value)} placeholder="e.g. 4 per pack" style={{ ...inp, flex: 1, minWidth: 0 }} />
            <input type="number" value={f.serves} onChange={e => set('serves', e.target.value)} placeholder="serves" style={{ ...inp, width: 70 }} />
          </div></div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
        <div><span style={lbl}>🔔 Reorder when stock ≤</span>
          <input type="number" value={f.reorderThreshold} onChange={e => set('reorderThreshold', e.target.value)} placeholder="e.g. 5" style={{ ...inp, width: '100%' }} /></div>
        <div><span style={lbl}>Reorder quantity</span>
          <input type="number" value={f.reorderQty} onChange={e => set('reorderQty', e.target.value)} placeholder="e.g. 20" style={{ ...inp, width: '100%' }} /></div>
        <div><span style={lbl}>Default supplier</span>
          <select value={f.defaultSupplierId} onChange={e => set('defaultSupplierId', e.target.value)} style={{ ...inp, width: '100%' }}>
            <option value="">— none —</option>
            {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select></div>
      </div>

      <button onClick={saveDetails} disabled={saving === p.id} style={{ ...styles.btnAction, alignSelf: 'flex-start' }}>
        {saving === p.id ? 'Saving…' : 'Save details'}
      </button>

      <div>
        <p style={{ fontSize: 12, fontFamily: 'system-ui', fontWeight: 600, color: '#1E1E1E', margin: '0 0 8px' }}>
          Photo gallery <span style={{ fontWeight: 400, color: '#8A8680' }}>— first photo is the cover</span>
        </p>
        <VulaImageUpload
          tenantId={tenantId}
          existingUrls={gallery}
          maxFiles={5}
          onUploaded={(urls) => {
            const all = [...gallery, ...urls]
            patch(p.id, { images: all, image_url: all[0] })
          }}
        />
        {gallery.length > 1 && (
          <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            {gallery.map((u, i) => (
              <div key={u} style={{ position: 'relative' }}>
                <img src={u} alt="" style={{ width: 64, height: 64, objectFit: 'cover', borderRadius: 8, border: i === 0 ? '2px solid var(--accent)' : '1px solid #DDD8CE' }} />
                <button title={i === 0 ? 'Cover photo' : 'Make cover'} onClick={() => {
                  const re = [u, ...gallery.filter(x => x !== u)]
                  patch(p.id, { images: re, image_url: re[0] })
                }} style={{ position: 'absolute', top: 2, left: 2, fontSize: 10, border: 'none', borderRadius: 4, background: 'rgba(255,255,255,.85)', cursor: 'pointer', padding: '1px 4px' }}>{i === 0 ? '★' : '☆'}</button>
                <button title="Remove photo" onClick={() => {
                  const re = gallery.filter(x => x !== u)
                  patch(p.id, { images: re, image_url: re[0] || null })
                }} style={{ position: 'absolute', top: 2, right: 2, fontSize: 10, border: 'none', borderRadius: 4, background: 'rgba(255,255,255,.85)', cursor: 'pointer', padding: '1px 4px', color: '#A23B2D' }}>✕</button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <p style={{ fontSize: 12, fontFamily: 'system-ui', fontWeight: 600, color: '#1E1E1E', margin: '0 0 6px' }}>Description</p>
        <textarea
          defaultValue={p.description || p.notes || ''}
          rows={3}
          onBlur={e => {
            const val = e.target.value.trim()
            if (val !== (p.description || p.notes || '')) patch(p.id, { description: val })
          }}
          style={{ width: '100%', padding: '8px 10px', border: '1px solid #DDD8CE', borderRadius: 6, fontFamily: 'system-ui', fontSize: 13, color: '#1E1E1E', resize: 'vertical', boxSizing: 'border-box' }}
          placeholder="e.g. Skin-on, boneless, great for braaing"
        />
      </div>

      <button onClick={() => deleteProduct(p)} disabled={saving === p.id} style={styles.btnDeleteProduct}>
        🗑 {p.archived ? 'Delete permanently' : 'Remove product'}
      </button>
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
        <div style={{ textAlign: 'center', maxWidth: 420, margin: '24px auto', background: '#FFFFFF', border: `1px solid ${'#DDD8CE'}`, borderRadius: 12, padding: 32 }}>
          <div style={{ fontSize: 32, marginBottom: 10 }}>🚚</div>
          <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: '#1E1E1E', marginBottom: 6 }}>Set up your suppliers</div>
          <p style={{ fontSize: 13, color: '#8A8680', lineHeight: 1.55, margin: '0 0 16px' }}>
            Add suppliers once and Vula auto-fills payment terms, VAT and account details when you
            scan their bills — and matches incoming invoices automatically.
          </p>
          {!editing && <button onClick={startNew} style={{ ...styles.btnAction }}>+ Add your first supplier</button>}
        </div>
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

      <PurchaseOrders tenantId={tenantId} />
    </div>
  )
}

// ── Purchase orders + auto-reorder (P3.3) ───────────────────────────────────

function PurchaseOrders({ tenantId }) {
  const [suggestions, setSuggestions] = useState([])
  const [pos, setPos] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(null)

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

  const fmt = c => `R${((c || 0) / 100).toFixed(2)}`
  const PO_STATUS = { draft: '📝 Draft', sent: '📤 Sent', received: '✅ Received', cancelled: '✕ Cancelled' }

  if (loading) return <p style={styles.loading}>Loading purchase orders…</p>

  return (
    <div style={{ marginTop: 28 }}>
      <p style={{ fontSize: 14, fontWeight: 700, fontFamily: 'system-ui', color: '#1E1E1E', margin: '0 0 10px' }}>
        📋 Purchase orders
      </p>

      {suggestions.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <p style={{ fontSize: 12.5, fontFamily: 'system-ui', color: '#b45309', fontWeight: 600, margin: '0 0 8px' }}>
            🔔 Low stock — suggested reorders
          </p>
          {suggestions.map((g, i) => (
            <div key={i} style={{ ...styles.productCard, background: 'rgba(180,83,9,0.06)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                <span style={styles.productName}>{g.supplier_name}</span>
                <button onClick={() => createFromSuggestion(g)} disabled={busy === (g.supplier_id || 'unassigned')} style={styles.btnAction}>
                  {busy === (g.supplier_id || 'unassigned') ? 'Creating…' : '+ Create PO'}
                </button>
              </div>
              <div style={{ ...styles.statSub, marginTop: 4 }}>
                {g.items.map(it => `${it.name} (${it.stock_quantity} left → order ${it.suggested_qty})`).join(' · ')}
              </div>
            </div>
          ))}
        </div>
      )}

      {pos.length === 0 ? (
        <p style={{ fontSize: 13, color: '#8A8680', fontFamily: 'system-ui' }}>No purchase orders yet — set a reorder threshold on a product to get suggestions.</p>
      ) : (
        <div style={styles.list}>
          {pos.map(po => (
            <div key={po.id} style={styles.productCard}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                <div>
                  <span style={styles.productName}>{po.supplier_name || 'Unassigned'}</span>
                  <span style={{ ...styles.statSub, marginLeft: 8 }}>{PO_STATUS[po.status] || po.status} · {fmt(po.total_cents)}</span>
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  {po.status === 'draft' && <button disabled={busy === po.id} onClick={() => advance(po, 'sent')} style={styles.btnGhost}>Mark sent</button>}
                  {po.status === 'sent' && <button disabled={busy === po.id} onClick={() => advance(po, 'received')} style={styles.btnAction}>Mark received</button>}
                  {(po.status === 'draft' || po.status === 'sent') && <button disabled={busy === po.id} onClick={() => advance(po, 'cancelled')} style={styles.btnDanger}>Cancel</button>}
                </div>
              </div>
              <div style={{ ...styles.statSub, marginTop: 4 }}>
                {(po.items || []).map(it => `${it.name} ×${it.quantity}`).join(' · ')}
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
  tabActive:    { color:'var(--accent, var(--accent))', borderBottom:'2px solid var(--accent, var(--accent))', fontWeight:600 },
  tabDivider:   { width:1, height:18, background:'#DDD8CE', margin:'0 6px', flex:'0 0 auto' },
  content:      { padding:'20px 28px', flex:1, overflowY:'auto' },
  contentBare:  { padding:'20px 24px', flex:1, minWidth:0 },  // controlled/shell mode — shell owns chrome
  loading:      { color:'#8A8680', fontSize:13, fontFamily:'system-ui' },
  empty:        { color:'#8A8680', fontSize:13, fontFamily:'system-ui', padding:'24px 0', textAlign:'center' },
  error:        { color:'#ef4444', fontSize:13, fontFamily:'system-ui' },

  statGrid:     { display:'grid', gridTemplateColumns:'repeat(2, 1fr)', gap:12, marginBottom:20 },
  statCard:     { background:'#fff', border:'1px solid #DDD8CE', borderRadius:8, padding:'16px 18px' },
  statValue:    { fontFamily:"'Cormorant Garamond', serif", fontSize:28, fontWeight:700, margin:'0 0 4px', color:'var(--accent, var(--accent))' },
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
  chipActive:   { background:'var(--accent, var(--accent))', color:'#fff', border:'1px solid var(--accent, var(--accent))' },

  list:         { display:'flex', flexDirection:'column', gap:8 },
  orderCard:    { background:'#fff', border:'1px solid #DDD8CE', borderRadius:8, padding:'14px 16px' },
  orderTop:     { display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:4 },
  orderId:      { fontFamily:"'Source Code Pro', monospace", fontSize:13, fontWeight:600, color:'#1E1E1E', marginRight:8 },
  orderAmount:  { fontFamily:'system-ui', fontSize:15, fontWeight:700, color:'var(--accent, var(--accent))' },
  orderMeta:    { fontFamily:'system-ui', fontSize:12, color:'#8A8680', display:'flex', gap:6, marginBottom:4, flexWrap:'wrap' },
  orderDate:    { fontFamily:'system-ui', fontSize:11, color:'#B5B0A8', margin:'2px 0 8px' },
  badge:        { padding:'2px 8px', borderRadius:12, fontSize:11, fontWeight:600 },
  actions:      { display:'flex', gap:6, flexWrap:'wrap' },
  btnAction:    { padding:'5px 12px', background:'var(--accent, var(--accent))', color:'#fff', border:'none', borderRadius:6, cursor:'pointer', fontSize:12, fontFamily:'system-ui', fontWeight:600 },
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
  packQty:      { fontWeight:700, color:'var(--accent, var(--accent))' },
  packPrice:    { color:'#6B7280', fontSize:13, minWidth:70, textAlign:'right' },
  detailTotal:  { display:'flex', justifyContent:'space-between', fontFamily:'system-ui', fontWeight:700, fontSize:16, color:'var(--accent, var(--accent))', marginTop:12, paddingTop:10, borderTop:'1px solid #DDD8CE' },
}
