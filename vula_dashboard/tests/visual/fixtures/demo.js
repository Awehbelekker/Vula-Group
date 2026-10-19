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
export const bankWorkers = [{ id: "wk-1", name: "Lwazi" }, { id: "wk-2", name: "Nomvula" }];
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
