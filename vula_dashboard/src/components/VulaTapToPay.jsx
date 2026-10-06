/**
 * VulaTapToPay.jsx — one-screen setup for Tap to Pay (NFC tag / QR → WhatsApp → PayFast → slip).
 * Four steps, top to bottom: connect PayFast → pick who gets paid + their share and get their tag →
 * run a R5 test payment → go live. Nothing here needs a developer: the server keeps the on/off
 * switch, and "Go live" stays locked until a real test payment has been confirmed.
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { T } from "../theme/tokens";
import { Card, Button, Badge, SectionTitle, inputStyle } from "./ui";
import { VULA_API } from "../lib/authFetch";

const MODE_BADGE = { off: ["muted", "Off"], testing: ["warn", "Testing"], live: ["ok", "Live"] };
const STATUS_TONE = { open: "muted", claimed: "warn", paid: "ok", cancelled: "muted", expired: "muted" };
const RAND = /^\d{1,7}(\.\d{1,2})?$/;

async function api(path, opts = {}) {
  const r = await fetch(`${VULA_API}${path}`, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Something went wrong — please try again.");
  return data;
}

function Step({ n, title, done, children }) {
  return (
    <Card style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ width: 24, height: 24, borderRadius: 12, display: "inline-flex", alignItems: "center", justifyContent: "center",
          background: done ? T.ok || "#16a34a" : T.surfaceAlt, color: done ? "#fff" : T.muted, fontSize: 13, fontWeight: 700 }}>{done ? "✓" : n}</span>
        <span style={{ fontWeight: 700, color: T.ink, fontSize: 15 }}>{title}</span>
      </div>
      {children}
    </Card>
  );
}

const hint = { fontSize: 12.5, color: T.muted };

export default function VulaTapToPay({ tenantId }) {
  const [st, setSt] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [shares, setShares] = useState({});        // member id -> percent string
  const [pf, setPf] = useState({ merchant_id: "", merchant_key: "", passphrase: "", mode: "test" });
  const [editPf, setEditPf] = useState(false);
  const [test, setTest] = useState(null);          // {url, qr, code}
  const [testMember, setTestMember] = useState("");
  const [bills, setBills] = useState([]);
  const [nb, setNb] = useState({ member: "", desc: "", amount: "", phone: "" });
  const [newCode, setNewCode] = useState("");
  const [enrol, setEnrol] = useState(null);        // {id, name, code, expires_in_minutes, app_url}
  const [devices, setDevices] = useState([]);
  const [unpaid, setUnpaid] = useState([]);
  const [closing, setClosing] = useState(null);     // bill id whose "paid another way" picker is open
  const poll = useRef(null);

  const load = useCallback(async () => {
    try {
      const s = await api(`/v1/tap/${tenantId}/setup`);
      setSt(s);
      setShares((cur) => {
        const next = { ...cur };
        s.staff.forEach((m) => { if (next[m.id] === undefined) next[m.id] = String(m.share_bp / 100); });
        return next;
      });
      setTestMember((m) => m || s.staff.find((x) => x.tag)?.id || s.staff[0]?.id || "");
      setNb((b) => ({ ...b, member: b.member || s.staff.find((x) => x.tag)?.id || "" }));
      api(`/v1/tap/${tenantId}/unpaid`).then((d) => setUnpaid(d.unpaid || [])).catch(() => {});
      api(`/v1/tap/${tenantId}/devices`).then((d) => setDevices(d.devices || [])).catch(() => {});
      if (s.mode !== "off") {
        const b = await api(`/v1/tap/${tenantId}/bills`).catch(() => ({ bills: [] }));
        setBills(b.bills || []);
      }
    } catch (e) { setErr(e.message); }
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  // while a test payment is pending, check every 4 s so the tick appears by itself
  useEffect(() => {
    clearInterval(poll.current);
    if (test && st && !st.tested) poll.current = setInterval(load, 4000);
    return () => clearInterval(poll.current);
  }, [test, st, load]);

  const run = async (key, fn) => {
    setBusy(key); setErr("");
    try { await fn(); } catch (e) { setErr(e.message); } finally { setBusy(""); }
  };

  const connectPayfast = () => run("pf", async () => {
    if (!pf.merchant_id.trim() || !pf.merchant_key.trim()) throw new Error("Enter your PayFast merchant ID and merchant key.");
    const r = await api(`/v1/payments/${tenantId}/providers`, { method: "POST", body: {
      provider: "payfast", mode: pf.mode, is_default: false,
      credentials: { merchant_id: pf.merchant_id.trim(), merchant_key: pf.merchant_key.trim(), passphrase: pf.passphrase } } });
    if (r.error) throw new Error(r.error);
    setPf({ merchant_id: "", merchant_key: "", passphrase: "", mode: pf.mode });
    setEditPf(false); await load();
  });

  const saveShares = () => run("shares", async () => {
    const staff_shares = {};
    for (const m of st.staff) {
      const v = Number(shares[m.id] ?? 0);
      if (!Number.isFinite(v) || v < 0 || v > 100) throw new Error(`${m.name}: enter a share between 0 and 100.`);
      staff_shares[m.id] = Math.round(v * 100);
    }
    setSt(await api(`/v1/tap/${tenantId}/setup/shares`, { method: "PUT", body: { default_share_bp: 0, staff_shares } }));
  });

  const getTag = (id, rotate = false) => run(`tag-${id}`, async () => {
    if (rotate && !window.confirm("Make a new tag? The old tag and QR code stop working straight away.")) return;
    await api(`/v1/tap/${tenantId}/staff/${id}/tag${rotate ? "?rotate=true" : ""}`, { method: "POST" });
    await load();
  });

  const startTest = () => run("test", async () => setTest(await api(`/v1/tap/${tenantId}/test/${testMember}`, { method: "POST" })));
  const goLive = () => run("live", async () => setSt(await api(`/v1/tap/${tenantId}/go-live`, { method: "POST" })));
  const pause = () => run("pause", async () => { setSt(await api(`/v1/tap/${tenantId}/pause`, { method: "POST" })); setBills([]); });

  const createBill = () => run("bill", async () => {
    if (!nb.desc.trim()) throw new Error("Say what the bill is for, for example “Beginner lesson”.");
    if (!RAND.test(nb.amount)) throw new Error("Enter the amount in rands, for example 500 or 500.00.");
    const tag = st.staff.find((m) => m.id === nb.member)?.tag;
    if (!tag) throw new Error("Pick a team member who has a tag.");
    const r = await api(`/v1/tap/${tenantId}/bills`, { method: "POST", body: {
      amount_cents: Math.round(Number(nb.amount) * 100),
      description: nb.desc.trim(), staff_id: nb.member, customer_phone: nb.phone.trim() || null } });
    setNewCode(r.bill_code ? `Bill created. If the customer pays from a different phone, give them code ${r.bill_code}.` : "Bill created — the customer can tap now.");
    setNb({ ...nb, desc: "", amount: "", phone: "" });
    await load();
  });
  const getEnrolCode = (m) => run(`enrol-${m.id}`, async () => setEnrol({ id: m.id, ...(await api(`/v1/tap/${tenantId}/members/${m.id}/enrol-code`, { method: "POST" })) }));
  const revokeDevice = (id) => run(`dev-${id}`, async () => {
    if (!window.confirm("Sign this phone out? They'll need a new code to use the app again.")) return;
    await api(`/v1/tap/${tenantId}/devices/${id}/revoke`, { method: "POST" }); await load();
  });

  const setReminders = (n) => run("rem", async () => { await api(`/v1/tap/${tenantId}/setup/reminders`, { method: "PUT", body: { max: n } }); await load(); });
  const remind = (id) => run(`r-${id}`, async () => { await api(`/v1/tap/${tenantId}/bills/${id}/remind`, { method: "POST" }); await load(); setNewCode("Reminder sent."); });
  const closeBill = (id, action, reason) => run(`c-${id}`, async () => {
    if (action === "write_off" && !window.confirm("Write this bill off? Reminders stop and it leaves your unpaid list.")) return;
    await api(`/v1/tap/${tenantId}/bills/${id}/close`, { method: "POST", body: { action, reason } }); setClosing(null); await load();
  });
  const when = (iso) => { try { return new Date(iso).toLocaleString("en-ZA", { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: "Africa/Johannesburg" }); } catch { return ""; } };

  const billAction = (id, action) => run(`b-${id}`, async () => { await api(`/v1/tap/${tenantId}/bills/${id}/${action}`, { method: "POST" }); await load(); });

  if (!st) return <div style={{ padding: 24, color: T.muted }}>{err || "Loading…"}</div>;
  const [tone, label] = MODE_BADGE[st.mode] || MODE_BADGE.off;
  const pfOk = st.payfast.connected;
  const staffWithTag = st.staff.filter((m) => m.tag);

  return (
    <div style={{ maxWidth: 820, margin: "0 auto", padding: 24, display: "flex", flexDirection: "column", gap: 14 }}>
      <SectionTitle sub="Customers tap a tag or scan a QR code, choose a tip, pay with PayFast and get their slip on WhatsApp. No card machine.">
        Tap to Pay <Badge tone={tone} style={{ marginLeft: 8 }}>{label}</Badge>
      </SectionTitle>
      {err && <div role="alert" style={{ background: "rgba(239,68,68,0.1)", color: "var(--danger)", borderRadius: 8, padding: "8px 12px", fontSize: 13 }}>{err}</div>}

      <Step n={1} title="Connect PayFast" done={pfOk}>
        {pfOk && !editPf ? (
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <span style={hint}>Connected · {st.payfast.mode === "test" ? "Test / sandbox keys" : "Live keys"}</span>
            <Button size="sm" variant="soft" onClick={() => setEditPf(true)}>Change keys</Button>
          </div>
        ) : (
          <>
            <div style={{ ...hint, background: T.surfaceAlt, borderRadius: 8, padding: "8px 10px" }}>
              Where to find these: PayFast dashboard → Settings → Integration (merchant ID, merchant key, passphrase).
              Start with your <b>sandbox</b> keys — the test in step 3 will tell you if they work.
            </div>
            <input style={inputStyle} placeholder="Merchant ID" value={pf.merchant_id} onChange={(e) => setPf({ ...pf, merchant_id: e.target.value })} />
            <input style={inputStyle} type="password" placeholder="Merchant key" value={pf.merchant_key} onChange={(e) => setPf({ ...pf, merchant_key: e.target.value })} />
            <input style={inputStyle} type="password" placeholder="Passphrase (the one set in PayFast)" value={pf.passphrase} onChange={(e) => setPf({ ...pf, passphrase: e.target.value })} />
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <select value={pf.mode} onChange={(e) => setPf({ ...pf, mode: e.target.value })} style={{ ...inputStyle, width: "auto" }}>
                <option value="test">Test / sandbox</option><option value="live">Live</option>
              </select>
              <Button size="sm" onClick={connectPayfast} disabled={busy === "pf"}>{busy === "pf" ? "Connecting…" : "Connect PayFast"}</Button>
              {editPf && <Button size="sm" variant="ghost" onClick={() => setEditPf(false)}>Cancel</Button>}
            </div>
            <span style={hint}>Keys are stored encrypted and never shown again.</span>
          </>
        )}
        {!st.whatsapp.connected && <span style={{ ...hint, color: "var(--warn)" }}>WhatsApp isn’t connected yet — connect your number in Settings first.</span>}
      </Step>

      <Step n={2} title="Who gets paid, and their tag" done={staffWithTag.length > 0}>
        {st.staff.length === 0 ? (
          <span style={hint}>Add your coaches or staff under Team (with their WhatsApp number), then come back.</span>
        ) : (
          <>
            <span style={hint}>
              Each person’s share is the % of the <b>bill</b> they keep (the shop keeps the rest). Tips always go 100% to the person who served.
            </span>
            {st.staff.map((m) => (
              <div key={m.id} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", borderTop: `1px solid ${T.border || "rgba(0,0,0,.08)"}`, paddingTop: 10 }}>
                <span style={{ minWidth: 140, fontWeight: 600, color: T.ink }}>{m.name}</span>
                <label style={{ ...hint, display: "flex", alignItems: "center", gap: 6 }}>
                  Share
                  <input style={{ ...inputStyle, width: 70 }} inputMode="decimal" value={shares[m.id] ?? ""} onChange={(e) => setShares({ ...shares, [m.id]: e.target.value })} />%
                </label>
                {m.tag ? (
                  <>
                    <img src={m.tag.qr} alt={`QR code for ${m.name}`} width={72} height={72} style={{ background: "#fff", borderRadius: 6 }} />
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <a href={m.tag.qr} target="_blank" rel="noreferrer" style={{ fontSize: 12.5 }}>Open / print QR</a>
                      <button type="button" style={{ ...hint, background: "none", border: 0, padding: 0, textAlign: "left", cursor: "pointer", textDecoration: "underline" }}
                        onClick={() => navigator.clipboard?.writeText(m.tag.url)}>Copy link (write this to an NFC tag)</button>
                      <button type="button" style={{ ...hint, background: "none", border: 0, padding: 0, textAlign: "left", cursor: "pointer" }}
                        onClick={() => getTag(m.id, true)}>Replace tag…</button>
                    </div>
                  </>
                ) : (
                  <Button size="sm" variant="soft" onClick={() => getTag(m.id)} disabled={busy === `tag-${m.id}`}>Create tag</Button>
                )}
              </div>
            ))}
            <div><Button size="sm" onClick={saveShares} disabled={busy === "shares"}>{busy === "shares" ? "Saving…" : "Save shares"}</Button></div>
          </>
        )}
      </Step>

      {st.staff.length > 0 && (
        <Card style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span style={{ fontWeight: 700, color: T.ink, fontSize: 15 }}>Coach app (optional)</span>
          <span style={hint}>
            Your team can create bills and see payments land live on their own phone with <b>Vula Pay</b>. Give each person a one-time code, then
            send them the app link — they enter their WhatsApp number, the code and choose a PIN. No email or password.
          </span>
          {st.staff.map((m) => (
            <div key={m.id} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ minWidth: 140, fontWeight: 600, color: T.ink }}>{m.name}</span>
              <Button size="sm" variant="soft" style={{ opacity: m.whatsapp ? 1 : 0.5 }} onClick={() => getEnrolCode(m)} disabled={busy === `enrol-${m.id}` || !m.whatsapp}>Get sign-in code</Button>
              {!m.whatsapp && <span style={hint}>Add their WhatsApp number under Team first.</span>}
            </div>
          ))}
          {enrol && (
            <div style={{ background: T.surfaceAlt, borderRadius: 10, padding: 12, display: "flex", flexDirection: "column", gap: 6 }}>
              <span style={hint}>Code for <b>{enrol.name}</b> (valid {enrol.expires_in_minutes} minutes, works once):</span>
              <span style={{ fontSize: 30, fontWeight: 800, letterSpacing: "0.2em", color: T.ink }}>{enrol.code}</span>
              <span style={hint}>App link to send them:</span>
              <a href={enrol.app_url} target="_blank" rel="noreferrer" style={{ fontSize: 13, wordBreak: "break-all" }}>{enrol.app_url}</a>
              <div><Button size="sm" variant="ghost" onClick={() => navigator.clipboard?.writeText(`Vula Pay: ${enrol.app_url}\nYour code: ${enrol.code} (valid ${enrol.expires_in_minutes} min)`)}>Copy message</Button></div>
            </div>
          )}
          {devices.filter((d) => !d.revoked_at).length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
              <span style={hint}>Phones signed in</span>
              {devices.filter((d) => !d.revoked_at).map((d) => {
                const who = st.staff.find((m) => m.id === d.member_id)?.name || "Team member";
                return (
                  <div key={d.id} style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13, color: T.ink }}>
                    <span style={{ flex: 1 }}>{who} · {d.last_seen_at ? `last used ${new Date(d.last_seen_at).toLocaleDateString()}` : "not used yet"}</span>
                    <Button size="sm" variant="ghost" onClick={() => revokeDevice(d.id)}>Sign out</Button>
                  </div>);
              })}
            </div>
          )}
        </Card>
      )}

      <Step n={3} title="Test with a R5 payment" done={st.tested}>
        {st.tested ? (
          <span style={hint}>Test payment received — PayFast is set up correctly.</span>
        ) : (
          <>
            <span style={hint}>Opens the real flow on your phone, end to end. Use your sandbox keys; with live keys it is a genuine R5 you can refund in PayFast. Nothing is booked to anyone’s earnings.</span>
            {st.blockers.filter((b) => !b.includes("tag")).map((b) => <span key={b} style={{ ...hint, color: "var(--warn)" }}>{b}</span>)}
            {st.staff.length > 0 && (
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <select value={testMember} onChange={(e) => setTestMember(e.target.value)} style={{ ...inputStyle, width: "auto" }}>
                  {st.staff.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
                <Button size="sm" style={{ opacity: pfOk && st.whatsapp.connected ? 1 : 0.5 }} onClick={startTest} disabled={busy === "test" || !pfOk || !st.whatsapp.connected}>{busy === "test" ? "Starting…" : "Start test"}</Button>
              </div>
            )}
            {test && (
              <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
                <img src={test.qr} alt="Scan with your phone" width={110} height={110} style={{ background: "#fff", borderRadius: 6 }} />
                <span style={hint}>Scan this with your phone camera (or tap an NFC tag with this link). WhatsApp opens — press Send, choose a tip, pay. This page ticks by itself when the payment is confirmed.</span>
              </div>
            )}
          </>
        )}
      </Step>

      <Step n={4} title="Go live" done={st.mode === "live"}>
        {st.mode === "live" ? (
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <span style={hint}>Customers can tap and pay now.</span>
            <Button size="sm" variant="ghost" onClick={pause} disabled={busy === "pause"}>Pause Tap to Pay</Button>
          </div>
        ) : (
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <Button size="sm" style={{ opacity: st.can_go_live ? 1 : 0.5 }} onClick={goLive} disabled={!st.can_go_live || busy === "live"}>{busy === "live" ? "Going live…" : "Go live"}</Button>
            {!st.can_go_live && <span style={hint}>{st.tested ? st.blockers[0] : "Finish the test payment first."}</span>}
            {st.mode === "testing" && <Button size="sm" variant="ghost" onClick={pause}>Switch off</Button>}
          </div>
        )}
        {st.mode === "live" && st.payfast.mode === "test" && <span style={{ ...hint, color: "var(--warn)" }}>You’re live on <b>sandbox</b> keys — no real money moves. Switch to your live PayFast keys (step 1) when you’re ready.</span>}
      </Step>

      {st.mode !== "off" && (
        <Card style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontWeight: 700, color: T.ink, fontSize: 15 }}>Unpaid bills</span>
            <label style={{ ...hint, display: "flex", gap: 6, alignItems: "center", marginLeft: "auto" }}>
              Reminders per bill
              <select value={st.reminders_max ?? 3} disabled={busy === "rem"} onChange={(e) => setReminders(Number(e.target.value))} style={{ ...inputStyle, width: "auto", padding: "5px 8px" }}>
                <option value={0}>Off</option><option value={1}>1</option><option value={2}>2</option><option value={3}>3</option>
              </select>
            </label>
          </div>
          <span style={hint}>
            If a customer sees their total and leaves, or their payment fails, we send up to {st.reminders_max ?? 3} polite WhatsApp
            reminder{(st.reminders_max ?? 3) === 1 ? "" : "s"} (about 10 minutes later, the next morning, then day 3), only between 08:00 and 20:00, at most one a day.
            They can reply STOP at any time. After the last one the bill waits here for you.
          </span>
          {unpaid.length === 0 && <span style={hint}>Nothing unpaid. Bills a customer walked away from will show up here.</span>}
          {unpaid.map((u) => (
            <div key={u.id} style={{ borderTop: `1px solid ${T.border || "rgba(0,0,0,.08)"}`, paddingTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ fontWeight: 600, color: T.ink }}>{u.description || "Bill"}</span>
                <span style={{ color: T.ink }}>R {(u.total_cents / 100).toFixed(2)}</span>
                {u.customer && <span style={hint}>customer {u.customer}</span>}
                <Badge tone={u.status === "abandoned" ? "warn" : "danger"} style={{ marginLeft: "auto" }}>{u.status === "abandoned" ? "Reminding" : "Needs follow-up"}</Badge>
              </div>
              <span style={hint}>
                {u.opted_out ? "Customer replied STOP — no more reminders." :
                  u.status === "abandoned" ? `Reminder ${u.reminders_sent} of ${u.reminders_max} sent${u.next_reminder_at ? ` · next ${when(u.next_reminder_at)}` : ""}` :
                  `${u.reminders_sent} reminder${u.reminders_sent === 1 ? "" : "s"} sent · no more automatic ones`}
                {u.skipped > 0 && ` · ${u.skipped} couldn't be sent (no approved WhatsApp reminder template yet)`}
              </span>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Button size="sm" variant="soft" style={{ opacity: u.opted_out ? 0.5 : 1 }} disabled={busy === `r-${u.id}` || u.opted_out} onClick={() => remind(u.id)}>Resend link</Button>
                {closing === u.id ? (
                  <>
                    {[["cash", "Cash"], ["eft", "EFT"], ["other", "Other"]].map(([v, l]) => <Button key={v} size="sm" onClick={() => closeBill(u.id, "paid_other", v)}>{l}</Button>)}
                    <Button size="sm" variant="ghost" onClick={() => setClosing(null)}>Cancel</Button>
                  </>
                ) : (
                  <>
                    <Button size="sm" variant="ghost" onClick={() => setClosing(u.id)}>Paid another way</Button>
                    <Button size="sm" variant="ghost" onClick={() => closeBill(u.id, "release")}>Release</Button>
                    <Button size="sm" variant="ghost" onClick={() => closeBill(u.id, "write_off")}>Write off</Button>
                  </>
                )}
              </div>
            </div>
          ))}
        </Card>
      )}

      {st.mode !== "off" && (
        <Card style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span style={{ fontWeight: 700, color: T.ink, fontSize: 15 }}>Create a bill</span>
          <span style={hint}>The customer taps the tag (or scans the QR), and the first phone to tap gets this bill.</span>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <select value={nb.member} onChange={(e) => setNb({ ...nb, member: e.target.value })} style={{ ...inputStyle, width: "auto" }}>
              <option value="">Who served?</option>
              {staffWithTag.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
            <input style={{ ...inputStyle, flex: "1 1 180px" }} placeholder="What for? e.g. Beginner lesson" value={nb.desc} onChange={(e) => setNb({ ...nb, desc: e.target.value })} />
            <input style={{ ...inputStyle, width: 110 }} inputMode="decimal" placeholder="Rands" value={nb.amount} onChange={(e) => setNb({ ...nb, amount: e.target.value })} />
            <input style={{ ...inputStyle, flex: "1 1 160px" }} inputMode="tel" placeholder="Customer WhatsApp (optional)" value={nb.phone} onChange={(e) => setNb({ ...nb, phone: e.target.value })} />
            <Button size="sm" onClick={createBill} disabled={busy === "bill"}>{busy === "bill" ? "Creating…" : "Create bill"}</Button>
          </div>
          {newCode && <span style={{ ...hint, color: "var(--ok)" }}>{newCode}</span>}
          {bills.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
              {bills.map((b) => (
                <div key={b.id} style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13, color: T.ink }}>
                  <span style={{ flex: 1 }}>{b.description}{b.is_test && !/test/i.test(b.description) ? " (test)" : ""}</span>
                  <span>R {(b.subtotal_cents / 100).toFixed(2)}</span>
                  <Badge tone={STATUS_TONE[b.status] || "muted"}>{b.status}</Badge>
                  {b.status === "claimed" && <Button size="sm" variant="ghost" onClick={() => billAction(b.id, "release")}>Release</Button>}
                  {(b.status === "open" || b.status === "claimed") && <Button size="sm" variant="ghost" onClick={() => billAction(b.id, "cancel")}>Cancel</Button>}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
