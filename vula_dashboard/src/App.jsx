import { useState, useEffect, lazy, Suspense } from "react";
import VulaLogin from "./components/VulaLogin";
import VulaPrivacy from "./components/VulaPrivacy";
// Lazy: pulls in @measured/puck (~1 MB) — only the public page-render route needs it.
const VulaPageRender = lazy(() => import("./components/VulaPageRender"));
const VulaInvoiceApproval = lazy(() => import("./components/VulaInvoiceApproval"));
const VulaUiKit = lazy(() => import("./components/VulaUiKit")); // #/ui-kit, master only (UX redesign)
import { useAuthStore } from "./store/auth";
import VulaDashboard from "./components/VulaDashboard";
import VulaQS from "./components/VulaQS";
import VulaQSPro from "./components/VulaQSPro";
import VulaTakeoff from "./components/VulaTakeoff";
import VulaOnboarding from "./components/VulaOnboarding";
import VulaAdmin from "./components/VulaAdmin";
import VulaDocuments from "./components/VulaDocuments";
import VulaProjects from "./components/VulaProjects";
import VulaQSRates from "./components/VulaQSRates";
import VulaContacts from "./components/VulaContacts";
import VulaFinances from "./components/VulaFinances";
import VulaFollowups from "./components/VulaFollowups";
import VulaTeam from "./components/VulaTeam";
import VulaProjectWorkspace from "./components/VulaProjectWorkspace";
import VulaQuickLauncher from "./components/VulaQuickLauncher";
import VulaReports from "./components/VulaReports";
import VulaPayments from "./components/VulaPayments";
import VulaMasterPanel from "./components/VulaMasterPanel";
import { VULA_API, authFetch } from "./lib/authFetch";
import { Button, promptDialog } from "./components/ui/index.jsx";
import { ArrowLeft, Eye } from "lucide-react";
import VulaLogo from "./components/brand/VulaLogo.jsx";
import VulaSubscriptions from "./components/VulaSubscriptions";
import VulaTraining from "./components/VulaTraining";
import VulaFieldOps from "./components/VulaFieldOps";
import VulaDraft from "./components/VulaDraft";
import VulaAgent from "./components/VulaAgent";
import VulaInvoices from "./components/VulaInvoices";
import VulaBudget from "./components/VulaBudget";
import VulaMerchantAdmin from "./components/VulaMerchantAdmin";
import VulaSmartScanner from "./components/VulaSmartScanner";
import VulaShell from "./components/VulaShell";
import { MERCHANT_GROUPS, MASTER_GROUPS, MASTER_ZONES, filterGroups, labelFor, merchantVisible, applyLabels, flattenNav, withV2Pages, withV2Moves } from "./navConfig";
import { applyBrand, applyThemeMode } from "./theme/tokens";
import { applyUiMode, uiV2For, rememberedTenantUi, rememberTenantUi } from "./lib/flags";

const COLORS = {
  bg: "var(--bg)",
  surface: "var(--surface)",
  border: "var(--border)",
  green: "var(--accent)",
  muted: "var(--muted)",
  charcoal: "var(--ink)",
  amber: "#F5C451",   // master-mode banner (was referenced but never defined -> no background)
};

const TABS = [
  { id: "dashboard", label: "Dashboard", component: VulaDashboard },
  { id: "agent", label: "Agent", component: VulaAgent },
  { id: "workspace", label: "Workspace", component: VulaProjectWorkspace },
  { id: "draft", label: "Draft", component: VulaDraft },
  { id: "qs", label: "Quick Cost", component: VulaQS },
  { id: "qspro", label: "QS Pro", component: VulaQSPro },
  { id: "takeoff", label: "Takeoff", component: VulaTakeoff },
  { id: "onboard", label: "Onboard Client", component: VulaOnboarding },
  { id: "projects", label: "Projects", component: VulaProjects },
  { id: "qsrates", label: "QS Rates", component: VulaQSRates },
  { id: "contacts", label: "Contacts", component: VulaContacts },
  { id: "finances", label: "Finances", component: VulaFinances },
  { id: "followups", label: "Follow-ups", component: VulaFollowups },
  { id: "team", label: "Team", component: VulaTeam },
  { id: "docs", label: "Documents", component: VulaDocuments },
  { id: "subscriptions", label: "Subscriptions", component: VulaSubscriptions },
  { id: "training", label: "Training KB", component: VulaTraining },
  { id: "admin", label: "Signups", component: VulaAdmin },
  { id: "field", label: "Field Ops", component: VulaFieldOps },
  { id: "reports", label: "Reports", component: VulaReports },
  { id: "payments", label: "Payments", component: VulaPayments },
  { id: "master", label: "🛠 Master", component: VulaMasterPanel },
  { id: "merchant", label: "Merchant", component: VulaMerchantAdmin },
  { id: "invoices", label: "Invoices", component: VulaInvoices },
  { id: "budget", label: "Budget", component: VulaBudget },
  { id: "scanner", label: "Smart Scanner", component: VulaSmartScanner },
];

// Tenant display names for the scoped merchant header.
const TENANT_NAMES = {
  "off-the-hook": "Off the Hook",
  "awake-sa": "Awake South Africa",
};

// The master's last-picked tenant, so a reload lands back on it. No hardcoded fallback tenant:
// silently showing a demo tenant's data when the real list or login is missing hid real faults.
const MASTER_TENANT_KEY = "vula.masterTenant";
const savedMasterTenant = () => { try { return localStorage.getItem(MASTER_TENANT_KEY) || ""; } catch { return ""; } };

// A #/master/tenant/{id} deep link (VulaMasterPanel's bookmarkable tenant drill-in) needs the
// Master shell mounted before it can read that hash back — seed the initial tab from it so a
// shared support-ticket link lands on the right screen instead of the regular dashboard.
const initialTab = () => window.location.hash.startsWith("#/master/tenant/") ? "master" : "dashboard";

// A link from WhatsApp straight to one page: #/go/{page} (a navConfig id — #/go/invoices,
// #/go/stock, #/go/team). It survives the login (a Google or email-link sign-in replaces the
// address, so it is kept for this tab), then opens once the business's menu is known — and only
// if this login may see that page (mobile pass, 24 Oct 2026).
const GO_KEY = "vula.go";
const goInHash = () => (window.location.hash.match(/^#\/go\/([a-z0-9-]+)/) || [])[1] || "";
const keepGoLink = () => { try { const g = goInHash(); if (g) sessionStorage.setItem(GO_KEY, g); } catch { /* private mode */ } };
keepGoLink();

function OpenLinkedPage({ ready, tick, open }) {
  useEffect(() => {
    if (!ready) return;
    let leaf = goInHash();
    try { leaf = leaf || sessionStorage.getItem(GO_KEY) || ""; sessionStorage.removeItem(GO_KEY); } catch { /* private mode */ }
    if (!leaf) return;
    if (goInHash()) window.history.replaceState(null, "", window.location.pathname + window.location.search);
    open(leaf);
  }, [ready, tick]);  // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

export default function App() {
  const [activeTab, setActiveTab] = useState(initialTab);
  const [merchTab, setMerchTab] = useState("overview");
  const [route, setRoute] = useState(window.location.hash);
  const [goTick, setGoTick] = useState(0);   // a #/go/ link opened while already signed in
  const [masterTenant, setMasterTenant] = useState(savedMasterTenant);
  const [masterTenants, setMasterTenants] = useState([]);
  const [impersonateReason, setImpersonateReason] = useState(""); // shown in the "viewing as tenant" banner
  const [masterZone, setMasterZone] = useState("platform");   // Platform Ops vs Vula's Business sidebar zone
  const [masterSubTab, setMasterSubTab] = useState("tenants");  // VulaMasterPanel's own sub-tab, lifted so "← Master HQ" restores it
  const [tenantModules, setTenantModules] = useState(null); // owner/staff shell nav gating
  const [tenantProfile, setTenantProfile] = useState(null); // what kind of business (tenants.tenant_profile)
  // switched onto the redesign (migration 215); starts from this device's memory of it
  const [tenantUiV2, setTenantUiV2] = useState(() => rememberedTenantUi(useAuthStore.getState().tenantId));
  const [tenantLabels, setTenantLabels] = useState(null); // the tenant's own section names (ui_labels)
  const [masterTenantUiV2, setMasterTenantUiV2] = useState(false); // the opened tenant's ui_v2, for master's "Open as tenant"
  const [navRequest, setNavRequest] = useState(null);   // open a section's sub-tab (redesigned shell)
  const [openEscalations, setOpenEscalations] = useState(0); // real Inbox badge (P0.4)
  const [brand, setBrand] = useState(null); // the tenant's brand kit (/v1/commerce/{t}/brand) — ONE source for name, logo, colours
  const { user, role, tenantId, logout, access, full, teamRole, teamPhone, setMember } = useAuthStore();

  // DB-driven tenant switcher: every configured tenant, not a hardcoded pair.
  useEffect(() => {
    if (role !== "master") return;
    fetch(`${VULA_API}/v1/tenants`).then(r => r.json()).then(d => {
      const list = (d.tenants || []).map(t => ({ id: t.tenant_id, label: t.display_name || t.tenant_id }));
      setMasterTenants(list);
      setMasterTenant(cur => (cur && list.some(t => t.id === cur)) ? cur : (list[0]?.id || ""));
    }).catch(() => {});
  }, [role]);

  useEffect(() => {
    if (role !== "master" || !masterTenant) return;
    try { localStorage.setItem(MASTER_TENANT_KEY, masterTenant); } catch { /* private mode */ }
  }, [role, masterTenant]);

  // "Open as tenant" shows the workspace as its owner sees it — in that tenant's own design
  // (migration 215/217), not master's (2026-10-19).
  useEffect(() => {
    if (role !== "master" || !masterTenant) return;
    let alive = true;
    setMasterTenantUiV2(rememberedTenantUi(masterTenant));
    fetch(`${VULA_API}/v1/tenants/${masterTenant}`).then((r) => r.json())
      .then((d) => { if (alive) { setMasterTenantUiV2(!!d.ui_v2); rememberTenantUi(masterTenant, !!d.ui_v2); } })
      .catch(() => {});
    return () => { alive = false; };
  }, [role, masterTenant]);

  // The signed-in user's own business ("" until the login carries one — never a stand-in).
  const ownTenant = tenantId && tenantId !== "master" ? tenantId : "";

  // Track hash changes for public legal routes
  useEffect(() => {
    const onHash = () => {
      setRoute(window.location.hash);
      if (goInHash()) { keepGoLink(); setGoTick((n) => n + 1); }
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Brand the whole portal from the tenant's brand kit (2026-09-29). ONE source: the brand kit
  // (commerce_invoice_settings, read via the public /brand endpoint) sets name, logo, icon,
  // colours, font, corners and density everywhere. A hardcoded tenantThemes.js wrapper used to
  // override it on every page, so Gerflor's saved navy "Gerflor Cape Town" showed as Vula-green
  // "Vula Commerce" and Off the Hook's own blue never appeared. Settings › Brand kit fires
  // "vula-brand-changed" after a save so the shell updates without a reload.
  // Public pages (storefront, invoice approval, legal) are the business's customer-facing
  // look and always light; the dashboard follows the person's Light/Dark/Auto choice.
  const isPublicRoute = /^#\/(page|approve-invoice|privacy|terms|data-deletion)\b/.test(route || "");
  useEffect(() => {
    if (isPublicRoute) document.documentElement.dataset.theme = "light";
    else applyThemeMode();
  }, [isPublicRoute]);
  // The redesign (2026-10-17): only for a tenant switched onto it, and never on the public
  // customer-facing pages, which keep their own look (lib/flags.js). Master HQ has no tenant
  // row of its own: it follows the build (2026-10-19), while "Open as tenant" follows that
  // tenant's own switch; ?ui=v1 still pins the current look on this device.
  const masterUi = activeTab === "merchant" ? masterTenantUiV2 : true;
  // Before sign-in there is no tenant yet: the login screen follows the build (2026-10-20).
  const v2 = !isPublicRoute && uiV2For(!user ? true : role === "master" ? masterUi : tenantUiV2);
  useEffect(() => { applyUiMode(v2); }, [v2]);
  useEffect(() => {
    // Master HQ wears Vula's own brand; only "Open as tenant" takes on that tenant's brand
    // (the whole master panel used to recolour to whichever tenant was picked in the switcher).
    const tid = (role === "master") ? (activeTab === "merchant" ? masterTenant : null) : ownTenant;
    setBrand(null); // reset on tenant switch so a stale logo/name never flashes for the wrong tenant
    applyBrand({});
    if (!tid) return;
    let alive = true;
    const load = () => fetch(`${VULA_API}/v1/commerce/${tid}/brand`)
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => {
        if (!alive || !b) return;
        applyBrand(b); setBrand(b);
        // the installed app (home-screen name + icon) and the browser tab wear the brand too
        const setLink = (rel, href) => {
          let l = document.querySelector(`link[rel="${rel}"]`);
          if (!l) { l = document.createElement("link"); l.rel = rel; document.head.appendChild(l); }
          l.href = href;
        };
        setLink("manifest", `${VULA_API}/v1/commerce/${tid}/manifest.webmanifest`);
        setLink("apple-touch-icon", `${VULA_API}/v1/commerce/${tid}/app-icon/180.png`);
        if (b.name) document.title = b.name;
      })
      .catch(() => {});
    load();
    window.addEventListener("vula-brand-changed", load);
    return () => { alive = false; window.removeEventListener("vula-brand-changed", load); };
  }, [role, tenantId, masterTenant, activeTab]);

  // Load this member's access scope (which modules they may see) for owners/staff.
  useEffect(() => {
    if (!user || (role !== "owner" && role !== "staff")) return;
    const tid = ownTenant;
    if (!tid) return;
    const API = VULA_API;
    fetch(`${API}/v1/team/${tid}/me?email=${encodeURIComponent(user.email || "")}`)
      .then((r) => r.json())
      .then((d) => setMember({ access: d.access, full: d.full, role: d.role, whatsapp: d.whatsapp }))
      .catch(() => setMember({ access: [], full: true }));
    // Tenant module gating for the sidebar (same source VulaMerchantAdmin uses internally).
    fetch(`${API}/v1/tenants/${tid}`)
      .then((r) => r.json())
      .then((d) => { setTenantModules(d.modules || d.tenant?.modules || []); setTenantProfile(d.profile || null); setTenantUiV2(!!d.ui_v2); rememberTenantUi(tid, !!d.ui_v2); setTenantLabels(d.ui_labels || null); })
      .catch(() => { setTenantModules(null); setTenantProfile(null); setTenantUiV2(false); setTenantLabels(null); });
  }, [user, role, tenantId, setMember]);

  // Real Inbox badge (P0.4): count of open escalations waiting on a human — polled per
  // effective tenant, for whichever shell (owner/staff or master-open-as-tenant) is live.
  useEffect(() => {
    if (!user || role === "master" && activeTab !== "merchant") return;
    const tid = (role === "master") ? masterTenant : ownTenant;
    if (!tid) return;
    const API = VULA_API;
    const poll = () => fetch(`${API}/v1/commerce/${tid}/admin/escalations?status=open`)
      .then(r => r.json())
      .then(d => setOpenEscalations(d.open_count ?? (d.escalations || []).length ?? 0))
      .catch(() => {});
    poll();
    const t = setInterval(poll, 20000);
    return () => clearInterval(t);
  }, [user, role, tenantId, masterTenant, activeTab]);

  const withInboxBadge = (groups, count) => !count ? groups : groups.map(g => ({
    ...g, items: g.items.map(it => it.id === "inbox" ? { ...it, badge: count > 99 ? "99+" : String(count) } : it),
  }));

  // The redesigned shell (Phase D): open any page, a section's sub-tab included; Quick Launcher
  // overlays by event; and the palette's contact lookup (the same list the Customers page reads).
  const navigateIn = (groups) => (leaf) => {
    const page = flattenNav(groups).find((p) => p.id === leaf);
    if (!page) return;
    if (page.section === leaf) setMerchTab(leaf);
    else setNavRequest({ section: page.section, subtab: leaf, n: Date.now() });
  };
  const launch = (key) => window.dispatchEvent(new CustomEvent("vula-launch", { detail: key }));
  const contactSearch = (tid, groups) => (flattenNav(groups).some((p) => p.id === "customers")
    ? async (q) => {
      const r = await fetch(`${VULA_API}/v1/commerce/${tid}/admin/customers?search=${encodeURIComponent(q)}`);
      const d = r.ok ? await r.json() : {};
      return (d.customers || []).slice(0, 6).map((c) => ({ id: c.phone, label: c.name || c.phone, detail: c.name ? c.phone : undefined, page: "customers" }));
    } : undefined);

  // Public legal pages — no auth required (needed for Meta app publishing)
  if (route === "#/privacy") return <VulaPrivacy view="privacy" />;
  if (route === "#/terms") return <VulaPrivacy view="terms" />;
  if (route === "#/data-deletion") return <VulaPrivacy view="data-deletion" />;

  // Public Puck page renderer — #/page/{tenant}/{slug} — available to every tenant, no auth.
  if (route.startsWith("#/page/")) {
    const parts = route.replace(/^#\/page\//, "").split("/");
    return (
      <Suspense fallback={<div style={{ padding: 24, color: "var(--muted)" }}>Loading…</div>}>
        <VulaPageRender tenant={parts[0]} slug={parts.slice(1).join("/")} />
      </Suspense>
    );
  }

  // Public invoice client-approval page — #/approve-invoice/{tenant}/{invoiceId}?token=... —
  // no auth, token in the query string is the only credential (see commerce.py's approve routes).
  if (route.startsWith("#/approve-invoice/")) {
    const parts = route.replace(/^#\/approve-invoice\//, "").split("?")[0].split("/");
    return (
      <Suspense fallback={<div style={{ padding: 24, color: "var(--muted)" }}>Loading…</div>}>
        <VulaInvoiceApproval tenant={parts[0]} invoiceId={parts[1]} />
      </Suspense>
    );
  }

  // Resolve effective tenant — master picks via the switcher; owners see their own
  const effectiveTenantId = (role === "master") ? masterTenant : ownTenant;

  const ActiveComponent = TABS.find((t) => t.id === activeTab)?.component ?? VulaDashboard;

  // Show login if not authenticated
  if (!user) {
    return <VulaLogin onSuccess={() => {}} v2={v2} />;
  }

  // #/ui-kit — every shared component on demo data (2026-10-17, UX redesign). Master only: it
  // shows no tenant data, but it isn't part of any tenant's product.
  if (route.startsWith("#/ui-kit") && role === "master") {
    return (
      <Suspense fallback={<div style={{ padding: 24, color: "var(--muted)" }}>Loading…</div>}>
        <VulaUiKit />
      </Suspense>
    );
  }

  // ── Merchant owners/staff get a scoped, single-store admin ───────────────────
  // Sidebar shell (UI overhaul Phase 2) themed as THEIR brand — logo + accent at the top,
  // "Powered by Vula" at the bottom. Same VulaMerchantAdmin content, shell-controlled nav.
  if ((role === "owner" || role === "staff") && !ownTenant) {
    return (
      <div style={{ padding: 32, color: "var(--text)", maxWidth: 480, margin: "10vh auto" }}>
        <h2 style={{ fontSize: 18, margin: "0 0 8px" }}>Your login isn't linked to a business yet</h2>
        <p style={{ fontSize: 14, color: "var(--muted)", lineHeight: 1.5 }}>
          Ask the business owner to add you under Team, or contact Vula support. Nothing is shown
          until your login belongs to a business, so you never see someone else's data.
        </p>
        <button onClick={logout} style={{ marginTop: 12, padding: "8px 14px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--surface)", cursor: "pointer" }}>
          Sign out
        </button>
      </div>
    );
  }

  if (role === "owner" || role === "staff") {
    const tenantName = brand?.name || TENANT_NAMES[effectiveTenantId] || effectiveTenantId;
    const visibleGroups = applyLabels(filterGroups(MERCHANT_GROUPS,
      merchantVisible({ full, access, modules: tenantModules, profile: tenantProfile })), tenantLabels);
    const isRep = teamRole === "sales_rep" && !full;
    const groups = withInboxBadge(v2 ? withV2Pages(withV2Moves(visibleGroups), { activity: !isRep }) : visibleGroups, openEscalations);
    return (
      <div>
        <VulaShell
          brand={{ logoUrl: brand?.logo_url, logoEmoji: (tenantName || "V")[0], name: tenantName, sub: brand?.tagline || "Business admin" }}
          groups={groups}
          activeId={merchTab}
          onSelect={setMerchTab}
          title={labelFor(groups, merchTab) || labelFor(MERCHANT_GROUPS, merchTab) || "Home"}
          userEmail={user.email}
          onLogout={logout}
          v2={v2} onNavigate={navigateIn(groups)} onLaunch={launch} searchContacts={contactSearch(effectiveTenantId, groups)}
        >
          <VulaMerchantAdmin tenantId={effectiveTenantId} tenantName={tenantName} navGroups={groups}
            access={access} full={full} teamRole={teamRole} teamPhone={teamPhone} activeTab={merchTab} onTabChange={setMerchTab}
            navRequest={navRequest} v2={v2} />
        </VulaShell>
        <OpenLinkedPage ready={tenantModules !== null} tick={goTick} open={navigateIn(groups)} />
        <VulaQuickLauncher tenantId={effectiveTenantId} access={access} full={full} hideFab={v2} v2={v2} />
      </div>
    );
  }

  // ── Master "Open as tenant": full merchant-shell takeover (P0.2) ─────────────
  // Selecting Merchant previously rendered VulaMerchantAdmin in its MODAL branch (off-centre
  // overlay + old tab strip). Now the master steps INTO the tenant's real shell — same
  // experience the owner gets — with a "← Master HQ" way back.
  if (activeTab === "merchant") {
    const mName = brand?.name || TENANT_NAMES[effectiveTenantId] || effectiveTenantId;
    const mAll = filterGroups(MERCHANT_GROUPS, () => true);
    const mGroups = withInboxBadge(v2 ? withV2Pages(withV2Moves(mAll)) : mAll, openEscalations); // master sees every module
    return (
      <div>
        {/* Prominent, impossible-to-miss impersonation banner — the small brand "sub" label
            below is easy to miss once scrolled past the header; this stays pinned above
            everything for the whole visit. Shows the reason logged with master_impersonate_tenant
            (vula/api/master.py) when one was given. */}
        {v2 ? (
          <div className="v2-impersonate" role="status">
            <Eye size={16} aria-hidden="true" />
            <span>Master mode: viewing {mName}'s real workspace</span>
            {impersonateReason && <span className="v2-impersonate-why">({impersonateReason})</span>}
          </div>
        ) : (
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
            padding: "6px 12px", background: COLORS.amber, color: "#1A1200",
            fontSize: 12, fontWeight: 600, textAlign: "center",
          }}>
            <span>👁 Master mode — viewing {mName}'s real workspace</span>
            {impersonateReason && <span style={{ fontWeight: 400, opacity: 0.85 }}>— {impersonateReason}</span>}
          </div>
        )}
        <VulaShell
          brand={{ logoUrl: brand?.logo_url, logoEmoji: (mName || "V")[0], name: mName, sub: "Viewing as tenant" }}
          groups={mGroups}
          activeId={merchTab}
          onSelect={setMerchTab}
          title={labelFor(MERCHANT_GROUPS, merchTab) || "Home"}
          userEmail={user.email}
          roleLabel="master"
          onLogout={logout}
          v2={v2} onNavigate={navigateIn(mGroups)} onLaunch={launch} searchContacts={contactSearch(effectiveTenantId, mGroups)}
          headerExtra={
            // Returns to Master (not "dashboard") — masterSubTab was never touched while
            // visiting the tenant, so this naturally restores whichever Master sub-tab
            // (Tenants/Health/Usage/...) the operator was on before "Open as tenant".
            v2 ? (
              <Button variant="secondary" size="sm" Icon={ArrowLeft} onClick={() => { setImpersonateReason(""); setActiveTab("master"); }}>
                Master HQ
              </Button>
            ) : (
              <button onClick={() => { setImpersonateReason(""); setActiveTab("master"); }}
                style={{ padding: "6px 12px", border: `1px solid ${COLORS.border}`, borderRadius: 6,
                         background: COLORS.surface, color: "var(--text)", fontSize: 12,
                         cursor: "pointer", fontWeight: 600 }}>
                ← Master HQ
              </button>
            )
          }
        >
          <VulaMerchantAdmin tenantId={effectiveTenantId} tenantName={mName} navGroups={mGroups}
            access={[]} full activeTab={merchTab} onTabChange={setMerchTab} navRequest={navRequest} v2={v2} />
        </VulaShell>
      </div>
    );
  }

  // ── Master (Vula operator) shell — grouped sidebar, tenant switcher in the top bar ──
  return (
    <>
      <VulaShell
        brand={{ logoEmoji: "◆", logo: <VulaLogo kind="mark" size={40} />, name: "Vula", sub: "Master · all tenants" }}
        zones={MASTER_ZONES}
        activeZoneId={masterZone}
        onZoneChange={setMasterZone}
        activeId={activeTab}
        onSelect={setActiveTab}
        title={labelFor(MASTER_GROUPS, activeTab) || "Dashboard"}
        userEmail={user.email}
        roleLabel={role === "master" ? "master" : undefined}
        onLogout={logout}
        v2={v2} onNavigate={setActiveTab} onLaunch={launch}
        headerExtra={role === "master" ? (
          <select
            value={masterTenant}
            onChange={(e) => setMasterTenant(e.target.value)}
            title="Switch tenant"
            style={{
              padding: "6px 10px", border: `1px solid ${COLORS.border}`,
              borderRadius: 6, background: COLORS.surface, color: "var(--text)",
              fontSize: 12, cursor: "pointer",
            }}
          >
            {!masterTenants.length && <option value="">{masterTenant || "No tenants loaded"}</option>}
            {masterTenants.map((t) => (
              <option key={t.id} value={t.id}>{t.label}</option>
            ))}
          </select>
        ) : null}
      >
        <div className="v1-page" style={{ padding: "4px 0 24px" }}>
          <ActiveComponent tenantId={effectiveTenantId} tenantName={effectiveTenantId} v2={v2}
            onOpenTenant={async (tid) => {
              // An optional support reason for the audit trail (vula_admin_audit + the tenant's
              // own vula_merchant_audit); blank is fine. 2026-09-29: an in-app dialog instead of
              // window.prompt, and Cancel now really cancels (it used to open the tenant anyway).
              const name = masterTenants.find((t) => t.id === tid)?.label || tid;
              const answer = await promptDialog(
                `You'll see ${name}'s workspace as its owner does. Anything you change is real and is recorded.\n\nWhy are you opening it? (optional — e.g. "support ticket #123")`,
                { title: `Open ${name}`, confirmLabel: "Open workspace" });
              if (answer === null) return;
              const reason = answer.trim();
              setImpersonateReason(reason);
              authFetch(`/v1/master/tenants/${tid}/impersonate`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ reason }),
              }).catch(() => {}); // best-effort audit write — never blocks opening the tenant
              setMasterTenant(tid);
              setActiveTab("merchant");
            }}
            {...(activeTab === "master" ? { activeTab: masterSubTab, onTabChange: setMasterSubTab } : {})} />
        </div>
      </VulaShell>
      <VulaQuickLauncher tenantId={effectiveTenantId} access={access} full={full} hideFab={v2} v2={v2} />
    </>
  );
}
