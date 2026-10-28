/**
 * useTapToPay.js — Money › Tap to Pay, shared by VulaTapToPay (current look) and
 * taptopay/TapToPayV2 (the redesign, 2026-10-20). Moved verbatim: connect PayFast, each team
 * member's share and NFC / QR tag, the coach app sign-in codes and phones, the R5 test payment
 * (polled until confirmed), go live / pause (locked until a real test payment), unpaid bills with
 * reminders, and creating a bill. Amounts typed in rands become integer cents here; the server
 * computes every split. Replacing a tag, signing a phone out and writing a bill off ask first.
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { VULA_API } from "../../lib/authFetch";

export const MODE_BADGE = { off: ["muted", "Off"], testing: ["warn", "Testing"], live: ["ok", "Live"] };
export const STATUS_TONE = { open: "muted", claimed: "warn", paid: "ok", cancelled: "muted", expired: "muted" };
export const RAND = /^\d{1,7}(\.\d{1,2})?$/;

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

export function useTapToPay(tenantId) {
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
  const [tabs, setTabs] = useState(null);           // {enabled, idle_hours, tabs:[…]} (bar tabs)
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
        // open tabs are managed in the Tabs card, not as bills
        setBills((b.bills || []).filter((x) => !(x.bill_type === "tab" && (x.status === "open" || x.status === "claimed"))));
        api(`/v1/tap/${tenantId}/tabs`).then(setTabs).catch(() => {});
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
    try { return await fn(); } catch (e) { setErr(e.message); } finally { setBusy(""); }
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

  // confirm: window.confirm in the current look, the redesign's confirmDialog in TapToPayV2
  const getTag = (id, rotate = false, confirm = (m) => window.confirm(m)) => run(`tag-${id}`, async () => {
    if (rotate && !(await confirm("Make a new tag? The old tag and QR code stop working straight away."))) return;
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
  const revokeDevice = (id, confirm = (m) => window.confirm(m)) => run(`dev-${id}`, async () => {
    if (!(await confirm("Sign this phone out? They'll need a new code to use the app again."))) return;
    await api(`/v1/tap/${tenantId}/devices/${id}/revoke`, { method: "POST" }); await load();
  });

  const setReminders = (n) => run("rem", async () => { await api(`/v1/tap/${tenantId}/setup/reminders`, { method: "PUT", body: { max: n } }); await load(); });
  const remind = (id) => run(`r-${id}`, async () => { await api(`/v1/tap/${tenantId}/bills/${id}/remind`, { method: "POST" }); await load(); setNewCode("Reminder sent."); });
  const closeBill = (id, action, reason, confirm = (m) => window.confirm(m)) => run(`c-${id}`, async () => {
    if (action === "write_off" && !(await confirm("Write this bill off? Reminders stop and it leaves your unpaid list."))) return;
    await api(`/v1/tap/${tenantId}/bills/${id}/close`, { method: "POST", body: { action, reason } }); setClosing(null); await load();
  });
  const when = (iso) => { try { return new Date(iso).toLocaleString("en-ZA", { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: "Africa/Johannesburg" }); } catch { return ""; } };

  // bar tabs: the owner switches them on; customers open one by tapping a tag; staff add items
  const setTabsOn = (enabled, idle_hours) => run("tabs", async () => {
    await api(`/v1/tap/${tenantId}/tabs/settings`, { method: "PUT", body: { enabled, idle_hours } });
    setTabs(await api(`/v1/tap/${tenantId}/tabs`));
  });
  const reloadTabs = () => run("tabs-load", async () => setTabs(await api(`/v1/tap/${tenantId}/tabs`)));
  const addTabItem = (id, item) => run(`t-${id}`, async () => {
    if (!item.desc.trim()) throw new Error("What did they order?");
    const qty = Number(item.qty);
    if (!Number.isInteger(qty) || qty < 1 || qty > 99) throw new Error("Quantity is 1 to 99.");
    if (!RAND.test(item.price.trim())) throw new Error("Enter the price in rands, for example 45 or 45.50.");
    await api(`/v1/tap/${tenantId}/tabs/${id}/items`, { method: "POST", body: {
      description: item.desc.trim(), quantity: qty, unit_cents: Math.round(Number(item.price.trim()) * 100) } });
    setTabs(await api(`/v1/tap/${tenantId}/tabs`));
    return true;
  });
  const voidTabItem = (id, itemId, confirm = (m) => window.confirm(m)) => run(`t-${id}`, async () => {
    if (!(await confirm("Remove this item from the tab?"))) return;
    await api(`/v1/tap/${tenantId}/tabs/${id}/items/${itemId}/void`, { method: "POST" });
    setTabs(await api(`/v1/tap/${tenantId}/tabs`));
  });
  const closeEmptyTab = (id) => run(`t-${id}`, async () => {
    await api(`/v1/tap/${tenantId}/tabs/${id}/close`, { method: "POST" });
    setTabs(await api(`/v1/tap/${tenantId}/tabs`));
  });

  const billAction = (id, action) => run(`b-${id}`, async () => { await api(`/v1/tap/${tenantId}/bills/${id}/${action}`, { method: "POST" }); await load(); });

  return { st, err, busy, shares, setShares, pf, setPf, editPf, setEditPf, test, testMember, setTestMember, bills, nb, setNb, newCode, enrol, devices, unpaid, closing, setClosing, connectPayfast, saveShares, getTag, startTest, goLive, pause, createBill, getEnrolCode, revokeDevice, setReminders, remind, closeBill, when, billAction, tabs, setTabsOn, reloadTabs, addTabItem, voidTabItem, closeEmptyTab };
}
