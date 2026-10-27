// Seeded demo data for the visual baseline — a made-up deli, "Harbour & Vine Deli" (tenant id
// "demo-shop"). EVERYTHING here is fictional: customer names are invented, phones are in the
// unallocated +27 82 000 0xxx block, emails use the reserved example.test domain. Never paste
// production rows into this file (see tests/visual/README.md).
//
// Shapes mirror the real FastAPI responses in vula_mind/vula/api/ (handler named next to each
// export). Money is integer cents, exactly as the API returns it.

export const TENANT_ID = "demo-shop";
export const TENANT_NAME = "Harbour & Vine Deli";
// The frozen "now" for every screenshot: Saturday 17 Oct 2026, 10:30 SAST.
export const NOW_ISO = "2026-10-17T10:30:00+02:00";

const API = "http://api.vula.test";
const SB = "http://supabase.vula.test";
export const LOGO_URL = `${SB}/storage/v1/object/public/brand/${TENANT_ID}/logo.svg`;

/** "2026-10-17T08:12:00+02:00" helper — day offset back from the 17th, local SAST time. */
const at = (daysAgo, hhmm) => {
  const d = 17 - daysAgo;
  return `2026-10-${String(d).padStart(2, "0")}T${hhmm}:00+02:00`;
};
const day = (daysAgo) => `2026-10-${String(17 - daysAgo).padStart(2, "0")}`;

// ── Customers (commerce.py::admin_list_customers / _aggregate_customers) ─────────────────────
export const customers = [
  { phone: "+27820000101", name: "Thandi Mokoena", orders: 9, total_spent_cents: 412_850, last_order_at: at(0, "08:42"), last_seen_at: at(0, "09:58"), source: "order", channel: "whatsapp", area: "Sea Point", tags: ["vip", "wholesale"] },
  { phone: "+27820000102", name: "Pieter van der Berg", orders: 6, total_spent_cents: 268_400, last_order_at: at(1, "16:20"), last_seen_at: at(1, "16:25"), source: "order", channel: "whatsapp", area: "Green Point", tags: ["vip"] },
  { phone: "+27820000103", name: "Ayesha Daniels", orders: 5, total_spent_cents: 189_900, last_order_at: at(2, "12:05"), last_seen_at: at(0, "09:41"), source: "order", channel: "web", area: "Woodstock", tags: [] },
  { phone: "+27820000104", name: "Sipho Ndlovu", orders: 4, total_spent_cents: 143_250, last_order_at: at(0, "07:55"), last_seen_at: at(0, "07:56"), source: "order", channel: "whatsapp", area: "Observatory", tags: ["allergy-nuts"] },
  { phone: "+27820000105", name: "Lerato Khumalo", orders: 3, total_spent_cents: 97_600, last_order_at: at(4, "11:30"), last_seen_at: at(4, "11:31"), source: "order", channel: "whatsapp", area: "Gardens", tags: [] },
  { phone: "+27820000106", name: "Marco Rossi", orders: 2, total_spent_cents: 61_200, last_order_at: at(6, "18:10"), last_seen_at: at(3, "10:02"), source: "order", channel: "web", area: "Tamboerskloof", tags: ["catering"] },
  { phone: "+27820000107", name: "Nomsa Dlamini", orders: 2, total_spent_cents: 38_450, last_order_at: at(9, "09:15"), last_seen_at: at(9, "09:16"), source: "order", channel: "whatsapp", area: "Sea Point", tags: [] },
  { phone: "+27820000108", name: "Johan Steyn", orders: 1, total_spent_cents: 24_900, last_order_at: at(12, "13:40"), last_seen_at: at(12, "13:41"), source: "order", channel: "web", area: "Mouille Point", tags: [] },
  { phone: "+27820000109", name: "Fatima Adams", orders: 0, total_spent_cents: 0, last_order_at: null, last_seen_at: at(0, "10:12"), source: "chat", channel: "whatsapp", area: "", tags: [] },
  { phone: "+27820000110", name: "Kabelo Molefe", orders: 0, total_spent_cents: 0, last_order_at: null, last_seen_at: at(1, "19:03"), source: "chat", channel: "whatsapp", area: "", tags: [] },
];
const cust = Object.fromEntries(customers.map((c) => [c.phone, c]));

// one customer's 360 (commerce.py::admin_customer_detail)
export const customerDetail = (phone) => {
  const c = cust[phone] || customers[0];
  return {
    profile: { name: c.name, phone: c.phone, email: `${c.name.split(" ")[0].toLowerCase()}@example.test`, preferred_language: "en",
      first_order_at: "2026-03-14T10:00:00+02:00", last_order_at: c.last_order_at },
    stats: { lifetime_value_cents: c.total_spent_cents, order_count: c.orders, paid_order_count: c.orders,
      avg_order_cents: c.orders ? Math.round(c.total_spent_cents / c.orders) : 0, invoice_outstanding_cents: c.orders > 5 ? 145_000 : 0 },
    payment_behavior: null, note: c.orders > 5 ? "Prefers delivery before 10am. Wholesale account." : "",
    events: c.orders ? [
      { type: "order", at: c.last_order_at, title: "Order HV-1052", detail: "paid", amount_cents: 48_500 },
      { type: "invoice", at: at(5, "09:00"), title: "INV-2026-0118", detail: "sent", amount_cents: 145_000 },
      { type: "order", at: at(9, "11:20"), title: "Order HV-1031", detail: "delivered", amount_cents: 36_900 },
    ] : [],
    conversation: [
      { role: "user", text: "Morning! Is the sourdough in yet?", at: at(1, "07:40") },
      { role: "assistant", text: "Morning! Yes — fresh out at 7. Shall I put one aside?", at: at(1, "07:41") },
    ],
    engagement: [{ campaign: "Spring platters", status: "clicked", at: at(6, "12:00") }],
  };
};

// ── Expenses (commerce.py::admin_list_expenses, /reports/expenses, /cards) ──────────────────
const exp = (n, date, description, amount_cents, extra = {}) => ({
  id: `exp-${String(n).padStart(3, "0")}`, tenant_id: TENANT_ID, date, description, amount_cents,
  vat_cents: Math.round(amount_cents * 15 / 115), supplier: null, channel: "dashboard", paid_by_name: "Demo Owner",
  paid_with: "company_card", card_last4: "4417", reimbursable: false, category: "Cost of sales", account_code: "5000",
  purpose_category: "stock", purpose_detail: null, project: "", section: null, notes: null, status: "submitted", ...extra,
});
export const expenses = [
  exp(9, "2026-10-17", "Sourdough flour, 25kg", 64_900, { supplier: "Eureka Mills", channel: "whatsapp" }),
  exp(8, "2026-10-16", "Delivery fuel", 85_000, { supplier: "Engen Sea Point", paid_by_name: "Zanele", paid_with: "personal", card_last4: null,
    reimbursable: true, category: "Motor vehicle", account_code: "6200", purpose_category: "delivery", channel: "whatsapp" }),
  exp(7, "2026-10-15", "Platter boards (x12)", 143_880, { supplier: "Builders Express", project: null, purpose_category: null, category: "Equipment", account_code: "6400" }),
  exp(6, "2026-10-14", "Cheese wheel — aged cheddar", 212_500, { supplier: "Fairview", status: "approved" }),
  exp(5, "2026-10-12", "Parking — supplier visit", 3_500, { paid_by_name: "Sipho", paid_with: "cash", card_last4: null, reimbursable: true,
    status: "reimbursed", category: "Travel", account_code: "6300", purpose_category: "travel" }),
  exp(4, "2026-10-11", "Card machine rolls", 18_950, { supplier: "Yoco", status: "unconfirmed", channel: "whatsapp", purpose_category: null }),
];
export const expenseReport = {
  total_cents: expenses.reduce((a, e) => a + e.amount_cents, 0),
  vat_cents: expenses.reduce((a, e) => a + e.vat_cents, 0),
  reimbursable_owed_cents: 85_000, count: expenses.length,
  by_person: [{ name: "Zanele", owed_cents: 85_000 }, { name: "Sipho", owed_cents: 0 }],
};
export const expenseLists = {
  projects: ["Spring market stall", "Bay Events platters"], sections: [],
  categories: [{ code: "5000", name: "Cost of sales" }, { code: "6200", name: "Motor vehicle" }, { code: "6300", name: "Travel" }, { code: "6400", name: "Equipment" }],
  purpose_categories: ["stock", "delivery", "travel", "equipment", "marketing", "other"],
};
export const companyCards = [{ id: "card-1", last4: "4417", label: "Company card", active: true }];

// ── Email contacts + follow-ups (email_connect.py) ──────────────────────────────────────────
export const emailContacts = [
  { id: "ec-1", name: "Lindiwe Khoza", email: "lindiwe@harbourvine.example.test", kind: "internal", message_count: 214 },
  { id: "ec-2", name: "Bay Events Co.", email: "accounts@bayevents.example.test", kind: "client", message_count: 37 },
  { id: "ec-3", name: "Eureka Mills", email: "orders@eurekamills.example.test", kind: "supplier", message_count: 22 },
  { id: "ec-4", name: "Fairview Cheese", email: "sales@fairview.example.test", kind: "supplier", message_count: 15 },
  { id: "ec-5", name: null, email: "newsletter@market.example.test", kind: "external", message_count: 4 },
];
export const emailFollowups = [
  { id: "fu-1", sender_name: "Bay Events Co.", sender: "accounts@bayevents.example.test", reason: "request", urgency: "high",
    tone: "polite", received_at: at(3, "09:10"), subject: "Platters for the 24th — confirm numbers?", summary: "Wants final numbers and a revised quote for 120 guests." },
  { id: "fu-2", sender_name: "Eureka Mills", sender: "orders@eurekamills.example.test", reason: "schedule", urgency: "normal",
    tone: null, received_at: at(0, "08:20"), subject: "Delivery slot next week", summary: "Asks whether Tuesday or Thursday suits for the flour delivery." },
];

// ── Team (team.py, users.py) ─────────────────────────────────────────────────────────────────
export const teamMembers = [
  { id: "tm-1", name: "Demo Owner", whatsapp: "+27820000001", role: "owner", active: true, access: [], notify: ["which_project", "followup_digest", "new_order"] },
  { id: "tm-2", name: "Zanele", whatsapp: "+27820000011", role: "staff", active: true, access: ["orders", "inbox", "stock"], notify: ["new_order", "low_stock"] },
  { id: "tm-3", name: "Sipho", whatsapp: "+27820000012", role: "sales_rep", active: false, access: ["rep-contacts", "rep-callsheet"], notify: [] },
];
export const teamLogins = [
  { user_id: "u-owner-0001", email: `owner@${TENANT_ID}.example.test`, role: "owner", last_sign_in: at(0, "07:30") },
  { user_id: "u-staff-0002", email: `zanele@${TENANT_ID}.example.test`, role: "staff", last_sign_in: null },
];
export const teamAudit = [
  { id: "au-1", created_at: at(1, "15:02"), actor_email: `owner@${TENANT_ID}.example.test`, action: "member_updated", detail: { name: "Zanele" } },
  { id: "au-2", created_at: at(4, "10:40"), actor_email: `owner@${TENANT_ID}.example.test`, action: "login_created", detail: { email: `zanele@${TENANT_ID}.example.test` } },
];

// ── Bookings (bookings.py, migration 050) — today is the frozen NOW day ─────────────────────
export const bookingServices = [
  { id: "svc-1", name: "Platter tasting", duration_min: 30, price_cents: 0, active: true },
  { id: "svc-2", name: "Catering consult", duration_min: 60, price_cents: 45_000, active: true },
  { id: "svc-3", name: "Cheese masterclass", duration_min: 90, price_cents: 65_000, active: false },
];
export const bookingsList = [
  { id: "bk-1", start_at: at(0, "09:30"), service_name: "Platter tasting", customer_name: "Fatima Adams", customer_phone: "+27820000109", channel: "whatsapp", status: "confirmed", notes: "20 guests, budget R2,500" },
  { id: "bk-2", start_at: at(0, "11:00"), service_name: "Catering consult", customer_name: "Bay Events Co.", customer_phone: null, channel: "dashboard", status: "pending", notes: null },
  { id: "bk-3", start_at: at(0, "14:30"), service_name: "Platter tasting", customer_name: "Thandi Mokoena", customer_phone: "+27820000101", channel: "whatsapp", status: "completed", notes: null },
  { id: "bk-4", start_at: at(-1, "10:00"), service_name: "Catering consult", customer_name: "Marco Rossi", customer_phone: "+27820000106", channel: "web", status: "confirmed", notes: null },
];
export const bookingAvailability = { closed: false, slots: ["10:00", "12:00", "12:30", "15:30", "16:00"].map((l) => ({ label: l, start_utc: `${l}Z` })) };
export const bookingRules = { slot_minutes: 30, workdays: [1, 2, 3, 4, 5, 6], open_time: "08:00", close_time: "17:00", buffer_min: 10, lead_hours: 2, horizon_days: 30 };

// ── Products (commerce.py::admin_list_products) ──────────────────────────────────────────────
export const products = [
  ["p-01", "Smoked snoek pâté (200g)", "extras", 8_900, 24],
  ["p-02", "Sourdough loaf", "extras", 6_500, 12],
  ["p-03", "Aged cheddar (250g)", "extras", 11_900, 3],
  ["p-04", "Olive tapenade (150g)", "extras", 7_400, 18],
  ["p-05", "Charcuterie board for 4", "extras", 42_500, 6],
  ["p-06", "Fresh linefish fillet (per kg)", "fresh_fish", 26_900, 9],
  ["p-07", "Free-range chicken thighs (1kg)", "fresh_chicken", 12_900, 2],
  ["p-08", "Rooibos & honey granola", "extras", 8_200, 30],
].map(([id, name, category, price_cents, stock_quantity], i) => ({
  id, tenant_id: TENANT_ID, name, category, price_cents, stock_quantity,
  in_stock: stock_quantity > 0, archived: false, sort_order: i, image_url: null, images: [],
  description: "", sold_by: "unit", status: "active", created_at: at(40, "09:00"),
}));

// per-tenant product categories (migration 073)
export const productCategories = [
  { key: "extras", label: "Deli & pantry", sort_order: 1 },
  { key: "fresh_fish", label: "Fresh fish", sort_order: 2 },
  { key: "fresh_chicken", label: "Fresh chicken", sort_order: 3 },
];

// ── Orders (commerce.py::admin_list_orders → service.list_orders) ────────────────────────────
const ord = (n, phone, status, total_cents, daysAgo, hhmm, slot, extra = {}) => ({
  id: `ord-${String(n).padStart(4, "0")}`, tenant_id: TENANT_ID, display_id: `HV-${1040 + n}`,
  customer_phone: phone, customer_name: cust[phone]?.name || null, customer_email: null,
  status, total_cents, subtotal_cents: total_cents, delivery_fee_cents: 0,
  delivery_slot: slot, channel: cust[phone]?.channel || "whatsapp",
  created_at: at(daysAgo, hhmm), updated_at: at(daysAgo, hhmm), yoco_checkout_id: null, ...extra,
});
export const orders = [
  ord(12, "+27820000101", "paid", 48_650, 0, "08:42", "Sat 17 Oct · 12:00–14:00"),
  ord(11, "+27820000104", "pending_payment", 21_300, 0, "07:55", "Sat 17 Oct · 15:00–17:00"),
  ord(10, "+27820000102", "packing", 63_900, 1, "16:20", "Sat 17 Oct · 09:00–11:00"),
  ord(9, "+27820000103", "confirmed", 34_800, 2, "12:05", "Sun 18 Oct · Collection"),
  ord(8, "+27820000101", "dispatched", 52_400, 2, "09:30", "Fri 16 Oct · 12:00–14:00"),
  ord(7, "+27820000105", "delivered", 29_700, 4, "11:30", "Wed 14 Oct · 15:00–17:00"),
  ord(6, "+27820000106", "delivered", 42_500, 6, "18:10", "Mon 12 Oct · Collection"),
  ord(5, "+27820000107", "cancelled", 15_400, 9, "09:15", null),
  ord(4, "+27820000102", "delivered", 38_200, 10, "14:45", "Wed 7 Oct · 12:00–14:00"),
  ord(3, "+27820000108", "refunded", 24_900, 12, "13:40", null),
];

// ── Home stats (commerce.py::admin_stats) — 7-day series, oldest → newest ────────────────────
const series = [
  [6, 214_500, 5], [5, 168_900, 4], [4, 251_300, 6], [3, 192_800, 4],
  [2, 310_600, 7], [1, 286_400, 6], [0, 48_650, 1],
].map(([d, revenue_cents, n]) => ({ date: day(d), revenue_cents, orders: n }));
export const stats = {
  total_orders: 214,
  total_revenue_cents: 7_865_400,
  today_orders: 1,
  today_revenue_cents: 48_650,
  pending_payment: 1,
  to_dispatch: 3,
  invoice_outstanding_cents: 1_284_500,
  invoice_overdue_cents: 386_400,
  invoice_paid_month_cents: 2_150_000,
  low_stock_count: 2,
  daily_revenue: series,
  bank_in_7d_cents: 1_420_300,
  bank_in_30d_cents: 5_610_800,
  bank_in_last_date: day(1),
  daily_bank_in: series.map((s) => ({ date: s.date, revenue_cents: Math.round(s.revenue_cents * 0.9) })),
  open_escalations: 3,
  oldest_escalation: { question: "Do you have gluten-free bread for a party of 12 on Saturday?", created_at: at(0, "08:05") },
  knowledge: { learned_answers: 27, taught: 11 },
  agent_recent: [
    { tool: "create_order", skill: "commerce_assistant", at: "2026-10-17T08:42:10Z" },
    { tool: "check_stock", skill: "commerce_assistant", at: "2026-10-17T08:40:51Z" },
    { tool: "send_payment_link", skill: "commerce_admin", at: "2026-10-17T07:58:02Z" },
    { tool: "lookup_customer", skill: "commerce_admin", at: "2026-10-17T07:31:44Z" },
  ],
};

// tenants.py::get_home_layout — the shop default plus the WhatsApp shop funnel (wa_shop module).
export const homeLayout = {
  cards: ["checklist", "attention", "sales", "trend", "shop", "customers", "assistant"],
  default: ["checklist", "attention", "sales", "trend", "shop", "customers", "assistant"],
  custom: false,
  available: ["today", "checklist", "attention", "sales", "trend", "shop", "jobcosting", "crosscheck", "customers", "assistant"],
};

// commerce/shop_stats.py::funnel
export const shopFunnel = {
  days: 7, tracked: true,
  steps: [
    { key: "shop_open", label: "Opened the shop", count: 186 },
    { key: "basket", label: "Filled a basket", count: 74 },
    { key: "basket_claimed", label: "Sent it to WhatsApp", count: 51 },
    { key: "ordered", label: "Placed an order", count: 33 },
    { key: "paid", label: "Paid", count: 29 },
  ],
  paid_cents: 1_184_300, searches: 142,
  top_searches: [{ term: "sourdough", count: 23 }, { term: "snoek pâté", count: 17 }, { term: "cheese board", count: 12 }],
  not_found: [{ term: "oat milk", count: 6 }, { term: "vegan cheese", count: 4 }],
};

// master.py::setup_checklist — partially done (8 of 12).
const steps = [
  ["created", "Created from business type", true, "settings", "15 modules enabled"],
  ["branding", "Brand kit", true, "settings", "logo/accent set"],
  ["team", "Team & logins", true, "team", "3 member(s)"],
  ["whatsapp", "WhatsApp connected", true, "settings", "connected"],
  ["templates", "Message templates approved", false, "wa-templates", "1 approved of 3 submitted"],
  ["payments", "Payments (gateway or EFT)", true, "payments", "configured"],
  ["vat", "VAT status confirmed", true, "invoices", "VAT registered"],
  ["profile", "Business profile answered", false, "settings", "6 of 9 answered"],
  ["golive_test", "Go-live test passed", false, "settings", "not run yet — Master › Tenants › Go-live test"],
  ["knowledge", "Products & knowledge", true, "products", "8 product(s), 9 document(s)"],
  ["storefront", "Storefront pages", false, "pages", "0 page(s)"],
  ["golive", "First order through", true, "orders", "214 order(s)"],
].map(([id, label, done, tab, detail]) => ({ id, label, done, tab, detail }));
export const setup = {
  tenant_id: TENANT_ID, display_name: TENANT_NAME, steps,
  done: steps.filter((s) => s.done).length, total: steps.length,
  progress_pct: Math.round((100 * steps.filter((s) => s.done).length) / steps.length),
};

// commerce.py::admin_cs_metrics
export const csMetrics = {
  tenant_id: TENANT_ID, period_days: 30, conversations: 318, ai_calls: 1_942,
  ai_cost_zar: 96.42, cost_per_conversation_zar: 0.3, pct_handled_local: 91,
  benchmark_cue_per_resolution_zar: 20,
};

// ── Inbox (commerce.py::admin_list_escalations / admin_list_conversations) ───────────────────
export const escalations = [
  { id: "esc-03", tenant_id: TENANT_ID, customer_phone: "+27820000109", status: "open", created_at: at(0, "10:12"),
    question: "Can you do a platter for 20 people next Friday? Budget about R2,500." },
  { id: "esc-02", tenant_id: TENANT_ID, customer_phone: "+27820000103", status: "open", created_at: at(0, "09:41"),
    question: "Is the aged cheddar unpasteurised? My partner is pregnant." },
  { id: "esc-01", tenant_id: TENANT_ID, customer_phone: "+27820000110", status: "open", created_at: at(0, "08:05"),
    question: "Do you have gluten-free bread for a party of 12 on Saturday?" },
];

const conv = (n, phone, last_message, last_role, daysAgo, hhmm, extra = {}) => ({
  id: `sess-${String(n).padStart(3, "0")}`, session_id: `sess-${String(n).padStart(3, "0")}`,
  session_key: phone, customer_phone: phone, customer_name: cust[phone]?.name || null,
  paused: false, last_message, last_role, last_at: at(daysAgo, hhmm),
  created_at: at(daysAgo + 3, "09:00"), updated_at: at(daysAgo, hhmm),
  assigned_to: null, agent_note: null, tags: [], ...extra,
});
export const conversations = [
  conv(1, "+27820000109", "Let me check with the team and come back to you shortly.", "assistant", 0, "10:13"),
  conv(2, "+27820000101", "Perfect, see you at 12. Thanks!", "user", 0, "09:58"),
  conv(3, "+27820000103", "I'll confirm that with the deli manager for you.", "assistant", 0, "09:42"),
  conv(4, "+27820000104", "Here's your payment link: https://pay.example.test/HV-1051", "assistant", 0, "07:56"),
  conv(5, "+27820000102", "Can I add a sourdough to tomorrow's order?", "user", 1, "16:25", { paused: true, assigned_to: "Zanele" }),
  conv(6, "+27820000110", "Do you have gluten-free bread?", "user", 1, "19:03"),
  conv(7, "+27820000106", "Collected, thank you — the board was a hit.", "user", 3, "10:02"),
  conv(8, "+27820000105", "Your order HV-1047 has been delivered. Enjoy!", "assistant", 4, "15:40"),
];

// one open thread (commerce.py::admin_get_thread) — the chat behind escalation esc-03
export const thread = {
  session_id: "sess-001", customer: cust["+27820000109"]?.name || null, phone: "+27820000109", paused: false,
  assigned_to: null, tags: ["catering"], agent_note: null,
  messages: [
    { id: "m1", role: "user", content: "Hi! Can you do a platter for 20 people next Friday?", created_at: at(0, "10:10") },
    { id: "m2", role: "assistant", content: "We'd love to. Our grazing platters start at R95 a person. What budget did you have in mind?", created_at: at(0, "10:11") },
    { id: "m3", role: "user", content: "Budget about R2,500.", created_at: at(0, "10:12") },
    { id: "m4", role: "assistant", content: "Let me check with the team and come back to you shortly.", created_at: at(0, "10:13") },
  ],
};

// ── Invoices (commerce.py::admin_list_invoices) ──────────────────────────────────────────────
const inv = (n, doc_type, status, phone, total_cents, issue, due, extra = {}) => ({
  id: `inv-${String(n).padStart(3, "0")}`, tenant_id: TENANT_ID, doc_type, direction: "outbound",
  invoice_number: `${doc_type === "quote" ? "QUO" : doc_type === "credit_note" ? "CN" : "INV"}-2026-${String(n).padStart(4, "0")}`,
  status, customer_name: cust[phone]?.name || "Bay Events Co.", customer_phone: phone,
  customer_email: phone ? `${(cust[phone]?.name || "events").split(" ")[0].toLowerCase()}@example.test` : "accounts@bayevents.example.test",
  total_cents, subtotal_cents: Math.round(total_cents / 1.15), vat_cents: total_cents - Math.round(total_cents / 1.15),
  vat_rate: 15, issue_date: issue, due_date: due, valid_until: null, project: null, sent_channel: null,
  line_items: [
    { description: "Catering platter", quantity: 1, unit_price_cents: Math.round(total_cents / 1.15 * 0.7) },
    { description: "Delivery", quantity: 1, unit_price_cents: Math.round(total_cents / 1.15 * 0.3) },
  ],
  created_at: `${issue}T09:00:00+02:00`, requires_approval: false, ...extra,
});
export const invoices = [
  inv(118, "invoice", "sent", "+27820000101", 186_300, "2026-10-14", "2026-10-28", { sent_channel: "whatsapp" }),
  inv(117, "invoice", "overdue", "+27820000106", 242_650, "2026-09-18", "2026-10-02", { sent_channel: "email" }),
  inv(116, "invoice", "overdue", null, 143_750, "2026-09-10", "2026-09-24", { sent_channel: "email" }),
  inv(115, "invoice", "paid", "+27820000102", 98_900, "2026-10-08", "2026-10-22", { sent_channel: "whatsapp" }),
  inv(114, "invoice", "part_paid", "+27820000103", 312_000, "2026-10-01", "2026-10-15", { sent_channel: "email" }),
  inv(113, "invoice", "paid", "+27820000105", 54_600, "2026-09-29", "2026-10-13", { sent_channel: "whatsapp" }),
  inv(119, "invoice", "draft", "+27820000107", 72_450, "2026-10-17", "2026-10-31"),
  inv(112, "invoice", "paid", "+27820000101", 205_000, "2026-09-22", "2026-10-06", { sent_channel: "manual" }),
  inv(41, "quote", "sent", "+27820000106", 465_000, "2026-10-15", null, { valid_until: "2026-10-29" }),
  inv(40, "quote", "accepted", "+27820000101", 128_800, "2026-10-09", null, { valid_until: "2026-10-23" }),
  inv(7, "credit_note", "paid", "+27820000102", 12_900, "2026-10-09", null),
];
export const inboundInvoices = [
  { ...inv(901, "invoice", "sent", null, 864_200, "2026-10-12", "2026-11-11"), direction: "inbound", invoice_number: "BILL-2026-0031",
    supplier: "Atlantic Dairy Co-op", customer_name: "Atlantic Dairy Co-op", customer_phone: null, customer_email: null, supplier_id: "sup-01" },
  { ...inv(902, "invoice", "paid", null, 312_500, "2026-10-05", "2026-11-04"), direction: "inbound", invoice_number: "BILL-2026-0030",
    supplier: "Westbank Bakery Supplies", customer_name: "Westbank Bakery Supplies", customer_phone: null, customer_email: null, supplier_id: null },
];

// commerce.py::admin_get_invoice_settings — onboarded, so the first-run wizard stays closed.
export const invoiceSettings = {
  tenant_id: TENANT_ID, onboarded: true, company_name: "Harbour & Vine Deli (Pty) Ltd", trading_as: TENANT_NAME,
  tagline: "Deli · Bakery · Catering", logo_url: LOGO_URL, icon_url: null, accent_color: "#2C5545",
  secondary_color: "#C8963E", ink_color: "#1E1E1E", font_pairing: "vula", corner_style: "rounded",
  density: "comfortable", logo_align: "left", logo_size: "md", header_sticky: true,
  header_nav_position: "right", header_cta_text: "Order on WhatsApp", header_cta_link: "",
  company_phone: "+27820000001", company_email: "hello@harbourvine.example.test",
  company_address: "12 Example Lane, Sea Point, Cape Town, 8005", vat_registered: true,
  vat_number: "4000000000", bank_name: "Example Bank", account_number: "0000000000",
  branch_code: "000000", payment_terms_days: 14, invoice_prefix: "INV", template: "classic",
};

// commerce.py::public_brand
export const brand = {
  name: TENANT_NAME, logo_url: LOGO_URL, icon_url: null, tagline: invoiceSettings.tagline,
  accent_color: "#2C5545", secondary_color: "#C8963E", ink_color: "#1E1E1E", font_pairing: "vula",
  corner_style: "rounded", density: "comfortable", logo_align: "left", logo_size: "md",
  header_sticky: true, header_nav_position: "right", header_cta_text: "Order on WhatsApp",
  header_cta_link: null, whatsapp: "+27820000001",
};

// ── Tenant + member (tenants.py::get_tenant/_public, team.py /me) ────────────────────────────
export const tenantModules = ["products", "orders", "payments", "invoices", "delivery", "wa_shop", "crm",
  "reports", "broadcasts", "marketing", "inbox", "team", "automations", "documents", "finances", "pages"];
export const tenant = {
  tenant_id: TENANT_ID, display_name: TENANT_NAME, business_type: "food", theme: {},
  store_url: null, modules: tenantModules, default_payment_provider: "yoco", status: "active",
  profile: { tenant_id: TENANT_ID, known: true, display_name: TENANT_NAME, business_type: "food",
    modules: tenantModules, uses_projects: false, sells_products: true, is_rep_business: false,
    aliases: [], description: "Neighbourhood deli, bakery and catering in Sea Point." },
  // the redesign opt-in (migration 215): on when the run is UI_V2=1
  ui_v2: process.env.UI_V2 === "1", ui_labels: null,
};
export const teamMe = { access: [], full: true, role: "owner", whatsapp: "+27820000001" };
// staff: the counter — chats, orders, bookings and customers only; rep: a sales_rep login with
// the default My Work access (navConfig.REP_DEFAULT_ACCESS)
export const teamMeFor = (role) => role === "staff"
  ? { access: ["inbox", "orders", "bookings", "customers", "contacts", "followups"], full: false, role: "staff", whatsapp: "+27820000008" }
  : role === "rep"
    ? { access: ["rep-contacts", "rep-callsheet", "rep-bookings", "rep-documents", "rep-reminders", "rep-expenses", "rep-expense-sheet", "rep-crm"],
      full: false, role: "sales_rep", whatsapp: "+27820000009" }
    : teamMe;

// ── Documents (documents.py::list_filed / documents_health / projects; server.py list_documents)
const doc = (n, filename, category, daysAgo, extra = {}) => ({
  id: `doc-${String(n).padStart(3, "0")}`, tenant_id: TENANT_ID, filename, category,
  mime: filename.endsWith(".pdf") ? "application/pdf" : "application/octet-stream",
  file_url: null, project: "Shop", status: "filed", summary: null, customer_phone: null,
  fields: {}, filed_by: "+27820000001", created_at: at(daysAgo, "09:00"), ...extra,
});
export const filedDocs = [
  doc(9, "Atlantic-Dairy-invoice-BILL-0031.pdf", "Invoice", 5, { summary: "Atlantic Dairy Co-op · R8,642.00 · due 11 Nov" }),
  doc(8, "Westbank-bakery-delivery-note.pdf", "Delivery Note", 6, { summary: "Flour, rye and seeds — 14 items" }),
  doc(7, "Catering-quote-Bay-Events.pdf", "Quote / Estimate", 7, { summary: "Platter for 60, 24 Oct", customer_phone: "+27820000106" }),
  doc(6, "Supplier-price-list-Oct-2026.xlsx", "Supplier Price List", 9, { summary: "Cheese & charcuterie — 46 lines" }),
  doc(5, "Health-certificate-2026.pdf", "Compliance", 14, { summary: "Certificate of acceptability, valid to Aug 2027" }),
  doc(4, "Insurance-schedule-2026.pdf", "Insurance", 20),
  doc(3, "Bank-statement-Sep-2026.pdf", "Account Statement", 16, { summary: "September statement — 312 lines" }),
  doc(2, "Shopfront-lease-addendum.docx", "Legal / Property", 30),
  doc(1, "Menu-spring-2026.pdf", "Marketing", 35, { project: null, status: "pending_project" }),
];
export const docsHealth = {
  tenant_id: TENANT_ID, total: 9,
  categories: { "Invoice": 1, "Delivery Note": 1, "Quote / Estimate": 1, "Supplier Price List": 1, "Compliance": 1, "Insurance": 1, "Account Statement": 1, "Legal / Property": 1, "Marketing": 1 },
  catch_all: 0, missing_details: 1, missing_by_category: { "Invoice": 1 }, pending_project: 1,
  unregistered_projects: {},
  samples: { missing: [{ id: "doc-009", filename: "Atlantic-Dairy-invoice-BILL-0031.pdf", category: "Invoice", project: "Shop", missing: ["invoice date"] }], catch_all: [] },
};
export const docProjects = { tenant_id: TENANT_ID, projects: [{ label: "Shop", clickup_list_id: null, registered: true }, { label: "Catering", clickup_list_id: null, registered: true }] };
export const kbFiles = {
  tenant_id: TENANT_ID, kb_chunks: 412, count: 3,
  documents: [
    { filename: "Menu-spring-2026.pdf", size_kb: 842, type: ".pdf" },
    { filename: "Allergen-guide.docx", size_kb: 64, type: ".docx" },
    { filename: "Catering-FAQ.txt", size_kb: 6, type: ".txt" },
  ],
};
export const docsLearn = { status: { running: false }, price_book: { items: 46, priced_lines: 128, suppliers: 4, labour_rates: 0, projects: 2 } };

// ── Settings (various) ──────────────────────────────────────────────────────────────────────
export const orderSettings = {
  tenant_id: TENANT_ID, delivery_areas: ["Sea Point", "Green Point", "Mouille Point", "Gardens"],
  delivery_fee_cents: 4_500, free_delivery_over_cents: 50_000, min_order_cents: 15_000,
  origin_lat: -33.9155, origin_lng: 18.3895, origin_label: "12 Example Lane, Sea Point", delivery_radius_km: 6,
  business_hours: { mon: { open: "07:30", close: "17:00" }, tue: { open: "07:30", close: "17:00" },
    wed: { open: "07:30", close: "17:00" }, thu: { open: "07:30", close: "17:00" },
    fri: { open: "07:30", close: "18:00" }, sat: { open: "08:00", close: "14:00" }, sun: null },
  business_hours_note: "Closed on public holidays.",
  after_hours_message: "Thanks for your message! We're closed right now — we'll reply first thing when we open.",
  payment_methods: ["yoco", "eft"],
};
export const learnedSummary = {
  voice: { current: "Warm, neighbourly and brief — like the person behind the counter.", suggested: "", suggested_at: null },
  learned_answers: {
    pending: [{ id: "la-1", question: "Do you deliver to Camps Bay?", answer: "Yes — Saturdays only, R65 delivery." }],
    pending_count: 1, approved_count: 26,
    recent_approved: [
      { id: "la-2", question: "Is the sourdough vegan?" },
      { id: "la-3", question: "Can I collect after 5pm?" },
      { id: "la-4", question: "Do you do gift hampers?" },
    ],
  },
  merchant_profiles: { decided_count: 11, total_count: 14, recent: [] },
};
const PROFILE_QS = [
  ["what_we_do", "In a sentence or two: what does the business sell or do, and for whom?", "A neighbourhood deli, bakery and catering kitchen for Sea Point locals and small events."],
  ["area", "Where do you work or deliver? (areas, towns, or 'collection only')", "Sea Point, Green Point, Mouille Point and Gardens. Collection from the shop any day."],
  ["hours", "What are your working hours, and when are you closed?", "Mon–Fri 07:30–17:00, Sat 08:00–14:00. Closed Sundays and public holidays."],
  ["payment", "How do customers pay you (EFT, card, cash, payment link), and what are your payment terms?", "Card, Yoco payment link or EFT. Catering invoices are 14 days."],
  ["pricing", "How do you price — fixed price list, quote per job, or per hour/m²? Anything you always add (call-out, delivery)?", "Fixed price list for the shop; catering is quoted per head. Delivery R45, free over R500."],
  ["lead_times", "How quickly can you usually deliver or start a job?", "Same day before 11:00; catering needs 3 days' notice."],
  ["cancellations", "What's your policy on cancellations, returns or refunds?", ""],
  ["team", "Who does what in the team (e.g. 'Sipho quotes, Lindi does accounts')?", ""],
  ["faq", "What do customers ask you most often — and what's the answer?", ""],
];
export const businessProfile = {
  questions: PROFILE_QS.map(([key, q]) => ({ key, q })),
  answers: Object.fromEntries(PROFILE_QS.filter(([, , a]) => a).map(([k, , a]) => [k, a])),
  missing: PROFILE_QS.filter(([, , a]) => !a).map(([k]) => k),
  answered: PROFILE_QS.filter(([, , a]) => a).length, total: PROFILE_QS.length,
};
export const yocoStatus = {
  tenant_id: TENANT_ID, status: "connected", mode: "test", masked_secret: "sk_test_••••••••3f9a",
  webhook_registered: true, last_test_at: "2026-10-15T09:12:00+02:00",
};
export const whatsappStatus = {
  tenant_id: TENANT_ID, status: "connected", phone_number: "+27 82 000 0001",
  verified_name: TENANT_NAME, webhook_registered: true, route_mode: "direct",
};
export const waProfile = {
  current: { about: "Deli · Bakery · Catering in Sea Point", profile_picture_url: null },
  proposed: { about: "Deli · Bakery · Catering in Sea Point", description: "Fresh bread daily, cheese, charcuterie and catering platters. Order here on WhatsApp.",
    address: "12 Example Lane, Sea Point, Cape Town", email: "hello@harbourvine.example.test",
    websites: ["https://harbourvine.example.test"], picture_url: null, vertical: "RESTAURANT" },
};
export const notConnected = { tenant_id: TENANT_ID, status: "not_connected" };

// ── Master (master.py::tenant_overview, tenants.py::list_tenants/registry) ───────────────────
const mt = (tenant_id, display_name, business_type, extra) => ({
  tenant_id, display_name, business_type, modules: tenantModules.slice(0, 10), theme: {}, active: true,
  plan: "growth", store_url: null, default_payment_provider: "yoco", share_knowledge_with_network: false,
  spend_cap_usd: null, paid: true, signup_status: "active", trial_ends: null,
  signup_email: `owner@${tenant_id}.example.test`, logins: 2, whatsapp: "connected", open_escalations: 0,
  last_activity: at(0, "09:58"), health: "green", health_reasons: [], dormant: false, ...extra,
});
export const masterTenants = [
  mt("demo-shop", TENANT_NAME, "food", { logins: 3, open_escalations: 3, health: "amber", health_reasons: ["3 unanswered question(s) for the team"] }),
  mt("demo-bakery", "Kloof Street Bakehouse", "food", { plan: "starter", last_activity: at(1, "17:20") }),
  mt("demo-salon", "Fynbos Hair Studio", "beauty", { modules: ["bookings", "payments", "crm", "inbox", "team"], last_activity: at(2, "12:44") }),
  mt("demo-build", "Ridgeline Renovations", "construction", { modules: ["projects", "estimating", "invoices", "documents", "fieldops", "team"], plan: "business", last_activity: at(0, "08:15") }),
  mt("demo-cycles", "Lowveld Cycle Works", "retail", { whatsapp: "token_expired", health: "red", health_reasons: ["WhatsApp token_expired"], last_activity: at(5, "10:01") }),
  mt("demo-plants", "Succulent Corner", "retail", { paid: false, signup_status: "trial", trial_ends: "2026-10-30T00:00:00Z", plan: "starter", logins: 1, last_activity: null, health: "amber", health_reasons: ["No messages yet"], dormant: true }),
  mt("demo-tutor", "Brightpath Tutoring", "services", { active: false, health: "off", health_reasons: ["Suspended"], paid: false, signup_status: "cancelled", logins: 1, last_activity: at(48, "09:00"), dormant: true }),
];
export const tenantsList = { tenants: masterTenants.map((t) => ({ tenant_id: t.tenant_id, display_name: t.display_name, business_type: t.business_type, theme: {}, store_url: null, modules: t.modules, default_payment_provider: "yoco", status: t.active ? "active" : "suspended", profile: null })) };
export const registry = {
  modules: [["products", "Products"], ["orders", "Orders"], ["bookings", "Bookings"], ["payments", "Payments"], ["invoices", "Invoices"], ["delivery", "Delivery"], ["pages", "Website / Pages"], ["crm", "Customers (CRM)"], ["reports", "Reports"], ["broadcasts", "Broadcasts"], ["inbox", "Team Inbox"], ["documents", "Documents / KB"], ["projects", "Projects"], ["team", "Team"]].map(([id, label]) => ({ id, label })),
  business_types: [["food", "Food / Restaurant / Takeaway"], ["retail", "Retail / E-commerce"], ["beauty", "Beauty / Salon"], ["construction", "Construction / Trades"], ["services", "Professional services"], ["other", "Other"]].map(([id, label]) => ({ id, label, modules: [] })),
};

export const manifest = {
  name: TENANT_NAME, short_name: "Harbour & Vine", start_url: "/", display: "standalone",
  background_color: "#FFFFFF", theme_color: "#2C5545", icons: [{ src: `${API}/v1/commerce/${TENANT_ID}/app-icon/192.png`, sizes: "192x192", type: "image/png" }],
};

// ── Command centre (vula_mind/vula/api/command_centre.py — UX redesign Phase E) ──────────────
// Role filtering is the server's job: owner gets front + back; the counter staff login (no
// Money) gets front only; a sales rep's Today is their own reminder.
const ccAt = (mins) => new Date(new Date(NOW_ISO).getTime() - mins * 60000).toISOString();
const lnk = (section, tab) => (tab ? { section, tab } : { section });
export const homeFor = (role) => ({
  front: {
    open_orders: { value: 4, link: lnk("sell", "orders") },
    won_month: { cents: 31840000, count: 214, target_cents: null, link: lnk("sell", "orders") },
    chats_waiting: { value: 3, link: lnk("inbox"), hot: [
      { contact_id: "+27820000110", since: ccAt(120) }, { contact_id: "+27820000103", since: ccAt(49) }, { contact_id: "+27820000109", since: ccAt(18) }] },
    first_reply: { seconds: 94, link: lnk("inbox") },
    new_contacts: { value: 12, link: lnk("people", "customers") },
  },
  back: role === "owner" ? {
    money_in: { cents: 18245000, link: lnk("money", "bank") },
    money_out: { cents: 9612000, link: lnk("money", "bank") },
    invoices_overdue: { value: 2, cents: 386400, link: lnk("money", "invoices") },
    bills_due: { value: 3, cents: 1254000, link: lnk("money", "budget") },
    documents_to_review: { value: 2, link: lnk("operate", "documents") },
    bank_unmatched: { value: 7, link: lnk("money", "bank") },
    stock_alerts: { value: 2, link: lnk("sell", "stock") },
  } : null,
  month_days_left: 14, as_of: NOW_ISO,
});
export const todayFor = (role) => ({
  date: "2026-10-17",
  items: role === "rep"
    ? [{ id: "follow_up:r1", kind: "follow_up", label: "Follow-up due", contact_id: "+27820000105", at: ccAt(-90), link: lnk("my-work", "rep-reminders") }]
    : [
      { id: "chat:e1", kind: "chat", label: "A customer is waiting for a person", contact_id: "+27820000110", at: ccAt(120), link: lnk("inbox") },
      { id: "chat:e2", kind: "chat", label: "A customer is waiting for a person", contact_id: "+27820000103", at: ccAt(49), link: lnk("inbox") },
      { id: "follow_up:r2", kind: "follow_up", label: "Follow-up overdue", contact_id: "+27820000104", at: ccAt(1500), link: lnk("people", "followups") },
      { id: "booking:b1", kind: "booking", label: "Booking · Platter tasting", contact_id: "+27820000106", at: ccAt(-150), link: lnk("sell", "bookings") },
      ...(role === "owner" ? [
        { id: "quote:q1", kind: "quote", label: "Quote QUO-2026-0031 drafted by Vula — approve", amount_cents: 250000, at: ccAt(300), link: lnk("money", "invoices") },
        { id: "document:d1", kind: "document", label: "Check Atlantic-Dairy-invoice-BILL-0031.pdf", at: ccAt(400), link: lnk("operate", "documents") },
      ] : []),
    ],
});
const item = (src, ref, side, type, actor, label, mins, extra = {}) =>
  ({ id: `${src}:${ref}`, source: src, ref, side, type, actor, label, at: ccAt(mins), ...extra });
export const activityFor = (role) => {
  const all = [
    item("vula", "1", "back", "vula_action", "vula", "Vula recorded a payment", 3, { tool: "record_payment", link: lnk("assistant-hub", "agentlog") }),
    item("escalation", "e3", "front", "escalation", "vula", "A customer is waiting for a person", 18, { contact_id: "+27820000109", link: lnk("inbox") }),
    item("chat", "s1", "front", "chat", "vula", "Chat with Fatima Adams", 17, { detail: "Vula answered", contact_id: "+27820000101", link: lnk("inbox") }),
    item("order", "o1", "front", "order", "customer", "Order HV-1052", 25, { detail: "paid", amount_cents: 48700, link: lnk("sell", "orders") }),
    item("invoice", "i1", "back", "invoice", "team", "Invoice INV-2026-0121", 40, { detail: "sent", amount_cents: 1250000, link: lnk("money", "invoices") }),
    item("document", "d1", "back", "document", "vula", "Filed Atlantic-Dairy-invoice-BILL-0031.pdf", 52, { detail: "invoice · needs review · 62% sure", link: lnk("operate", "documents") }),
    item("chat", "s2", "front", "chat", "team", "Chat with Pieter van der Berg", 64, { detail: "Taken over by your team", link: lnk("inbox") }),
    item("expense", "x1", "back", "expense", "vula", "Expense · Atlantic Dairy", 90, { detail: "pending", amount_cents: 330000, link: lnk("money", "expenses") }),
    item("booking", "b1", "front", "booking", "customer", "Booking · Platter tasting", 130, { detail: "17 Oct 12:30", link: lnk("sell", "bookings") }),
    item("consent", "c1", "front", "consent", "customer", "Opted out of messages", 200, { detail: "stop_keyword", link: lnk("people", "customers") }),
  ];
  const items = role === "owner" ? all : role === "rep" ? [] : all.filter((i) => i.side === "front");
  return { items, next_cursor: null };
};

// ── Projects command centre (projects.py, migration 07x/18x) ─────────────────────────────────
export const projectsList = [
  { id: "pj-1", name: "Bay Events catering", number: "BE-2026", client: "Bay Events Co.", status: "active", aliases: ["Bay Events"], parent_id: null },
  { id: "pj-1a", name: "Bay Events catering — Spring gala", phase: "Spring gala", number: null, client: "Bay Events Co.", status: "active", aliases: [], parent_id: "pj-1" },
  { id: "pj-1b", name: "Bay Events catering — Year-end function", phase: "Year-end function", number: null, client: "Bay Events Co.", status: "on_hold", aliases: [], parent_id: "pj-1" },
  { id: "pj-2", name: "Spring market stall", number: null, client: "", status: "complete", aliases: [], parent_id: null },
];
export const projectCodes = [
  { id: "cd-1", code_ref: "R638", title: "Regulations governing general hygiene requirements for food premises", version: "2018", status: "current", doc_id: "kb-1" },
  { id: "cd-2", code_ref: "SANS 10049", title: "Food safety management — Requirements for prerequisite programmes", version: "Ed 4", status: "superseded", doc_id: null },
];
export const projectDetail = (id) => {
  const p = projectsList.find((x) => x.id === id) || projectsList[0];
  return { ...p, codes: id === "pj-1" ? [projectCodes[0]] : [], team: id === "pj-1" ? [{ id: "pt-1", name: "Thandi Mokoena", role: "Event coordinator" }] : [] };
};
export const projectOverview = (id) => id === "pj-1"
  ? { phases: [
      { id: "pj-1a", name: projectsList[1].name, phase: "Spring gala", documents: 4, boq: { total_cents: 4850000 } },
      { id: "pj-1b", name: projectsList[2].name, phase: "Year-end function", documents: 1, boq: null }],
    documents: { Quote: 2, Invoice: 3 }, documents_with_phases: { Quote: 3, Invoice: 5, "Menu / spec": 2 },
    boq: { title: "Contract value", total_cents: 12000000 }, parent: null }
  : { phases: [], documents: { Invoice: 1 }, documents_with_phases: { Invoice: 1 }, boq: null,
      parent: projectsList.find((x) => x.id === id)?.parent_id ? { id: "pj-1", name: projectsList[0].name } : null };
export const projectBoq = { total_cents: 12000000, sections: [{ section: "Food", budget_cents: 8200000 }, { section: "Staff", budget_cents: 2600000 }, { section: "Hire", budget_cents: 1200000 }] };
// The project tracker, online BOQ and QS view (vula/commerce/project_tracker.py, boq_online.py; #227/#229).
// Same project as projectFinancials: R 120 000 budget, R 64 500.50 invoiced, R 40 000 paid. Cents.
export const projectTracker = {
  position: {
    project: "Bay Events catering", budget_title: "Budget (BOQ)", budget_cents: 12000000, variations_claimed_cents: 0,
    sections: projectBoq.sections,
    trades: [{ trade: "Food", cents: 2950000 }, { trade: "Staff", cents: 840000 }, { trade: "Hire", cents: 485000 }],
    unallocated_trade_cents: 0,
    business_cost_cents: 4275000, paid_directly_cents: 650000, paid_directly_recorded_cents: 650000,
    paid_directly_suppliers_cents: 0, paid_directly_slips_cents: 0, to_pay_directly_cents: 0,
    direct_payments: [{ id: "dp-1", supplier: "Cape Marquees", description: "Marquee deposit", amount_cents: 650000, status: "paid", paid_on: "2026-10-09" }],
    spent_cents: 4925000, spent_pct: 41, budget_left_cents: 7075000,
    fee_pct: 10, fee_cents: 427500, open_book_total_cents: 4702500, received_cents: 4000000, open_book_balance_cents: 702500,
    invoices: [{ number: "INV-0041" }, { number: "INV-0044" }, { number: "INV-0047" }],
    invoiced_cents: 6450050, invoices_paid_cents: 4000000, invoices_owed_cents: 2450050, to_invoice_cents: 5549950,
    profit_cents: 395000,
  },
  client_views: {},
  shares: [{ id: "sh-1", view: "price", active: true, views: 3, created_at: "2026-10-20T09:12:00Z", expires_at: "2027-04-18T09:12:00Z" }],
};
export const projectQsView = { view: {
  project: "Bay Events catering", baseline_locked: false, budget_cents: 12000000, variations_claimed_cents: 0, contract_cents: 12000000,
  sections: projectBoq.sections, claims: { latest: null },
  invoices: projectTracker.position.invoices, invoiced_cents: 6450050, invoices_paid_cents: 4000000, invoices_owed_cents: 2450050, to_invoice_cents: 5549950,
  programme: null, documents: [{ name: "Bay-Events-quote.pdf", category: "quote", filed: "2026-10-02" }],
} };
export const boqOnline = { project: "Bay Events catering", total_cents: 0, lines: [] };
export const boqLibrary = { trades: ["Preliminaries", "Food", "Staff", "Hire"], units: ["Item", "No", "m²", "hr"],
  items: [{ key: "Staff — waiter (hr)", trade: "Staff", description: "Waiter", unit: "hr", rate: 95, rate_cents: 9500, kind: "own", source: "own rate" }] };

export const projectFinancials = { project: "Bay Events catering", contract: 120000, invoiced: 64500.5, invoiced_paid: 40000, invoice_count: 3,
  quoted: 12000, quote_count: 1, paid_in: 40000, spent: 31275.25, labour_cents: 480000, net: 8724.75, remaining: 88724.75, outstanding: 24500.5,
  latest_claim: null, transactions: [] };

// ── Knowledge-base visibility + the customer's-eye search (documents.py, KB retrieval 1b) ──
export const kbVisibility = { strict: false, documents: [
  { doc_id: "kb-1", filename: "Menu-spring-2026.pdf", audience: "public" },
  { doc_id: "kb-2", filename: "Allergen-guide.docx", audience: "untagged" },
  { doc_id: "kb-3", filename: "Catering-FAQ.txt", audience: "public" },
  { doc_id: "kb-4", filename: "Staff-rota-October.xlsx", audience: "staff" },
] };
export const customerView = (q) => ({ strict: false,
  visible_documents: kbVisibility.documents.filter((d) => d.audience !== "staff"),
  results: q ? [{ filename: "Catering-FAQ.txt", audience: "public", excerpt: "We deliver Tuesday to Saturday within the City Bowl and Atlantic Seaboard." }] : [] });

// ── Bank & matching (bank.py, migration 057) ─────────────────────────────────────────────────
export const bankSummary = {
  txn_count: 7, money_in_cents: 1_874_250, money_out_cents: 1_236_480, matched: 3, needs_input: 1,
  unmatched_credits: 1, unmatched_debits: 2, invoices_sent_unpaid_cents: 864_200, invoices_paid_cents: 1_412_000,
};
export const bankAccounts = { vat_registered: true, accounts: [
  { code: "sales", name: "Sales" }, { code: "cost_of_sales", name: "Cost of sales" }, { code: "casual_labour", name: "Casual labour" },
  { code: "rent", name: "Rent" }, { code: "bank_charges", name: "Bank charges" }, { code: "motor_vehicle", name: "Motor vehicle" },
] };
export const bankWorkers = [
  { id: "wk-1", name: "Lwazi", id_number: "9001015800087", bank_account: "62001234567", phone: "+27820000301", rate_cents: 45_000, rate_period: "daily", type: "casual", default_project: "Bay Events catering" },
  { id: "wk-2", name: "Nomvula", id_number: "", bank_account: "", phone: "", rate_cents: 250_000, rate_period: "weekly", type: "subcontractor", default_project: "" },
];
export const bankTxns = [
  { id: "bt-1", direction: "in", amount_cents: 864_200, txn_date: day(1), description: "PAYMENT RECEIVED BAY EVENTS CO INV-0041", match_status: "unmatched", account_code: "sales", categorized_by: "rule", vat_cents: 112_722 },
  { id: "bt-2", direction: "out", amount_cents: 864_200, txn_date: day(2), description: "ATLANTIC DAIRY COOP DEBIT ORDER 0031", match_status: "unmatched", account_code: "cost_of_sales", categorized_by: "ai", vat_cents: 112_722 },
  { id: "bt-3", direction: "out", amount_cents: 45_000, txn_date: day(2), description: "CASH SEND LWAZI", match_status: "unmatched", account_code: "casual_labour", categorized_by: "default", vat_cents: 0 },
  { id: "bt-4", direction: "in", amount_cents: 412_850, txn_date: day(3), description: "EFT THANDI MOKOENA ORD-1042", match_status: "matched", account_code: "sales", categorized_by: "match", vat_cents: 53_850 },
  { id: "bt-5", direction: "out", amount_cents: 1_850_000, txn_date: day(5), description: "RENT SEA POINT PROPERTIES OCT", match_status: "matched", account_code: "rent", categorized_by: "rule", vat_cents: 0 },
  { id: "bt-6", direction: "out", amount_cents: 18_480, txn_date: day(6), description: "MONTHLY ACCOUNT FEE", match_status: "ignored", account_code: "bank_charges", categorized_by: "rule", vat_cents: 0 },
  { id: "bt-7", direction: "out", amount_cents: 62_000, txn_date: day(8), description: "ENGEN SEA POINT FUEL", match_status: "needs_input", account_code: "motor_vehicle", categorized_by: "ai", vat_cents: 8_087, worker_id: "wk-1" },
];
export const bankGroups = [
  { key: "ATLANTIC DAIRY COOP", count: 4, total_cents: 3_456_800, oldest: day(30), newest: day(2), txn_ids: ["bt-2"], account_code: "cost_of_sales" },
  { key: "CASH SEND LWAZI", count: 6, total_cents: 270_000, oldest: day(40), newest: day(2), txn_ids: ["bt-3"], account_code: "casual_labour" },
];
export const bankCandidates = [
  { id: "inv-0041", number: "INV-0041", party: "Bay Events Co.", balance_cents: 864_200, exact: true, status: "sent" },
  { id: "inv-0038", number: "INV-0038", party: "Fairview Cheese", balance_cents: 1_200_000, exact: false, status: "part_paid" },
];

// ── Payments gateways (api/payments.py) + Accounts / books (reports.py, migration 058) ───────
export const paymentProviders = {
  connected: [{ provider: "yoco", is_default: true, mode: "live" }, { provider: "ozow", is_default: false, mode: "test" }],
  available: [["yoco", "Yoco", ["secret_key"]], ["payfast", "PayFast", ["merchant_id", "merchant_key", "passphrase"]],
    ["peach", "Peach Payments", ["entity_id", "access_token", "webhook_secret"]], ["ikhokha", "iKhokha", ["app_id", "app_secret"]],
    ["ozow", "Ozow (instant EFT)", ["site_code", "private_key", "api_key"]], ["paystack", "Paystack", ["secret_key"]]]
    .map(([id, label, fields]) => ({ id, label, fields })),
};
export const reportPnl = {
  total_income_cents: 18_742_500, total_expense_cents: 12_364_800, net_profit_cents: 6_377_700,
  income: [{ account: "Sales", amount_cents: 16_120_000 }, { account: "Catering", amount_cents: 2_622_500 }],
  expenses: [{ account: "Cost of sales", amount_cents: 7_412_000 }, { account: "Rent", amount_cents: 1_850_000 },
    { account: "Casual labour", amount_cents: 1_920_000 }, { account: "Motor vehicle", amount_cents: 642_800 }, { account: "Bank charges", amount_cents: 1_540_000 }],
};
export const reportVat = { vat_registered: true, output_vat_cents: 2_444_674, input_vat_cents: 1_198_210, net_vat_cents: 1_246_464,
  note: "From bank lines with VAT recorded — check against your invoices before filing." };

// ── Finances (finances/insights, reports, projects/finances, trial balance, cross-check) ─────
export const financeInsights = {
  revenue_cents: 18_742_500, expenses_cents: 12_364_800, gross_profit_cents: 6_377_700, margin_pct: 34, orders_count: 142,
  vat: { collected_cents: 2_444_674, note: "VAT collected is from paid invoices only." },
  receivables: { outstanding_cents: 864_200, count: 3, overdue_cents: 312_400,
    aging: { current: 551_800, days_1_30: 212_400, days_31_60: 100_000, days_61_90: 0, days_90_plus: 0 } },
  payment_behavior: { overall: { on_time_pct: 82, sample_size: 61 },
    watch_list: [{ customer_name: "Bay Events Co.", customer_phone: "+27820000106", label: "frequently_late", on_time_pct: 40, avg_days_late: 12 }] },
  top_customers: [{ name: "Bay Events Co.", revenue_cents: 2_622_500 }, { name: "Thandi Mokoena", revenue_cents: 412_850 }, { name: "Pieter van der Berg", revenue_cents: 268_400 }],
  top_expense_categories: [{ category: "Cost of sales", amount_cents: 7_412_000 }, { category: "Casual labour", amount_cents: 1_920_000 }, { category: "Rent", amount_cents: 1_850_000 }],
};
const trendDay = (i) => { const d = new Date(Date.UTC(2026, 9, 17 - 29 + i)); return d.toISOString().slice(0, 10); };
const REV = [412, 520, 0, 610, 735, 880, 455, 390, 505, 0, 640, 720, 910, 480, 430, 560, 0, 690, 760, 1020, 515, 470, 585, 0, 705, 790, 1150, 540, 610, 665];
export const salesReport = {
  total_revenue_cents: 16_747_000, total_orders: 142,   // = the sum of revenue_trend
  revenue_trend: REV.map((r, i) => ({ date: trendDay(i), orders: r ? Math.round(r / 140) + 2 : 0, revenue_cents: r * 1_000 })),
  top_products: [{ name: "Catering platter (60)", units: 9, revenue_cents: 2_622_500 }, { name: "Sourdough loaf", units: 412, revenue_cents: 2_266_000 },
    { name: "Cheese board", units: 88, revenue_cents: 1_672_000 }, { name: "Coffee beans 1kg", units: 41, revenue_cents: 1_066_000 }],
};
export const projectFinances = {
  total_in: 64500.5, total_out: 31275.25,
  projects: [{ project: "Catering", count: 12, in: 52_400, out: 24_100.25, budget: 60_000, remaining: 35_899.75 },
    { project: "Shop", count: 31, in: 12_100.5, out: 7_175, budget: 0, remaining: null }],
  transactions: [
    { id: "ft-1", direction: "in", counterparty: "Bay Events Co.", description: "INV-0041 payment", project: "Catering", amount: 8642, reconciled: true, kind: "payment" },
    { id: "ft-2", direction: "out", counterparty: "Atlantic Dairy Co-op", description: "BILL-0031", project: "Shop", amount: 8642, reconciled: false, kind: "invoice" },
    { id: "ft-3", direction: "out", counterparty: null, filename: "Westbank-bakery-delivery-note.pdf", description: null, project: null, amount: 1240.5, reconciled: false, kind: "delivery_note" },
  ],
};
export const projectClaims = { claims: [
  { id: "cl-1", claim_number: 1, cumulative_value_cents: 3_000_000, retention_cents: 150_000, retention_pct: 5, certified_to_date_cents: 2_850_000, this_payment_cents: 2_850_000, status: "certified", linked_invoice_id: "inv-0039" },
  { id: "cl-2", claim_number: 2, cumulative_value_cents: 5_200_000, retention_cents: 260_000, retention_pct: 5, certified_to_date_cents: 4_940_000, this_payment_cents: 2_090_000, status: "draft", linked_invoice_id: null },
] };
export const trialBalance = { total_debit_cents: 31_442_150, total_credit_cents: 31_442_150, accounts: [
  { code: "1000", name: "Bank", type: "asset", debit_cents: 18_742_500, credit_cents: 0 },
  { code: "4000", name: "Sales", type: "income", debit_cents: 0, credit_cents: 18_742_500 },
  { code: "5000", name: "Cost of sales", type: "expense", debit_cents: 7_412_000, credit_cents: 0 },
  { code: "2000", name: "Accounts payable", type: "liability", debit_cents: 5_287_650, credit_cents: 12_699_650 },
] };
export const crossCheck = { text: "9 documents filed; 7 are in the books and 5 are matched to the bank.", unbooked: [], unbooked_total_cents: 0,
  bills_unpaid: [], no_document: [], no_document_total_cents: 0, sales_unpaid: [], money_in_unmatched_cents: 0, money_in_unmatched_lines: 0, unexplained_in_sample: [] };

// ── Discounts (migration 091), delivery list, recurring orders (migration 051) ────────────────
export const discountCodes = [
  { id: "dc-1", code: "SPRING15", type: "percent", value: 15, min_order_cents: 30_000, usage_count: 42, usage_limit: 100, active: true,
    starts_at: "2026-10-01T00:00:00+02:00", ends_at: "2026-10-31T23:59:59+02:00", first_order_only: false, per_customer_limit: 1 },
  { id: "dc-2", code: "WELCOME50", type: "fixed", value: 5_000, min_order_cents: null, usage_count: 9, usage_limit: null, active: true,
    starts_at: null, ends_at: null, first_order_only: true, per_customer_limit: null },
  { id: "dc-3", code: "FREESHIP", type: "free_shipping", value: 0, min_order_cents: 50_000, usage_count: 120, usage_limit: null, active: false,
    starts_at: null, ends_at: "2026-09-30T23:59:59+02:00", first_order_only: false, per_customer_limit: null },
];
const delOrder = (id, display, slot, status, name, phone, address, total, items, notes) => ({ id, display_id: display, delivery_slot: slot, status,
  customer_name: name, customer_phone: phone, delivery_address: address, total_cents: total, delivery_notes: notes || null,
  commerce_order_items: items.map(([product_name, quantity]) => ({ product_name, quantity })) });
export const deliveryList = (() => {
  const orders = [
    delOrder("do-1", "ORD-1051", "morning", "paid", "Thandi Mokoena", "+27820000101", "14 Beach Rd, Sea Point", 41_285, [["Sourdough loaf", 2], ["Cheese board", 1]]),
    delOrder("do-2", "ORD-1052", "morning", "pending_payment", "Sipho Ndlovu", "+27820000104", "3 Lower Main Rd, Observatory", 18_900, [["Coffee beans 1kg", 1]], "Gate code 4417"),
    delOrder("do-3", "ORD-1053", "afternoon", "confirmed", "Ayesha Daniels", "+27820000103", "22 Albert Rd, Woodstock", 26_450, [["Catering platter (12)", 1]]),
  ];
  const paid = orders.filter((o) => o.status !== "pending_payment");
  return { date: "2026-10-17", orders, total: orders.length, paid_count: paid.length, unpaid_count: orders.length - paid.length,
    paid_revenue_cents: paid.reduce((a, o) => a + o.total_cents, 0), unpaid_revenue_cents: orders.filter((o) => o.status === "pending_payment").reduce((a, o) => a + o.total_cents, 0) };
})();
export const recurringSubs = [
  { id: "rs-1", customer_name: "Pieter van der Berg", customer_phone: "+27820000102", cadence: "weekly", status: "active", payment_method: "cod",
    items: [{ product_id: "p-02", product_name: "Sourdough loaf", quantity: 2, unit_price_cents: 6_500 }], delivery_cents: 4_500, next_run: "2026-10-23", last_run: "2026-10-16T07:00:00+02:00" },
  { id: "rs-2", customer_name: "Bay Events Co.", customer_phone: "+27820000106", cadence: "monthly", status: "paused", payment_method: "eft",
    items: [{ product_id: "p-03", product_name: "Aged cheddar (250g)", quantity: 3, unit_price_cents: 11_900 }], delivery_cents: 0, next_run: "2026-11-01", last_run: null },
];

// ── Job costing + a fuller cross-check (Finances › Ledger, project businesses) ────────────────
// Only the job-costing test routes these in (with a uses_projects profile); every other screen
// keeps the bakery's empty cross-check above.
const trade = (t, cents) => ({ trade: t, cents });
export const jobCosting = {
  fee_default_pct: 10, fees_earned_cents: 3_700_000, fees_target_cents: 7_972_000, overheads_cents: 2_480_000, overhead_rate_pct: 3.3,
  business_result_cents: 1_220_000, unallocated_project_spend_cents: 1_215_000, unallocated_project_lines: 7,
  projects: [
    { project: "Clifton house", status: "on track", first: "2026-03-02", last: "2026-10-14", fee_pct: 10,
      received_cents: 48_400_000, cost_cents: 43_600_000, target_received_cents: 47_960_000, fee_earned_cents: 4_800_000, fee_target_cents: 4_360_000,
      overhead_share_cents: 1_450_000, profit_cents: 3_350_000, unallocated_trade_cents: 0, variations: null,
      trades: [trade("Structural steel", 14_200_000), trade("Brickwork", 11_800_000), trade("Electrical", 9_100_000), trade("Plumbing", 8_500_000)] },
    { project: "Bo-Kaap shopfit", status: "loss", first: "2026-07-20", last: "2026-10-09", fee_pct: 12,
      received_cents: 29_000_000, cost_cents: 30_100_000, target_received_cents: 33_712_000, fee_earned_cents: -1_100_000, fee_target_cents: 3_612_000,
      overhead_share_cents: 1_030_000, profit_cents: -2_130_000, unallocated_trade_cents: 2_400_000,
      variations: { documents: 2, claimed_cents: 1_850_000, extra_cost_cents: 2_260_000 },
      trades: [trade("Joinery", 16_300_000), trade("Ceilings", 6_200_000), trade("Electrical", 5_200_000)] },
  ],
};
export const crossCheckFull = { text: "42 documents filed; 37 are in the books and 31 are matched to the bank. 4 things need you.",
  unbooked: [{ id: "ub-1", supplier: "Cape Timber Supplies", filename: "ctsupplies-inv-2231.pdf", category: "materials", date: "2026-10-08", total_cents: 1_865_000 }],
  unbooked_total_cents: 1_865_000,
  bills_unpaid: [
    { invoice_id: "bi-1", supplier: "Atlantic Electrical", invoice_number: "AE-0918", issue_date: "2026-09-30", total_cents: 4_312_500,
      likely_payment: { txn_id: "tx-77", txn_date: "2026-10-06", description: "ATLANTIC ELEC EFT" }, possible_payments: 1 },
    { invoice_id: "bi-2", supplier: "Peninsula Plumbing", invoice_number: "PP-332", issue_date: "2026-10-02", total_cents: 980_000, likely_payment: null, possible_payments: 3 },
  ],
  no_document: [{ counterparty: "Builders Warehouse", lines: 4, last: "2026-10-12", projects: ["Clifton house"], total_cents: 2_745_050 }],
  no_document_total_cents: 2_745_050,
  sales_unpaid: [{ invoice_id: "si-1", customer: "Mokoena Family Trust", invoice_number: "INV-0207", issue_date: "2026-10-01", total_cents: 11_500_000,
    likely_payment: { txn_id: "tx-81", txn_date: "2026-10-15" } }],
  money_in_unmatched_cents: 650_000, money_in_unmatched_lines: 1,
  unexplained_in_sample: [{ txn_id: "tx-90", description: "CASH DEPOSIT SEA POINT", txn_date: "2026-10-11", amount_cents: 650_000 }] };
export const vatScenario = { vat_registered: true, over_threshold: false,
  text: "Last 3 months: R 136 440.00 VAT on sales, R 98 112.30 claimable — about R 38 327.70 to pay SARS.",
  months: [
    { month: "2026-08", sales: 29_000_000, output: 4_350_000, input_claimable: 3_105_000, input_no_vat_number: 210_000, net_cents: 1_245_000 },
    { month: "2026-09", sales: 33_400_000, output: 5_010_000, input_claimable: 3_870_230, input_no_vat_number: 0, net_cents: 1_139_770 },
    { month: "2026-10", sales: 28_560_000, output: 4_284_000, input_claimable: 2_836_000, input_no_vat_number: 95_000, net_cents: 1_448_000 },
  ] };

// ── Master › a tenant's drill-in (master.py: usage, audit, conversations, errors) ─────────────
export const masterUsage = { days: 14, per_tenant: {
  "demo-shop": { calls: 1842, ai_cost_usd: 6.48, infra_cost_usd: 0.31, vectors: 12406, storage_mb: 214.6 } } };
export const masterAudit = { events: [
  { id: "au-1", created_at: "2026-10-17T08:05:00+02:00", actor_email: "operator@vula.example.test", action: "master_update_tenant", detail: { plan: "growth" } },
  { id: "au-2", created_at: "2026-10-16T15:42:00+02:00", actor_email: "operator@vula.example.test", action: "master_impersonate_tenant", detail: { reason: "support ticket #118" } },
] };
export const masterThreads = { threads: [
  { phone: "+27820000101", last_message: "Can you do a platter for 12 on Saturday?", last_role: "user", last_at: "2026-10-17T09:58:00+02:00" },
  { phone: "+27820000104", last_message: "Your order HV-1051 is on its way.", last_role: "assistant", last_at: "2026-10-17T08:31:00+02:00" },
] };
export const masterThread = { messages: [
  { role: "user", text: "Can you do a platter for 12 on Saturday?", created_at: "2026-10-17T09:57:00+02:00" },
  { role: "assistant", text: "Let me check with the team and come back to you within the hour.", created_at: "2026-10-17T09:58:00+02:00" },
] };
export const masterErrors = { window_hours: 48,
  webhook_failures: [{ id: "wf-1", created_at: "2026-10-17T07:12:00+02:00", phone: "+27820000109", msg_type: "image", error: "Media download timed out (Meta 131052)" }],
  verification_flags: [{ created_at: "2026-10-16T18:20:00+02:00", task: "commerce_admin", outcome: "defect_found" }] };

// ── Master › Health, Users (master.py: health, qdrant-backup, users) ──────────────────────────
export const masterHealth = {
  whatsapp: [
    { tenant_id: "demo-shop", phone_number: "+27 82 000 0001", status: "connected", webhook_registered: true },
    { tenant_id: "demo-cycles", phone_number: "+27 82 000 0007", status: "error", webhook_registered: false, last_error: "Token expired (190)" },
  ],
  scheduler: { leader: "web-7f9c", lease_expired: false },
  scheduled_jobs: [
    { tenant_id: "demo-shop", job_type: "morning_brief", enabled: true, hour: 7, minute: 0, template_name: "vula_morning_brief", last_fired_at: "2026-10-17T07:00:04+02:00" },
    { tenant_id: "demo-bakery", job_type: "payment_reminders", enabled: false, hour: 9, minute: 30, template_name: null, last_fired_at: null },
  ],
  llm_router_24h: { total: 412, local: 371, cloud: 41, escalation_reasons: { "local unreachable": 9, "frontier task": 32 } },
  skill_routing_24h: { total: 388, fallthrough_to_reasoning_pct: 11, by_match: { keyword: 301, llm_fallback: 44, reasoning: 43 } },
  escalations: { open: 3, oldest: "2026-10-16T16:12:00+02:00" },
  vrl_health: { total_events: 96, low_coverage: false, last_event_at: "2026-10-17T10:02:00+02:00",
    by_verifier: { adversarial: { accepted: 51, total: 58, defect_found: 7 }, deterministic: { accepted: 38, total: 38, defect_found: 0 } } },
  webhook_failures_24h: { count: 1, recent: [{ created_at: "2026-10-17T07:12:00+02:00", tenant_id: "demo-shop", msg_type: "image", error: "Media download timed out (Meta 131052)" }] },
  migration_state: [{ migration: "217", applied: false, note: "ui_v2 default on", table: "vula_tenant_config" }],
};
export const qdrantBackups = { statuses: [
  { tenant_id: "demo-shop", last_backup_status: "ok", last_backup_at: "2026-10-17T02:00:00+02:00", last_backup_error: null },
  { tenant_id: "demo-build", last_backup_status: "error", last_backup_at: "2026-10-17T02:00:00+02:00", last_backup_error: "snapshot upload: 403 from storage" },
] };
export const masterUsers = { users: [
  { user_id: "u-owner", email: "owner@harbourvine.example.test", tenant_id: "demo-shop", role: "owner", created_at: "2026-06-02T10:00:00+02:00" },
  { user_id: "u-staff", email: "counter@harbourvine.example.test", tenant_id: "demo-shop", role: "staff", created_at: "2026-07-14T09:00:00+02:00" },
  { user_id: "u-master", email: "operator@vula.example.test", tenant_id: "master", role: "master", created_at: "2026-01-05T08:00:00+02:00" },
] };

// ── Master › Onboard, Knowledge, Benchmark, Conversations, Models (master.py) ─────────────────
// Muizenberg is the one not yet provisioned (Master › Onboard); the rest already have a workspace
// (their tenant_id is a known tenant), so they only show in Signups and Subscriptions.
export const signups = { signups: [
  { company_name: "Muizenberg Surf Co", contact_name: "Lindiwe Khumalo", email: "lindiwe@surfco.example.test", whatsapp: "+27820000201", industry: "retail", plan: "growth",
    status: "provisioning", paid: false, trial_ends: "2026-10-12T10:00:00Z", created_at: "2026-09-20T09:00:00Z" },
  { company_name: "Harbour & Vine Deli", contact_name: "Demo Owner", email: "owner@harbourvine.example.test", whatsapp: "+27820000001", industry: "retail", plan: "business",
    tenant_id: "demo-shop", status: "active", paid: true, trial_ends: "2026-09-01T10:00:00Z", created_at: "2026-08-02T09:00:00Z" },
  { company_name: "Ridgeline Renovations", contact_name: "Sam Petersen", email: "sam@ridgeline.example.test", whatsapp: "", industry: "construction", plan: "starter",
    tenant_id: "demo-build", status: "active", paid: false, trial_ends: "2026-10-19T10:00:00Z", created_at: "2026-09-22T09:00:00Z" },
  { company_name: "Bay Events Co", contact_name: "Naledi Mokoena", email: "naledi@bayevents.example.test", whatsapp: "+27820000301", industry: "services", plan: "growth",
    tenant_id: "demo-shop", status: "active", paid: false, trial_ends: "2026-11-10T10:00:00Z", created_at: "2026-10-11T09:00:00Z" },
] };
export const learnedAnswers = { candidates: [
  { id: "la-1", tenant_id: "demo-shop", tenant_display_name: TENANT_NAME, source: "web_research",
    question: "Do I need a liquor licence to sell wine with a catering platter in the Western Cape?",
    answer: "Yes — selling alcohol, even bundled with food, needs a licence under the Western Cape Liquor Act (2008). A special event licence covers once-off functions." },
  { id: "la-2", tenant_id: "demo-build", tenant_display_name: "Ridgeline Renovations", source: "tenant_review",
    question: "How long does plaster need before painting?", answer: "About a week per 3 mm in normal weather; damp-test first." },
] };
export const goLive = { status: "done", passed: 10, total: 12, pass_pct: 83, ready: false, failures: [
  { prompt: "What did we make last month?", why: ["Invented a figure (R 84 000) not in the books"], answer: "You made about R 84 000 last month." },
] };
export const benchmark = {
  latest: { components: [
    { component: "routing", passed: 41, total: 50, pass_pct: 82, judge_avg: null, p50_secs: 0.4 },
    { component: "commerce_admin", passed: 23, total: 25, pass_pct: 92, judge_avg: 4.4, p50_secs: 6.1 },
  ], gaps: [{ id: "g-1", component: "routing", prompt: "add 2 boxes of tiles to the Clifton quote", why: ["Routed to reasoning, expected commerce_admin"], answer: "" }] },
  runs: [{ id: "br-1", created_at: "2026-10-16T21:00:00+02:00", skill: null, status: "done", passed: 64, total: 75, secs: 412, judge_cost_usd: 0.83 }],
};
export const conversationCheck = { totals: { replies: 128, flagged: 4 }, tenants: [
  { tenant_id: "demo-shop", replies: 74, flagged: 3, unfollowed_promises: 1, counts: { promise: 1, cant_answer: 2, repeat: 0 },
    examples: { promise: [{ question: "Can you do a platter for 12 on Saturday?", reply: "Let me check with the team and come back to you.", next: "No escalation raised" }],
      cant_answer: [{ question: "Do you have gluten-free rye?", reply: "I couldn't find that in your catalogue." }] } },
  { tenant_id: "demo-bakery", replies: 54, flagged: 1, unfollowed_promises: 0, counts: { caveat: 1 }, examples: { caveat: [{ question: "Price for 3 dozen?", reply: "R 540 (checker: confirm the dozen price)." }] } },
] };
export const evalCandidates = { candidates: ["openrouter/anthropic/claude-haiku-5-5", "openrouter/openai/gpt-mini", "openrouter/qwen/qwen3-32b"],
  openrouter_configured: true, current: { model_worker_cloud: "openrouter/anthropic/claude-haiku-5-5", model_worker_cheap: "openrouter/qwen/qwen3-32b", cloud_model_by_task: null } };
export const evalReports = { reports: [
  { id: "ev-1", created_at: "2026-10-16T14:10:00+02:00", model: "openrouter/anthropic/claude-haiku-5-5", skill: null, status: "done", passed: 46, total: 50,
    p50_secs: 1.8, p95_secs: 4.2, cost_per_100_usd: 0.41, errors: 0, failures: [{ prompt: "mark INV-0207 paid", expect: "mark_invoice_paid", got: "list_invoices" }] },
  { id: "ev-2", created_at: "2026-10-16T14:10:00+02:00", model: "openrouter/qwen/qwen3-32b", skill: null, status: "done", passed: 31, total: 50,
    p50_secs: 2.6, p95_secs: 7.9, cost_per_100_usd: 0.05, errors: 2, failures: [] },
] };
export const feedbackCases = { cases: [
  { id: "fb-1", tenant_id: "demo-shop", question: "how much did I spend on dairy", routes_to: "reasoning", correction: "Should use finance_admin", yaml: "- prompt: how much did I spend on dairy\n  expect: reasoning" },
] };

// ── Smart Scanner (/admin/scan, /scan/commit, /scan/match-lines) ────────────────────────────────
export const scanResult = { ok: true, extracted: {
  doc_type: "delivery_note", confidence: "high", supplier: "Atlantic Dairy Co-op", date: "2026-10-17", category: "stock",
  reference: "DN-55821", total_cents: 184_250,
  line_items: [
    { description: "Aged cheddar 250g x 12", quantity: 12, unit: "ea", unit_price_cents: 7_950, total_cents: 95_400 },
    { description: "Cream cheese tubs 500g", quantity: 6, unit: "ea", unit_price_cents: 14_808, total_cents: 88_850 },
  ] } };
export const scanCommitPreview = { ok: true, preview: { supplier: "Atlantic Dairy Co-op", supplier_known: true, payment_terms_days: 30,
  due_date: "2026-11-16", days_until_due: 30, supplier_match: { supplier_name: "Atlantic Dairy Co-op", tier: "exact name", confidence: 1, auto_applied: true } } };
export const scanMatches = { lines: [
  { description: "Aged cheddar 250g x 12", quantity: 12, unit_price_cents: 7_950, match: { product_id: "p-03", name: "Aged cheddar (250g)" }, suggestions: [] },
  { description: "Cream cheese tubs 500g", quantity: 6, unit_price_cents: 14_808, match: null, suggestions: [{ product_id: "p-01", name: "Smoked snoek pâté (200g)" }] },
] };

// ── Stock (stock/useStock.js; commerce.py stock-take / receive / adjust, migrations 182–183) ──
export const stockCounts = [
  { id: "sc-1", note: "Month end — cold room", status: "open", started_by: "owner@harbourvine.example.test", created_at: at(0, "07:30") },
  { id: "sc-2", note: "September close", status: "applied", started_by: "owner@harbourvine.example.test", created_at: at(16, "17:05") },
];
const countLine = (pid, expected, counted, cost) => {
  const p = products.find((x) => x.id === pid);
  return { product_id: pid, variant_id: null, name: p.name, expected, counted, variance: counted - expected,
    variance_cents: cost == null ? null : (counted - expected) * cost };
};
export const stockCountDetail = (id) => ({
  count: stockCounts.find((c) => c.id === id) || stockCounts[0],
  lines: [countLine("p-03", 3, 2, 6_200), countLine("p-06", 9, 9.5, 14_000), countLine("p-02", 12, 12, null)],
  variance_cents: -6_200 + 7_000, lines_without_cost: 1,
  uncounted: [{ name: "Charcuterie board for 4", expected: 6 }, { name: "Rooibos & honey granola", expected: 30 }],
});
export const purchaseOrders = [
  { id: "po-1", po_number: "PO-0031", supplier_name: "Atlantic Dairy Co-op", status: "sent",
    items: [{ product_id: "p-03", quantity: 12, unit_cost_cents: 6_200 }, { product_id: "p-02", quantity: 6, unit_cost_cents: 3_100 }] },
  { id: "po-2", po_number: "PO-0029", supplier_name: "Cape Charcuterie", status: "received", items: [] },
];
export const stockMovements = [
  { id: "mv-1", reason: "sale", delta: -1, qty_after: 2, actor: "shop", created_at: at(0, "09:10") },
  { id: "mv-2", reason: "receive", delta: 12, qty_after: 3, actor: "owner@harbourvine.example.test", created_at: at(3, "11:00") },
  { id: "mv-3", reason: "count", delta: -2, qty_after: 5, actor: "counter@harbourvine.example.test", created_at: at(16, "17:30") },
];

// ── Suppliers + purchase orders (commerce.py suppliers / purchase-orders, P3.3) ──
export const suppliers = [
  { id: "sup-1", name: "Atlantic Dairy Co-op", payment_terms_days: 30, tax_id: "4123456789", contact_phone: "+27215550101",
    contact_email: "orders@atlanticdairy.example.test", aliases: ["Atlantic Dairy", "ADC"], notes: "" },
  { id: "sup-2", name: "Cape Charcuterie", payment_terms_days: 14, tax_id: "", contact_phone: "+27215550188", contact_email: "", aliases: [], notes: "" },
];
export const reorderSuggestions = [
  { supplier_id: "sup-1", supplier_name: "Atlantic Dairy Co-op",
    items: [{ product_id: "p-03", name: "Aged cheddar (250g)", stock_quantity: 3, suggested_qty: 12, unit_cost_cents: 6_200 }] },
];
export const supplierPurchaseOrders = [
  { id: "po-3", po_number: "PO-0032", supplier_name: "Cape Charcuterie", status: "draft", total_cents: 96_000,
    items: [{ name: "Charcuterie board for 4", quantity: 4 }] },
  ...purchaseOrders.map((p) => ({ ...p, total_cents: p.id === "po-1" ? 93_000 : 0, sent_channel: p.status === "sent" ? "email" : null,
    items: p.items.map((it) => ({ ...it, name: products.find((x) => x.id === it.product_id)?.name })) })),
];

// ── Costing (vula_mind/vula/api/manufacturing.py — Manufacturing Core, phases 2–3) ──
const mat = (id, name, base_unit, stock_qty, unit_cost, pack_size = null, pack_unit = null) =>
  ({ id, name, base_unit, stock_qty, unit_cost, pack_size, pack_unit });
export const mfgOverview = {
  ready: true,
  settings: { labour_rate_cents: 8_500, overhead_rule: "per_labour_hour", overhead_rate_cents: 2_400 },
  products: [{ id: "p-01", name: "Smoked snoek pâté (200g)" }, { id: "p-04", name: "Olive tapenade (150g)" }, { id: "p-08", name: "Rooibos & honey granola" }],
  materials: [
    mat("m-1", "Smoked snoek", "g", 4_200, "0.1450"),
    mat("m-2", "Cream cheese", "g", 6_000, "0.0820", 1000, "g"),
    mat("m-3", "Glass jar 200ml", "each", 96, "6.5000"),
    mat("m-4", "Kalamata olives", "g", -250, null),
  ],
  recipes: [
    { product_id: "p-01", product: "Smoked snoek pâté (200g)", recipe_version: 3, vat_registered: true,
      expected: { good: 24, unit_cost_cents: 3_412, materials_cents: 2_655, labour_cents: 531, overhead_cents: 226, missing_costs: [],
        lines: [{ material_id: "m-1", name: "Smoked snoek", qty_base: 2400, base_unit: "g", cost_cents: 34_800 },
                { material_id: "m-2", name: "Cream cheese", qty_base: 2400, base_unit: "g", cost_cents: 19_680 },
                { material_id: "m-3", name: "Glass jar 200ml", qty_base: 24, base_unit: "each", cost_cents: 15_600 }] },
      margin: { margin_cents: 4_327, margin_pct: 55.9 },
      runs: [{ unit_cost_cents: 3_398 }, { unit_cost_cents: 3_455 }] },
    { product_id: "p-04", product: "Olive tapenade (150g)", recipe_version: 1, vat_registered: true,
      expected: { good: 20, unit_cost_cents: 2_980, materials_cents: 2_300, labour_cents: 480, overhead_cents: 200, missing_costs: ["Kalamata olives"],
        lines: [{ material_id: "m-4", name: "Kalamata olives", qty_base: 3000, base_unit: "g", cost_cents: null },
                { material_id: "m-3", name: "Glass jar 200ml", qty_base: 20, base_unit: "each", cost_cents: 13_000 }] },
      margin: { margin_cents: 1_455, margin_pct: 16.8 }, runs: [] },
  ],
  runs: [
    { id: "run-2", product: "Smoked snoek pâté (200g)", good_qty: "24", reject_qty: "1", unit_cost: "33.98", status: "confirmed", confirmed_at: at(1, "15:10") },
    { id: "run-1", product: "Smoked snoek pâté (200g)", good_qty: "22", reject_qty: "0", unit_cost: "34.55", status: "reversed", confirmed_at: at(6, "11:40") },
  ],
};
export const mfgRecipe = { recipe: { batch_yield: "24", labour_hours: "1.5", lines: [
  { material: "Smoked snoek", qty: "2.4", unit: "kg", waste_pct: "5" }, { material: "Cream cheese", qty: "2.4", unit: "kg", waste_pct: "0" },
  { material: "Glass jar 200ml", qty: "24", unit: "each", waste_pct: "0" }] } };
export const mfgSuggest = { suggestions: [
  { key: "s-1", description: "KALAMATA OLIVES PITTED 2KG", invoice_price_cents: 31_900, supplier: "Cape Charcuterie", latest_on: "2026-10-02",
    observations: 3, low_cents: 29_900, high_cents: 31_900, vat_note: "VAT taken off", per_unit: "R 138.70 / kg" }] };
export const mfgBank = { since: "2026-07-01", labour_monthly_cents: 1_860_000, overhead_monthly_cents: 942_000,
  overhead_by_account: [{ label: "Rent", monthly_cents: 650_000 }, { label: "Electricity", monthly_cents: 292_000 }],
  labour_rate_cents: 9_300, overhead_per_hour_cents: 4_710, overhead_per_batch_cents: null, overhead_per_unit_cents: null };

// ── Import (commerce.py import/extract + import/invoices + import/chats/learn) ──
export const importOrders = [
  { customer_name: "Thandi Mokoena", customer_phone: "+27820000101", date: "2026-09-04", paid: true, delivered: true,
    items: [{ quantity: 2, name: "Smoked snoek pâté (200g)", unit_price_rands: 89 }, { quantity: 1, name: "Sourdough loaf", unit_price_rands: 65 }],
    total_rands: 243, delivery_address: "12 Beach Rd, Sea Point" },
  { customer_name: "", customer_phone: "+27820000177", date: "2026-09-11", paid: false, delivered: false,
    items: [{ quantity: 1, name: "Charcuterie board for 4" }], total_rands: null },
];
export const importInvoices = [
  { invoice_number: "INV-003983", customer_name: "Seaside Guesthouse", date: "2026-08-28", total_rands: 4_860.5, status: "paid" },
  { invoice_number: "INV-003991", customer_name: "Bo-Kaap Bistro", date: "2026-09-15", total_rands: 2_310, status: "unpaid" },
];
export const chatLearn = { chats: 3, messages: 412, business: "Harbour & Vine", voice_samples: 96, tone_suggested: true, answers_to_review: 7, pairs_found: 31 };

// ── Broadcast (commerce.py broadcasts / segments / campaigns / wa-templates) ──
export const broadcasts = [
  { id: "bc-2", name: "🐟 Weekly fish specials", template_name: "weekly_specials", audience_filter: "active_30d", status: "sent",
    created_at: at(3, "16:30"), sent_count: 184, delivered_count: 179, read_count: 142, clicked_count: 37 },
  { id: "bc-1", name: "Spring platter offer", template_name: "promo_image", audience_filter: "all,seg:sg-1", status: "sent",
    created_at: at(10, "17:00"), sent_count: 412, delivered_count: 398, read_count: 301, clicked_count: 64 },
];
export const broadcastFunnel = {
  funnel: { sent: 184, delivered: 179, read: 142, clicked: 37, failed: 5, attributed_orders: 9, attributed_revenue_cents: 418_500 },
  recipients: [
    { phone: "+27820000101", status: "read", clicked_at: at(3, "16:41") },
    { phone: "+27820000104", status: "read", clicked_at: at(3, "17:02") },
    { phone: "+27820000199", status: "failed", error: "131026 Message undeliverable" },
  ],
};
export const segments = [{ id: "sg-1", name: "Lapsed regulars", criteria: { not_ordered_within_days: 30, min_spend: 500, tags: ["vip"] } }];
export const audienceCounts = { counts: { all: 486, active_30d: 184, high_value: 97 }, segments: { "sg-1": 42 } };
export const campaigns = [{ id: "cp-1", name: "🔔 Reorder reminder", audience_filter: "seg:sg-1", recurrence: "weekly", next_run_at: at(-2, "16:30"), active: true }];
export const waTemplates = [
  { name: "weekly_specials", category: "MARKETING", status: "APPROVED", header_type: "NONE", language: "en",
    body_text: "Hi {{1}}! This week's fresh catch is in: {{2}}. Reply to order, or STOP to opt out." },
  { name: "promo_image", category: "MARKETING", status: "APPROVED", header_type: "IMAGE", language: "en",
    body_text: "{{1}} Tap Shop now to have a look. Reply STOP if you'd rather not get offers." },
  { name: "order_ready", category: "UTILITY", status: "PENDING", header_type: "NONE", language: "en", body_text: "Your order {{1}} is ready." },
];
export const broadcastAnalytics = {
  days: 90,
  totals: { broadcasts: 6, sent: 1_842, open_rate: 74.2, click_rate: 16.8, attributed_revenue_cents: 1_286_400, attributed_orders: 31 },
  trend: [{ week: "2026-08-24", sent: 210, clicked: 31 }, { week: "2026-08-31", sent: 0, clicked: 0 }, { week: "2026-09-07", sent: 412, clicked: 64 },
    { week: "2026-09-14", sent: 188, clicked: 22 }, { week: "2026-09-21", sent: 402, clicked: 70 }, { week: "2026-09-28", sent: 0, clicked: 0 },
    { week: "2026-10-05", sent: 446, clicked: 81 }, { week: "2026-10-12", sent: 184, clicked: 37 }],
  best_performing: [{ id: "bc-0", name: "Braai pack launch", click_rate: 24.1 }, { id: "bc-2", name: "Weekly fish specials", click_rate: 20.1 }],
};

// ── Marketing: email, scheduling, copy, client onboarding ──
export const emailCampaigns = [
  { id: "em-1", name: "September newsletter", subject: "September newsletter", audience_filter: "all", status: "sent", created_at: at(16, "09:00"), sent_count: 212, failed_count: 3 },
];
export const scheduledJobs = {
  approved_templates: ["weekly_specials", "promo_image"],
  jobs: [
    { job_type: "delivery_briefing", label: "Delivery briefing", description: "Tomorrow's deliveries, to the owner every evening.", kind: "daily", enabled: true, hour: 18, minute: 0, template_name: null, last_fired_at: "2026-10-16T16:00:00Z" },
    { job_type: "weekly_specials", label: "Weekly specials reminder", description: "Reminds you to send this week's specials.", kind: "weekly", enabled: true, hour: 9, minute: 30, day_of_week: 3, template_name: "weekly_specials" },
    { job_type: "unpaid_chase", label: "Unpaid-order chase", description: "Nudges customers with an unpaid order.", kind: "interval", enabled: false, interval_minutes: 60, template_name: null },
  ],
};
export const marketingSaved = [{ id: "ms-1", kind: "promo", topic: "Braai packs", body: "Braai season is here! Our braai packs are R399 this weekend only." }];
export const onboardingStats = { total: 312, invited: 180, awaiting_reply: 64, in_progress: 12, opted_in: 41, complete: 9, opted_out: 6 };

// ── Tap to Pay (vula/api/tap.py) ──
const QR = `data:image/svg+xml;utf8,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 21"><rect width="21" height="21" fill="white"/><rect x="1" y="1" width="7" height="7"/><rect x="13" y="1" width="7" height="7"/><rect x="1" y="13" width="7" height="7"/></svg>')}`;
export const tapSetup = (over = {}) => ({
  mode: "testing", tested: true, can_go_live: true, blockers: [], reminders_max: 3,
  payfast: { connected: true, mode: "test" }, whatsapp: { connected: true },
  staff: [
    { id: "tm-1", name: "Lerato (coach)", share_bp: 6000, whatsapp: "+27820000201", tag: { qr: QR, url: "https://pay.vula.test/t/ab12cd" } },
    { id: "tm-2", name: "Sipho (coach)", share_bp: 5000, whatsapp: null, tag: null },
  ],
  ...over,
});
export const tapUnpaid = [
  { id: "ub-1", description: "Beginner lesson", total_cents: 55_000, customer: "+27820000105", status: "abandoned", reminders_sent: 1, reminders_max: 3, next_reminder_at: "2026-10-18T07:00:00Z", opted_out: false, skipped: 0 },
  { id: "ub-2", description: "Board hire", total_cents: 25_000, customer: null, status: "unpaid", reminders_sent: 3, reminders_max: 3, opted_out: false, skipped: 1 },
];
export const tapBills = [
  { id: "tb-1", description: "Private lesson", subtotal_cents: 75_000, status: "claimed", is_test: false },
  { id: "tb-2", description: "R5 test payment", subtotal_cents: 500, status: "paid", is_test: true },
];
export const tapDevices = [{ id: "dv-1", member_id: "tm-1", last_seen_at: "2026-10-16T09:20:00Z", revoked_at: null }];

// ── Budget (expenses due + recurring bills) ──
export const expensesDue = {
  overdue: [{ id: "exp-due-1", description: "Fairview cheese account", supplier: "Fairview", due_date: "2026-10-10", amount_cents: 318_000 }],
  upcoming: [{ id: "exp-due-2", description: "Rent", supplier: "Sea Point Properties", due_date: "2026-11-01", amount_cents: 2_150_000 }],
  grand_total_cents: 2_468_000,
};
export const recurringBills = [
  { id: "rb-1", description: "Rent", supplier: "Sea Point Properties", category: "rent", cadence: "monthly", next_due: "2026-11-01", amount_cents: 2_150_000 },
  { id: "rb-2", description: "POS software", supplier: null, category: "utilities", cadence: "monthly", next_due: "2026-10-28", amount_cents: 49_900 },
];

// ── Assistant hub (assistant / agent-activity / persona / automations / flows) ──
export const agentEvents = [
  { kind: "tool", tool: "update_order_status", narrated: "Marked order HV-1052 as dispatched", who: "admin", consequential: true, at: "2026-10-17T07:40:00Z" },
  { kind: "tool", tool: "check_stock", args: { product: "Aged cheddar" }, who: "customer", consequential: false, at: "2026-10-17T07:10:00Z" },
  { kind: "route", task: "customer_reply", escalated: false, at: "2026-10-17T06:55:00Z" },
  { kind: "route", task: "document_extract", escalated: true, reason: "local GPU unreachable", at: "2026-10-16T15:20:00Z" },
];
export const persona = { persona_prompt: "Warm and quick. Small family deli, not corporate.", persona_prompt_suggested: "Friendly and brief, says 'shot!' for thanks, signs off with the shop name." };
export const automations = [
  { id: "au-1", name: "On its way", trigger_type: "order_status", trigger_config: { to_status: "dispatched" }, action_type: "whatsapp_customer", enabled: true, fire_count: 14, created_from: "form" },
  { id: "au-2", name: "Cheddar running low", trigger_type: "low_stock", trigger_config: {}, action_type: "whatsapp_team", enabled: false, fire_count: 0, created_from: "conversation" },
];
export const automationFirings = [{ id: "fi-1", message: "Hi Thandi, your order HV-1052 is on its way!", action_type: "whatsapp_customer" }];
export const flows = [{ id: "fl-1", name: "Book a tasting", trigger_phrases: ["book a tasting", "tasting"], steps: [{}, {}, {}], enabled: true, fire_count: 6 }];

// ── Sales rep (My Work: Home, Today, Contacts, Call sheet, Reminders, Claim sheet, CRM) ──
export const repToday = {
  appointments: [
    { id: "bk-r1", start_local: "08:30", end_local: "09:30", customer_name: "Atlantic Dairy", location: "Paarden Eiland", service_name: "Meeting", notes: "new cheese range", has_minutes: true },
    { id: "bk-r2", start_local: "14:00", end_local: "15:00", customer_name: "Bay Hotel kitchens", location: "Camps Bay", service_name: "Tasting", has_minutes: false },
  ],
  notes: [{ id: "mn-1", booking_id: null, summary: "Quick call with Fairview: sample box Thursday" }],
};
export const repReminders = [
  { id: "rm-1", text: "Send Bay Hotel the wholesale price list", due_at: at(2, "09:00"), status: "open" },
  { id: "rm-2", text: "Follow up with Atlantic Dairy on the cheese range", due_at: at(-2, "10:00"), status: "open" },
  { id: "rm-3", text: "Book the Fairview tasting", due_at: null, status: "open" },
  { id: "rm-4", text: "Drop samples at Harbour Fish", due_at: at(4, "11:00"), status: "done" },
];
export const repCallSheet = {
  config: { call_sheet_recipient_email: "sales@harbourvine.example.test", call_sheet_day_of_week: 4, call_sheet_hour: 17, call_sheet_minute: 0, call_sheet_last_sent_at: at(7, "17:00") },
  entries: [
    { id: "cs-1", text: "Atlantic Dairy (Paarden Eiland): new cheese range, wants pricing by Friday", source: "meeting", created_at: at(0, "09:35") },
    { id: "cs-2", text: "Fairview: sample box Thursday", source: "manual", created_at: at(1, "15:10") },
  ],
};
export const repContacts = [
  { id: "ct-1", name: "Lerato Khumalo", phone: "+27820000201", email: "lerato@atlanticdairy.example.test", company: "Atlantic Dairy", title: "Buyer", notes: "Prefers WhatsApp" },
  { id: "ct-2", name: "Johan Smit", phone: "nophone-ct-2", email: "", company: "Bay Hotel", title: "Head chef", notes: "" },
];
export const repExpenseSheet = {
  config: { expense_sheet_recipient_email: "accounts@harbourvine.example.test", expense_sheet_day_of_month: 25, expense_budget_cents: 300_000, expense_sheet_last_sent_at: null },
  mtd_spend_cents: 278_450,
};
export const dynamicsResults = [
  { id: "d-1", fullname: "Lerato Khumalo", emailaddress1: "lerato@atlanticdairy.example.test", telephone1: "+27 21 555 0101", jobtitle: "Buyer" },
  { id: "d-2", fullname: "Johan Smit", jobtitle: "Head chef" },
];

// ── Construction (Workspace, Field ops, Labour, QS rates) ──
export const labourReport = {
  total_cents: 315_000, payment_count: 6,
  by_worker: [{ worker: "Lwazi", id_number: "9001015800087", total_cents: 225_000, payments: 5 }, { worker: "Nomvula", id_number: null, total_cents: 90_000, payments: 1 }],
  by_project: [{ project: "Bay Events catering", total_cents: 270_000 }, { project: "Unassigned", total_cents: 45_000 }],
};
export const projectLabels = ["Bay_Events_catering", "Spring_market_stall"];
export const wsThreads = [{ id: "th-1", title: "Gala budget" }, { id: "th-2", title: "Who still owes us" }];
export const wsMessages = [
  { role: "user", content: "How much of the gala budget is left?" },
  { role: "assistant", content: "R 18 400 of R 64 000 left. Catering hire is the biggest open line." },
];
export const wsTasks = [
  { id: "tk-1", title: "Confirm tent hire", status: "open", clickup_task_id: "cu-91" },
  { id: "tk-2", title: "Send deposit invoice", status: "done", clickup_task_id: null },
];
export const qsRates = {
  own: [
    { id: "qr-1", description: "Drywall partition, 102mm", code: "DW-102", unit: "m2", rate: 485, source: "DIGG 2026",
      drift: { learned_rate: 540, change_pct: 11.3, source: "3 invoices, Sep 2026" } },
    { id: "qr-2", description: "Skim plaster", unit: "m2", rate: 95.5, source: "" },
  ],
  learned: [
    { description: "Rhino board 6.4mm", unit: "each", kind: "material", rate_cents: 21_950, low_cents: 19_900, high_cents: 23_500, source: "4 invoices", latest_cents: 23_500, latest_supplier: "BuildIt Paarden Eiland", basis: "paid", projects: ["Bay Events catering"] },
    { description: "General labourer", unit: "day", kind: "labour", rate_cents: 45_000, low_cents: 45_000, high_cents: 45_000, source: "5 labour payments", basis: "paid", projects: [] },
    { description: "Scaffold hire", unit: "week", kind: "plant", rate_cents: 180_000, low_cents: 150_000, high_cents: 210_000, source: "2 quotes", basis: "quoted", projects: [] },
  ],
};
export const fieldContractors = [
  { id: "fc-1", name: "Jane Dlamini", phone: "+27820000401", trade: "electrician" },
  { id: "fc-2", name: "Pieter Botha", phone: "+27820000402", trade: "carpenter" },
];
export const fieldStatus = {
  tasks_complete: 4, tasks_in_progress: 2, tasks_awaiting_sign_off: 1, tasks_rejected: 0, completion_pct: 57, team_size: 3,
  tasks: [
    { id: "ft-1", title: "Install roof trusses", trade: "carpenter", due_date: "2026-10-18", status: "in_progress", evidence_count: 0 },
    { id: "ft-2", title: "First-fix electrics", trade: "electrician", due_date: "2026-10-16", status: "awaiting_sign_off", evidence_count: 2 },
    { id: "ft-3", title: "Foundation slab", trade: "builder", due_date: "2026-10-09", status: "complete", evidence_count: 3 },
  ],
};
export const fieldTask = (id) => ({
  id, notes: "Use the 2.5mm cable throughout; DB board on the north wall.",
  evidence: [{ id: "ev-1", photo_url: "http://api.vula.test/media/evidence-1.png", caption: "DB board" }, { id: "ev-2", photo_url: "http://api.vula.test/media/evidence-2.png", caption: "" }],
  sign_off: null,
});
export const fieldDaily = { tasks: [
  { task_id: "ft-1", contractor_id: "fc-2", contractor_name: "Pieter Botha", title: "Install roof trusses", project_id: "pj-1" },
  { task_id: "ft-4", contractor_id: "fc-1", contractor_name: "Jane Dlamini", title: "Second-fix lights", project_id: "pj-1" },
] };

// ── Estimating (Draft, Takeoff rates) ──
export const draftResult = {
  document_label: "Fee Proposal", word_count: 412, sources_used: 3,
  content: "FEE PROPOSAL\nBay Events catering — Spring gala marquee\n\n1. Scope\nConcept to construction administration for a 450 m² temporary venue.\n\n2. Fee\nR 186 000 excl. VAT, in four stages.",
};
export const takeoffRates = [
  { trade: "drywall", item: "Partition, single skin 70mm", unit: "m²", low: 420, mid: 550, high: 680, source: "Gyproc 2025", updated_at: "2026-10-12T08:00:00Z" },
  { trade: "flooring", item: "LVT plank, laid", unit: "m²", low: 280, mid: 400, high: 520, source: "Polyflor SA 2025", updated_at: "2026-10-12T08:00:00Z" },
];

// ── Storefront pages (the Puck page builder) ──
export const sitePages = [
  { slug: "about", title: "About us", status: "published" },
  { slug: "winter-specials", title: "Winter specials", status: "draft" },
];
export const sitePage = (slug) => ({
  slug, title: slug === "about" ? "About us" : "Winter specials", status: slug === "about" ? "published" : "draft",
  seo: { title: "About Harbour & Vine", description: "A family deli in Sea Point." },
  puck_data: { content: [{ type: "Hero", props: { id: "Hero-1", title: "A family deli in Sea Point", subtitle: "Cheese, bread and catering since 2014.", image: "", overlayOpacity: 55, ctaText: "Order on WhatsApp", ctaHref: "/shop", ctaText2: "", ctaHref2: "" } }], root: { props: {} } },
});
export const sitePageVersions = [{ id: "pv-1", title: "About us", created_at: "2026-10-12T09:30:00Z" }];

// ── Master › Training KB ──
export const trainingStatus = { seeded: true, chunks: 1842 };
export const trainingTopics = { topics: [
  { topic: "JBCC contracts and payment certificates", filename: "jbcc.md", chars: 18400 },
  { topic: "Reading architectural drawings", filename: "drawings.md", chars: 12600 },
] };
