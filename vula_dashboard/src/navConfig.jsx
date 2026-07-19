/**
 * navConfig.jsx — the grouped sidebar navigation for both shells (UI overhaul Phase 2).
 * One source of truth: VulaShell renders these groups; App.jsx / VulaMerchantAdmin map ids to
 * components exactly as before — every pre-overhaul tab id is present, nothing dropped.
 */

// Merchant shell (owner/staff, and master "open as tenant"). ids = VulaMerchantAdmin tab ids.
export const MERCHANT_GROUPS = [
  { label: "", items: [
    { id: "overview", icon: "🏠", label: "Home" },
  ]},
  { label: "Sell", items: [
    { id: "orders", icon: "📦", label: "Orders" },
    { id: "products", icon: "🐟", label: "Products" },
    { id: "customers", icon: "👥", label: "Customers" },
    { id: "contacts", icon: "📇", label: "Contacts" },
    { id: "import", icon: "📥", label: "Import" },
    { id: "broadcast", icon: "📢", label: "Broadcast" },
    { id: "wa-templates", icon: "📨", label: "Templates" },
    { id: "scheduling", icon: "⏰", label: "Scheduling" },
    { id: "marketing", icon: "✨", label: "Marketing" },
    { id: "subscriptions", icon: "🔁", label: "Recurring" },
    { id: "delivery", icon: "🛵", label: "Delivery" },
    { id: "suppliers", icon: "🚚", label: "Suppliers" },
  ]},
  { label: "Store", items: [
    { id: "pages", icon: "🎨", label: "Storefront pages" },
  ]},
  { label: "Money", items: [
    { id: "invoices", icon: "🧾", label: "Invoices" },
    { id: "expenses", icon: "💸", label: "Expenses" },
    { id: "bank", icon: "🏦", label: "Bank" },
    { id: "books", icon: "📒", label: "Books" },
    { id: "payments", icon: "💳", label: "Payments" },
    { id: "budget", icon: "💰", label: "Budget" },
    { id: "scanner", icon: "📷", label: "Scanner" },
    { id: "reports", icon: "📈", label: "Reports" },
    { id: "finances", icon: "💵", label: "Finances" },
  ]},
  { label: "Operate", items: [
    { id: "inbox", icon: "📮", label: "Inbox" },
    { id: "bookings", icon: "📅", label: "Bookings" },
    { id: "followups", icon: "📬", label: "Follow-ups" },
    { id: "projects", icon: "🏗️", label: "Projects" },
    { id: "workspace", icon: "🗂️", label: "Workspace" },
    { id: "fieldops", icon: "👷", label: "Field Ops" },
    { id: "labour", icon: "🧱", label: "Labour" },
    { id: "qsrates", icon: "📐", label: "QS Rates" },
    { id: "documents", icon: "📂", label: "Documents" },
  ]},
  { label: "Estimating", items: [
    { id: "qs", icon: "🧮", label: "Quick Cost" },
    { id: "qspro", icon: "📐", label: "QS Pro" },
    { id: "takeoff", icon: "📏", label: "Takeoff" },
    { id: "draft", icon: "✍️", label: "AI Draft" },
    { id: "training", icon: "📚", label: "Training KB" },
  ]},
  { label: "Automate", items: [
    { id: "assistant", icon: "💬", label: "Assistant" },
    { id: "agentlog", icon: "🧠", label: "Agent" },
    { id: "automations", icon: "⚡", label: "Automations" },
  ]},
  { label: "", items: [
    { id: "team", icon: "👥", label: "Team" },
    { id: "settings", icon: "⚙️", label: "Settings" },
  ]},
];

// Master shell (Vula operator console — NOT a tenant's own tools). ids = App.jsx TABS ids.
// Construction-specific tools (QS/Takeoff/Draft/Training/Workspace/Projects/Field Ops/Docs) were
// moved to the merchant shell (P3.1, 2026-07-19) so any construction/architecture tenant gets
// them via "Open as tenant", not just DIGG via a special master-only path. Reach DIGG's own tools
// through Master → Tenants → Open as tenant → digg-demo.
export const MASTER_GROUPS = [
  { label: "", items: [
    { id: "dashboard", icon: "🏠", label: "Dashboard" },
    { id: "master", icon: "🛠", label: "Master" },
  ]},
  { label: "Debug", items: [
    { id: "agent", icon: "🤖", label: "Agent (test rig)" },
  ]},
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
    { id: "commerce", icon: "🛒", label: "Commerce" },
    { id: "merchant", icon: "🏪", label: "Merchant" },
  ]},
];

// Mirrors VulaMerchantAdmin's internal gating (kept in sync — used by the shell sidebar).
const MERCHANT_CORE = new Set(['overview', 'assistant', 'agentlog', 'inbox', 'settings', 'suppliers', 'qsrates', 'pages', 'marketing', 'bank', 'books', 'labour', 'expenses', 'import', 'wa-templates', 'scheduling']);
const MERCHANT_MODMAP = {
  customers: 'crm', contacts: 'crm', broadcast: 'broadcasts', subscriptions: 'orders',
  qs: 'estimating', qspro: 'estimating', takeoff: 'estimating', draft: 'ai_draft', training: 'training',
};

/** Visibility predicate for merchant nav items: member access + tenant modules. */
export function merchantVisible({ full, access, modules }) {
  const canSee = (id) => full || id === 'overview' || (access || []).includes(id);
  const tenantHas = (id) => modules === null || !modules.length || MERCHANT_CORE.has(id)
    || (modules || []).includes(MERCHANT_MODMAP[id] || id);
  return (id) => {
    if (id === 'team' || id === 'settings') return !!full;
    return canSee(id) && tenantHas(id);
  };
}

/** Filter groups with a per-item visibility predicate; drops empty groups. */
export function filterGroups(groups, visible) {
  return groups
    .map(g => ({ ...g, items: g.items.filter(it => visible(it.id)) }))
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
