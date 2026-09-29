/**
 * VulaMerchantAdmin.jsx
 *
 * Merchant-facing admin for a single Vula Commerce tenant.
 * Opened via master's "Open as tenant" (a full VulaShell takeover), the owner/staff's own
 * dedicated shell, or the legacy "Manage tenant" modal.
 *
 * Tabs:
 *   📊 Overview — daily revenue, orders to dispatch, pending payments
 *   📦 Orders   — list, filter by status, update fulfilment status
 *   🐟 Products — toggle stock on/off, edit price, mark weekly special
 */

import { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react'
import { SectionTabs, toast, confirmDialog, Skeleton, ErrorCard } from './ui/index.jsx'
import { useSectionTabs } from '../hooks/useSectionTabs'
import { MERCHANT_GROUPS } from '../navConfig.jsx'
import { useAuthStore } from '../store/auth'
import VulaImageUpload from './VulaImageUpload'
import { downloadCsv, parseCsv } from '../lib/csv'
import VulaSmartScanner from './VulaSmartScanner'
import VulaStock from './VulaStock'
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
import VulaEmailCampaigns from './VulaEmailCampaigns'
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
import VulaJobCosting from './VulaJobCosting'
import VulaCrossCheck from './VulaCrossCheck'
import GlassCard from './GlassCard'
import { BrandKitSettings } from './VulaSettings'
import VulaQS from './VulaQS'
import VulaQSPro from './VulaQSPro'
import VulaTakeoff from './VulaTakeoff'
import VulaRepHome from './VulaRepHome'
import VulaRepCrmContacts from './VulaRepCrmContacts'
import VulaRepCallSheet from './VulaRepCallSheet'
import VulaRepExpenseSheet from './VulaRepExpenseSheet'
import VulaRepReminders from './VulaRepReminders'
import VulaRepCrmLookup from './VulaRepCrmLookup'
import VulaDraft from './VulaDraft'
import VulaAutomations from './VulaAutomations'
import VulaFlowBuilder from './VulaFlowBuilder'
// Lazy-loaded: the Puck page builder is ~1 MB — keep it out of the main bundle until the Pages tab opens.
const VulaPages = lazy(() => import('./VulaPages'))
import VulaPayments from './VulaPayments'
import { VULA_API } from '../lib/authFetch'


const STATUS_LABELS = {
  pending_payment: { label: 'Awaiting payment', color: 'var(--warn)', bg: 'rgba(245,158,11,0.12)' },
  paid:            { label: 'Paid',              color: 'var(--ok)', bg: 'rgba(34,197,94,0.12)' },
  confirmed:       { label: 'Confirmed',         color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' },
  packing:         { label: 'Packing',            color: '#8b5cf6', bg: 'rgba(139,92,246,0.12)' },
  dispatched:      { label: 'Dispatched',         color: '#0ea5e9', bg: 'rgba(14,165,233,0.12)' },
  delivered:       { label: 'Delivered',          color: 'var(--ok)', bg: 'rgba(16,185,129,0.12)' },
  cancelled:       { label: 'Cancelled',          color: 'var(--danger)', bg: 'rgba(239,68,68,0.12)' },
  refunded:        { label: 'Refunded',           color: 'var(--muted)', bg: 'rgba(107,114,128,0.12)' },
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

// Look up a top-level section's (already access/module-filtered) subtabs from navGroups —
// the same data App.jsx already computed for the sidebar, so a "Money" etc. section shows
// exactly the sub-tabs this tenant/member is actually allowed to see, with zero extra fetch.
function subtabsFor(navGroups, sectionId) {
  for (const g of navGroups || []) {
    const hit = g.items.find(it => it.id === sectionId)
    if (hit?.subtabs) return hit.subtabs
  }
  return null
}

// Always controlled + full-page: the sidebar (VulaShell via App.jsx) owns navigation and passes
// activeTab/onTabChange, for both the owner/staff shell and master's "Open as tenant" takeover —
// confirmed the only two real callers, both always pass these. (The old uncontrolled/modal mode,
// with its own hand-rolled tab strip and gating, was dead code superseded by that shell takeover
// — deleted 2026-07-21 rather than kept as an unused second nav path to drift out of sync again.)
export default function VulaMerchantAdmin({ tenantId, tenantName, navGroups, access = [], full = true, teamRole = null, teamPhone = null, activeTab, onTabChange }) {
  // Who is connecting WhatsApp/Yoco/email — recorded as connected_by (was always blank).
  const adminEmail = useAuthStore(st => st.user?.email) || ""
  const tab = activeTab
  const setTab = onTabChange
  // A member with a defined access list sees only those modules (+ overview). Owners/
  // managers (full) see everything including Team/Settings. Module-level (business-type) gating
  // now lives once, in navConfig.jsx, driving the sidebar itself — this is just the access-list
  // safety net for a tab a signed-in staff member shouldn't be on.
  const canSee = (id) => full || id === 'overview' || (access || []).includes(id)
  // If the current tab isn't visible to this member, fall back to a safe default.
  useEffect(() => { if (!canSee(tab)) setTab('overview') }, [access, full])  // eslint-disable-line
  const [products, setProducts] = useState([])
  const [broadcastDraft, setBroadcastDraft] = useState(null)   // Marketing → "Send as broadcast" handoff (P2.1)
  const [invoiceSupplierFilter, setInvoiceSupplierFilter] = useState(null)  // Suppliers → Invoices click-through
  // Generic cross-section handoff (IA overhaul 2026-07-22): a click somewhere in one section
  // (e.g. Suppliers, inside Sell) that should land the user on a SPECIFIC sub-tab of ANOTHER
  // section (e.g. Invoices, inside Money) now switches the top-level tab AND records which
  // sub-tab that section should open on — each Section wrapper below consumes+clears this once.
  const [pendingNav, setPendingNav] = useState(null)   // { subtab } for whichever section `tab` is about to become
  const navigateTo = (section, subtab) => { setPendingNav(subtab ? { subtab } : null); setTab(section) }

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products`)
      .then(r => r.json()).then(d => setProducts(d.products || [])).catch(() => {})
  }, [tenantId])

  const inner = (
    <>
        {/* Content */}
        {/* Keyed by tenantId (2026-08-12 urgent fix): every tab component below fetches its own
            tenant-scoped data into local state (pages, settings, brand, the open page-editor
            document, etc.) via useEffect([tenantId]) — but without a key here, switching tenants
            (e.g. master's "Open as tenant" takeover) re-renders the SAME component instance with
            a new tenantId prop instead of remounting it, so any state that isn't itself reset by
            an effect (most visibly VulaPages' `editing` — a page open in the Puck editor) kept
            showing the PREVIOUS tenant's data until something else forced a remount. Keying this
            wrapper by tenantId forces a full remount of whichever tab is active on every tenant
            switch, so no component can carry stale state across tenants — not a perf concern,
            this only remounts the one currently-visible tab, not the whole dashboard shell. */}
        <div key={tenantId} style={styles.contentBare}>
          {/* A restricted sales_rep login gets their own summary here instead of the tenant-wide
              owner overview — 'overview' itself stays always-visible (merchantVisible's own
              rule), only WHAT renders on it differs for this one role. */}
          {tab === 'overview'  && (teamRole === 'sales_rep' && !full
            ? <VulaRepHome tenantId={tenantId} repPhone={teamPhone} onNavigate={navigateTo} />
            : <OverviewTab tenantId={tenantId} onNavigate={navigateTo} />)}
          {tab === 'my-work' && <MyWorkSection tenantId={tenantId} repPhone={teamPhone} subtabs={subtabsFor(navGroups, 'my-work')}
            pendingSubtab={pendingNav?.subtab} onConsumePendingNav={() => setPendingNav(null)} />}
          {tab === 'inbox'     && <VulaInbox        tenantId={tenantId} />}
          {tab === 'assistant-hub' && <AssistantSection tenantId={tenantId} subtabs={subtabsFor(navGroups, 'assistant-hub')}
            pendingSubtab={pendingNav?.subtab} onConsumePendingNav={() => setPendingNav(null)} />}
          {tab === 'sell' && <SellSection tenantId={tenantId} products={products} subtabs={subtabsFor(navGroups, 'sell')}
            pendingSubtab={pendingNav?.subtab} onConsumePendingNav={() => setPendingNav(null)}
            onViewInvoices={(supplierId) => { setInvoiceSupplierFilter(supplierId); navigateTo('money', 'invoices') }} />}
          {tab === 'money' && <MoneySection tenantId={tenantId} products={products} subtabs={subtabsFor(navGroups, 'money')}
            pendingSubtab={pendingNav?.subtab} onConsumePendingNav={() => setPendingNav(null)}
            invoiceSupplierFilter={invoiceSupplierFilter} onClearSupplierFilter={() => setInvoiceSupplierFilter(null)} />}
          {tab === 'people' && <PeopleSection tenantId={tenantId} subtabs={subtabsFor(navGroups, 'people')}
            pendingSubtab={pendingNav?.subtab} onConsumePendingNav={() => setPendingNav(null)} />}
          {tab === 'marketing-hub' && <MarketingSection tenantId={tenantId} subtabs={subtabsFor(navGroups, 'marketing-hub')}
            pendingSubtab={pendingNav?.subtab} onConsumePendingNav={() => setPendingNav(null)}
            draftBody={broadcastDraft} onConsumeDraft={() => setBroadcastDraft(null)}
            onSendAsBroadcast={(text) => { setBroadcastDraft(text); navigateTo('marketing-hub', 'broadcast') }} />}
          {tab === 'pages'     && <Suspense fallback={<div style={{ padding: 20, color: 'var(--muted)' }}>Loading page builder…</div>}><VulaPages tenantId={tenantId} /></Suspense>}
          {tab === 'operate' && <OperateSection tenantId={tenantId} subtabs={subtabsFor(navGroups, 'operate')}
            pendingSubtab={pendingNav?.subtab} onConsumePendingNav={() => setPendingNav(null)} />}
          {tab === 'estimating' && <EstimatingSection tenantId={tenantId} subtabs={subtabsFor(navGroups, 'estimating')}
            pendingSubtab={pendingNav?.subtab} onConsumePendingNav={() => setPendingNav(null)} />}
          {tab === 'team'      && <VulaTeam          tenantId={tenantId} />}
          {tab === 'settings'  && <VulaSettings      tenantId={tenantId} tenantName={tenantName} adminEmail={adminEmail} />}
        </div>
    </>
  )

  // The shell (VulaShell via App.jsx) already provides width + chrome, so no constraining wrapper
  // — except the Puck page editor, which needs the FULL viewport (a 1100px clamp cripples it).
  return <div style={tab === 'pages' ? undefined : { maxWidth: 1100 }}>{inner}</div>
}

// ── Section wrappers (IA overhaul 2026-07-22) ─────────────────────────────────
// Each mirrors VulaMasterPanel's own established pattern (one sidebar entry -> an inner
// useSectionTabs/SectionTabs strip) via the shared primitive, rather than a bespoke one-off.
// `subtabs` is the already access/module-filtered list from navConfig (via subtabsFor above) —
// these components never re-derive gating themselves.

function AssistantSection({ tenantId, subtabs, pendingSubtab, onConsumePendingNav }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'assistant' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      {active === 'assistant' && <VulaAssistant tenantId={tenantId} />}
      {active === 'agentlog' && <VulaAgentActivity tenantId={tenantId} />}
      {active === 'automations' && <VulaAutomations tenantId={tenantId} />}
      {active === 'flows' && <VulaFlowBuilder tenantId={tenantId} />}
    </div>
  )
}

function SellSection({ tenantId, products, subtabs, pendingSubtab, onConsumePendingNav, onViewInvoices }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'orders' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      {active === 'orders' && <OrdersTab tenantId={tenantId} />}
      {active === 'products' && <ProductsTab tenantId={tenantId} />}
      {active === 'discounts' && <DiscountCodesTab tenantId={tenantId} />}
      {active === 'subscriptions' && <VulaRecurringOrders tenantId={tenantId} />}
      {active === 'delivery' && <><VulaOrderWorkflow tenantId={tenantId} /><DeliveryTab tenantId={tenantId} /></>}
      {active === 'bookings' && <VulaBookings tenantId={tenantId} />}
      {active === 'stock' && <VulaStock tenantId={tenantId} products={products} />}
      {active === 'suppliers' && <SuppliersTab tenantId={tenantId} onViewInvoices={onViewInvoices} />}
      {active === 'import' && <VulaImport tenantId={tenantId} />}
    </div>
  )
}

function MoneySection({ tenantId, products, subtabs, pendingSubtab, onConsumePendingNav, invoiceSupplierFilter, onClearSupplierFilter }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'invoices' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      {active === 'invoices' && <VulaInvoices tenantId={tenantId} products={products} initialSupplierId={invoiceSupplierFilter} onClearSupplierFilter={onClearSupplierFilter} />}
      {active === 'expenses' && <VulaExpenses tenantId={tenantId} />}
      {active === 'bank' && <VulaBankRec tenantId={tenantId} />}
      {active === 'books' && <VulaAccounting tenantId={tenantId} />}
      {active === 'payments' && <VulaPayments tenantId={tenantId} />}
      {active === 'budget' && <VulaBudget tenantId={tenantId} />}
      {active === 'scanner' && <VulaSmartScanner tenantId={tenantId} products={products} />}
      {active === 'finances' && <FinancesSubsection tenantId={tenantId} />}
    </div>
  )
}

// Reports+Finances double-mount fix: VulaFinanceInsights mounted once, then an Insights/Ledger
// toggle underneath — previously two competing top-level tabs each mounted VulaFinanceInsights
// a second time alongside a different sibling (VulaReports vs VulaFinances).
const FINANCES_INNER_TABS = [{ id: 'insights', label: 'Insights' }, { id: 'ledger', label: 'Ledger' }]
function FinancesSubsection({ tenantId }) {
  const { tabs, active, setActive } = useSectionTabs(FINANCES_INNER_TABS, { defaultTabId: 'insights' })
  return (
    <div>
      <VulaFinanceInsights tenantId={tenantId} />
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      {active === 'insights' && <VulaReports tenantId={tenantId} />}
      {active === 'ledger' && <VulaFinances tenantId={tenantId} />}
    </div>
  )
}

function PeopleSection({ tenantId, subtabs, pendingSubtab, onConsumePendingNav }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'customers' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      {active === 'customers' && <VulaCustomers tenantId={tenantId} />}
      {active === 'contacts' && <VulaContacts tenantId={tenantId} />}
      {active === 'followups' && <VulaFollowups tenantId={tenantId} />}
    </div>
  )
}

// Broadcast tab bug fix: this used to render VulaClientOnboarding then VulaBroadcast glued
// together under one "📢 Broadcast" label — two unrelated features, now two real sub-tabs.
function MarketingSection({ tenantId, subtabs, pendingSubtab, onConsumePendingNav, draftBody, onConsumeDraft, onSendAsBroadcast }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'broadcast' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      {active === 'onboarding' && <VulaClientOnboarding tenantId={tenantId} />}
      {active === 'broadcast' && <VulaBroadcast tenantId={tenantId} draftBody={draftBody} onConsumeDraft={onConsumeDraft} />}
      {active === 'email-campaigns' && <VulaEmailCampaigns tenantId={tenantId} />}
      {active === 'wa-templates' && <VulaWATemplates tenantId={tenantId} />}
      {active === 'scheduling' && <VulaScheduledJobs tenantId={tenantId} />}
      {active === 'marketing' && <VulaMarketing tenantId={tenantId} onSendAsBroadcast={onSendAsBroadcast} />}
    </div>
  )
}

function OperateSection({ tenantId, subtabs, pendingSubtab, onConsumePendingNav }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'projects' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      {active === 'projects' && <VulaProjects tenantId={tenantId} />}
      {active === 'workspace' && <VulaProjectWorkspace tenantId={tenantId} />}
      {active === 'fieldops' && <VulaFieldOps tenantId={tenantId} />}
      {active === 'labour' && <VulaLabour tenantId={tenantId} />}
      {active === 'qsrates' && <VulaQSRates tenantId={tenantId} />}
      {active === 'documents' && <VulaDocuments tenantId={tenantId} />}
    </div>
  )
}

function MyWorkSection({ tenantId, repPhone, subtabs, pendingSubtab, onConsumePendingNav }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'rep-contacts' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      {active === 'rep-contacts' && <VulaRepCrmContacts tenantId={tenantId} repPhone={repPhone} />}
      {active === 'rep-callsheet' && <VulaRepCallSheet tenantId={tenantId} repPhone={repPhone} />}
      {active === 'rep-bookings' && <VulaBookings tenantId={tenantId} />}
      {active === 'rep-documents' && <VulaDocuments tenantId={tenantId} defaultFiledBy={repPhone} />}
      {active === 'rep-reminders' && <VulaRepReminders tenantId={tenantId} repPhone={repPhone} />}
      {active === 'rep-expenses' && <VulaExpenses tenantId={tenantId} defaultPaidBy={repPhone} />}
      {active === 'rep-expense-sheet' && <VulaRepExpenseSheet tenantId={tenantId} repPhone={repPhone} />}
      {active === 'rep-crm' && <VulaRepCrmLookup tenantId={tenantId} />}
    </div>
  )
}

function EstimatingSection({ tenantId, subtabs, pendingSubtab, onConsumePendingNav }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'qs' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      {active === 'qs' && <VulaQS tenantId={tenantId} />}
      {active === 'qspro' && <VulaQSPro tenantId={tenantId} />}
      {active === 'takeoff' && <VulaTakeoff tenantId={tenantId} />}
      {active === 'draft' && <VulaDraft tenantId={tenantId} />}
    </div>
  )
}

// ── Overview ─────────────────────────────────────────────────────────────────

// Which sidebar section a tab lives in (subtabs are nested inside sections since the IA overhaul).
function sectionFor(tabId) {
  for (const g of MERCHANT_GROUPS) {
    for (const it of g.items) {
      if (it.id === tabId) return { section: tabId }
      if (it.subtabs?.some(st => st.id === tabId)) return { section: it.id, subtab: tabId }
    }
  }
  return { section: tabId }
}

// The go-live checklist master already had (GET /admin/setup, same computation) — shown to the
// business itself until everything's done, each open step linking to the tab that fixes it.
function GoLiveChecklist({ tenantId, onNavigate }) {
  const [setup, setSetup] = useState(null)
  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/setup`)
      .then(r => (r.ok ? r.json() : null)).then(setSetup).catch(() => {})
  }, [tenantId])
  if (!setup || setup.done >= setup.total) return null
  const open = setup.steps.filter(st => !st.done)
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 14, marginBottom: 16 }}>
      <p style={{ margin: '0 0 8px', fontWeight: 700, fontSize: 14 }}>
        🚀 Getting you live — {setup.done}/{setup.total} done ({setup.progress_pct}%)
      </p>
      <div style={{ height: 6, background: 'var(--surface-alt)', borderRadius: 3, marginBottom: 10 }}>
        <div style={{ width: `${setup.progress_pct}%`, height: 6, background: 'var(--accent)', borderRadius: 3 }} />
      </div>
      {open.map(st => (
        <button key={st.id} onClick={() => { const n = sectionFor(st.tab || 'settings'); onNavigate && onNavigate(n.section, n.subtab) }}
                style={{ display: 'flex', justifyContent: 'space-between', width: '100%', border: 'none',
                         background: 'none', padding: '6px 0', cursor: 'pointer', fontSize: 13, textAlign: 'left' }}>
          <span>☐ {st.label} <span style={{ color: 'var(--muted)' }}>— {st.detail}</span></span>
          <span style={{ color: 'var(--accent)', fontWeight: 600 }}>Do this →</span>
        </button>
      ))}
    </div>
  )
}

// Home cards (2026-09-29, Ian: tenants had no way to "customise what they want to view").
// Each business picks which cards it sees and in what order — GET/PUT /v1/tenants/{t}/home,
// default by business type (a project business gets job costing, a shop gets sales).
const HOME_CARD_LABELS = {
  checklist: 'Go-live checklist',
  attention: 'Needs attention',
  sales: 'Sales today & totals',
  trend: 'Revenue / money in — 7 days',
  jobcosting: 'Job costing (projects)',
  crosscheck: 'Cross-check & VAT',
  customers: 'Customer service',
  assistant: 'Your assistant & knowledge',
}

function HomeCustomiser({ layout, onSave, onCancel }) {
  const [cards, setCards] = useState(layout.cards)
  const hidden = (layout.available || []).filter(c => !cards.includes(c))
  const move = (i, d) => {
    const next = [...cards]; const j = i + d
    if (j < 0 || j >= next.length) return
    ;[next[i], next[j]] = [next[j], next[i]]; setCards(next)
  }
  const btn = { border: '1px solid var(--border)', background: 'var(--surface)', borderRadius: 6, padding: '3px 9px', cursor: 'pointer', fontSize: 12 }
  return (
    <div style={{ ...ovS.chartCard, marginTop: 0, marginBottom: 16 }}>
      <p style={ovS.sectionLabel}>Customise Home</p>
      <p style={{ fontSize: 12.5, color: 'var(--muted)', margin: '0 0 10px' }}>Choose the cards your business sees, in your order. Everyone on your team sees the same Home.</p>
      {cards.map((c, i) => (
        <div key={c} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--border-soft)', fontSize: 13 }}>
          <span style={{ flex: 1 }}>{HOME_CARD_LABELS[c] || c}</span>
          <button style={btn} onClick={() => move(i, -1)} disabled={i === 0} aria-label="Move up">↑</button>
          <button style={btn} onClick={() => move(i, 1)} disabled={i === cards.length - 1} aria-label="Move down">↓</button>
          <button style={btn} onClick={() => setCards(cards.filter(x => x !== c))}>Hide</button>
        </div>))}
      {hidden.length > 0 && (
        <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>Add:</span>
          {hidden.map(c => <button key={c} style={btn} onClick={() => setCards([...cards, c])}>+ {HOME_CARD_LABELS[c] || c}</button>)}
        </div>)}
      <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
        <button onClick={() => onSave(cards)} style={{ ...btn, background: 'var(--accent)', color: '#fff', border: 'none', padding: '7px 14px' }}>Save</button>
        <button onClick={() => onSave([])} style={{ ...btn, padding: '7px 14px' }}>Reset to default</button>
        <button onClick={onCancel} style={{ ...btn, padding: '7px 14px' }}>Cancel</button>
      </div>
    </div>
  )
}

// "Make it yours" (2026-09-29): a business whose brand kit is still Vula's default (no logo,
// Vula green) gets its brand set up from Home on day one — upload a logo or paste a website,
// Vula suggests the colours, preview, save. Hidden once a brand is set or when dismissed.
function MakeItYours({ tenantId }) {
  const [show, setShow] = useState(false)
  const key = `vula-brand-dismissed-${tenantId}`
  useEffect(() => {
    let hidden = false
    try { hidden = localStorage.getItem(key) === '1' } catch { /* private mode */ }
    if (hidden) return
    fetch(`${VULA_API}/v1/commerce/${tenantId}/brand`).then(r => r.ok ? r.json() : null)
      .then(b => { if (b && !b.logo_url && (!b.accent_color || b.accent_color.toUpperCase() === '#2C5545')) setShow(true) })
      .catch(() => {})
  }, [tenantId])  // eslint-disable-line
  if (!show) return null
  const dismiss = () => { try { localStorage.setItem(key, '1') } catch { /* private mode */ } setShow(false) }
  return (
    <div className="vula-panel" style={{ background: 'var(--surface)', border: '1px solid var(--accent)', borderRadius: 'var(--r-card)', padding: 'var(--s4)', marginBottom: 'var(--s4)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
        <p style={{ ...ovS.sectionLabel, margin: 0, color: 'var(--accent)' }}>✨ Make it yours</p>
        <button onClick={dismiss} style={{ marginLeft: 'auto', border: 'none', background: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 12.5 }}>Not now</button>
      </div>
      <p style={{ fontSize: 13, color: 'var(--muted)', margin: '0 0 12px' }}>Upload your logo or paste your website — Vula picks your colours, and your whole dashboard, invoices, emails and phone app take them on.</p>
      <BrandKitSettings tenantId={tenantId} />
    </div>
  )
}

function OverviewTab({ tenantId, onNavigate }) {
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [layout, setLayout] = useState(null)
  const [editing, setEditing] = useState(false)
  const [saveErr, setSaveErr] = useState('')

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/stats`)
      .then(r => r.json())
      .then(setStats)
      .catch(() => {})
      .finally(() => setLoading(false))
    fetch(`${VULA_API}/v1/tenants/${tenantId}/home`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setLayout(d && Array.isArray(d.cards) ? d : null))
      .catch(() => setLayout(null))
  }, [tenantId])

  const saveLayout = async (cards) => {
    setSaveErr('')
    try {
      const r = await fetch(`${VULA_API}/v1/tenants/${tenantId}/home`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cards }) })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setSaveErr(d.detail || 'Could not save.'); return }
      setLayout(d); setEditing(false)
    } catch { setSaveErr('Could not save.') }
  }

  if (loading) return (
    <div aria-busy="true" style={{ display: 'grid', gap: 12 }}>
      {[0, 1, 2].map(i => <div key={i} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-card)', padding: 16 }}>
        <Skeleton height={14} width="30%" /><Skeleton height={42} style={{ marginTop: 12 }} /></div>)}
    </div>)

  const s = stats || {}
  const fmt = cents => `R${(Number(cents || 0) / 100).toLocaleString('en-ZA', { maximumFractionDigits: 0 })}`
  const orderSeries = s.daily_revenue || []
  const weekOrders = orderSeries.reduce((sum, d) => sum + (d.orders || 0), 0)
  // A shop that sells face to face (Off the Hook: card machine + EFT, hardly any online orders)
  // sees its takings from the bank instead of R0 from online orders.
  const bankLed = (s.total_orders || 0) < 5 && (s.bank_in_30d_cents || 0) > 0
  const series = bankLed ? (s.daily_bank_in || []) : orderSeries
  const aov = s.total_orders ? s.total_revenue_cents / s.total_orders : 0
  // { section, subtab } pairs now that Orders/Invoices/Products live nested inside Sell/Money —
  // was a flat `tab` id before the IA overhaul (2026-07-22).
  const alerts = [
    { show: s.open_escalations > 0,     label: 'Customer waiting on you', value: s.open_escalations,   hint: (s.oldest_escalation?.question || 'answer on WhatsApp').slice(0, 46), section: 'inbox', color: 'var(--danger)' },
    { show: s.to_dispatch > 0,          label: 'To dispatch',      value: s.to_dispatch,                hint: 'orders ready to send', section: 'sell',  subtab: 'orders',   color: 'var(--info)' },
    { show: s.pending_payment > 0,      label: 'Awaiting payment', value: s.pending_payment,            hint: 'unpaid orders',        section: 'sell',  subtab: 'orders',   color: 'var(--warn)' },
    { show: s.invoice_overdue_cents > 0,label: 'Invoices overdue', value: fmt(s.invoice_overdue_cents), hint: 'chase these',          section: 'money', subtab: 'invoices', color: 'var(--danger)' },
    { show: s.low_stock_count > 0,      label: 'Low stock',        value: s.low_stock_count,            hint: 'items running out',    section: 'sell',  subtab: 'products', color: 'var(--danger)' },
  ].filter(a => a.show)

  const cards = {
    checklist: () => <GoLiveChecklist tenantId={tenantId} onNavigate={onNavigate} />,
    sales: () => !stats ? <ErrorCard what="sales figures" /> : bankLed ? (
      <div style={styles.statGrid}>
        <StatCard label="Money in — 7 days" value={fmt(s.bank_in_7d_cents)} sub="card + EFT, from your bank" accent="var(--accent)" />
        <StatCard label="Money in — 30 days" value={fmt(s.bank_in_30d_cents)} sub={s.bank_in_last_date ? `bank lines up to ${s.bank_in_last_date}` : 'from your bank'} />
        <StatCard label="Online orders" value={s.total_orders || 0} sub={`${fmt(s.total_revenue_cents)} through WhatsApp/web`} accent="var(--info)" />
        <StatCard label="Invoices owed" value={fmt(s.invoice_outstanding_cents)} sub={s.invoice_overdue_cents ? `${fmt(s.invoice_overdue_cents)} overdue` : 'none overdue'} accent={s.invoice_overdue_cents ? 'var(--danger)' : 'var(--muted)'} />
      </div>) : (
      <div style={styles.statGrid}>
        <StatCard label="Today's revenue" value={fmt(s.today_revenue_cents)} sub={`${s.today_orders} orders today`} accent="var(--accent)" />
        <StatCard label="Total revenue"   value={fmt(s.total_revenue_cents)} sub={`${s.total_orders} orders`} />
        <StatCard label="Avg order value" value={fmt(aov)}                   sub="per paid order" accent="var(--info)" />
        <StatCard label="This week"       value={weekOrders}                 sub="orders (7 days)" accent="var(--accent-2)" />
      </div>),
    trend: () => <TrendChart series={series} fmt={fmt} title={bankLed ? 'Money in — last 7 days (bank)' : 'Revenue — last 7 days'} />,
    customers: () => <VulaCSMetrics tenantId={tenantId} />,
    jobcosting: () => <VulaJobCosting tenantId={tenantId} />,
    crosscheck: () => <VulaCrossCheck tenantId={tenantId} />,
    attention: () => alerts.length > 0 ? (
      <div style={{ marginTop: 18 }}>
        <p style={ovS.sectionLabel}>Needs attention</p>
        <div style={ovS.alertRow}>
          {alerts.map(a => (
            <button key={a.label} onClick={() => onNavigate && onNavigate(a.section, a.subtab)} className="vula-tile" style={ovS.alertCard}>
              <span style={{ ...ovS.alertValue, color: a.color }}>{a.value}</span>
              <span style={ovS.alertLabel}>{a.label}</span>
              <span style={ovS.alertHint}>{a.hint} →</span>
            </button>
          ))}
        </div>
      </div>
    ) : (
      <p style={{ ...ovS.sectionLabel, marginTop: 18 }}>✓ All caught up — nothing needs attention right now.</p>
    ),
    // Glass-box AI + knowledge — what the assistant did and knows (UI overhaul P3)
    assistant: () => ((s.agent_recent || []).length === 0
        && !(s.knowledge && (s.knowledge.learned_answers > 0 || s.knowledge.taught > 0))) ? null : (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: 14, marginTop: 18 }}>
        {(s.agent_recent || []).length > 0 && (
          <div className="vula-panel" style={ovS.chartCard}>
            <p style={ovS.sectionLabel}>🧠 Your assistant, recently <button onClick={() => onNavigate && onNavigate('assistant-hub', 'agentlog')} style={{ float: 'right', border: 'none', background: 'none', color: 'var(--accent)', fontSize: 12, cursor: 'pointer', fontWeight: 600 }}>Watch it work →</button></p>
            {(s.agent_recent || []).map((a, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, fontSize: 12.5, padding: '5px 0', borderBottom: '1px solid var(--border-soft)', alignItems: 'center' }}>
                <span style={{ fontFamily: 'monospace', fontSize: 11.5, color: 'var(--accent)' }}>{a.tool}</span>
                <span style={{ marginLeft: 'auto', color: 'var(--muted)', fontSize: 11 }}>{String(a.at || '').slice(11, 16)}</span>
              </div>
            ))}
          </div>
        )}
        {s.knowledge && (s.knowledge.learned_answers > 0 || s.knowledge.taught > 0) && (
          <div className="vula-panel" style={ovS.chartCard}>
            <p style={ovS.sectionLabel}>📚 Knowledge pulling through</p>
            <div style={{ display: 'flex', gap: 18, fontSize: 13 }}>
              <div><b style={{ fontSize: 20, fontFamily: 'monospace' }}>{s.knowledge.learned_answers}</b><div style={{ fontSize: 11.5, color: 'var(--muted)' }}>learned answers</div></div>
              <div><b style={{ fontSize: 20, fontFamily: 'monospace' }}>{s.knowledge.taught}</b><div style={{ fontSize: 11.5, color: 'var(--muted)' }}>taught by you</div></div>
            </div>
            <p style={{ fontSize: 11.5, color: 'var(--muted)', margin: '10px 0 0' }}>Everything the assistant knows is visible and editable in <button onClick={() => onNavigate && onNavigate('assistant-hub', 'agentlog')} style={{ border: 'none', background: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: 11.5, padding: 0, fontWeight: 600 }}>Assistant › Activity</button>.</p>
          </div>
        )}
      </div>
    ),
  }
  // No saved layout reachable (older backend, network) → the Home as it always was.
  const order = (layout?.cards || ['checklist', 'sales', 'trend', 'customers', 'attention', 'assistant'])
    .filter(c => cards[c])

  return (
    <div className="vula-home">
      <div className="vula-home-bg" aria-hidden="true" />
      {layout && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
          {!editing && <button onClick={() => setEditing(true)} style={{ border: 'none', background: 'none', color: 'var(--accent)', fontSize: 12.5, cursor: 'pointer', fontWeight: 600 }}>⚙ Customise Home</button>}
        </div>)}
      <MakeItYours tenantId={tenantId} />
      {editing && layout && <HomeCustomiser layout={layout} onSave={saveLayout} onCancel={() => setEditing(false)} />}
      {saveErr && <p style={styles.error}>{saveErr}</p>}
      {order.map(c => <GlassCard key={c} dark={c === 'customers'}>{cards[c]()}</GlassCard>)}
    </div>
  )
}

function TrendChart({ series, fmt, title = 'Revenue — last 7 days' }) {
  if (!series.length) return null
  const max = Math.max(1, ...series.map(d => d.revenue_cents))
  const dayName = iso => new Date(iso).toLocaleDateString('en-ZA', { weekday: 'short' })
  return (
    <div style={ovS.chartCard}>
      <p style={ovS.sectionLabel}>{title}</p>
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
  sectionLabel: { fontSize: 12, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.4px', margin: '0 0 8px' },
  chartCard: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 18, marginTop: 16 },
  bars: { display: 'flex', alignItems: 'flex-end', gap: 10, height: 120 },
  barCol: { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, height: '100%' },
  barWrap: { flex: 1, width: '100%', display: 'flex', alignItems: 'flex-end' },
  bar: { width: '100%', background: 'linear-gradient(180deg, var(--accent), var(--accent-2))', borderRadius: '4px 4px 0 0', minHeight: 2 },
  barLabel: { fontSize: 11, color: 'var(--muted)' },
  chartFoot: { fontSize: 11, color: 'var(--faint)', margin: '10px 0 0', textAlign: 'right' },
  alertRow: { display: 'flex', gap: 10, flexWrap: 'wrap' },
  alertCard: { flex: '1 1 150px', minWidth: 140, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: '14px 16px', textAlign: 'left', cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 2 },
  alertValue: { fontSize: 22, fontWeight: 700 },
  alertLabel: { fontSize: 13, fontWeight: 600, color: 'var(--text)' },
  alertHint: { fontSize: 11, color: 'var(--muted)' },
}

function StatCard({ label, value, sub, accent = 'var(--muted)' }) {
  return (
    <div className="vula-tile" style={styles.statCard}>
      <p style={{ ...styles.statValue, color: accent }}>{value}</p>
      <p style={styles.statLabel}>{label}</p>
      <p style={styles.statSub}>{sub}</p>
    </div>
  )
}

// ── Orders ────────────────────────────────────────────────────────────────────

// Only orders that never became real fulfilment can be deleted — mirrors the backend guard on
// DELETE/bulk-delete (pending_payment/cancelled only), so the UI never offers a delete that would
// just 400. Anything past this needs cancel/refund first, same as before.
const ORDER_DELETABLE = new Set(['pending_payment', 'cancelled'])

function OrdersTab({ tenantId }) {
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

      {selected.size > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'rgba(162,59,45,0.06)',
          border: '1px solid rgba(162,59,45,0.25)', borderRadius: 8, padding: '8px 12px', marginBottom: 10 }}>
          <span style={{ fontSize: 13}}>{selected.size} selected</span>
          <button onClick={deleteSelected} disabled={deleting} style={styles.btnDanger}>
            {deleting ? 'Deleting…' : `🗑 Delete ${selected.size} selected`}
          </button>
          <button onClick={() => setSelected(new Set())} style={{ ...styles.btnGhost, marginLeft: 'auto' }}>Clear</button>
        </div>
      )}

      {loading && <p style={styles.loading}>Loading orders…</p>}
      {!loading && orders.length === 0 && <p style={styles.empty}>No orders found.</p>}

      <div style={styles.list}>
        {orders.map(o => {
          const s = STATUS_LABELS[o.status] || STATUS_LABELS.pending_payment
          const nextStatuses = NEXT_STATUSES[o.status] || []
          const fmt = cents => `R${(cents / 100).toFixed(2)}`
          const canDelete = ORDER_DELETABLE.has(o.status)
          return (
            <div key={o.id} style={styles.orderCard}>
              <div style={styles.orderTop}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {canDelete && (
                    <input type="checkbox" checked={selected.has(o.id)} onChange={() => toggleSelect(o.id)}
                      title="Select for bulk delete" style={{ cursor: 'pointer' }} />
                  )}
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
                    onClick={() => ns === 'refunded' ? refundOrder(o) : advance(o.id, ns)}
                    style={ns === 'cancelled' ? styles.btnDanger : styles.btnAction}
                  >
                    {updating === o.id ? '…' : `→ ${STATUS_LABELS[ns]?.label || ns}`}
                  </button>
                ))}
                {canDelete && (
                  <button disabled={updating === o.id} onClick={() => deleteOne(o)} style={styles.btnDanger}>
                    {updating === o.id ? '…' : '🗑 Delete'}
                  </button>
                )}
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

  const field = { padding: '9px 11px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, boxSizing: 'border-box', width: '100%' }

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
              <div style={{ textAlign: 'right', fontWeight: 700, fontSize: 14, paddingTop: 4, borderTop: '1px solid var(--border-soft)' }}>
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

          {error && <p style={{ color: 'var(--danger)', fontSize: 13, margin: 0 }}>{error}</p>}

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
      <style>body{font-family:system-ui;padding:24px;color:var(--ink)}h1{font-size:20px}
      table{width:100%;border-collapse:collapse;margin-top:12px}td,th{padding:6px 4px;border-bottom:1px solid var(--border);text-align:left}</style>
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
                  <div style={{ borderLeft: '2px solid var(--border-soft)', paddingLeft: 12, marginLeft: 4 }}>
                    {depth.timeline.map((t, i) => (
                      <div key={i} style={{ padding: '4px 0', fontSize: 12.5 }}>
                        <b style={{ color: 'var(--ink)' }}>{t.label}</b>
                        <span style={{ display: 'block', fontSize: 11, color: 'var(--muted)' }}>
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
                        background: m.role === 'user' ? 'var(--surface-alt)' : 'var(--accent-soft, rgba(44,85,69,.10))',
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
      <style>body{font-family:system-ui;padding:24px;color:var(--ink)}h1{font-size:20px}h2{font-size:15px;margin-top:18px}
      table{width:100%;border-collapse:collapse;margin-top:6px}td,th{padding:6px 4px;border-bottom:1px solid var(--border);text-align:left;font-size:12px;vertical-align:top}</style>
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
          <StatCard label="Paid"   value={data.paid_count}   sub={fmt(data.paid_revenue_cents)}   accent="var(--ok)" />
          <StatCard label="Unpaid" value={data.unpaid_count} sub={fmt(data.unpaid_revenue_cents)} accent="var(--warn)" />
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
                      <span style={{ ...styles.badge, color: paid ? 'var(--ok)' : 'var(--warn)', background: paid ? 'rgba(34,197,94,0.12)' : 'rgba(245,158,11,0.12)' }}>
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
  const [importRows, setImportRows] = useState(null) // parsed CSV preview rows, or null when closed
  const [importing, setImporting] = useState(false)
  const [importResults, setImportResults] = useState(null)
  const fileInputRef = useRef(null)

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
    if (!(await confirmDialog(`Remove "${p.name}" from the shop? (Products with order history are archived and can be restored.)`, { danger: true, confirmLabel: 'Yes' }))) return
    setSaving(p.id)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/${p.id}`, { method: 'DELETE' })
    await load()
    setSaving(null)
  }

  const PRODUCT_CSV_COLUMNS = [
    { key: 'name', label: 'name' }, { key: 'slug', label: 'slug' },
    { label: 'price_cents', get: p => p.price_cents }, { key: 'category', label: 'category' },
    { key: 'sold_by', label: 'sold_by' }, { key: 'description', label: 'description' },
    { key: 'image_url', label: 'image_url' }, { key: 'in_stock', label: 'in_stock' },
    { key: 'is_daily_catch', label: 'is_daily_catch' }, { key: 'status', label: 'status' },
    { key: 'pricing_mode', label: 'pricing_mode' }, { key: 'price_per_kg_cents', label: 'price_per_kg_cents' },
    { key: 'min_weight_g', label: 'min_weight_g' }, { key: 'max_weight_g', label: 'max_weight_g' },
    { key: 'reference_weight_g', label: 'reference_weight_g' }, { key: 'weight_grams', label: 'weight_grams' },
    { key: 'stock_quantity', label: 'stock_quantity' }, { key: 'catch_source', label: 'catch_source' },
    { key: 'fisherman_name', label: 'fisherman_name' },
    { label: 'seo_title', get: p => p.seo?.title || '' }, { label: 'seo_description', get: p => p.seo?.description || '' },
  ]

  function exportCsv() {
    downloadCsv(`${tenantId}-products`, products, PRODUCT_CSV_COLUMNS)
  }

  function onImportFile(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const rows = parseCsv(String(reader.result || ''))
      setImportResults(null)
      setImportRows(rows)
    }
    reader.readAsText(file)
    e.target.value = '' // allow re-selecting the same file
  }

  function validateImportRow(row) {
    const errors = []
    if (!row.name?.trim()) errors.push('missing name')
    const price = parseFloat(row.price_cents)
    if (!row.price_cents || isNaN(price) || price <= 0) errors.push('missing/invalid price_cents')
    return errors
  }

  async function commitImport() {
    setImporting(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/import`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rows: importRows }),
    })
    const d = await r.json()
    setImportResults(d)
    setImporting(false)
    await load()
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
            <button onClick={exportCsv} style={styles.btnGhost} disabled={!products.length}>⬇ Export CSV</button>
            <button onClick={() => fileInputRef.current?.click()} style={styles.btnGhost}>⬆ Import CSV</button>
            <input ref={fileInputRef} type="file" accept=".csv,text/csv" onChange={onImportFile} style={{ display: 'none' }} />
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
            <p style={{ fontSize: 11, color: 'var(--muted)', margin: 0 }}>
              You can add a photo after creating, via the 📷 Photos button.
            </p>
          </form>
        )}
      </div>

      {showCats && (
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 14, marginBottom: 16 }}>
          <p style={{ fontSize: 12.5, fontWeight: 600, margin: '0 0 8px' }}>Your categories</p>
          {(categories.length ? categories : Object.keys(CATEGORY_LABELS).map(k => ({ key: k, label: CATEGORY_LABELS[k] }))).map(c => (
            <span key={c.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, background: 'var(--surface-alt)', borderRadius: 999, padding: '4px 12px', margin: '0 6px 6px 0' }}>
              {c.label}
            </span>
          ))}
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <input placeholder="New category (e.g. Smoked Fish)" value={newCat} onChange={e => setNewCat(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && addCategory()}
              style={{ flex: 1, padding: '7px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13}} />
            <button onClick={addCategory} style={styles.btnAction}>Add</button>
          </div>
          {!categories.length && <p style={{ fontSize: 11, color: 'var(--muted)', margin: '8px 0 0' }}>Adding your first category needs migration 073.</p>}
        </div>
      )}

      {importRows && (
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 14, marginBottom: 16 }}>
          <p style={{ fontSize: 12.5, fontWeight: 600, margin: '0 0 8px' }}>
            Import preview — {importRows.length} row{importRows.length === 1 ? '' : 's'}
          </p>
          <div style={{ maxHeight: 260, overflow: 'auto', border: '1px solid var(--border-soft)', borderRadius: 8 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12}}>
              <thead>
                <tr style={{ background: 'var(--bg)', textAlign: 'left' }}>
                  <th style={{ padding: '6px 10px' }}>#</th>
                  <th style={{ padding: '6px 10px' }}>Name</th>
                  <th style={{ padding: '6px 10px' }}>Price (c)</th>
                  <th style={{ padding: '6px 10px' }}>Category</th>
                  <th style={{ padding: '6px 10px' }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {importRows.map((row, i) => {
                  const errors = validateImportRow(row)
                  const isNewCat = row.category && ![...catKeys].includes(row.category)
                  return (
                    <tr key={i} style={{ borderTop: '1px solid var(--border-soft)', background: errors.length ? 'rgba(162,59,45,0.06)' : undefined }}>
                      <td style={{ padding: '6px 10px', color: 'var(--muted)' }}>{i + 1}</td>
                      <td style={{ padding: '6px 10px' }}>{row.name || <em style={{ color: 'var(--danger)' }}>missing</em>}</td>
                      <td style={{ padding: '6px 10px' }}>{row.price_cents || <em style={{ color: 'var(--danger)' }}>missing</em>}</td>
                      <td style={{ padding: '6px 10px' }}>{row.category || 'extras'}{isNewCat && <span style={{ color: 'var(--muted)' }}> (new)</span>}</td>
                      <td style={{ padding: '6px 10px' }}>
                        {errors.length ? <span style={{ color: 'var(--danger)' }}>⚠ {errors.join(', ')}</span> : <span style={{ color: 'var(--ok)' }}>OK</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {importResults ? (
            <p style={{ fontSize: 12.5, margin: '10px 0 0' }}>
              ✅ {importResults.created} created · {importResults.updated} updated
              {importResults.errors > 0 && <span style={{ color: 'var(--danger)' }}> · {importResults.errors} failed</span>}
              {importResults.results?.filter(r => r.status === 'error').map((r, i) => (
                <span key={i} style={{ display: 'block', color: 'var(--danger)', marginTop: 4 }}>Row {r.row + 1} ({r.slug}): {r.error}</span>
              ))}
            </p>
          ) : (
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <button onClick={commitImport} disabled={importing || importRows.every(r => validateImportRow(r).length)} style={styles.btnAction}>
                {importing ? 'Importing…' : `Import ${importRows.length} product${importRows.length === 1 ? '' : 's'}`}
              </button>
              <button onClick={() => { setImportRows(null); setImportResults(null) }} style={styles.btnGhost}>Cancel</button>
            </div>
          )}
          {importResults && (
            <button onClick={() => { setImportRows(null); setImportResults(null) }} style={{ ...styles.btnGhost, marginTop: 10 }}>Close</button>
          )}
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
                      <span style={{ ...styles.badge, color: 'var(--warn)', background: 'rgba(245,158,11,0.12)', marginLeft: 6 }}>
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

                  {/* Expand for the full editor — stock, sale price, weight/pack, supplier,
                      SEO, photos, delete. Labelled "Edit" (not "Photos") since that undersold
                      how much lives behind this one button. */}
                  <button
                    onClick={() => setExpandedId(expandedId === p.id ? null : p.id)}
                    style={styles.btnGhost}
                  >
                    {expandedId === p.id ? '▲ Less' : '✎ Edit'}
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

// Small caps section header — groups the edit panel's ~20 fields into named clusters (Basics /
// Pricing & sale / Origin & story / Stock & reordering / Photo gallery / Description / SEO) so it
// reads as organized instead of one long wall of inputs.
function SectionLabel({ children }) {
  return (
    <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)', margin: '4px 0 0' }}>
      {children}
    </p>
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
    pricingMode: p.pricing_mode || 'fixed',
    pricePerKg: p.price_per_kg_cents != null ? (p.price_per_kg_cents / 100).toFixed(2) : '',
    minWeight: p.min_weight_g ?? '', maxWeight: p.max_weight_g ?? '', referenceWeight: p.reference_weight_g ?? '',
    catchSource: p.catch_source || '', fishermanName: p.fisherman_name || '',
    seoTitle: (p.seo && p.seo.title) || '', seoDescription: (p.seo && p.seo.description) || '',
    status: p.status || (p.archived ? 'archived' : 'active'),
    productType: p.product_type || 'single',
    bundleItems: Array.isArray(p.bundle_items) ? p.bundle_items : [],
    cookingTips: p.cooking_tips || '',
  })
  const [suppliers, setSuppliers] = useState([])
  const [allProducts, setAllProducts] = useState([])
  const [writingTips, setWritingTips] = useState(false)
  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/suppliers`).then(r => r.json())
      .then(d => setSuppliers(d.suppliers || [])).catch(() => {})
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products`).then(r => r.json())
      .then(d => setAllProducts((d.products || []).filter(x => x.id !== p.id && (x.product_type || 'single') === 'single')))
      .catch(() => {})
  }, [tenantId])  // eslint-disable-line
  const set = (k, v) => setF(prev => ({ ...prev, [k]: v }))
  const inp = { padding: '7px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, boxSizing: 'border-box' }
  const lbl = { fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }

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
    upd.pricing_mode = f.pricingMode
    const byWeight = f.pricingMode === 'by_weight'
    upd.price_per_kg_cents = (byWeight && f.pricePerKg !== '') ? Math.round(parseFloat(f.pricePerKg) * 100) || null : null
    upd.min_weight_g = (byWeight && f.minWeight !== '') ? parseInt(f.minWeight) || null : null
    upd.max_weight_g = (byWeight && f.maxWeight !== '') ? parseInt(f.maxWeight) || null : null
    upd.reference_weight_g = (byWeight && f.referenceWeight !== '') ? parseInt(f.referenceWeight) || null : null
    upd.catch_source = f.catchSource.trim() || null
    upd.fisherman_name = f.fishermanName.trim() || null
    upd.seo = { title: f.seoTitle.trim() || undefined, description: f.seoDescription.trim() || undefined }
    upd.status = f.status
    upd.archived = f.status === 'archived'
    upd.product_type = f.productType
    upd.bundle_items = f.productType === 'bundle'
      ? f.bundleItems.filter(i => i.product_id && (i.quantity || 1) > 0)
      : []
    upd.cooking_tips = f.cookingTips.trim() || null
    patch(p.id, upd)
  }

  async function writeTipsWithAI() {
    setWritingTips(true)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/${p.id}/generate-cooking-tips`, { method: 'POST' })
      const d = await r.json()
      if (r.ok && d.cooking_tips) set('cookingTips', d.cooking_tips)
    } catch {}
    setWritingTips(false)
  }

  const gallery = (p.images && p.images.length) ? p.images : (p.image_url ? [p.image_url] : [])

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-soft)', display: 'flex', flexDirection: 'column', gap: 12 }}>
      {p.archived && (
        <div style={{ fontSize: 12.5, color: 'var(--danger)' }}>
          📦 Archived — hidden from the shop. <button onClick={() => patch(p.id, { archived: false })} style={{ ...styles.btnGhost, color: 'var(--accent)' }}>Restore</button>
        </div>
      )}

      <SectionLabel>Basics</SectionLabel>
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
        <div><span style={lbl}>Status</span>
          <select value={f.status} onChange={e => set('status', e.target.value)} style={{ ...inp, width: '100%' }}>
            <option value="active">Active — visible everywhere</option>
            <option value="draft">Draft — hidden, in progress</option>
            <option value="unlisted">Unlisted — direct link only</option>
            <option value="archived">Archived — removed from sale</option>
          </select></div>
      </div>

      <SectionLabel>Pricing &amp; sale</SectionLabel>
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
        <div><span style={lbl}>Pricing</span>
          <select value={f.pricingMode} onChange={e => set('pricingMode', e.target.value)} style={{ ...inp, width: '100%' }}>
            <option value="fixed">Fixed price</option>
            <option value="by_weight">By weight (per kg)</option>
          </select></div>
        {f.pricingMode === 'by_weight' && (<>
          <div><span style={lbl}>Price per kg (R)</span>
            <input type="number" step="0.01" value={f.pricePerKg} onChange={e => set('pricePerKg', e.target.value)} placeholder="e.g. 290.00" style={{ ...inp, width: '100%' }} /></div>
          <div><span style={lbl}>Weight range (g)</span>
            <div style={{ display: 'flex', gap: 6 }}>
              <input type="number" value={f.minWeight} onChange={e => set('minWeight', e.target.value)} placeholder="min" style={{ ...inp, flex: 1, minWidth: 0 }} />
              <input type="number" value={f.maxWeight} onChange={e => set('maxWeight', e.target.value)} placeholder="max" style={{ ...inp, flex: 1, minWidth: 0 }} />
            </div></div>
          <div><span style={lbl}>Typical/reference weight (g)</span>
            <input type="number" value={f.referenceWeight} onChange={e => set('referenceWeight', e.target.value)} placeholder="e.g. 500" style={{ ...inp, width: '100%' }} /></div>
        </>)}
      </div>

      <SectionLabel>Origin &amp; story <span style={{ fontWeight: 400, color: 'var(--muted)' }}>— optional, shown on the product page</span></SectionLabel>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
        <div><span style={lbl}>Catch source</span>
          <input value={f.catchSource} onChange={e => set('catchSource', e.target.value)} placeholder="e.g. Line-caught, Hout Bay" style={{ ...inp, width: '100%' }} /></div>
        <div><span style={lbl}>Fisherman / supplier name</span>
          <input value={f.fishermanName} onChange={e => set('fishermanName', e.target.value)} placeholder="e.g. Skipper Jan" style={{ ...inp, width: '100%' }} /></div>
      </div>

      <SectionLabel>📦 Box deal / bundle <span style={{ fontWeight: 400, color: 'var(--muted)' }}>— sell several products as one (e.g. Braai Box)</span></SectionLabel>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <select value={f.productType} onChange={e => set('productType', e.target.value)} style={{ ...inp, maxWidth: 260 }}>
          <option value="single">Normal product</option>
          <option value="bundle">Bundle — box of other products</option>
        </select>
        {f.productType === 'bundle' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {f.bundleItems.map((item, idx) => (
              <div key={idx} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <select value={item.product_id || ''} onChange={e => {
                  const items = [...f.bundleItems]; items[idx] = { ...items[idx], product_id: e.target.value }
                  set('bundleItems', items)
                }} style={{ ...inp, flex: 2 }}>
                  <option value="">— choose product —</option>
                  {allProducts.map(ap => <option key={ap.id} value={ap.id}>{ap.name}</option>)}
                </select>
                <input type="number" min="1" value={item.quantity || 1} onChange={e => {
                  const items = [...f.bundleItems]; items[idx] = { ...items[idx], quantity: parseInt(e.target.value) || 1 }
                  set('bundleItems', items)
                }} style={{ ...inp, width: 64 }} />
                <button onClick={() => set('bundleItems', f.bundleItems.filter((_, i) => i !== idx))}
                        style={{ background: 'transparent', border: 'none', color: 'var(--danger)', fontSize: 18, cursor: 'pointer' }}>×</button>
              </div>
            ))}
            <button onClick={() => set('bundleItems', [...f.bundleItems, { product_id: '', quantity: 1 }])}
                    style={{ ...styles.btnGhost, alignSelf: 'flex-start' }}>+ Add item to box</button>
            <p style={{ fontSize: 11, color: 'var(--muted)', margin: 0 }}>
              The box sells at THIS product&apos;s price — set it above. Contents show on the product page.
            </p>
          </div>
        )}
      </div>

      <SectionLabel>🍳 How to cook it <span style={{ fontWeight: 400, color: 'var(--muted)' }}>— shown on the product page</span></SectionLabel>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <textarea value={f.cookingTips} onChange={e => set('cookingTips', e.target.value)} rows={3}
                  placeholder="e.g. Braai over medium coals for 4–5 minutes a side, skin down first…"
                  style={{ ...inp, width: '100%', resize: 'vertical'}} />
        <button onClick={writeTipsWithAI} disabled={writingTips}
                style={{ ...styles.btnGhost, alignSelf: 'flex-start', color: 'var(--accent)' }}>
          {writingTips ? '✨ Writing…' : '✨ Write it for me'}
        </button>
      </div>

      <SectionLabel>Stock &amp; reordering <span style={{ fontWeight: 400, color: 'var(--muted)' }}>— optional, for your own supply planning</span></SectionLabel>
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
        <SectionLabel>Photo gallery <span style={{ fontWeight: 400, color: 'var(--muted)' }}>— first photo is the cover</span></SectionLabel>
        <VulaImageUpload
          tenantId={tenantId}
          existingUrls={gallery}
          maxFiles={5}
          onUploaded={(urls) => {
            const all = [...gallery, ...urls]
            patch(p.id, { images: all, image_url: all[0] })
          }}
        />
        <AIPhotoButton tenantId={tenantId} product={p}
          onDone={(d) => patch(p.id, { image_url: d.image_url, images: d.gallery })} />
        {gallery.length > 1 && (
          <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            {gallery.map((u, i) => (
              <div key={u} style={{ position: 'relative' }}>
                <img src={u} alt="" style={{ width: 64, height: 64, objectFit: 'cover', borderRadius: 8, border: i === 0 ? '2px solid var(--accent)' : '1px solid var(--border)' }} />
                <button title={i === 0 ? 'Cover photo' : 'Make cover'} onClick={() => {
                  const re = [u, ...gallery.filter(x => x !== u)]
                  patch(p.id, { images: re, image_url: re[0] })
                }} style={{ position: 'absolute', top: 2, left: 2, fontSize: 10, border: 'none', borderRadius: 4, background: 'rgba(255,255,255,.85)', cursor: 'pointer', padding: '1px 4px' }}>{i === 0 ? '★' : '☆'}</button>
                <button title="Remove photo" onClick={() => {
                  const re = gallery.filter(x => x !== u)
                  patch(p.id, { images: re, image_url: re[0] || null })
                }} style={{ position: 'absolute', top: 2, right: 2, fontSize: 10, border: 'none', borderRadius: 4, background: 'rgba(255,255,255,.85)', cursor: 'pointer', padding: '1px 4px', color: 'var(--danger)' }}>✕</button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <SectionLabel>Description</SectionLabel>
        <textarea
          defaultValue={p.description || p.notes || ''}
          rows={3}
          onBlur={e => {
            const val = e.target.value.trim()
            if (val !== (p.description || p.notes || '')) patch(p.id, { description: val })
          }}
          style={{ width: '100%', padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, color: 'var(--ink)', resize: 'vertical', boxSizing: 'border-box' }}
          placeholder="e.g. Skin-on, boneless, great for braaing"
        />
      </div>

      <div>
        <VariantsEditor
          tenantId={tenantId}
          productId={p.id}
          basePriceCents={p.price_cents}
          options={p.options}
          suppliers={suppliers}
          onOptionsChange={(names) => patch(p.id, { options: names })}
        />
      </div>

      <div>
        <SectionLabel>🔍 SEO <span style={{ fontWeight: 400, color: 'var(--muted)' }}>— optional, improves search/social sharing</span></SectionLabel>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <input value={f.seoTitle} onChange={e => set('seoTitle', e.target.value)}
                 placeholder="SEO title (browser tab / Google)" style={{ ...inp, width: '100%' }} />
          <textarea value={f.seoDescription} onChange={e => set('seoDescription', e.target.value)} rows={2}
                    placeholder="SEO description (search result snippet)"
                    style={{ ...inp, width: '100%', resize: 'vertical'}} />
        </div>
      </div>

      <button onClick={() => deleteProduct(p)} disabled={saving === p.id} style={styles.btnDeleteProduct}>
        🗑 {p.archived ? 'Delete permanently' : 'Remove product'}
      </button>
    </div>
  )
}

// ── AI product photo (generate a store-ready photo in the tenant's house style) ──────────────

function AIPhotoButton({ tenantId, product, onDone }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  async function generate() {
    if (busy) return
    setBusy(true); setErr(null)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/${product.id}/generate-photo`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'generate' }),
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Generation failed')
      onDone(d)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ marginTop: 8 }}>
      <button onClick={generate} disabled={busy}
        style={{ padding: '7px 14px', background: busy ? 'var(--border)' : 'var(--accent)', color: '#fff',
                 border: 'none', borderRadius: 6, fontSize: 12, fontWeight: 600,
                 cursor: busy ? 'wait' : 'pointer'}}>
        {busy ? '✨ Generating photo… (~30s)' : '✨ AI photo — generate a store-ready shot'}
      </button>
      {err && <p style={{ color: 'var(--danger)', fontSize: 12, margin: '6px 0 0' }}>{err}</p>}
      <p style={{ fontSize: 11, color: 'var(--muted)', margin: '4px 0 0' }}>
        Generates a professional photo in your house style. Click again for another angle — each one is added to the gallery.
      </p>
    </div>
  )
}

// ── Product variants (migration 087, Phase 4 — SKU/barcode, per-variant price/stock) ─────────

function VariantsEditor({ tenantId, productId, basePriceCents, options, suppliers, onOptionsChange }) {
  const [variants, setVariants] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(null)
  const [optionsInput, setOptionsInput] = useState((options || []).join(', '))
  const [showAdd, setShowAdd] = useState(false)
  const [newVariant, setNewVariant] = useState({})
  const [bulkStock, setBulkStock] = useState('')
  const [error, setError] = useState(null)
  const optionNames = options || []
  const inp = { padding: '6px 8px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12.5, boxSizing: 'border-box' }
  const lbl = { fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 2 }

  const load = useCallback(async () => {
    setLoading(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/${productId}/variants`)
    const d = await r.json()
    setVariants(d.variants || [])
    setLoading(false)
  }, [tenantId, productId])

  useEffect(() => { load() }, [load])

  async function patchVariant(id, data) {
    setSaving(id)
    setError(null)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/${productId}/variants/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      setError(d.detail || 'Could not save that change.')
    }
    await load()
    setSaving(null)
  }

  async function deleteVariant(id) {
    if (!(await confirmDialog('Remove this variant?', { danger: true, confirmLabel: 'Yes' }))) return
    setSaving(id)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/${productId}/variants/${id}`, { method: 'DELETE' })
    await load()
    setSaving(null)
  }

  async function addVariant() {
    setError(null)
    const option_values = {}
    optionNames.forEach(name => { if (newVariant[name]) option_values[name] = newVariant[name] })
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/${productId}/variants`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        option_values,
        sku: newVariant.sku || null,
        barcode: newVariant.barcode || null,
        price_cents: newVariant.price ? Math.round(parseFloat(newVariant.price) * 100) || null : null,
        stock_quantity: (newVariant.stock !== undefined && newVariant.stock !== '') ? parseInt(newVariant.stock) : null,
      }),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      setError(d.detail || 'Could not add that variant.')
      return
    }
    setNewVariant({})
    setShowAdd(false)
    await load()
  }

  async function applyBulkStock() {
    const qty = parseInt(bulkStock)
    if (bulkStock === '' || isNaN(qty)) return
    await Promise.all(variants.map(v => fetch(
      `${VULA_API}/v1/commerce/${tenantId}/admin/products/${productId}/variants/${v.id}`,
      { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stock_quantity: qty }) }
    )))
    setBulkStock('')
    await load()
  }

  async function saveOptions() {
    const names = optionsInput.split(',').map(s => s.trim()).filter(Boolean)
    const changed = names.length !== optionNames.length || names.some((n, i) => n !== optionNames[i])
    if (changed && variants.length > 0) {
      if (!await confirmDialog(
        `${variants.length} existing variant${variants.length === 1 ? '' : 's'} use the current option names. ` +
        `Changing them won't delete those variants, but customers won't be able to pick them until you update ` +
        `each variant's option values below. Continue?`
      )) return
    }
    onOptionsChange(names)
  }

  return (
    <div>
      <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)', margin: '0 0 8px' }}>
        🧩 Variants <span style={{ fontWeight: 400, color: 'var(--muted)' }}>— e.g. Size, Colour — each gets its own SKU/barcode/price/stock</span>
      </p>
      <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
        <input value={optionsInput} onChange={e => setOptionsInput(e.target.value)} placeholder="Option names, e.g. Size, Colour"
               style={{ ...inp, flex: 1 }} />
        <button onClick={saveOptions} style={styles.btnGhost}>Save options</button>
      </div>
      {error && <p style={{ fontSize: 12, color: 'var(--danger)', margin: '0 0 10px' }}>⚠ {error}</p>}

      {loading ? <p style={styles.loading}>Loading variants…</p> : (
        <>
          {variants.length > 0 && (
            <div style={{ overflowX: 'auto', marginBottom: 10 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12}}>
                <thead>
                  <tr style={{ background: 'var(--bg)', textAlign: 'left' }}>
                    <th style={{ padding: '5px 8px' }}>Options</th>
                    <th style={{ padding: '5px 8px' }}>SKU</th>
                    <th style={{ padding: '5px 8px' }}>Barcode</th>
                    <th style={{ padding: '5px 8px' }}>Price (R)</th>
                    <th style={{ padding: '5px 8px' }}>Stock</th>
                    <th style={{ padding: '5px 8px' }}>Reorder ≤</th>
                    <th style={{ padding: '5px 8px' }}>Supplier</th>
                    <th style={{ padding: '5px 8px' }}></th>
                  </tr>
                </thead>
                <tbody>
                  {variants.map(v => (
                    <tr key={v.id} style={{ borderTop: '1px solid var(--border-soft)', opacity: v.archived ? 0.5 : 1 }}>
                      <td style={{ padding: '5px 8px' }}>
                        {Object.entries(v.option_values || {}).map(([k, val]) => `${k}: ${val}`).join(', ') || '—'}
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <input defaultValue={v.sku || ''} disabled={saving === v.id}
                               onBlur={e => e.target.value !== (v.sku || '') && patchVariant(v.id, { sku: e.target.value || null })}
                               style={{ ...inp, width: 80 }} />
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <input defaultValue={v.barcode || ''} disabled={saving === v.id}
                               onBlur={e => e.target.value !== (v.barcode || '') && patchVariant(v.id, { barcode: e.target.value || null })}
                               style={{ ...inp, width: 100 }} />
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <input type="number" step="0.01" disabled={saving === v.id}
                               defaultValue={v.price_cents != null ? (v.price_cents / 100).toFixed(2) : ''}
                               placeholder={basePriceCents != null ? (basePriceCents / 100).toFixed(2) : ''}
                               onBlur={e => {
                                 const val = e.target.value === '' ? null : Math.round(parseFloat(e.target.value) * 100)
                                 patchVariant(v.id, { price_cents: (val && val > 0) ? val : null })
                               }}
                               style={{ ...inp, width: 70 }} />
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <input type="number" disabled={saving === v.id} defaultValue={v.stock_quantity ?? ''}
                               onBlur={e => patchVariant(v.id, { stock_quantity: e.target.value === '' ? null : parseInt(e.target.value) })}
                               style={{ ...inp, width: 60 }} />
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <input type="number" disabled={saving === v.id} defaultValue={v.reorder_threshold ?? ''}
                               onBlur={e => patchVariant(v.id, { reorder_threshold: e.target.value === '' ? null : parseInt(e.target.value) })}
                               style={{ ...inp, width: 55 }} />
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <select defaultValue={v.default_supplier_id || ''} disabled={saving === v.id}
                                onChange={e => patchVariant(v.id, { default_supplier_id: e.target.value || null })}
                                style={{ ...inp, width: 110 }}>
                          <option value="">— none —</option>
                          {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                        </select>
                      </td>
                      <td style={{ padding: '5px 8px', whiteSpace: 'nowrap' }}>
                        <button onClick={() => patchVariant(v.id, { archived: !v.archived })} disabled={saving === v.id} style={styles.btnGhost}>
                          {v.archived ? 'Restore' : 'Archive'}
                        </button>
                        <button onClick={() => deleteVariant(v.id)} disabled={saving === v.id} style={{ ...styles.btnGhost, color: 'var(--danger)' }}>✕</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {variants.length > 0 && (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 10 }}>
              <input type="number" value={bulkStock} onChange={e => setBulkStock(e.target.value)}
                     placeholder="Set all variants' stock to…" style={{ ...inp, width: 170 }} />
              <button onClick={applyBulkStock} style={styles.btnGhost}>Apply to all</button>
            </div>
          )}

          {!showAdd ? (
            <button onClick={() => setShowAdd(true)} style={styles.btnGhost} disabled={!optionNames.length}>
              + Add variant
            </button>
          ) : (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              {optionNames.map(name => (
                <div key={name}><span style={lbl}>{name}</span>
                  <input value={newVariant[name] || ''} onChange={e => setNewVariant(prev => ({ ...prev, [name]: e.target.value }))}
                         style={{ ...inp, width: 80 }} /></div>
              ))}
              <div><span style={lbl}>SKU</span>
                <input value={newVariant.sku || ''} onChange={e => setNewVariant(prev => ({ ...prev, sku: e.target.value }))} style={{ ...inp, width: 80 }} /></div>
              <div><span style={lbl}>Barcode</span>
                <input value={newVariant.barcode || ''} onChange={e => setNewVariant(prev => ({ ...prev, barcode: e.target.value }))} style={{ ...inp, width: 100 }} /></div>
              <div><span style={lbl}>Price (R, blank = inherit)</span>
                <input type="number" step="0.01" value={newVariant.price || ''} onChange={e => setNewVariant(prev => ({ ...prev, price: e.target.value }))} style={{ ...inp, width: 70 }} /></div>
              <div><span style={lbl}>Stock</span>
                <input type="number" value={newVariant.stock || ''} onChange={e => setNewVariant(prev => ({ ...prev, stock: e.target.value }))} style={{ ...inp, width: 60 }} /></div>
              <button onClick={addVariant} style={styles.btnAction}>Add</button>
              <button onClick={() => { setShowAdd(false); setNewVariant({}) }} style={styles.btnGhost}>Cancel</button>
            </div>
          )}
          {!optionNames.length && (
            <p style={{ fontSize: 11, color: 'var(--muted)', margin: '6px 0 0' }}>
              Set option names above (e.g. "Size, Colour") before adding variants.
            </p>
          )}
        </>
      )}
    </div>
  )
}

// ── Discount codes (migration 091) ────────────────────────────────────────────

const BLANK_DISCOUNT = {
  code: '', type: 'percent', value: '', min_order_cents: '', starts_at: '', ends_at: '',
  usage_limit: '', active: true, first_order_only: false, per_customer_limit: '',
}

function DiscountCodesTab({ tenantId }) {
  const [codes, setCodes] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState(null) // null | {} (new) | code row
  const [form, setForm] = useState(BLANK_DISCOUNT)
  const [error, setError] = useState(null)
  const inp = { padding: '7px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, boxSizing: 'border-box' }

  const load = useCallback(async () => {
    setLoading(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/discount-codes`)
    const d = await r.json()
    setCodes(d.discount_codes || [])
    setLoading(false)
  }, [tenantId])

  useEffect(() => { load() }, [load])

  function startEdit(c) {
    setForm({
      code: c.code || '', type: c.type || 'percent',
      value: c.value != null ? (c.type === 'fixed' ? (c.value / 100).toFixed(2) : String(c.value)) : '',
      min_order_cents: c.min_order_cents != null ? (c.min_order_cents / 100).toFixed(2) : '',
      starts_at: (c.starts_at || '').slice(0, 10), ends_at: (c.ends_at || '').slice(0, 10),
      usage_limit: c.usage_limit ?? '', active: c.active !== false,
      first_order_only: c.first_order_only || false, per_customer_limit: c.per_customer_limit ?? '',
    })
    setEditing(c)
    setError(null)
  }
  function startNew() { setForm(BLANK_DISCOUNT); setEditing({}); setError(null) }
  function cancel() { setEditing(null); setForm(BLANK_DISCOUNT); setError(null) }

  async function save(e) {
    e.preventDefault()
    if (!form.code.trim()) return
    setSaving(true)
    setError(null)
    const payload = {
      code: form.code.trim(),
      type: form.type,
      value: form.type === 'fixed'
        ? Math.round(parseFloat(form.value || 0) * 100)
        : (parseInt(form.value, 10) || 0),
      min_order_cents: form.min_order_cents !== '' ? Math.round(parseFloat(form.min_order_cents) * 100) : null,
      starts_at: form.starts_at ? `${form.starts_at}T00:00:00+02:00` : null,
      ends_at: form.ends_at ? `${form.ends_at}T23:59:59+02:00` : null,
      usage_limit: form.usage_limit !== '' ? parseInt(form.usage_limit, 10) : null,
      active: form.active,
      first_order_only: form.first_order_only,
      per_customer_limit: form.per_customer_limit !== '' ? parseInt(form.per_customer_limit, 10) : null,
    }
    const isEdit = editing && editing.id
    const r = await fetch(
      `${VULA_API}/v1/commerce/${tenantId}/admin/discount-codes${isEdit ? `/${editing.id}` : ''}`,
      { method: isEdit ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }
    )
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      setError(d.detail || 'Could not save that code.')
      setSaving(false)
      return
    }
    cancel()
    setSaving(false)
    await load()
  }

  async function remove(c) {
    if (!(await confirmDialog(`Delete code "${c.code}"?`, { danger: true, confirmLabel: 'Yes' }))) return
    setSaving(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/discount-codes/${c.id}`, { method: 'DELETE' })
    await load()
    setSaving(false)
  }

  async function toggleActive(c) {
    setSaving(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/discount-codes/${c.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active: !c.active }),
    })
    await load()
    setSaving(false)
  }

  function valueLabel(c) {
    if (c.type === 'percent') return `${c.value}% off`
    if (c.type === 'fixed') return `R${(c.value / 100).toFixed(2)} off`
    return 'Free shipping'
  }

  if (loading) return <p style={styles.loading}>Loading discount codes…</p>

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        {!editing ? (
          <button onClick={startNew} style={styles.btnAction}>+ Add discount code</button>
        ) : (
          <form onSubmit={save} style={styles.addProductForm}>
            {error && <p style={{ fontSize: 12, color: 'var(--danger)', margin: '0 0 4px' }}>⚠ {error}</p>}
            <input placeholder="Code, e.g. SUMMER20" value={form.code} required
                   onChange={e => setForm({ ...form, code: e.target.value.toUpperCase() })} style={inp} />
            <div style={{ display: 'flex', gap: 8 }}>
              <select value={form.type} onChange={e => setForm({ ...form, type: e.target.value })} style={{ ...inp, flex: 1 }}>
                <option value="percent">Percent off</option>
                <option value="fixed">Fixed amount off</option>
                <option value="free_shipping">Free shipping</option>
              </select>
              {form.type !== 'free_shipping' && (
                <input type="number" step={form.type === 'fixed' ? '0.01' : '1'}
                       placeholder={form.type === 'fixed' ? 'Amount (R)' : 'Percent (1-100)'}
                       value={form.value} onChange={e => setForm({ ...form, value: e.target.value })} style={{ ...inp, flex: 1 }} />
              )}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input type="number" step="0.01" placeholder="Minimum order (R, optional)" value={form.min_order_cents}
                     onChange={e => setForm({ ...form, min_order_cents: e.target.value })} style={{ ...inp, flex: 1 }} />
              <input type="number" placeholder="Usage limit (optional)" value={form.usage_limit}
                     onChange={e => setForm({ ...form, usage_limit: e.target.value })} style={{ ...inp, flex: 1 }} />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input type="number" placeholder="Max uses per customer (optional)" value={form.per_customer_limit}
                     onChange={e => setForm({ ...form, per_customer_limit: e.target.value })} style={{ ...inp, flex: 1 }} />
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, flex: 1 }}>
                <input type="checkbox" checked={form.first_order_only}
                       onChange={e => setForm({ ...form, first_order_only: e.target.checked })} />
                First order only
              </label>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <div style={{ flex: 1 }}>
                <span style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Starts (optional)</span>
                <input type="date" value={form.starts_at} onChange={e => setForm({ ...form, starts_at: e.target.value })} style={{ ...inp, width: '100%' }} />
              </div>
              <div style={{ flex: 1 }}>
                <span style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Ends (optional)</span>
                <input type="date" value={form.ends_at} onChange={e => setForm({ ...form, ends_at: e.target.value })} style={{ ...inp, width: '100%' }} />
              </div>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13}}>
              <input type="checkbox" checked={form.active} onChange={e => setForm({ ...form, active: e.target.checked })} />
              Active
            </label>
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="submit" disabled={saving} style={styles.btnAction}>
                {saving ? 'Saving…' : (editing.id ? 'Save changes' : 'Add code')}
              </button>
              <button type="button" onClick={cancel} style={styles.btnGhost}>Cancel</button>
            </div>
          </form>
        )}
      </div>

      {codes.length === 0 ? (
        <div style={{ textAlign: 'center', maxWidth: 420, margin: '24px auto', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 32 }}>
          <div style={{ fontSize: 32, marginBottom: 10 }}>🏷️</div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', marginBottom: 6 }}>No discount codes yet</div>
          <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.55, margin: '0 0 16px' }}>
            Create a code customers can type at checkout — percent off, a fixed amount, or free shipping.
          </p>
          {!editing && <button onClick={startNew} style={styles.btnAction}>+ Add your first code</button>}
        </div>
      ) : (
        <div style={styles.list}>
          {codes.map(c => (
            <div key={c.id} style={{ ...styles.productCard, opacity: c.active ? 1 : 0.55 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                <div>
                  <span style={{ fontWeight: 700, fontFamily: 'monospace', fontSize: 14 }}>{c.code}</span>
                  <span style={{ marginLeft: 10, fontSize: 13, color: 'var(--ok)'}}>{valueLabel(c)}</span>
                </div>
                <button onClick={() => toggleActive(c)} disabled={saving} style={c.active ? styles.btnStock : styles.btnStockOff}>
                  {c.active ? '✓ Active' : '✗ Inactive'}
                </button>
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}>
                {c.min_order_cents ? `Min order R${(c.min_order_cents / 100).toFixed(2)} · ` : ''}
                Used {c.usage_count || 0}{c.usage_limit ? ` / ${c.usage_limit}` : ''}
                {c.starts_at ? ` · from ${c.starts_at.slice(0, 10)}` : ''}
                {c.ends_at ? ` · until ${c.ends_at.slice(0, 10)}` : ''}
                {c.first_order_only ? ' · first order only' : ''}
                {c.per_customer_limit ? ` · max ${c.per_customer_limit}/customer` : ''}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <button onClick={() => startEdit(c)} style={styles.btnGhost}>Edit</button>
                <button onClick={() => remove(c)} disabled={saving} style={{ ...styles.btnGhost, color: 'var(--danger)' }}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Suppliers ───────────────────────────────────────────────────────────────────

const BLANK_SUPPLIER = {
  name: '', aliases: '', payment_terms_days: 30, category: 'general',
  contact_phone: '', contact_email: '', account_number: '', tax_id: '', notes: '',
}

function SuppliersTab({ tenantId, onViewInvoices }) {
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
    if (!(await confirmDialog(`Delete supplier "${s.name}"?`, { danger: true, confirmLabel: 'Yes' }))) return
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
            <p style={{ fontSize: 11, color: 'var(--muted)', margin: 0 }}>
              Tax number & aliases improve auto-matching when you scan this supplier's bills.
            </p>
          </form>
        )}
      </div>

      {suppliers.length === 0 ? (
        <div style={{ textAlign: 'center', maxWidth: 420, margin: '24px auto', background: 'var(--surface)', border: `1px solid ${'var(--border)'}`, borderRadius: 12, padding: 32 }}>
          <div style={{ fontSize: 32, marginBottom: 10 }}>🚚</div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', marginBottom: 6 }}>Set up your suppliers</div>
          <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.55, margin: '0 0 16px' }}>
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
                  {onViewInvoices && <button onClick={() => onViewInvoices(s.id)} style={styles.btnGhost}>🧾 Invoices</button>}
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

  if (loading) return <p style={styles.loading}>Loading purchase orders…</p>

  return (
    <div style={{ marginTop: 28 }}>
      <p style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink)', margin: '0 0 10px' }}>
        📋 Purchase orders
      </p>

      {suggestions.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <p style={{ fontSize: 12.5, color: 'var(--warn)', fontWeight: 600, margin: '0 0 8px' }}>
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
        <p style={{ fontSize: 13, color: 'var(--muted)'}}>No purchase orders yet — set a reorder threshold on a product to get suggestions.</p>
      ) : (
        <div style={styles.list}>
          {pos.map(po => (
            <div key={po.id} style={styles.productCard}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                <div>
                  <span style={styles.productName}>{po.supplier_name || 'Unassigned'}</span>
                  <span style={{ ...styles.statSub, marginLeft: 8 }}>{PO_STATUS[po.status] || po.status} · {fmt(po.total_cents)}</span>
                </div>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  {po.status === 'draft' && (
                    <select value={channel[po.id] || 'email'} onChange={e => setChannel({ ...channel, [po.id]: e.target.value })}
                            style={{ ...styles.apInput, padding: '4px 6px', fontSize: 12 }}>
                      <option value="email">Email</option>
                      <option value="whatsapp">WhatsApp</option>
                      <option value="both">Both</option>
                    </select>
                  )}
                  {po.status === 'draft' && <button disabled={busy === po.id} onClick={() => sendPo(po)} style={styles.btnAction}>Send</button>}
                  {po.status === 'draft' && <button disabled={busy === po.id} onClick={() => advance(po, 'sent')} style={styles.btnGhost} title="Mark sent without Vula dispatching it — e.g. you phoned the order in">Mark sent manually</button>}
                  {po.status === 'sent' && <button disabled={busy === po.id} onClick={() => advance(po, 'received')} style={styles.btnAction}>Mark received</button>}
                  {(po.status === 'draft' || po.status === 'sent') && <button disabled={busy === po.id} onClick={() => advance(po, 'cancelled')} style={styles.btnDanger}>Cancel</button>}
                </div>
              </div>
              <div style={{ ...styles.statSub, marginTop: 4 }}>
                {(po.items || []).map(it => `${it.name} ×${it.quantity}`).join(' · ')}
                {po.sent_channel && ` · sent via ${po.sent_channel}`}
              </div>
              {sendMsg[po.id] && <div style={{ fontSize: 11, color: sendMsg[po.id].includes('error') || sendMsg[po.id] === 'network' ? 'var(--danger)' : 'var(--muted)', marginTop: 4 }}>{sendMsg[po.id]}</div>}
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
  panel:        { width:'100%', maxWidth:680, background:'var(--bg)', overflowY:'auto', display:'flex', flexDirection:'column', boxShadow:'-4px 0 24px rgba(0,0,0,0.15)' },
  header:       { display:'flex', alignItems:'flex-start', justifyContent:'space-between', padding:'24px 28px 0', borderBottom:'1px solid var(--border)', paddingBottom:16 },
  title:        { fontFamily:"var(--font-display)", fontSize:26, fontWeight:700, color:'var(--ink)', margin:0 },
  subtitle:     { fontSize:12, color:'var(--muted)', margin:'2px 0 0' },
  closeBtn:     { background:'transparent', border:'none', fontSize:28, cursor:'pointer', color:'var(--muted)', lineHeight:1 },
  content:      { padding:'20px 28px', flex:1, overflowY:'auto' },
  contentBare:  { padding:'20px 24px', flex:1, minWidth:0 },  // shell mode — shell owns chrome
  loading:      { color:'var(--muted)', fontSize:13},
  empty:        { color:'var(--muted)', fontSize:13, padding:'24px 0', textAlign:'center' },
  error:        { color:'var(--danger)', fontSize:13},

  statGrid:     { display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(150px, 1fr))', gap:12, marginBottom:20 },
  statCard:     { background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, padding:'16px 18px' },
  statValue:    { fontFamily:"var(--font-display)", fontSize:28, fontWeight:700, margin:'0 0 4px', color:'var(--accent, var(--accent))' },
  statLabel:    { fontSize:12, fontWeight:600, color:'var(--ink)', margin:'0 0 2px' },
  statSub:      { fontSize:11, color:'var(--muted)', margin:0 },

  delBar:       { display:'flex', alignItems:'center', gap:8, marginBottom:16 },
  dateInput:    { padding:'7px 10px', border:'1px solid var(--border)', borderRadius:6, fontSize:13, color:'var(--ink)' },
  slotHeader:   { fontFamily:"var(--font-display)", fontSize:18, fontWeight:700, color:'var(--ink)', margin:'0 0 8px' },
  slotCount:    { fontSize:12, fontWeight:400, color:'var(--muted)' },
  delAddress:   { fontSize:12, color:'var(--muted)', margin:'2px 0' },
  delItems:     { fontSize:12, color:'var(--ink)', margin:'6px 0 0' },

  chips:        { display:'flex', gap:6, flexWrap:'wrap', marginBottom:16 },
  chip:         { padding:'5px 12px', borderRadius:20, border:'1px solid var(--border)', background:'var(--surface)', cursor:'pointer', fontSize:12, color:'var(--muted)' },
  chipActive:   { background:'var(--accent, var(--accent))', color:'#fff', border:'1px solid var(--accent, var(--accent))' },

  list:         { display:'flex', flexDirection:'column', gap:8 },
  orderCard:    { background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, padding:'14px 16px' },
  orderTop:     { display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:4 },
  orderId:      { fontFamily:"'Source Code Pro', monospace", fontSize:13, fontWeight:600, color:'var(--ink)', marginRight:8 },
  orderAmount:  { fontSize:15, fontWeight:700, color:'var(--accent, var(--accent))' },
  orderMeta:    { fontSize:12, color:'var(--muted)', display:'flex', gap:6, marginBottom:4, flexWrap:'wrap' },
  orderDate:    { fontSize:11, color:'var(--faint)', margin:'2px 0 8px' },
  badge:        { padding:'2px 8px', borderRadius:12, fontSize:11, fontWeight:600 },
  actions:      { display:'flex', gap:6, flexWrap:'wrap' },
  btnAction:    { padding:'5px 12px', background:'var(--accent, var(--accent))', color:'#fff', border:'none', borderRadius:6, cursor:'pointer', fontSize:12, fontWeight:600 },
  btnDanger:    { padding:'5px 12px', background:'transparent', color:'var(--danger)', border:'1px solid rgba(239,68,68,0.3)', borderRadius:6, cursor:'pointer', fontSize:12},

  catHeader:    { fontFamily:"var(--font-display)", fontSize:18, fontWeight:700, color:'var(--ink)', margin:'0 0 8px' },
  productCard:  { background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, padding:'12px 14px' },
  productTop:   { display:'flex', alignItems:'center', gap:8, marginBottom:8 },
  productName:  { fontSize:13, fontWeight:600, color:'var(--ink)' },
  productMeta:  { display:'flex', alignItems:'center', gap:10 },
  priceRow:     { display:'flex', alignItems:'center', gap:2 },
  priceLabel:   { fontSize:13, color:'var(--ink)', fontWeight:600 },
  priceInput:   { width:60, padding:'3px 6px', border:'1px solid var(--border)', borderRadius:4, fontSize:13, color:'var(--ink)', textAlign:'right' },
  priceUnit:    { fontSize:12, color:'var(--muted)' },
  btnStock:     { padding:'4px 10px', background:'rgba(34,197,94,0.12)', color:'var(--ok)', border:'1px solid rgba(34,197,94,0.3)', borderRadius:20, cursor:'pointer', fontSize:12, fontWeight:600, whiteSpace:'nowrap' },
  btnStockOff:  { padding:'4px 10px', background:'rgba(239,68,68,0.1)', color:'var(--danger)', border:'1px solid rgba(239,68,68,0.3)', borderRadius:20, cursor:'pointer', fontSize:12, fontWeight:600, whiteSpace:'nowrap' },
  btnGhost:     { padding:'4px 10px', background:'transparent', color:'var(--muted)', border:'1px solid var(--border)', borderRadius:20, cursor:'pointer', fontSize:11},
  addProductForm:{ display:'flex', flexDirection:'column', gap:8, background:'var(--surface)', border:'1px solid var(--border)', borderRadius:10, padding:14 },
  apInput:      { flex:1, padding:'9px 11px', border:'1px solid var(--border)', borderRadius:6, fontSize:13, boxSizing:'border-box' },
  btnDeleteProduct:{ marginTop:12, padding:'7px 12px', background:'transparent', color:'var(--danger)', border:'1px solid rgba(239,68,68,0.3)', borderRadius:6, cursor:'pointer', fontSize:12},
  detailBlock:  { background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, padding:14, marginBottom:14 },
  detailName:   { fontSize:15, fontWeight:700, color:'var(--ink)', margin:'0 0 4px' },
  detailMeta:   { fontSize:13, color:'var(--muted)', margin:'2px 0' },
  detailNotes:  { fontSize:13, color:'var(--ink)', margin:'6px 0 0', fontStyle:'italic' },
  detailSection:{ fontSize:12, fontWeight:600, color:'var(--ink)', margin:'0 0 8px' },
  packRow:      { display:'flex', alignItems:'center', gap:10, background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, padding:'10px 12px', fontSize:14, color:'var(--ink)' },
  packQty:      { fontWeight:700, color:'var(--accent, var(--accent))' },
  packPrice:    { color:'var(--muted)', fontSize:13, minWidth:70, textAlign:'right' },
  detailTotal:  { display:'flex', justifyContent:'space-between', fontWeight:700, fontSize:16, color:'var(--accent, var(--accent))', marginTop:12, paddingTop:10, borderTop:'1px solid var(--border)' },
}
