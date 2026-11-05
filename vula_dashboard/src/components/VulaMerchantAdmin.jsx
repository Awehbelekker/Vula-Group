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

import { useState, useEffect, useCallback, useRef, Suspense } from 'react'
import { SectionTabs, confirmDialog } from './ui/index.jsx'
import { lazyScreen, ScreenSuspense } from '../lib/lazyScreen.jsx'
import { toQty } from './stock/useStock'
import { useSectionTabs } from '../hooks/useSectionTabs'
import { iconFor, sectionOf } from '../navConfig.jsx'
import { useAuthStore } from '../store/auth'
import VulaImageUpload from './VulaImageUpload'
import { downloadCsv, parseCsv } from '../lib/csv'
const VulaSmartScanner = lazyScreen(() => import('./VulaSmartScanner'))
const VulaStock = lazyScreen(() => import('./VulaStock'))
import VulaHomeV2 from './VulaHomeV2'
import VulaToday from './VulaToday'
import VulaActivity from './VulaActivity'
const VulaCosting = lazyScreen(() => import('./VulaCosting'))
const VulaInvoices = lazyScreen(() => import('./VulaInvoices'))
const VulaBookings = lazyScreen(() => import('./VulaBookings'))
const VulaMarketing = lazyScreen(() => import('./VulaMarketing'))
const VulaFinanceInsights = lazyScreen(() => import('./VulaFinanceInsights'))
const FinancesV2 = lazyScreen(() => import('./finances/FinancesV2'))
import { useDiscounts, valueLabel } from './discounts/useDiscounts'
const DiscountsV2 = lazyScreen(() => import('./discounts/DiscountsV2'))
const SuppliersV2 = lazyScreen(() => import('./suppliers/SuppliersV2'))
import { escapeHtml as esc } from '../lib/html'
const DeliveryV2 = lazyScreen(() => import('./delivery/DeliveryV2'))
const VulaBankRec = lazyScreen(() => import('./VulaBankRec'))
const VulaAccounting = lazyScreen(() => import('./VulaAccounting'))
const VulaLabour = lazyScreen(() => import('./VulaLabour'))
const VulaExpenses = lazyScreen(() => import('./VulaExpenses'))
const VulaImport = lazyScreen(() => import('./VulaImport'))
const VulaRecurringOrders = lazyScreen(() => import('./VulaRecurringOrders'))
const VulaAgentActivity = lazyScreen(() => import('./VulaAgentActivity'))
const VulaBudget = lazyScreen(() => import('./VulaBudget'))
const VulaBroadcast = lazyScreen(() => import('./VulaBroadcast'))
const VulaEmailCampaigns = lazyScreen(() => import('./VulaEmailCampaigns'))
const VulaWATemplates = lazyScreen(() => import('./VulaWATemplates'))
const VulaScheduledJobs = lazyScreen(() => import('./VulaScheduledJobs'))
const VulaCustomers = lazyScreen(() => import('./VulaCustomers'))
const VulaAssistant = lazyScreen(() => import('./VulaAssistant'))
const VulaInbox = lazyScreen(() => import('./VulaInbox'))
const VulaSettings = lazyScreen(() => import('./VulaSettings'))
const VulaDocuments = lazyScreen(() => import('./VulaDocuments'))
const VulaProjects = lazyScreen(() => import('./VulaProjects'))
const VulaQSRates = lazyScreen(() => import('./VulaQSRates'))
const VulaContacts = lazyScreen(() => import('./VulaContacts'))
const VulaFinances = lazyScreen(() => import('./VulaFinances'))
const VulaFollowups = lazyScreen(() => import('./VulaFollowups'))
const VulaTeam = lazyScreen(() => import('./VulaTeam'))
const VulaProjectWorkspace = lazyScreen(() => import('./VulaProjectWorkspace'))
const VulaFieldOps = lazyScreen(() => import('./VulaFieldOps'))
const VulaReports = lazyScreen(() => import('./VulaReports'))

const VulaClientOnboarding = lazyScreen(() => import('./VulaClientOnboarding'))

const VulaQS = lazyScreen(() => import('./VulaQS'))
const VulaQSPro = lazyScreen(() => import('./VulaQSPro'))
const VulaTakeoff = lazyScreen(() => import('./VulaTakeoff'))
const VulaRepHome = lazyScreen(() => import('./VulaRepHome'))
const VulaRepCrmContacts = lazyScreen(() => import('./VulaRepCrmContacts'))
const VulaRepCallSheet = lazyScreen(() => import('./VulaRepCallSheet'))
const VulaRepExpenseSheet = lazyScreen(() => import('./VulaRepExpenseSheet'))
const VulaRepReminders = lazyScreen(() => import('./VulaRepReminders'))
const VulaRepCrmLookup = lazyScreen(() => import('./VulaRepCrmLookup'))
const VulaDraft = lazyScreen(() => import('./VulaDraft'))
const VulaAutomations = lazyScreen(() => import('./VulaAutomations'))
const VulaFlowBuilder = lazyScreen(() => import('./VulaFlowBuilder'))
// Lazy-loaded: the Puck page builder is ~1 MB — keep it out of the main bundle until the Pages tab opens.
const VulaPages = lazyScreen(() => import('./VulaPages'))
const VulaPayments = lazyScreen(() => import('./VulaPayments'))
const VulaTapToPay = lazyScreen(() => import('./VulaTapToPay'))
import { VULA_API } from '../lib/authFetch'
import { NEXT_STATUSES, ORDER_DELETABLE, ORDER_FILTERS, useOrders } from './orders/useOrders'
const OrdersV2 = lazyScreen(() => import('./orders/OrdersV2'))
const ProductsV2 = lazyScreen(() => import('./products/ProductsV2'))

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
    // the redesign shows each sub-tab's Lucide icon in place of its emoji (index.css picks one)
    if (hit?.subtabs) return hit.subtabs.map(t => ({ ...t, Icon: iconFor(t.id) || undefined }))
  }
  return null
}

// Always controlled + full-page: the sidebar (VulaShell via App.jsx) owns navigation and passes
// activeTab/onTabChange, for both the owner/staff shell and master's "Open as tenant" takeover —
// confirmed the only two real callers, both always pass these. (The old uncontrolled/modal mode,
// with its own hand-rolled tab strip and gating, was dead code superseded by that shell takeover
// — deleted 2026-07-21 rather than kept as an unused second nav path to drift out of sync again.)
export default function VulaMerchantAdmin({ tenantId, tenantName, navGroups, access = [], full = true, teamRole = null, teamPhone = null, activeTab, onTabChange, navRequest }) {
  // Who is connecting WhatsApp/Yoco/email — recorded as connected_by (was always blank).
  const adminEmail = useAuthStore(st => st.user?.email) || ""
  const tab = activeTab
  const setTab = onTabChange
  // A member with a defined access list sees only those modules (+ overview). Owners/
  // managers (full) see everything including Team/Settings. Module-level (business-type) gating
  // now lives once, in navConfig.jsx, driving the sidebar itself — this is just the access-list
  // safety net for a tab a signed-in staff member shouldn't be on.
  // today / activity (the redesign's own pages) are filtered on the server, like overview.
  // A section is allowed when any of its sub-tabs is (2026-11-05): a sales rep's access lists
  // rep-contacts, rep-callsheet… never "my-work" itself, so the check bounced a rep who opened My
  // Work back to Home whenever their access arrived from the server after the tap.
  const allowed = (id) => (access || []).includes(id) || (subtabsFor(navGroups, id) || []).some((t) => (access || []).includes(t.id))
  const canSee = (id) => full || id === 'overview' || (id === 'today' || id === 'activity') || allowed(id)
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
  // a sub-tab opens in whichever section holds it in this menu (the redesign moves some — e.g.
  // Stock from Sell to Operate — so a link naming the old section still lands right)
  const navigateTo = (section, subtab) => {
    setPendingNav(subtab ? { subtab } : null)
    setTab(subtab ? sectionOf(navGroups, subtab, section) : section)
  }
  // The redesigned shell's search and Add menu open a sub-tab directly (2026-10-17): App passes
  // { section, subtab, n } — n changes on every request so the same page can be reopened.
  useEffect(() => { if (navRequest) navigateTo(navRequest.section, navRequest.subtab) }, [navRequest])  // eslint-disable-line

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
        <div key={tenantId} className="vma-content" style={styles.contentBare}>
          <ScreenSuspense>
          {/* A restricted sales_rep login gets their own summary here instead of the tenant-wide
              owner overview — 'overview' itself stays always-visible (merchantVisible's own
              rule), only WHAT renders on it differs for this one role. */}
          {tab === 'overview'  && (teamRole === 'sales_rep' && !full
            ? <VulaRepHome tenantId={tenantId} repPhone={teamPhone} onNavigate={navigateTo} />
            : <VulaHomeV2 tenantId={tenantId} onNavigate={navigateTo} />)}
          {tab === 'today' && <VulaToday tenantId={tenantId} onNavigate={navigateTo} />}
          {tab === 'activity' && <VulaActivity tenantId={tenantId} onNavigate={navigateTo} />}
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
          {tab === 'operate' && <OperateSection tenantId={tenantId} products={products} subtabs={subtabsFor(navGroups, 'operate')}
            pendingSubtab={pendingNav?.subtab} onConsumePendingNav={() => setPendingNav(null)}
            onViewInvoices={(supplierId) => { setInvoiceSupplierFilter(supplierId); navigateTo('money', 'invoices') }} />}
          {tab === 'estimating' && <EstimatingSection tenantId={tenantId} subtabs={subtabsFor(navGroups, 'estimating')}
            pendingSubtab={pendingNav?.subtab} onConsumePendingNav={() => setPendingNav(null)} />}
          {tab === 'team'      && <VulaTeam          tenantId={tenantId} />}
          {tab === 'settings'  && <VulaSettings      tenantId={tenantId} tenantName={tenantName} adminEmail={adminEmail} />}
          </ScreenSuspense>
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
      <ScreenSuspense>
      {active === 'assistant' && <VulaAssistant tenantId={tenantId} />}
      {active === 'agentlog' && <VulaAgentActivity tenantId={tenantId} />}
      {active === 'automations' && <VulaAutomations tenantId={tenantId} />}
      {active === 'flows' && <VulaFlowBuilder tenantId={tenantId} />}
      </ScreenSuspense>
    </div>
  )
}

function SellSection({ tenantId, products, subtabs, pendingSubtab, onConsumePendingNav, onViewInvoices }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'orders' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      <ScreenSuspense>
      {active === 'orders' && <OrdersTab tenantId={tenantId} />}
      {active === 'products' && <ProductsTab tenantId={tenantId} />}
      {active === 'discounts' && <DiscountCodesTab tenantId={tenantId} />}
      {active === 'subscriptions' && <VulaRecurringOrders tenantId={tenantId} />}
      {active === 'delivery' && (<DeliveryV2 tenantId={tenantId} />)}
      {active === 'bookings' && <VulaBookings tenantId={tenantId} />}
      {active === 'stock' && <VulaStock tenantId={tenantId} products={products} />}
      {active === 'costing' && <VulaCosting tenantId={tenantId} />}
      {active === 'suppliers' && (<SuppliersV2 tenantId={tenantId} onViewInvoices={onViewInvoices} />)}
      {active === 'import' && <VulaImport tenantId={tenantId} />}
      </ScreenSuspense>
    </div>
  )
}

function MoneySection({ tenantId, products, subtabs, pendingSubtab, onConsumePendingNav, invoiceSupplierFilter, onClearSupplierFilter }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'invoices' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      <ScreenSuspense>
      {active === 'invoices' && <VulaInvoices tenantId={tenantId} products={products} initialSupplierId={invoiceSupplierFilter} onClearSupplierFilter={onClearSupplierFilter} />}
      {active === 'expenses' && <VulaExpenses tenantId={tenantId} />}
      {active === 'bank' && <VulaBankRec tenantId={tenantId} />}
      {active === 'books' && <VulaAccounting tenantId={tenantId} />}
      {active === 'payments' && <VulaPayments tenantId={tenantId} />}
      {active === 'taptopay' && <VulaTapToPay tenantId={tenantId} />}
      {active === 'budget' && <VulaBudget tenantId={tenantId} />}
      {active === 'scanner' && <VulaSmartScanner tenantId={tenantId} products={products} />}
      {active === 'finances' && <FinancesSubsection tenantId={tenantId} />}
      </ScreenSuspense>
    </div>
  )
}

// Reports+Finances double-mount fix: VulaFinanceInsights mounted once, then an Insights/Ledger
// toggle underneath — previously two competing top-level tabs each mounted VulaFinanceInsights
// a second time alongside a different sibling (VulaReports vs VulaFinances).
const FINANCES_INNER_TABS = [{ id: 'insights', label: 'Insights' }, { id: 'ledger', label: 'Ledger' }]
function FinancesSubsection({ tenantId }) {
  const { tabs, active, setActive } = useSectionTabs(FINANCES_INNER_TABS, { defaultTabId: 'insights' })
  return <FinancesV2 tenantId={tenantId} />   // the redesign: same data, finances/FinancesV2.jsx
  return (
    <div>
      <VulaFinanceInsights tenantId={tenantId} />
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      <ScreenSuspense>
      {active === 'insights' && <VulaReports tenantId={tenantId} />}
      {active === 'ledger' && <VulaFinances tenantId={tenantId} />}
      </ScreenSuspense>
    </div>
  )
}

function PeopleSection({ tenantId, subtabs, pendingSubtab, onConsumePendingNav }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'customers' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      <ScreenSuspense>
      {active === 'customers' && <VulaCustomers tenantId={tenantId} />}
      {active === 'contacts' && <VulaContacts tenantId={tenantId} />}
      {active === 'followups' && <VulaFollowups tenantId={tenantId} />}
      </ScreenSuspense>
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
      <ScreenSuspense>
      {active === 'onboarding' && <VulaClientOnboarding tenantId={tenantId} />}
      {active === 'broadcast' && <VulaBroadcast tenantId={tenantId} draftBody={draftBody} onConsumeDraft={onConsumeDraft} />}
      {active === 'email-campaigns' && <VulaEmailCampaigns tenantId={tenantId} />}
      {active === 'wa-templates' && <VulaWATemplates tenantId={tenantId} />}
      {active === 'scheduling' && <VulaScheduledJobs tenantId={tenantId} />}
      {active === 'marketing' && <VulaMarketing tenantId={tenantId} onSendAsBroadcast={onSendAsBroadcast} />}
      </ScreenSuspense>
    </div>
  )
}

function OperateSection({ tenantId, products, subtabs, pendingSubtab, onConsumePendingNav, onViewInvoices }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'projects' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      <ScreenSuspense>
      {active === 'projects' && <VulaProjects tenantId={tenantId} />}
      {active === 'workspace' && <VulaProjectWorkspace tenantId={tenantId} />}
      {active === 'fieldops' && <VulaFieldOps tenantId={tenantId} />}
      {active === 'labour' && <VulaLabour tenantId={tenantId} />}
      {active === 'qsrates' && <VulaQSRates tenantId={tenantId} />}
      {active === 'documents' && <VulaDocuments tenantId={tenantId} />}
      {/* the redesign files Stock and Suppliers here (navConfig.withV2Moves) */}
      {active === 'stock' && <VulaStock tenantId={tenantId} products={products} />}
      {active === 'suppliers' && (<SuppliersV2 tenantId={tenantId} onViewInvoices={onViewInvoices} />)}
      </ScreenSuspense>
    </div>
  )
}

function MyWorkSection({ tenantId, repPhone, subtabs, pendingSubtab, onConsumePendingNav }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'rep-contacts' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      <ScreenSuspense>
      {active === 'rep-contacts' && <VulaRepCrmContacts tenantId={tenantId} repPhone={repPhone} />}
      {active === 'rep-callsheet' && <VulaRepCallSheet tenantId={tenantId} repPhone={repPhone} />}
      {active === 'rep-bookings' && <VulaBookings tenantId={tenantId} />}
      {active === 'rep-documents' && <VulaDocuments tenantId={tenantId} defaultFiledBy={repPhone} />}
      {active === 'rep-reminders' && <VulaRepReminders tenantId={tenantId} repPhone={repPhone} />}
      {active === 'rep-expenses' && <VulaExpenses tenantId={tenantId} defaultPaidBy={repPhone} />}
      {active === 'rep-expense-sheet' && <VulaRepExpenseSheet tenantId={tenantId} repPhone={repPhone} />}
      {active === 'rep-crm' && <VulaRepCrmLookup tenantId={tenantId} />}
      </ScreenSuspense>
    </div>
  )
}

function EstimatingSection({ tenantId, subtabs, pendingSubtab, onConsumePendingNav }) {
  const { tabs, active, setActive } = useSectionTabs(subtabs || [], { defaultTabId: 'qs' })
  useEffect(() => { if (pendingSubtab) { setActive(pendingSubtab); onConsumePendingNav() } }, [pendingSubtab])  // eslint-disable-line
  return (
    <div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      <ScreenSuspense>
      {active === 'qs' && <VulaQS tenantId={tenantId} />}
      {active === 'qspro' && <VulaQSPro tenantId={tenantId} />}
      {active === 'takeoff' && <VulaTakeoff tenantId={tenantId} />}
      {active === 'draft' && <VulaDraft tenantId={tenantId} />}
      </ScreenSuspense>
    </div>
  )
}

// ── Orders ────────────────────────────────────────────────────────────────────

// Only orders that never became real fulfilment can be deleted — mirrors the backend guard on
// DELETE/bulk-delete (pending_payment/cancelled only), so the UI never offers a delete that would
// just 400. Anything past this needs cancel/refund first, same as before.

function OrdersTab({ tenantId }) {
  return <OrdersV2 tenantId={tenantId} DetailDrawer={OrderDetailDrawer} ManualOrderModal={ManualOrderModal} />
  return <OrdersTabV1 tenantId={tenantId} />
}

// data + actions in orders/useOrders.js (shared with the redesign's orders/OrdersV2.jsx)
function OrdersTabV1({ tenantId }) {
  const {
    orders, loading, load, filter, setFilter, updating, detailId, setDetailId, showManual, setShowManual,
    selected, setSelected, deleting, refundOrder, advance, toggleSelect, deleteOne, deleteSelected, exportCsv,
  } = useOrders(tenantId)
  const filters = ORDER_FILTERS

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
          <button onClick={exportCsv} style={styles.btnGhost} disabled={!orders.length}>⬇ Export CSV</button>
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

// Deposit / balance on WhatsApp (2026-10-13, Ian): the customer gets the order number, what's
// on it and a Pay button (card or EFT) in their chat; paying the deposit confirms the order.
function OrderPaymentRequest({ tenantId, order, fmt }) {
  const [pay, setPay] = useState(null)
  const [deposit, setDeposit] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin/orders/${order.id}`

  const load = () => fetch(`${base}/payment`).then(r => r.ok ? r.json() : null).then(setPay).catch(() => {})
  useEffect(() => { load() }, [tenantId, order.id])   // eslint-disable-line react-hooks/exhaustive-deps

  if (['cancelled', 'refunded'].includes(order.status) || !order.customer_phone) return null

  async function send(kind) {
    setBusy(true); setNote(null)
    const body = { kind }
    if (kind === 'deposit') {
      const v = deposit.trim()
      if (v.endsWith('%')) body.pct = parseFloat(v)
      else body.amount_cents = Math.round(parseFloat(v.replace(/[R,\s]/g, '')) * 100)
    }
    try {
      const r = await fetch(`${base}/payment-request`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      })
      const d = await r.json()
      if (!r.ok) setNote({ ok: false, text: d.detail || 'Could not send.' })
      else setNote({ ok: d.sent, text: d.sent
        ? (d.via_template
          ? `Outside WhatsApp's 24-hour window — the customer got the payment reminder. Send again once they reply.`
          : `Sent: ${d.kind === 'deposit' ? 'Pay deposit' : 'Pay balance'} button for ${fmt(d.amount_cents)} (invoice ${d.invoice_number}).`)
        : `Invoice ${d.invoice_number} is ready but WhatsApp didn't deliver — share this link: ${d.link}` })
      load()
    } catch { setNote({ ok: false, text: 'Could not send.' }) }
    setBusy(false)
  }

  const balance = pay ? pay.balance_cents : order.total_cents
  return (
    <div style={{ margin: '10px 0 4px', padding: 12, borderRadius: 'var(--r-card, 12px)', background: 'var(--surface-alt)' }}>
      <p style={{ ...styles.detailSection, marginTop: 0 }}>Payment on WhatsApp</p>
      {pay && pay.paid_cents > 0 && (
        <p style={styles.detailMeta}>Paid {fmt(pay.paid_cents)} · balance {fmt(pay.balance_cents)}</p>
      )}
      {pay?.open_request && (
        <p style={styles.detailMeta}>Waiting on the {pay.open_request.kind} ({fmt(pay.open_request.amount_cents)}, {pay.open_request.invoice_number})</p>
      )}
      {balance > 0 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 6 }}>
          {!(pay && pay.paid_cents > 0) && (
            <>
              <input value={deposit} onChange={e => setDeposit(e.target.value)} placeholder="Deposit e.g. 1500 or 50%"
                style={{ flex: '1 1 150px', padding: '8px 10px', fontSize: 16, borderRadius: 'var(--r-input, 8px)', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)' }} />
              <button disabled={busy || !deposit.trim()} onClick={() => send('deposit')} style={{ ...styles.btnAction, color: 'var(--on-accent, #fff)', padding: '8px 14px' }}>Request deposit</button>
            </>
          )}
          <button disabled={busy} onClick={() => send('balance')} style={{ ...styles.btnGhost, padding: '8px 14px', fontSize: 12 }}>
            {pay && pay.paid_cents > 0 ? `Request balance ${fmt(balance)}` : 'Request full amount'}
          </button>
        </div>
      )}
      {note && <p style={{ ...styles.detailMeta, color: note.ok ? 'var(--ok)' : 'var(--danger)', marginTop: 6 }}>{note.text}</p>}
    </div>
  )
}

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
    // Customer text is escaped (lib/html.js): this about:blank window shares the dashboard's origin.
    const rows = items.map(i =>
      `<tr><td>${esc(i.product_name || i.name || 'Item')}</td><td style="text-align:right">${esc(i.quantity)}</td><td style="text-align:right">${fmt(i.unit_price_cents || i.price_cents)}</td></tr>`
    ).join('')
    w.document.write(`
      <html><head><title>Packing slip ${esc(order.display_id)}</title>
      <style>body{font-family:system-ui;padding:24px;color:black}h1{font-size:20px}
      table{width:100%;border-collapse:collapse;margin-top:12px}td,th{padding:6px 4px;border-bottom:1px solid silver;text-align:left}</style>
      </head><body>
      <h1>Packing slip — ${esc(order.display_id)}</h1>
      <p><strong>${esc(order.customer_name || '')}</strong> · ${esc(order.customer_phone || '')}</p>
      <p>${esc(order.delivery_address || 'No address')} · ${esc(order.delivery_slot || '')}</p>
      ${order.delivery_notes ? `<p>Notes: ${esc(order.delivery_notes)}</p>` : ''}
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

              <OrderPaymentRequest tenantId={tenantId} order={order} fmt={fmt} />

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

  // v2: the redesigned list (products/ProductsV2.jsx) — same state and actions; the full
  // editor (ProductEditPanel) opens in a Sheet there
  return <ProductsV2 EditPanel={ProductEditPanel} api={{
      tenantId, products, categories, loading, saving, editPrice, setEditPrice, savePrice, patch, deleteProduct,
      catKeys, catLabel, showAdd, setShowAdd, form, setForm, adding, createProduct, showCats, setShowCats,
      newCat, setNewCat, addCategory, exportCsv, fileInputRef, onImportFile, importRows, importing, importResults,
      validateImportRow, commitImport, closeImport: () => { setImportRows(null); setImportResults(null) },
    }} />
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
    if (f.stock !== '' && !isNaN(toQty(f.stock))) upd.stock_quantity = toQty(f.stock)
    const saleC = f.sale === '' ? null : Math.round(parseFloat(f.sale) * 100)
    upd.sale_price_cents = (saleC && saleC > 0) ? saleC : null
    upd.sale_ends_at = f.saleEnds ? `${f.saleEnds}T23:59:59+02:00` : null
    if (f.weight !== '') upd.weight_grams = parseInt(f.weight) || null
    if (f.packSize !== '') upd.pack_size = f.packSize
    if (f.serves !== '') upd.serves = parseInt(f.serves) || null
    upd.reorder_threshold = f.reorderThreshold === '' ? null : toQty(f.reorderThreshold) || null
    upd.reorder_qty = f.reorderQty === '' ? null : toQty(f.reorderQty) || null
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
          <input type="number" step="any" value={f.stock} onChange={e => set('stock', e.target.value)} placeholder="e.g. 12 or 2.5" style={{ ...inp, width: '100%' }} /></div>
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
          <input type="number" step="any" value={f.reorderThreshold} onChange={e => set('reorderThreshold', e.target.value)} placeholder="e.g. 5" style={{ ...inp, width: '100%' }} /></div>
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
        stock_quantity: (newVariant.stock !== undefined && newVariant.stock !== '') ? toQty(newVariant.stock) : null,
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
    const qty = toQty(bulkStock)
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
                        <input type="number" step="any" disabled={saving === v.id} defaultValue={v.stock_quantity ?? ''}
                               onBlur={e => patchVariant(v.id, { stock_quantity: e.target.value === '' ? null : toQty(e.target.value) })}
                               style={{ ...inp, width: 60 }} />
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <input type="number" disabled={saving === v.id} defaultValue={v.reorder_threshold ?? ''}
                               onBlur={e => patchVariant(v.id, { reorder_threshold: e.target.value === '' ? null : toQty(e.target.value) })}
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

function DiscountCodesTab({ tenantId }) {
  return <DiscountsV2 tenantId={tenantId} />
  return <DiscountCodesV1 tenantId={tenantId} />
}

function DiscountCodesV1({ tenantId }) {
  const { codes, loading, saving, editing, form, setForm, error, startEdit, startNew, cancel, save, remove, toggleActive } = useDiscounts(tenantId)
  const inp = { padding: '7px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, boxSizing: 'border-box' }

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

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = {
  overlay:      { position:'fixed', inset:0, background:'rgba(0,0,0,0.5)', zIndex:200, display:'flex', justifyContent:'flex-end' },
  panel:        { width:'100%', maxWidth:680, background:'var(--bg)', overflowY:'auto', display:'flex', flexDirection:'column', boxShadow:'-4px 0 24px rgba(0,0,0,0.15)' },
  header:       { display:'flex', alignItems:'flex-start', justifyContent:'space-between', padding:'24px 28px 0', borderBottom:'1px solid var(--border)', paddingBottom:16 },
  title:        { fontFamily:"var(--font-display)", fontSize:26, fontWeight:700, color:'var(--ink)', margin:0 },
  subtitle:     { fontSize:12, color:'var(--muted)', margin:'2px 0 0' },
  closeBtn:     { background:'transparent', border:'none', fontSize:28, cursor:'pointer', color:'var(--muted)', lineHeight:1 },
  content:      { padding:'20px 28px', flex:1, overflowY:'auto' },
  contentBare:  { flex:1, minWidth:0 },  // shell mode — shell owns chrome; padding: .vma-content (index.css)
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
