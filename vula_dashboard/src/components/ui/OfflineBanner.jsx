/**
 * OfflineBanner.jsx — says so when the phone has no signal (2026-10-27, PWA pass 1). The app
 * still opens offline from the service worker's copy (public/sw.js), but nothing can be loaded or
 * saved until the signal is back; this keeps that from looking like a broken screen.
 */
import { useEffect, useState } from "react";
import { WifiOff } from "lucide-react";
import { API_HEALTH_EVENT } from "../../lib/apiHealth";

export function OfflineBanner() {
  const [offline, setOffline] = useState(() => typeof navigator !== "undefined" && navigator.onLine === false);
  // The phone is online but Vula's server isn't answering (lib/authFetch.js): say so, so empty
  // lists aren't read as lost data.
  const [apiDown, setApiDown] = useState(false);
  useEffect(() => {
    const on = () => setOffline(false), off = () => setOffline(true);
    const health = (e) => setApiDown(!!e.detail?.down);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    window.addEventListener(API_HEALTH_EVENT, health);
    return () => {
      window.removeEventListener("online", on); window.removeEventListener("offline", off);
      window.removeEventListener(API_HEALTH_EVENT, health);
    };
  }, []);
  if (offline) {
    return (
      <div className="vui-offline" role="status">
        <WifiOff size={16} aria-hidden="true" />
        <span>You’re offline. Vula shows what it already has; nothing new loads or saves until you’re back online.</span>
      </div>
    );
  }
  if (!apiDown) return null;
  return (
    <div className="vui-offline" role="status">
      <WifiOff size={16} aria-hidden="true" />
      <span>Vula can’t be reached right now. Your data is safe; lists may look empty or out of date until it’s back.</span>
    </div>
  );
}
