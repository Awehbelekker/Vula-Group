/**
 * appBadge.js — the number on the installed app's icon (2026-10-27, PWA pass 4): customers
 * waiting for a person (the Inbox count). A notification that arrives while Vula is closed puts a
 * dot there (public/sw.js); opening Vula sets the real number, or clears it. Phones and browsers
 * without app badges simply show nothing.
 */
export function showOnAppIcon(count) {
  try {
    if (typeof navigator === "undefined" || !("setAppBadge" in navigator)) return;
    const n = Math.max(0, Math.floor(Number(count) || 0));
    (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(() => {});
  } catch { /* not allowed here */ }
}
