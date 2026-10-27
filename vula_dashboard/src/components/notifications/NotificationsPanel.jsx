/**
 * NotificationsPanel.jsx — notifications on this phone or computer (2026-10-27, PWA pass 3).
 * They're the alerts this person already gets on WhatsApp (new paid orders, a customer waiting,
 * approvals …): the same choices, changed in Team or by WhatsApp. Turning them on here only adds
 * this device. lib/push.js does the work; public/sw.js shows them.
 *
 *   <NotificationsPanel tenantId />   Settings › On this device › Notifications
 *   <NotificationsSheet />            the same in a sheet, from the menus (everyone has those)
 *   openNotifications()               opens the sheet
 */
import { useCallback, useEffect, useState } from "react";
import { Bell, BellOff, Download, Send } from "lucide-react";
import * as push from "../../lib/push";
import { chimeOn, playAlertChime, setChimeOn } from "../../lib/alertChime";
import { useInstall } from "../../lib/install";
import { useAuthStore } from "../../store/auth";
import { startInstall } from "../install/InstallVula.jsx";
import { Button, Card, Sheet } from "../ui/index.jsx";
import "../settings/settings.css";

const OPEN_EVENT = "vula:notifications";
export const openNotifications = () => window.dispatchEvent(new Event(OPEN_EVENT));

export function NotificationsPanel({ tenantId }) {
  const install = useInstall();
  const [state, setState] = useState("checking");   // checking | unsupported | install | off | on | blocked | unavailable
  const [about, setAbout] = useState(null);          // {public_key, enabled, alerts}
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [chime, setChime] = useState(chimeOn());

  const check = useCallback(async () => {
    if (!push.supported()) return setState(push.needsInstallFirst() ? "install" : "unsupported");
    if (push.needsInstallFirst()) return setState("install");
    try {
      const a = await push.info(tenantId);
      setAbout(a);
      if (!a.enabled || !a.public_key) return setState("unavailable");
      if (push.permission() === "denied") return setState("blocked");
      setState((await push.currentSubscription()) ? "on" : "off");
    } catch {
      setState("unavailable");
    }
  }, [tenantId]);
  useEffect(() => { check(); }, [check, install.installed]);

  const on = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await push.turnOn(tenantId, about.public_key);
      setState(r === "blocked" ? "blocked" : "on");
      if (r === "on") setMsg("Notifications are on for this device.");
    } catch (e) {
      setMsg(e.message || "Couldn't turn notifications on.");
    } finally { setBusy(false); }
  };
  const off = async () => {
    setBusy(true); setMsg(null);
    await push.turnOff(tenantId);
    setBusy(false); setState("off"); setMsg("Notifications are off for this device.");
  };
  const test = async () => {
    setBusy(true); setMsg(null);
    const sent = await push.sendTest(tenantId).catch(() => false);
    setBusy(false);
    setMsg(sent ? "Sent — it should pop up in a moment." : "That didn't go through. Turn notifications off and on again.");
  };

  const alerts = about?.alerts || [];
  return (
    <Card className="vst-card vui-push">
      {state === "checking" && <p className="vst-sub">Checking this device…</p>}
      {state === "unsupported" && <p className="vst-body">This browser can’t show notifications. You still get every alert on WhatsApp.</p>}
      {state === "unavailable" && <p className="vst-body">Notifications aren’t available just now. You still get every alert on WhatsApp.</p>}
      {state === "install" && (
        <>
          <p className="vst-body">On iPhone and iPad, notifications work once Vula is on your home screen. Put it there, open Vula from the home screen, then come back here.</p>
          <div className="vst-row"><Button Icon={Download} onClick={() => startInstall(install)}>Install Vula</Button></div>
        </>)}
      {state === "blocked" && (
        <p className="vst-body">Notifications are blocked for Vula on this device. Allow them in your browser’s or phone’s settings for this site, then come back here.</p>)}
      {(state === "off" || state === "on") && (
        <>
          <p className="vst-body">{state === "on" ? "Notifications are on for this device." : "Get your Vula alerts as notifications on this device, as well as on WhatsApp."}</p>
          {alerts.length > 0 ? (
            <ul className="vcn-steps" aria-label="Alerts you get">
              {alerts.map((a) => <li key={a}>{a}</li>)}
            </ul>
          ) : (
            <p className="vst-sub">You don’t get any alerts yet. Your business owner chooses them for you in Team.</p>)}
          <p className="vst-sub">They’re the alerts you get on WhatsApp. To change them, ask your owner to update Team, or reply on WhatsApp, e.g. “stop low stock alerts”.</p>
          <label className="vui-push-chime">
            <input type="checkbox" checked={chime}
              onChange={(e) => { setChimeOn(e.target.checked); setChime(e.target.checked); if (e.target.checked) playAlertChime(); }} />
            <span>Play a chime when an alert arrives while Vula is open on this screen</span>
          </label>
          <div className="vst-row">
            {state === "off"
              ? <Button Icon={Bell} onClick={on} disabled={busy}>{busy ? "Turning on…" : "Turn on notifications"}</Button>
              : <>
                  <Button variant="secondary" Icon={Send} onClick={test} disabled={busy}>Send a test</Button>
                  <Button variant="quiet" Icon={BellOff} onClick={off} disabled={busy}>Turn off</Button>
                </>}
          </div>
        </>)}
      {msg && <p className="vst-msg" role="status">{msg}</p>}
    </Card>
  );
}

export function NotificationsSheet() {
  const [open, setOpen] = useState(false);
  const { tenantId, role } = useAuthStore();
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener(OPEN_EVENT, show);
    return () => window.removeEventListener(OPEN_EVENT, show);
  }, []);
  if (!tenantId || role === "master") return null;
  return (
    <Sheet open={open} title="Notifications" onClose={() => setOpen(false)}>
      {open && <NotificationsPanel tenantId={tenantId} />}
    </Sheet>
  );
}
