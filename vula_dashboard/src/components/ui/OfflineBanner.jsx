/**
 * OfflineBanner.jsx — says so when the phone has no signal (2026-10-27, PWA pass 1). The app
 * still opens offline from the service worker's copy (public/sw.js), but nothing can be loaded or
 * saved until the signal is back; this keeps that from looking like a broken screen.
 */
import { useEffect, useState } from "react";
import { WifiOff } from "lucide-react";

export function OfflineBanner() {
  const [offline, setOffline] = useState(() => typeof navigator !== "undefined" && navigator.onLine === false);
  useEffect(() => {
    const on = () => setOffline(false), off = () => setOffline(true);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, []);
  if (!offline) return null;
  return (
    <div className="vui-offline" role="status">
      <WifiOff size={16} aria-hidden="true" />
      <span>You’re offline. Vula shows what it already has; nothing new loads or saves until you’re back online.</span>
    </div>
  );
}
