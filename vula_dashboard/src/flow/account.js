/**
 * account.js — a customer's own sign-in for the customer app (24 Nov 2026).
 *
 * Optional: the app works with no account (a request is opened by the token kept on the phone).
 * Signing in with Google ties the requests a customer makes to their login, so they see them on
 * any phone. A customer's login has no business behind it and opens nothing but that person's own
 * requests.
 *
 * It talks to the sign-in service (Supabase Auth, the one the dashboard uses) over plain requests,
 * so the customer app doesn't carry the dashboard's sign-in library: off to Google, back with the
 * session in the address's #fragment, which is read once and removed from the address.
 */
const API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const AUTH = (import.meta.env.VITE_SUPABASE_URL || "").replace(/\/$/, "");
const KEY = import.meta.env.VITE_SUPABASE_ANON_KEY || "";
const STORE = "fc.session";

const read = () => { try { return JSON.parse(localStorage.getItem(STORE) || "null"); } catch { return null; } };
const write = (s) => { try { s ? localStorage.setItem(STORE, JSON.stringify(s)) : localStorage.removeItem(STORE); } catch { /* private mode: this visit only */ } };
const post = (path, body, token) => fetch(`${AUTH}/auth/v1/${path}`, {
  method: "POST", headers: { "Content-Type": "application/json", apikey: KEY, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  body: JSON.stringify(body || {}) });

function fromAddress() {
  const h = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  if (!h.get("access_token")) return null;
  window.history.replaceState(null, "", window.location.pathname + window.location.search);   // never left in the address
  return { token: h.get("access_token"), refresh: h.get("refresh_token") || "", until: Date.now() + (Number(h.get("expires_in")) || 3600) * 1000, email: "" };
}

async function who(s) {
  const r = await fetch(`${AUTH}/auth/v1/user`, { headers: { apikey: KEY, Authorization: `Bearer ${s.token}` } });
  return r.ok ? (await r.json()).email || "" : null;
}

/** The current session ({token, email}) or null. Renewed when it is about to run out. */
export async function session() {
  if (!AUTH || !KEY) return null;
  let s = fromAddress() || read();
  if (!s) return null;
  try {
    if (s.until - Date.now() < 60000) {
      if (!s.refresh) throw new Error("ended");
      const r = await post("token?grant_type=refresh_token", { refresh_token: s.refresh });
      if (!r.ok) throw new Error("ended");
      const d = await r.json();
      s = { ...s, token: d.access_token, refresh: d.refresh_token || s.refresh, until: Date.now() + (d.expires_in || 3600) * 1000 };
    }
    if (!s.email) { const email = await who(s); if (email === null) throw new Error("ended"); s.email = email; }
    write(s);
    return { token: s.token, email: s.email };
  } catch { write(null); return null; }
}

/** Off to Google and back to this same page. */
export function signIn() {
  if (!AUTH) throw new Error("Sign-in isn't set up here.");
  const back = `${window.location.origin}${window.location.pathname}${window.location.search}`;
  window.location.assign(`${AUTH}/auth/v1/authorize?provider=google&redirect_to=${encodeURIComponent(back)}`);
}

export async function signOut() {
  const s = read();
  write(null);
  try { if (s?.token) await post("logout", {}, s.token); } catch { /* signed out on this phone either way */ }
}

/** The signed-in customer's own requests at every school, with the name and number they gave last. */
export async function myAccount(token) {
  const r = await fetch(`${API}/v1/flow/account`, { headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok) throw new Error(r.status === 401 ? "Your sign-in has ended. Sign in again." : "Your bookings couldn't be loaded. Try again.");
  return r.json();
}
