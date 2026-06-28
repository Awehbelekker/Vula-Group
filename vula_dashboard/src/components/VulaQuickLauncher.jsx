/**
 * VulaQuickLauncher.jsx — floating Vula button (pop-up stack). Everywhere, mobile + desktop.
 * Tap the FAB → action icons fan upward; chat/scan/etc. open in an overlay sheet.
 * Access-aware: only shows actions the member may use.
 */
import { useState, useEffect } from "react";
import VulaAssistant from "./VulaAssistant";
import VulaSmartScanner from "./VulaSmartScanner";
import VulaFollowups from "./VulaFollowups";
import VulaFinances from "./VulaFinances";
import VulaInvoices from "./VulaInvoices";

const C = { green: "#2C5545", surface: "#FFFFFF", border: "#DDD8CE", text: "#2A2A2A" };

const ACTIONS = [
  { key: "assistant", icon: "💬", label: "Vula AI", comp: VulaAssistant, always: true },
  { key: "scanner", icon: "📷", label: "Smart scan", comp: VulaSmartScanner },
  { key: "followups", icon: "📬", label: "Follow-ups", comp: VulaFollowups },
  { key: "finances", icon: "💵", label: "Finances", comp: VulaFinances },
  { key: "invoices", icon: "🧾", label: "New invoice", comp: VulaInvoices },
];

export default function VulaQuickLauncher({ tenantId, access = [], full = true }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(null);   // action key with an open overlay

  // App-icon shortcuts (?launch=chat|scan) open straight into an action.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search).get("launch");
    if (p === "chat") setActive("assistant");
    if (p === "scan") setActive("scanner");
  }, []);

  const allowed = ACTIONS.filter((a) => a.always || full || (access || []).includes(a.key));
  const ActiveComp = ACTIONS.find((a) => a.key === active)?.comp;

  return (
    <>
      {/* Overlay sheet */}
      {ActiveComp && (
        <div style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(20,18,15,0.45)", display: "flex", justifyContent: "center", alignItems: "flex-end" }} onClick={() => setActive(null)}>
          <div onClick={(e) => e.stopPropagation()} style={{ background: "#F7F4EE", width: "100%", maxWidth: 560, height: "92vh", borderRadius: "16px 16px 0 0", overflow: "hidden", display: "flex", flexDirection: "column", boxShadow: "0 -8px 40px rgba(0,0,0,0.25)" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 16px", borderBottom: `1px solid ${C.border}`, background: C.surface }}>
              <span style={{ fontWeight: 700, color: C.text, fontSize: 14 }}>{ACTIONS.find((a) => a.key === active)?.label}</span>
              <button onClick={() => setActive(null)} style={{ border: "none", background: "none", fontSize: 22, color: "#8A8680", cursor: "pointer", lineHeight: 1 }}>×</button>
            </div>
            <div style={{ flex: 1, overflowY: "auto" }}><ActiveComp tenantId={tenantId} /></div>
          </div>
        </div>
      )}

      {/* Pop-up stack */}
      {open && (
        <div style={{ position: "fixed", right: 20, bottom: 86, zIndex: 900, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 10, paddingBottom: "env(safe-area-inset-bottom)" }}>
          {allowed.map((a, i) => (
            <button key={a.key} onClick={() => { setActive(a.key); setOpen(false); }}
              style={{ display: "flex", alignItems: "center", gap: 10, border: "none", background: "transparent", cursor: "pointer",
                animation: `vulaPop 0.18s ease ${i * 0.03}s both` }}>
              <span style={{ background: C.surface, border: `1px solid ${C.border}`, color: C.text, fontSize: 12.5, fontWeight: 600, padding: "5px 10px", borderRadius: 8, boxShadow: "0 2px 8px rgba(0,0,0,0.12)" }}>{a.label}</span>
              <span style={{ width: 46, height: 46, borderRadius: "50%", background: C.surface, border: `1px solid ${C.border}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, boxShadow: "0 2px 10px rgba(0,0,0,0.15)" }}>{a.icon}</span>
            </button>
          ))}
        </div>
      )}

      {/* FAB */}
      <button onClick={() => setOpen(!open)} aria-label="Vula quick actions"
        style={{ position: "fixed", right: 20, bottom: 20, zIndex: 901, width: 58, height: 58, borderRadius: "50%", border: "none",
          background: C.green, color: "#fff", fontSize: 24, cursor: "pointer", boxShadow: "0 4px 16px rgba(44,85,69,0.45)",
          transform: open ? "rotate(45deg)" : "none", transition: "transform 0.2s", marginBottom: "env(safe-area-inset-bottom)" }}>
        {open ? "＋" : "✦"}
      </button>

      <style>{`@keyframes vulaPop { from { opacity: 0; transform: translateY(8px) scale(0.9); } to { opacity: 1; transform: none; } }`}</style>
    </>
  );
}
