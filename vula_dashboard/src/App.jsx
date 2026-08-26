import { useState, useEffect, lazy, Suspense } from "react";
import VulaLogin from "./components/VulaLogin";
import VulaPrivacy from "./components/VulaPrivacy";
// Lazy: pulls in @measured/puck (~1 MB) — only the public page-render route needs it.
const VulaPageRender = lazy(() => import("./components/VulaPageRender"));
const VulaInvoiceApproval = lazy(() => import("./components/VulaInvoiceApproval"));
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
import { VULA_API } from "./lib/authFetch";
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
import { MERCHANT_GROUPS, MASTER_GROUPS, MASTER_ZONES, filterGroups, labelFor, merchantVisible } from "./navConfig";
import { getTenantTheme, themeVars } from "./theme/tenantThemes";
import { applyAccent, applyInk, applyFontPairing } from "./theme/tokens";

const COLORS = {
  bg: "#F7F4EE",
  surface: "#FFFFFF",
  border: "#DDD8CE",
  green: "#2C5545",
  muted: "#8A8680",
  charcoal: "#1E1E1E",
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

// Fallback switcher list if /v1/tenants is unreachable — the live list is DB-driven.
const MASTER_TENANTS_FALLBACK = [
  { id: "digg-demo", label: "DIGG Architecture" },
  { id: "off-the-hook", label: "Off the Hook" },
];

export default function App() {
  const [activeTab, setActiveTab] = useState("dashboard");
  const [merchTab, setMerchTab] = useState("overview");
  const [route, setRoute] = useState(window.location.hash);
  const [masterTenant, setMasterTenant] = useState("digg-demo");
  const [masterTenants, setMasterTenants] = useState(MASTER_TENANTS_FALLBACK);
  const [masterZone, setMasterZone] = useState("platform");   // Platform Ops vs Vula's Business sidebar zone
  const [tenantModules, setTenantModules] = useState(null); // owner/staff shell nav gating
  const [openEscalations, setOpenEscalations] = useState(0); // real Inbox badge (P0.4)
  const [brandLogoUrl, setBrandLogoUrl] = useState(null); // live logo_url from Settings, overrides tenantThemes' static fallback
  const { user, role, tenantId, logout, access, full, teamRole, teamPhone, setMember } = useAuthStore();

  // DB-driven tenant switcher: every configured tenant, not a hardcoded pair.
  useEffect(() => {
    if (role !== "master") return;
    fetch(`${VULA_API}/v1/tenants`).then(r => r.json()).then(d => {
      const list = (d.tenants || []).map(t => ({ id: t.tenant_id, label: t.display_name || t.tenant_id }));
      if (list.length) setMasterTenants(list);
    }).catch(() => {});
  }, [role]);

  // Track hash changes for public legal routes
  useEffect(() => {
    const onHash = () => setRoute(window.location.hash);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Brand the whole portal from the tenant's brand kit: baseline from tenantThemes, then
  // override from the DB (commerce_invoice_settings — accent/ink/font) so a tenant's own choices
  // drive buttons/tabs/borders/headings everywhere — not just the invoice PDF (P3 brand kit).
  useEffect(() => {
    const tid = (role === "master") ? masterTenant
      : (tenantId && tenantId !== "master" ? tenantId : "digg-demo");
    const baseTheme = getTenantTheme(tid);
    applyAccent(baseTheme.accent);
    applyInk(baseTheme.ink);
    let meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) { meta = document.createElement("meta"); meta.name = "theme-color"; document.head.appendChild(meta); }
    meta.content = baseTheme.accent;

    const API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
    setBrandLogoUrl(null); // reset on tenant switch so a stale logo never flashes for the wrong tenant
    fetch(`${API}/v1/commerce/${tid}/admin/invoice-settings`)
      .then((r) => r.json())
      .then((d) => {
        const s = d?.settings || {};
        const c = s.accent_color;
        if (c && /^#?[0-9a-fA-F]{3,8}$/.test(c)) {
          const hex = c.startsWith("#") ? c : `#${c}`;
          applyAccent(hex); meta.content = hex;
        }
        if (s.ink_color && /^#?[0-9a-fA-F]{3,8}$/.test(s.ink_color)) {
          applyInk(s.ink_color.startsWith("#") ? s.ink_color : `#${s.ink_color}`);
        }
        if (s.font_pairing) applyFontPairing(s.font_pairing);
        if (s.logo_url) setBrandLogoUrl(s.logo_url);
      })
      .catch(() => {});
  }, [role, tenantId, masterTenant]);

  // Load this member's access scope (which modules they may see) for owners/staff.
  useEffect(() => {
    if (!user || (role !== "owner" && role !== "staff")) return;
    const tid = tenantId && tenantId !== "master" ? tenantId : "digg-demo";
    const API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
    fetch(`${API}/v1/team/${tid}/me?email=${encodeURIComponent(user.email || "")}`)
      .then((r) => r.json())
      .then((d) => setMember({ access: d.access, full: d.full, role: d.role, whatsapp: d.whatsapp }))
      .catch(() => setMember({ access: [], full: true }));
    // Tenant module gating for the sidebar (same source VulaMerchantAdmin uses internally).
    fetch(`${API}/v1/tenants/${tid}`)
      .then((r) => r.json())
      .then((d) => setTenantModules(d.modules || d.tenant?.modules || []))
      .catch(() => setTenantModules([]));
  }, [user, role, tenantId, setMember]);

  // Real Inbox badge (P0.4): count of open escalations waiting on a human — polled per
  // effective tenant, for whichever shell (owner/staff or master-open-as-tenant) is live.
  useEffect(() => {
    if (!user || role === "master" && activeTab !== "merchant") return;
    const tid = (role === "master") ? masterTenant
      : (tenantId && tenantId !== "master" ? tenantId : "digg-demo");
    const API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
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

  // Public legal pages — no auth required (needed for Meta app publishing)
  if (route === "#/privacy") return <VulaPrivacy view="privacy" />;
  if (route === "#/terms") return <VulaPrivacy view="terms" />;
  if (route === "#/data-deletion") return <VulaPrivacy view="data-deletion" />;

  // Public Puck page renderer — #/page/{tenant}/{slug} — available to every tenant, no auth.
  if (route.startsWith("#/page/")) {
    const parts = route.replace(/^#\/page\//, "").split("/");
    return (
      <Suspense fallback={<div style={{ padding: 24, fontFamily: "system-ui", color: "#8A8680" }}>Loading…</div>}>
        <VulaPageRender tenant={parts[0]} slug={parts.slice(1).join("/")} />
      </Suspense>
    );
  }

  // Public invoice client-approval page — #/approve-invoice/{tenant}/{invoiceId}?token=... —
  // no auth, token in the query string is the only credential (see commerce.py's approve routes).
  if (route.startsWith("#/approve-invoice/")) {
    const parts = route.replace(/^#\/approve-invoice\//, "").split("?")[0].split("/");
    return (
      <Suspense fallback={<div style={{ padding: 24, fontFamily: "system-ui", color: "#8A8680" }}>Loading…</div>}>
        <VulaInvoiceApproval tenant={parts[0]} invoiceId={parts[1]} />
      </Suspense>
    );
  }

  // Resolve effective tenant — master picks via the switcher; owners see their own
  const effectiveTenantId = (role === "master")
    ? masterTenant
    : (tenantId && tenantId !== "master" ? tenantId : "digg-demo");

  const ActiveComponent = TABS.find((t) => t.id === activeTab)?.component ?? VulaDashboard;

  // Show login if not authenticated
  if (!user) {
    return <VulaLogin onSuccess={() => {}} />;
  }

  // ── Merchant owners/staff get a scoped, single-store admin ───────────────────
  // Sidebar shell (UI overhaul Phase 2) themed as THEIR brand — logo + accent at the top,
  // "Powered by Vula" at the bottom. Same VulaMerchantAdmin content, shell-controlled nav.
  if (role === "owner" || role === "staff") {
    const theme = getTenantTheme(effectiveTenantId);
    const tenantName = theme.name || TENANT_NAMES[effectiveTenantId] || effectiveTenantId;
    const groups = withInboxBadge(filterGroups(MERCHANT_GROUPS,
      merchantVisible({ full, access, modules: tenantModules })), openEscalations);
    return (
      <div style={{ ...themeVars(theme) }}>
        <VulaShell
          brand={{ logoUrl: brandLogoUrl || theme.logoUrl, logoEmoji: (tenantName || "V")[0], name: tenantName, sub: theme.tagline || "Business admin" }}
          groups={groups}
          activeId={merchTab}
          onSelect={setMerchTab}
          title={labelFor(MERCHANT_GROUPS, merchTab) || "Home"}
          userEmail={user.email}
          onLogout={logout}
        >
          <VulaMerchantAdmin tenantId={effectiveTenantId} tenantName={tenantName} navGroups={groups}
            access={access} full={full} teamRole={teamRole} teamPhone={teamPhone} activeTab={merchTab} onTabChange={setMerchTab} />
        </VulaShell>
        <VulaQuickLauncher tenantId={effectiveTenantId} access={access} full={full} />
      </div>
    );
  }

  // ── Master "Open as tenant": full merchant-shell takeover (P0.2) ─────────────
  // Selecting Merchant previously rendered VulaMerchantAdmin in its MODAL branch (off-centre
  // overlay + old tab strip). Now the master steps INTO the tenant's real shell — same
  // experience the owner gets — with a "← Master HQ" way back.
  if (activeTab === "merchant") {
    const mTheme = getTenantTheme(effectiveTenantId);
    const mName = mTheme.name || TENANT_NAMES[effectiveTenantId] || effectiveTenantId;
    const mGroups = withInboxBadge(filterGroups(MERCHANT_GROUPS, () => true), openEscalations); // master sees every module
    return (
      <div style={{ ...themeVars(mTheme) }}>
        <VulaShell
          brand={{ logoUrl: brandLogoUrl || mTheme.logoUrl, logoEmoji: (mName || "V")[0], name: mName, sub: "Viewing as tenant" }}
          groups={mGroups}
          activeId={merchTab}
          onSelect={setMerchTab}
          title={labelFor(MERCHANT_GROUPS, merchTab) || "Home"}
          userEmail={user.email}
          roleLabel="master"
          onLogout={logout}
          headerExtra={
            // Returns to Master (not "dashboard") — masterSubTab was never touched while
            // visiting the tenant, so this naturally restores whichever Master sub-tab
            // (Tenants/Health/Usage/...) the operator was on before "Open as tenant".
            <button onClick={() => setActiveTab("master")}
              style={{ padding: "6px 12px", border: `1px solid ${COLORS.border}`, borderRadius: 6,
                       background: COLORS.surface, color: "var(--text, #2A2A2A)", fontSize: 12,
                       cursor: "pointer", fontFamily: "system-ui", fontWeight: 600 }}>
              ← Master HQ
            </button>
          }
        >
          <VulaMerchantAdmin tenantId={effectiveTenantId} tenantName={mName} navGroups={mGroups}
            access={[]} full activeTab={merchTab} onTabChange={setMerchTab} />
        </VulaShell>
      </div>
    );
  }

  // ── Master (Vula operator) shell — grouped sidebar, tenant switcher in the top bar ──
  return (
    <>
      <VulaShell
        brand={{ logoEmoji: "◆", name: "Vula", sub: "Master · all tenants" }}
        zones={MASTER_ZONES}
        activeZoneId={masterZone}
        onZoneChange={setMasterZone}
        activeId={activeTab}
        onSelect={setActiveTab}
        title={labelFor(MASTER_GROUPS, activeTab) || "Dashboard"}
        userEmail={user.email}
        roleLabel={role === "master" ? "master" : undefined}
        onLogout={logout}
        headerExtra={role === "master" ? (
          <select
            value={masterTenant}
            onChange={(e) => setMasterTenant(e.target.value)}
            title="Switch tenant"
            style={{
              padding: "6px 10px", border: `1px solid ${COLORS.border}`,
              borderRadius: 6, background: COLORS.surface, color: "var(--text, #2A2A2A)",
              fontSize: 12, fontFamily: "system-ui", cursor: "pointer",
            }}
          >
            {masterTenants.map((t) => (
              <option key={t.id} value={t.id}>{t.label}</option>
            ))}
          </select>
        ) : null}
      >
        <div style={{ padding: "4px 0 24px" }}>
          <ActiveComponent tenantId={effectiveTenantId} tenantName={effectiveTenantId}
            onOpenTenant={(tid) => { setMasterTenant(tid); setActiveTab("merchant"); }}
            {...(activeTab === "master" ? { activeTab: masterSubTab, onTabChange: setMasterSubTab } : {})} />
        </div>
      </VulaShell>
      <VulaQuickLauncher tenantId={effectiveTenantId} access={access} full={full} />
    </>
  );
}
