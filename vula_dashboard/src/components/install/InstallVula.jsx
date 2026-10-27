/**
 * InstallVula.jsx — "Install Vula" (2026-10-27, PWA pass 2). Where the browser offers its own
 * install pop-up (Chrome, Edge, Samsung Internet: lib/install.js keeps the offer), the button
 * opens that. Where it doesn't (every iPhone, or a browser that already used its offer), a sheet
 * shows the few taps it takes on that phone.
 *
 *   startInstall(install)   the button's action, from any menu
 *   <InstallSteps />        the steps sheet; mounted once (main.jsx)
 *   <InstallCard />         the one-time card on the phone's Home
 */
import { useEffect, useState } from "react";
import { Download, EllipsisVertical, Share, SquarePlus } from "lucide-react";
import { useInstall } from "../../lib/install";
import { Button, Card, Sheet } from "../ui/index.jsx";

const STEPS_EVENT = "vula:install-steps";

export async function startInstall(install) {
  if (install.canPrompt && (await install.prompt()) !== "unavailable") return;
  window.dispatchEvent(new Event(STEPS_EVENT));
}

const STEPS = {
  ios: {
    intro: "On iPhone and iPad, Vula goes on your home screen from Safari’s Share button:",
    items: [
      [Share, <>Tap <b>Share</b> (the square with an arrow) at the bottom of Safari — at the top on an iPad.</>],
      [SquarePlus, <>Scroll down and tap <b>Add to Home Screen</b>.</>],
      [Download, <>Tap <b>Add</b>. Vula is now on your home screen, with your business’s logo.</>],
    ],
    note: "In Chrome on iPhone, the Share button is at the top, next to the address.",
  },
  android: {
    intro: "Vula goes on your home screen from the browser’s menu:",
    items: [
      [EllipsisVertical, <>Tap the browser’s menu <b>⋮</b> at the top right.</>],
      [Download, <>Tap <b>Install app</b> or <b>Add to Home screen</b>.</>],
      [SquarePlus, <>Tap <b>Install</b>. Vula is now on your home screen, with your business’s logo.</>],
    ],
  },
  desktop: {
    intro: "In Chrome or Edge on a computer:",
    items: [
      [Download, <>Click the <b>install</b> icon at the right of the address bar (or the menu ⋮ › <b>Install Vula</b>).</>],
      [SquarePlus, <>Click <b>Install</b>. Vula opens in its own window, and from your Start menu or Dock.</>],
    ],
    note: "Safari and Firefox on a computer don’t install apps — use Chrome or Edge.",
  },
};

export function InstallSteps() {
  const [open, setOpen] = useState(false);
  const install = useInstall();
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener(STEPS_EVENT, show);
    return () => window.removeEventListener(STEPS_EVENT, show);
  }, []);
  const s = STEPS[install.platform] || STEPS.desktop;
  return (
    <Sheet open={open} title="Put Vula on your home screen" onClose={() => setOpen(false)}
      footer={<Button onClick={() => setOpen(false)}>Done</Button>}>
      <p className="vui-install-intro">{s.intro}</p>
      <ol className="vui-install-steps" aria-label="How to install Vula">
        {s.items.map(([Icon, text], i) => (
          <li key={i}><Icon size={20} aria-hidden="true" /><span>{text}</span></li>))}
      </ol>
      {s.note && <p className="vui-install-note">{s.note}</p>}
    </Sheet>
  );
}

/** The one-time Home card: phones only, not when Vula is already installed, gone for 30 days on "Not now". */
export function InstallCard() {
  const install = useInstall();
  if (install.installed || install.dismissed || install.platform === "desktop") return null;
  return (
    <Card className="vui-install-card" role="region" aria-label="Install Vula">
      <div className="vui-install-card-text">
        <strong>Put Vula on your home screen</strong>
        <span>It opens like an app, next to WhatsApp, and keeps working when the signal drops.</span>
      </div>
      <div className="vui-install-card-actions">
        <Button Icon={Download} onClick={() => startInstall(install)}>Install Vula</Button>
        <Button variant="ghost" onClick={install.dismiss}>Not now</Button>
      </div>
    </Card>
  );
}
