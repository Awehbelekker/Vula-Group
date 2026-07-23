/**
 * navConfig.jsx — the grouped sidebar navigation for both shells (UI overhaul Phase 2).
 * One source of truth: VulaShell renders these groups; App.jsx / VulaMerchantAdmin map ids to
 * components exactly as before — every pre-overhaul tab id is present, nothing dropped.
 */

// Merchant shell (owner/staff, and master "open as tenant"). ids = VulaMerchantAdmin tab ids.
//
// IA overhaul (2026-07-22): 43 flat tabs collapsed into 10 top-level sections. A section with a
// `subtabs` array is a SINGLE sidebar entry whose click target (id) leads to VulaMerchantAdmin
// rendering a dedicated "Section" wrapper component that owns its own inner SectionTabs strip —
// exactly the pattern VulaMasterPanel already used for Tenants/Health/Usage/etc., just formalized
// into the shared useSectionTabs/SectionTabs primitive so both admins use the one mechanism. A
// section's own top-level id (e.g. "money") is a synthetic grouping id, never itself a real
// permission/module key — only its children (e.g. "invoices") are. See merchantVisible/filterGroups
// below for how a section survives (any child visible) vs. which of its children render (each
// individually gated, same as before this restructuring — nothing about the actual gating rules
// changed, only how the visible tab set is grouped for display).
export const MERCHANT_GROUPS = [
  { label: "", items: [
    { id: "overview", icon: "🏠", label: "Home" },
  ]},
  { label: "", items: [
    { id: "inbox", icon: "📮", label: "Inbox" },
  ]},
  { label: "Assistant", items: [
    { id: "assistant-hub", icon: "💬", label: "Assistant", subtabs: [
      { id: "assistant", icon: "💬", label: "Chat" },
      { id: "agentlog", icon: "🧠", label: "Activity" },
      { id: "automations", icon: "⚡", label: "Automations" },
    ]},
  ]},
  { label: "Sell", items: [
    { id: "sell", icon: "📦", label: "Sell", subtabs: [
      { id: "orders", icon: "📦", label: "Orders" },
      { id: "products", icon: "🐟", label: "Products" },
      { id: "discounts", icon: "🏷️", label: "Discounts" },
      { id: "subscriptions", icon: "🔁", label: "Subscriptions" },
      { id: "delivery", icon: "🛵", label: "Delivery" },
      { id: "bookings", icon: "📅", label: "Bookings" },
      { id: "suppliers", icon: "🚚", label: "Suppliers" },
      { id: "import", icon: "📥", label: "Import" },
    ]},
  ]},
  { label: "Money", items: [
    { id: "money", icon: "💰", label: "Money", subtabs: [
      { id: "invoices", icon: "🧾", label: "Invoices" },
      { id: "expenses", icon: "💸", label: "Expenses" },
      { id: "bank", icon: "🏦", label: "Bank" },
      { id: "books", icon: "📒", label: "Books" },
      { id: "payments", icon: "💳", label: "Payments" },
      { id: "budget", icon: "💰", label: "Budget" },
      { id: "scanner", icon: "📷", label: "Scanner" },
      { id: "finances", icon: "💵", label: "Finances" },
    ]},
  ]},
  { label: "People", items: [
    { id: "people", icon: "👥", label: "People", subtabs: [
      { id: "customers", icon: "👥", label: "Customers" },
      { id: "contacts", icon: "📇", label: "Contacts" },
      { id: "followups", icon: "📬", label: "Follow-ups" },
    ]},
  ]},
  { label: "Marketing", items: [
    { id: "marketing-hub", icon: "✨", label: "Marketing", subtabs: [
      { id: "onboarding", icon: "🎉", label: "Client Onboarding" },
      { id: "broadcast", icon: "📢", label: "Broadcast" },
      { id: "email-campaigns", icon: "✉️", label: "Email" },
      { id: "wa-templates", icon: "📨", label: "Templates" },
      { id: "scheduling", icon: "⏰", label: "Scheduling" },
      { id: "marketing", icon: "✨", label: "Campaigns" },
    ]},
  ]},
  { label: "", items: [
    { id: "pages", icon: "🎨", label: "Storefront" },
  ]},
  { label: "Operate", items: [
    { id: "operate", icon: "🏗️", label: "Operate", subtabs: [
      { id: "projects", icon: "🏗️", label: "Projects" },
      { id: "workspace", icon: "🗂️", label: "Workspace" },
      { id: "fieldops", icon: "👷", label: "Field Ops" },
      { id: "labour", icon: "🧱", label: "Labour" },
      { id: "qsrates", icon: "📐", label: "QS Rates" },
      { id: "documents", icon: "📂", label: "Documents" },
    ]},
  ]},
  { label: "Estimating", items: [
    { id: "estimating", icon: "🧮", label: "Estimating", subtabs: [
      { id: "qs", icon: "🧮", label: "Quick Cost" },
      { id: "qspro", icon: "📐", label: "QS Pro" },
      { id: "takeoff", icon: "📏", label: "Takeoff" },
      { id: "draft", icon: "✍️", label: "AI Draft" },
      { id: "training", icon: "📚", label: "Training KB" },
    ]},
  ]},
  { label: "Admin", items: [
    { id: "team", icon: "👥", label: "Team" },
    { id: "settings", icon: "⚙️", label: "Settings" },
  ]},
];

// Master shell (Vula operator console — NOT a tenant's own tools). ids = App.jsx TABS ids.
// Construction-specific tools (QS/Takeoff/Draft/Training/Workspace/Projects/Field Ops/Docs) were
// moved to the merchant shell (P3.1, 2026-07-19) so any construction/architecture tenant gets
// them via "Open as tenant", not just DIGG via a special master-only path. Reach DIGG's own tools
// through Master → Tenants → Open as tenant → digg-demo.
//
// IA overhaul (2026-07-22): this used to be one flat sidebar mixing platform-operator tools
// (Master) with Vula-the-company's own internal business-admin tools (Office/Clients) at the
// same visual weight — a real "which of these am I even looking at" problem. Split into two
// zones with a segmented switch (VulaShell's `zones` prop). "Dashboard" (a standalone document-
// upload/RAG-query tool that predates this split and doesn't fit either concern) folds into
// Platform Ops alongside the Agent test rig — both are dev/ops-facing tools, not real platform-
// operations or business-admin functionality.
export const MASTER_ZONES = [
  { id: "platform", label: "Platform Ops", groups: [
    { label: "", items: [
      { id: "master", icon: "🛠", label: "Master" },
    ]},
    { label: "Debug", items: [
      { id: "dashboard", icon: "🏠", label: "Dashboard" },
      { id: "agent", icon: "🤖", label: "Agent (test rig)" },
    ]},
  ]},
  { id: "business", label: "Vula's Business", groups: [
    { label: "Office", items: [
      { id: "contacts", icon: "📇", label: "Contacts" },
      { id: "finances", icon: "💵", label: "Finances" },
      { id: "followups", icon: "📬", label: "Follow-ups" },
      { id: "team", icon: "👥", label: "Team" },
      { id: "invoices", icon: "🧾", label: "Invoices" },
      { id: "budget", icon: "💰", label: "Budget" },
      { id: "reports", icon: "📈", label: "Reports" },
      { id: "payments", icon: "💳", label: "Payments" },
    ]},
    { label: "Clients", items: [
      { id: "onboard", icon: "🚀", label: "Onboard Client" },
      { id: "admin", icon: "📝", label: "Signups" },
      { id: "subscriptions", icon: "🔁", label: "Subscriptions" },
      { id: "merchant", icon: "🏪", label: "Merchant" },
    ]},
  ]},
];

// Flat view of MASTER_ZONES — labelFor/withInboxBadge and any other caller that just needs "all
// master groups regardless of zone" (e.g. to resolve a label without knowing which zone an id is
// in) can keep using this instead of picking a zone.
export const MASTER_GROUPS = MASTER_ZONES.flatMap((z) => z.groups);

// This used to be a hand-copy of VulaMerchantAdmin's own internal CORE/MODMAP — and had already
// drifted (missing `discounts: 'products'`) before that internal copy was deleted (2026-07-21).
// This is now the ONLY copy, so it can't drift again by construction.
const MERCHANT_CORE = new Set(['overview', 'assistant', 'agentlog', 'inbox', 'settings', 'suppliers', 'qsrates', 'pages', 'marketing', 'bank', 'books', 'labour', 'expenses', 'import', 'wa-templates', 'scheduling']);
const MERCHANT_MODMAP = {
  customers: 'crm', contacts: 'crm', broadcast: 'broadcasts', subscriptions: 'orders',
  qs: 'estimating', qspro: 'estimating', takeoff: 'estimating', draft: 'ai_draft', training: 'training',
  discounts: 'products',
  onboarding: 'broadcasts',  // rides the same module key it was invisibly bundled under before the Broadcast/Onboarding split
  'email-campaigns': 'broadcasts',  // same gate as WhatsApp broadcast — both are bulk-outreach features
};

/** Visibility predicate for merchant nav items: member access + tenant modules. Operates on a
 * real tab id (a leaf — either a standalone item's own id, or one of a section's subtab ids).
 * A section's own synthetic id (e.g. "money") is never passed to this — see filterGroups. */
export function merchantVisible({ full, access, modules }) {
  const canSee = (id) => full || id === 'overview' || (access || []).includes(id);
  const tenantHas = (id) => modules === null || !modules.length || MERCHANT_CORE.has(id)
    || (modules || []).includes(MERCHANT_MODMAP[id] || id);
  return (id) => {
    if (id === 'team' || id === 'settings') return !!full;
    return canSee(id) && tenantHas(id);
  };
}

/** Filter groups with a per-item visibility predicate; drops empty groups. A `subtabs` item
 * survives if at least one of its own subtabs is visible (its subtabs list is itself filtered
 * down to just the visible ones) — the section-level "any of my children" generalization of the
 * same empty-group collapse this already did one level up. */
export function filterGroups(groups, visible) {
  return groups
    .map(g => ({
      ...g,
      items: g.items
        .map(it => it.subtabs ? { ...it, subtabs: it.subtabs.filter(st => visible(st.id)) } : it)
        .filter(it => it.subtabs ? it.subtabs.length > 0 : visible(it.id)),
    }))
    .filter(g => g.items.length);
}

/** Find an item's label across groups (for the top-bar title). */
export function labelFor(groups, id) {
  for (const g of groups) {
    const hit = g.items.find(it => it.id === id);
    if (hit) return hit.label;
  }
  return "";
}
