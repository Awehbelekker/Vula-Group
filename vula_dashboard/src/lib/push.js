/**
 * push.js — notifications on this phone or computer (2026-10-27, PWA pass 3). The device asks the
 * browser for a push subscription with Vula's public key and hands it to the API
 * (/v1/push/{tenant}/…, vula_mind/vula/api/dash_push.py); public/sw.js shows what arrives. The
 * alerts are the ones this person already gets on WhatsApp.
 */
import { VULA_API } from "./authFetch";
import { isInstalled, platform } from "./install";

export function supported() {
  return typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

/** iPhones only allow notifications for an app on the home screen (iOS 16.4+). */
export function needsInstallFirst() {
  return platform() === "ios" && !isInstalled();
}

export function permission() {
  try { return Notification.permission; } catch { return "default"; }
}

// Never waits on navigator.serviceWorker.ready: with no worker registered (blocked, private mode,
// not yet installed) it never settles, and sign-out (store/auth.js) would hang on it.
async function registration() {
  try { return (await navigator.serviceWorker.getRegistration("/")) || null; } catch { return null; }
}

const within = (ms, p) => Promise.race([p, new Promise((resolve) => setTimeout(() => resolve(null), ms))]);

export async function currentSubscription() {
  if (!supported()) return null;
  try { return await within(3000, (async () => (await registration())?.pushManager.getSubscription() || null)()); }
  catch { return null; }
}

export async function info(tenantId) {
  const r = await fetch(`${VULA_API}/v1/push/${tenantId}/key`);
  if (!r.ok) throw new Error("Couldn't check notifications just now.");
  return r.json();
}

function keyBytes(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

const post = (tenantId, what, body) => fetch(`${VULA_API}/v1/push/${tenantId}/${what}`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** Ask permission, subscribe this device, tell the API. Returns "on" | "blocked" | throws. */
export async function turnOn(tenantId, publicKey) {
  const granted = await Notification.requestPermission();
  if (granted !== "granted") return "blocked";
  const reg = await registration();
  if (!reg) throw new Error("Vula isn’t fully set up on this device yet — reload the page and try again.");
  const sub = (await reg.pushManager.getSubscription())
    || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) });
  const r = await post(tenantId, "subscribe", sub.toJSON());
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Couldn't turn notifications on.");
  return "on";
}

export async function turnOff(tenantId) {
  const sub = await currentSubscription();
  if (!sub) return;
  await within(4000, post(tenantId, "unsubscribe", { endpoint: sub.endpoint }).catch(() => {}));
  await within(2000, sub.unsubscribe().catch(() => {}));
}

export async function sendTest(tenantId) {
  const sub = await currentSubscription();
  if (!sub) return false;
  const r = await post(tenantId, "test", { endpoint: sub.endpoint });
  return r.ok && (await r.json()).sent;
}
