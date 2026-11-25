/**
 * AccountCard.jsx — sign in, or who is signed in (the customer app). Signing in is optional:
 * it keeps a customer's bookings with their login, so they show on any phone.
 */
import { useState } from "react";
import { signIn, signOut } from "./account";

export default function AccountCard({ acct, onSignedOut }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const go = () => { setBusy(true); setError(""); try { signIn(); } catch (e) { setError(e.message); setBusy(false); } };
  const out = async () => { setBusy(true); await signOut(); setBusy(false); onSignedOut(); };
  return (
    <section className="fc-card">
      {acct ? (<>
        <p className="fc-sub">Signed in as <b>{acct.email}</b>. Your bookings are kept with your account, so you'll see them on any phone.</p>
        <button type="button" className="fc-btn quiet" disabled={busy} onClick={out}>Sign out</button>
      </>) : (<>
        <h3>Keep your bookings with you</h3>
        <p className="fc-sub">Sign in to see your bookings on any phone. You can also book without an account.</p>
        <button type="button" className="fc-btn alt" disabled={busy} onClick={go}>{busy ? "Opening Google…" : "Sign in with Google"}</button>
      </>)}
      {error && <p className="fc-err" role="alert">{error}</p>}
    </section>
  );
}
