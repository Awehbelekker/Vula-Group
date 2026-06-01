import { useState } from "react";
import VulaLogin from "./components/VulaLogin";
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
];

export default function App() {
  const [activeTab, setActiveTab] = useState("dashboard");
  const { user, role, logout } = useAuthStore();
  const ActiveComponent = TABS.find((t) => t.id === activeTab)?.component ?? VulaDashboard;

  // Show login if not authenticated
  if (!user) {
    return <VulaLogin onSuccess={() => {}} />;
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

      {/* Active view */}
      <ActiveComponent />
    </div>
  );
}
