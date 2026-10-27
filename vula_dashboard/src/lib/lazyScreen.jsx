/**
 * lazyScreen.jsx — screens load when they're first opened, not with the app (2026-10-27).
 *
 * Opening Vula used to download every screen first (one 2 MB file; ~500 KB on the wire). Now the
 * app is the shell, sign-in and the home page; every other screen is its own small file, fetched
 * when it's opened — from the phone itself on an installed app (public/sw.js keeps them all,
 * precache.js lists them). scripts/check-size.mjs keeps it that way.
 *
 *   const VulaInvoices = lazyScreen(() => import('./VulaInvoices'))
 *   const BrandKit = lazyScreen(() => import('./VulaSettings'), 'BrandKitSettings')   // a named export
 *   <ScreenSuspense>…</ScreenSuspense>    the placeholder while one loads
 *
 * A screen that can't be fetched — the app open across an update whose old files are gone, a
 * dropped connection — reloads the page once (a fresh app has the new files); if it still fails,
 * the error reaches ErrorBoundary as before, rather than looping.
 */
import { lazy, Suspense } from "react";
import { Skeleton } from "../components/ui/index.jsx";

const RELOADED = "vula.screen.reloaded";

function reloadOnce(err) {
  try {
    const last = Number(sessionStorage.getItem(RELOADED) || 0);
    if (Date.now() - last > 30000) {
      sessionStorage.setItem(RELOADED, String(Date.now()));
      window.location.reload();
      return new Promise(() => {});          // the page is going; never render the failure
    }
  } catch { /* no sessionStorage: fall through to the error */ }
  throw err;
}

export function lazyScreen(load, name = "default") {
  return lazy(() => load()
    .then((m) => (name === "default" ? m : { default: m[name] }))
    .catch(reloadOnce));
}

/** The placeholder while a screen loads: the page's own skeleton lines, nothing that jumps. */
export function ScreenSuspense({ children }) {
  return (
    <Suspense fallback={<div className="vula-screen-loading" role="status" aria-label="Loading" style={{ padding: "var(--space-4, 16px)" }}><Skeleton lines={4} height={14} /></div>}>
      {children}
    </Suspense>
  );
}
