import { useState, useEffect } from "react";
import VulaLogin from "./components/VulaLogin";
import VulaPrivacy from "./components/VulaPrivacy";
import { useAuthStore } from "./store/auth";
import VulaDashboard from "./components/VulaDashboard";
import VulaQS from "./components/VulaQS";
import VulaQSPro from "./components/VulaQSPro";
import VulaTakeoff from "./components/VulaTakeoff";
import VulaOnboarding from "./components/VulaOnboarding";
import VulaAdmin from "./components/VulaAdmin";
import VulaDocuments from "./components/VulaDocuments";
import VulaSubscriptions from "./components/VulaSubscriptions";
import VulaTraining from "./components/VulaTraining";
import VulaFieldOps from "./components/VulaFieldOps";
import VulaCommerce from "./components/VulaCommerce";
import VulaDraft from "./components/VulaDraft";
import VulaAgent from "./components/VulaAgent";
import VulaInvoices from "./components/VulaInvoices";
import VulaBudget from "./components/VulaBudget";
import VulaMerchantAdmin from "./components/VulaMerchantAdmin";
import VulaSmartScanner from "./components/VulaSmartScanner";
import { getTenantTheme, themeVars } from "./theme/tenantThemes";

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
  { id: "draft", label: "Draft", component: VulaDraft },
  { id: "qs", label: "Quick Cost", component: VulaQS },
  { id: "qspro", label: "QS Pro", component: VulaQSPro },
  { id: "takeoff", label: "Takeoff", component: VulaTakeoff },
  { id: "onboard", label: "Onboard Client", component: VulaOnboarding },
  { id: "docs", label: "Documents", component: VulaDocuments },
  { id: "subscriptions", label: "Subscriptions", component: VulaSubscriptions },
  { id: "training", label: "Training KB", component: VulaTraining },
  { id: "admin", label: "Signups", component: VulaAdmin },
  { id: "field", label: "Field Ops", component: VulaFieldOps },
  { id: "commerce", label: "Commerce", component: VulaCommerce },
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

export default function App() {
  const [activeTab, setActiveTab] = useState("dashboard");
  const [route, setRoute] = useState(window.location.hash);
  const { user, role, tenantId, logout } = useAuthStore();

  // Track hash changes for public legal routes
  useEffect(() => {
    const onHash = () => setRoute(window.location.hash);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Match browser/PWA chrome (theme-color) to the tenant accent for owners/staff.
  useEffect(() => {
    if (role !== "owner" && role !== "staff") return;
    const tid = tenantId && tenantId !== "master" ? tenantId : "digg-demo";
    const accent = getTenantTheme(tid).accent;
    let meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
      meta = document.createElement("meta");
      meta.name = "theme-color";
      document.head.appendChild(meta);
    }
    meta.content = accent;
  }, [role, tenantId]);

  // Public legal pages — no auth required (needed for Meta app publishing)
  if (route === "#/privacy") return <VulaPrivacy view="privacy" />;
  if (route === "#/terms") return <VulaPrivacy view="terms" />;
  if (route === "#/data-deletion") return <VulaPrivacy view="data-deletion" />;

  // Resolve effective tenant — master sees digg-demo as default, owners see their own
  const effectiveTenantId = tenantId && tenantId !== "master" ? tenantId : "digg-demo";

  const ActiveComponent = TABS.find((t) => t.id === activeTab)?.component ?? VulaDashboard;

  // Show login if not authenticated
  if (!user) {
    return <VulaLogin onSuccess={() => {}} />;
  }

  // ── Merchant owners/staff get a scoped, single-store admin ───────────────────
  // No construction tools, no master Commerce view — just their shop, themed
  // as their own brand (logo + accent), "Powered by Vula".
  if (role === "owner" || role === "staff") {
    const theme = getTenantTheme(effectiveTenantId);
    const tenantName = theme.name || TENANT_NAMES[effectiveTenantId] || effectiveTenantId;
    return (
      <div style={{ minHeight: "100vh", background: COLORS.bg, ...themeVars(theme) }}>
        <nav style={{
          background: COLORS.surface, borderBottom: `1px solid ${COLORS.border}`,
          padding: "0 20px", display: "flex", alignItems: "center", gap: 12,
          position: "sticky", top: 0, zIndex: 200, height: 56, boxSizing: "border-box",
        }}>
          {theme.logoUrl ? (
            <img src={theme.logoUrl} alt={tenantName}
                 style={{ height: 30, width: "auto", objectFit: "contain" }} />
          ) : (
            <div style={{
              fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700,
              color: "var(--ink)",
            }}>
              {tenantName}
            </div>
          )}
          <span style={{
            fontSize: 10, color: COLORS.muted, fontFamily: "system-ui",
            border: `1px solid ${COLORS.border}`, borderRadius: 10, padding: "2px 8px",
          }}>
            Powered by Vula
          </span>
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ fontSize: 12, color: COLORS.muted, fontFamily: "system-ui" }}>
              {user.email}
            </span>
            <button
              onClick={() => { logout(); }}
              style={{
                padding: "6px 14px", border: `1px solid ${COLORS.border}`,
                borderRadius: 6, background: "transparent",
                color: COLORS.muted, fontSize: 12, cursor: "pointer", fontFamily: "system-ui",
              }}
            >
              Sign out
            </button>
          </div>
        </nav>
        <VulaMerchantAdmin tenantId={effectiveTenantId} tenantName={tenantName} fullPage />
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", background: COLORS.bg }}>
      {/* Top nav */}
      <nav style={{
        background: COLORS.surface,
        borderBottom: `1px solid ${COLORS.border}`,
        padding: "0 24px",
        display: "flex", alignItems: "center", gap: 0,
        position: "sticky", top: 0, zIndex: 200,
      }}>
        <div style={{
          fontFamily: "'Cormorant Garamond', serif",
          fontSize: 22, fontWeight: 700,
          color: COLORS.charcoal, marginRight: 32,
          padding: "16px 0",
        }}>
          Vula
        </div>
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            style={{
              padding: "18px 20px",
              border: "none", background: "none",
              cursor: "pointer",
              fontSize: 13, fontWeight: activeTab === t.id ? 600 : 400,
              color: activeTab === t.id ? COLORS.green : COLORS.muted,
              borderBottom: activeTab === t.id ? `2px solid ${COLORS.green}` : "2px solid transparent",
              fontFamily: "system-ui",
              transition: "all 0.15s",
            }}
          >
            {t.label}
          </button>
        ))}

        {/* User + logout */}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12, paddingRight: 8 }}>
          <span style={{ fontSize: 12, color: COLORS.muted, fontFamily: "system-ui" }}>
            {user.email} {role === "master" ? "· master" : ""}
          </span>
          <button
            onClick={() => { logout(); }}
            style={{
              padding: "6px 14px", border: `1px solid ${COLORS.border}`,
              borderRadius: 6, background: "transparent",
              color: COLORS.muted, fontSize: 12, cursor: "pointer",
              fontFamily: "system-ui",
            }}
          >
            Sign out
          </button>
        </div>
      </nav>

      {/* Active view — pass tenantId to every component that needs it */}
      <ActiveComponent tenantId={effectiveTenantId} tenantName={effectiveTenantId} />
    </div>
  );
}
