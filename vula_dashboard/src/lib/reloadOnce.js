/**
 * reloadOnce.js — fetching a screen's file when it's opened (lazyScreen.jsx), and what to do when
 * that fails:
 *   - no signal: wait until the phone is back online, then try again (a reload with no signal
 *     would only lose the page);
 *   - online, it still fails: the app was open across an update and its old files are gone, so
 *     reload the page once — a fresh app has this build's files. A second failure within 30
 *     seconds is passed on to ErrorBoundary instead of reloading in a loop.
 */
const KEY = "vula.screen.reloaded";

const onceOnline = () => new Promise((resolve) => globalThis.addEventListener("online", resolve, { once: true }));

export function reloadOnce(err, { now = Date.now(), storage = globalThis.sessionStorage, reload = () => globalThis.location.reload() } = {}) {
  try {
    const last = Number(storage.getItem(KEY) || 0);
    if (now - last > 30000) {
      storage.setItem(KEY, String(now));
      reload();
      return new Promise(() => {});          // the page is going; never render the failure
    }
  } catch { /* no sessionStorage: fall through to the error */ }
  throw err;
}

export async function loadScreen(load, { isOnline = () => globalThis.navigator?.onLine !== false, backOnline = onceOnline, ...reloadOpts } = {}) {
  try {
    return await load();
  } catch (err) {
    if (isOnline()) return reloadOnce(err, reloadOpts);
    await backOnline();
    try {
      return await load();
    } catch (again) {
      return reloadOnce(again, reloadOpts);
    }
  }
}
