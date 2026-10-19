// mockApi.js — answers every Vula API + Supabase call from the seeded demo fixtures, so the
// dashboard renders realistic screens with no backend and no production data.
//
//   await installMockApi(page, { role: "owner" | "master" })
//
// Routes /vula\.test/ (http://api.vula.test and http://supabase.vula.test — the hosts
// playwright.config.js points VITE_API_URL / VITE_SUPABASE_URL at). Everything that is not
// localhost or *.vula.test is aborted: Google Fonts (fonts.googleapis.com / fonts.gstatic.com),
// OpenStreetMap tiles, the Facebook SDK… — so runs are offline and deterministic. That means
// screenshots use the system fallback fonts, not Inter/Cormorant; that is intended.
//
// Unknown GET endpoints answer {} (POST/PUT/PATCH/DELETE answer {ok:true}); set MOCK_DEBUG=1
// to log each unmatched path once.
import * as F from "./fixtures/demo.js";

// 1x1 transparent PNG (app icons etc.)
const PNG_1PX = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", "base64");
// The demo shop's logo: a simple monogram badge (no external fonts — generic sans).
const LOGO_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160">
<rect width="160" height="160" rx="36" fill="#2C5545"/><circle cx="80" cy="80" r="56" fill="none" stroke="#C8963E" stroke-width="5"/>
<text x="80" y="96" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="46" font-weight="700" fill="#F4EFE6">H&amp;V</text></svg>`;

const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

/** [method | "*", RegExp on pathname, (match, url, request) => body | {__raw}] — first hit wins. */
function routes(role, modules = []) {
  const T = "([^/]+)";
  const C = `^/v1/commerce/${T}`;
  const A = `${C}/admin`;
  const r = (m, re, fn) => [m, new RegExp(re + "$"), fn];
  return [
    // ── tenant / member / brand ──
    r("GET", `^/v1/tenants/?`, () => F.tenantsList),
    r("GET", `^/v1/tenants/registry`, () => F.registry),
    r("GET", `^/v1/tenants/${T}/home`, () => F.homeLayout),
    r("GET", `^/v1/tenants/${T}`, (m) => (m[1] === F.TENANT_ID ? (modules.length ? { ...F.tenant, modules: [...F.tenant.modules, ...modules] } : F.tenant) : { tenant_id: m[1], modules: [], theme: {}, profile: null })),
    r("GET", `^/v1/team/${T}/me`, () => F.teamMeFor(role)),
    r("GET", `^/v1/team/${T}/audit`, () => ({ events: F.teamAudit })),
    r("GET", `^/v1/team/${T}`, () => ({ members: F.teamMembers })),
    r("GET", `^/v1/users/${T}/users`, () => ({ users: F.teamLogins })),
    // a sales rep's own screens (VulaRepHome / VulaRepToday) — nothing due, nothing booked
    r("GET", `${A}/reminders`, () => ({ reminders: [] })),
    r("GET", `${A}/call-sheet`, () => ({ contacts: [], rows: [] })),
    r("GET", `${A}/rep/today`, () => ({ bookings: [], notes: [] })),
    r("GET", `${C}/brand`, () => F.brand),
    r("GET", `${C}/manifest\\.webmanifest`, () => F.manifest),
    r("GET", `${C}/app-icon/\\d+\\.png`, () => ({ __raw: { status: 200, contentType: "image/png", body: PNG_1PX } })),

    // ── Home ──
    r("GET", `${A}/stats`, () => F.stats),
    r("GET", `${A}/setup`, () => F.setup),
    r("GET", `${A}/cs-metrics`, () => F.csMetrics),
    r("GET", `${A}/shop-funnel`, () => F.shopFunnel),

    // ── Inbox ──
    // command centre (UX redesign Phase E) — filtered per role like the server does
    r("GET", `${A}/home`, () => F.homeFor(role)),
    r("GET", `${A}/today`, () => F.todayFor(role)),
    r("GET", `${A}/activity`, (m, url) => {
      const d = F.activityFor(role);
      const side = url.searchParams.get("side"), actor = url.searchParams.get("actor");
      return { ...d, items: d.items.filter((i) => (!side || i.side === side) && (!actor || i.actor === actor)) };
    }),
    r("GET", `${A}/escalations`, (m, url) => {
      const st = url.searchParams.get("status") || "open";
      const rows = st === "open" ? F.escalations : [];
      return { escalations: rows, open_count: st === "open" ? rows.length : null };
    }),
    r("GET", `${A}/conversations`, (m) => ({ tenant_id: m[1], conversations: F.conversations })),
    r("GET", `${A}/conversations/${T}/messages`, () => F.thread),
    r("GET", `${A}/canned-replies`, (m) => ({ tenant_id: m[1], canned_replies: [] })),

    // ── People / Sell ──
    r("GET", `${A}/customers`, (m, url) => {
      const aud = url.searchParams.get("audience") || "all";
      const q = (url.searchParams.get("search") || "").toLowerCase();
      let rows = F.customers;
      if (aud === "active_30d") rows = rows.filter((c) => c.last_order_at || c.last_seen_at);
      if (aud === "high_value") rows = rows.filter((c) => c.total_spent_cents > 50_000);
      if (q) rows = rows.filter((c) => (c.name || "").toLowerCase().includes(q) || (c.phone || "").includes(q));
      rows = [...rows].sort((a, b) => b.total_spent_cents - a.total_spent_cents);
      return { customers: rows, count: rows.length, total_all: F.customers.length, audience: aud };
    }),
    r("GET", `${A}/expenses`, (m, url) => {
      const st = url.searchParams.get("status"), reimb = url.searchParams.get("reimbursable");
      const rows = F.expenses.filter((e) => (!st || e.status === st) && (!reimb || e.reimbursable));
      return { expenses: rows, ...F.expenseLists };
    }),
    r("GET", `${A}/reports/expenses`, () => F.expenseReport),
    r("GET", `${A}/cards`, () => ({ cards: F.companyCards })),
    r("GET", `${A}/customers/${T}/detail`, (m) => F.customerDetail(decodeURIComponent(m[2]))),
    r("GET", `${A}/orders`, (m, url) => {
      const st = url.searchParams.get("status");
      const rows = st ? F.orders.filter((o) => o.status === st) : F.orders;
      return { orders: rows, count: rows.length };
    }),
    r("GET", `${A}/products`, () => ({ products: F.products, count: F.products.length })),
    r("GET", `${A}/categories`, () => ({ categories: F.productCategories })),

    // ── Money ──
    r("GET", `${A}/invoices`, (m, url) => {
      const dt = url.searchParams.get("doc_type");
      const dir = url.searchParams.get("direction");
      let rows = dir === "inbound" ? F.inboundInvoices : dir === "outbound" ? F.invoices : [...F.invoices, ...F.inboundInvoices];
      if (dt) rows = rows.filter((i) => i.doc_type === dt);
      return { invoices: rows, count: rows.length };
    }),
    r("GET", `${A}/invoice-settings`, () => ({ settings: F.invoiceSettings, onboarded: true, email_configured: true })),

    // ── Documents ──
    r("GET", `^/v1/documents/${T}/filed`, (m) => ({ tenant_id: m[1], documents: F.filedDocs, count: F.filedDocs.length, total: F.filedDocs.length })),
    r("GET", `^/v1/documents/${T}/health`, () => F.docsHealth),
    r("GET", `^/v1/documents/${T}/projects`, () => F.docProjects),
    r("GET", `^/v1/projects/${T}/codes`, () => ({ codes: F.projectCodes })),
    r("GET", `^/v1/projects/${T}/p/([^/]+)/overview`, (m) => F.projectOverview(m[2])),
    r("GET", `^/v1/projects/${T}/p/([^/]+)/boq`, () => F.projectBoq),
    r("GET", `^/v1/projects/${T}/p/([^/]+)/financials`, () => F.projectFinancials),
    r("GET", `^/v1/projects/${T}/p/([^/]+)`, (m) => F.projectDetail(m[2])),
    r("GET", `^/v1/projects/${T}`, (m) => ({ tenant_id: m[1], projects: F.projectsList })),
    r("GET", `^/documents/${T}/kb`, () => F.kbVisibility),
    r("GET", `^/documents/${T}/customer-view`, (m, url) => F.customerView(url.searchParams.get("q"))),
    r("GET", `^/documents/${T}`, () => F.kbFiles),
    r("GET", `${A}/documents/learn`, () => F.docsLearn),
    r("GET", `${A}/documents/reread`, () => ({ candidates: 0, status: {} })),
    r("GET", `${A}/documents/sort-projects`, () => ({ waiting: 0, would_file: 0, by_reason: {}, by_project: {}, still_ask: 0, suggested_by_month: {} })),
    r("GET", `${A}/site-import`, () => ({ status: null })),
    r("GET", `^/v1/google/status/${T}`, () => F.notConnected),

    // ── Settings ──
    r("GET", `${A}/order-settings`, () => ({ settings: F.orderSettings })),
    r("GET", `${A}/learned-summary`, () => F.learnedSummary),
    r("GET", `${A}/business-profile`, () => F.businessProfile),
    r("PUT", `${A}/business-profile`, (m, url, req) => {        // the real endpoint echoes the saved profile
      const { answers = {}, public_keys = [] } = req.postDataJSON() || {};
      const answered = Object.values(answers).filter((v) => (v || "").trim()).length;
      return { ...F.businessProfile, answers, public_keys, answered };
    }),
    r("GET", `${A}/shopify`, () => ({ connected: false })),
    r("GET", `${A}/woocommerce`, () => ({ connected: false })),
    r("GET", `${A}/shop-domain`, () => ({ state: "none", domain: null, suggestion: "shop.harbourvine.example.test" })),
    r("GET", `${A}/wa-profile`, () => F.waProfile),
    r("GET", `^/v1/yoco/status/${T}`, () => F.yocoStatus),
    r("GET", `^/v1/whatsapp/connect/status/${T}`, () => F.whatsappStatus),
    r("GET", `^/v1/clickup/status/${T}`, () => F.notConnected),
    r("GET", `^/v1/microsoft/status/${T}`, () => F.notConnected),
    r("GET", `^/v1/bookings/${T}/services`, () => ({ services: F.bookingServices })),
    r("GET", `^/v1/bookings/${T}/availability`, () => F.bookingAvailability),
    r("GET", `^/v1/bookings/${T}/settings`, () => ({ settings: F.bookingRules })),
    r("GET", `^/v1/bookings/${T}`, () => ({ bookings: F.bookingsList })),
    r("GET", `^/v1/email/contacts/${T}`, () => ({ contacts: F.emailContacts })),
    r("GET", `^/v1/email/followups/${T}`, () => ({ followups: F.emailFollowups })),
    r("GET", `^/v1/email/status/${T}`, (m) => ({ tenant_id: m[1], accounts: [] })),

    // ── Master ──
    r("GET", `^/status`, () => ({ status: "ok" })),   // server.py health check (master's Dashboard tab)
    r("GET", `^/v1/master/tenants`, () => (role === "master" ? { tenants: F.masterTenants } : { __raw: { status: 403, contentType: "application/json", body: '{"detail":"Master access required."}' } })),
    r("GET", `^/v1/master/tenants/${T}/setup`, () => F.setup),
    r("GET", `^/v1/admin/signups`, () => ({ signups: [] })),
  ];
}

const seenUnmatched = new Set();

export async function installMockApi(page, { role = "owner", modules = [] } = {}) {
  // modules: extra tenant modules for one test (e.g. "followups"), so the shared fixture — and the
  // flag-off baseline screenshots — stay as they are
  const table = routes(role, modules);
  const debug = process.env.MOCK_DEBUG === "1";

  // Offline: anything that isn't the dev server or the fake hosts is aborted (fonts, map tiles…).
  await page.route((url) => !/^(localhost|127\.0\.0\.1)$/.test(url.hostname) && !/\.vula\.test$/.test(url.hostname),
    (route) => route.abort());

  await page.route(/vula\.test/, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const method = req.method();
    if (method === "OPTIONS") {
      return route.fulfill({ status: 204, headers: { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" } });
    }
    const cors = { "access-control-allow-origin": "*" };

    if (url.hostname === "supabase.vula.test") {
      if (url.pathname.endsWith("/logo.svg")) {
        return route.fulfill({ status: 200, contentType: "image/svg+xml", body: LOGO_SVG, headers: cors });
      }
      // Auth (getSession/refresh/signOut) and anything else: harmless empty answers.
      return route.fulfill({ ...json({}), headers: cors });
    }

    for (const [m, re, fn] of table) {
      if (m !== "*" && m !== method) continue;
      const hit = url.pathname.match(re);
      if (!hit) continue;
      const out = fn(hit, url, req);
      if (out && out.__raw) return route.fulfill({ ...out.__raw, headers: cors });
      return route.fulfill({ ...json(out), headers: cors });
    }

    const key = `${method} ${url.pathname}`;
    if (debug && !seenUnmatched.has(key)) { seenUnmatched.add(key); console.log("[mockApi] unmatched", key); }
    if (/\.(png|jpe?g|webp|gif|svg|ico)$/.test(url.pathname)) return route.fulfill({ status: 404, headers: cors, body: "" });
    return route.fulfill({ ...json(method === "GET" ? {} : { ok: true }), headers: cors });
  });
}

/** Seed the persisted auth store + theme before the app boots (no Supabase login). */
export async function seedSession(page, { role = "owner", theme = "light" } = {}) {
  // staff = a front-of-house login with a limited access list; rep = a sales_rep login
  // (REP_DEFAULT_ACCESS). Both are "staff" dashboard logins; /team/me gives their scope.
  const who = {
    master: { user: { id: "u-master", email: "operator@vula.example.test", name: "Demo Operator" }, tenantId: "master", role: "master", access: [], full: true },
    owner: { user: { id: "u-owner", email: "owner@harbourvine.example.test", name: "Demo Owner" }, tenantId: F.TENANT_ID, role: "owner", access: [], full: true },
    staff: { user: { id: "u-staff", email: "counter@harbourvine.example.test", name: "Demo Counter" }, tenantId: F.TENANT_ID,
      role: "staff", access: F.teamMeFor("staff").access, full: false },
    rep: { user: { id: "u-rep", email: "rep@harbourvine.example.test", name: "Demo Rep" }, tenantId: F.TENANT_ID,
      role: "staff", access: F.teamMeFor("rep").access, full: false, teamRole: "sales_rep", teamPhone: "+27820000009" },
  };
  const auth = who[role] || who.owner;
  await page.addInitScript(([a, t, masterTenant, v2]) => {
    try {
      localStorage.setItem("vula-auth", JSON.stringify({ state: a, version: 0 }));
      localStorage.setItem("vula-theme-mode", t);
      if (masterTenant) localStorage.setItem("vula.masterTenant", masterTenant);
      // Master HQ has no tenant flag of its own: a UI_V2 run previews it (lib/flags.js override)
      if (v2 && a.role === "master") localStorage.setItem("vula.ui", "v2");
    } catch { /* storage blocked */ }
  }, [auth, theme, role === "master" ? F.TENANT_ID : "", process.env.UI_V2 === "1"]);
}
