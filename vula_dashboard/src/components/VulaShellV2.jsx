/**
 * VulaShellV2.jsx — the redesigned app shell (2026-10-17, UX redesign Phase D). VulaShell
 * renders this instead of its own layout when App passes v2 (lib/flags.js: the VITE_UI_V2
 * build + the tenant's ui_v2). Same props as VulaShell, plus a few optional ones; gating is
 * still the caller's (groups arrive filtered by navConfig.merchantVisible/filterGroups).
 *
 *   desktop ≥1200px  248px sidebar (Home/Inbox/Assistant, Front house, Back house, Admin, My Work),
 *                    top bar with search (command palette), inbox, theme, user menu
 *   tablet 768–1199  72px icon rail with tooltips; the menu button opens the full sidebar
 *   mobile <768      top bar (brand, inbox) + floating toolbar: Search, Add, Inbox, More
 *                    (More = the whole grouped nav in a sheet; no burger)
 * "Powered by Vula" appears once: the sidebar foot, or the More sheet on a phone.
 */
import { useEffect, useMemo, useState } from "react";
import { Inbox, LogOut, Menu, Moon, PanelLeftOpen, Plus, Search, Sun, SunMoon, X,
  FilePlus2, Wallet, ScanLine, UserPlus, Megaphone } from "lucide-react";
import { getThemeMode, setThemeMode, DARK_MODE_READY } from "../theme/tokens";
import { iconFor, houseGroups, flattenNav, ADD_ACTIONS } from "../navConfig";
import { useBreakpoint } from "../hooks/useBreakpoint";
import { IconButton, Sheet, ListRow, CommandPalette, useCommandPaletteHotkey, FloatingToolbar } from "./ui/index.jsx";
import "./VulaShellV2.css";

const ADD_ICONS = { quote: FilePlus2, expense: Wallet, scan: ScanLine, contact: UserPlus, broadcast: Megaphone };
const MODES = [["auto", "Theme: follows your device", SunMoon], ["light", "Theme: light", Sun], ["dark", "Theme: dark", Moon]];

function BrandChip({ brand, compact }) {
  return (
    <div className="v2-brand">
      {brand?.logoUrl
        ? <img src={brand.logoUrl} alt="" className="v2-brand-logo" />
        : <span className="v2-brand-mark" aria-hidden="true">{brand?.logoEmoji || "V"}</span>}
      {!compact && (
        <span className="v2-brand-text">
          <b>{brand?.name}</b>
          {brand?.sub && <span>{brand.sub}</span>}
        </span>)}
    </div>
  );
}

function NavList({ groups, activeId, onSelect, rail }) {
  return (
    <nav className="v2-nav" aria-label="Main">
      {groups.map((g, gi) => (
        <div key={gi} className="v2-nav-group">
          {g.label && (rail ? <hr className="v2-nav-sep" aria-hidden="true" /> : <div className="v2-nav-label">{g.label}</div>)}
          {g.items.map((it) => {
            const Icon = iconFor(it.id);
            const current = activeId === it.id;
            return (
              <button key={it.id} type="button" className={`v2-nav-item${current ? " on" : ""}`} onClick={() => onSelect(it.id)}
                aria-current={current ? "page" : undefined} title={rail ? it.label : undefined} aria-label={rail ? it.label : undefined}>
                <span className="v2-nav-ic" aria-hidden="true">{Icon ? <Icon size={20} strokeWidth={1.75} /> : it.icon}</span>
                {!rail && <span className="v2-nav-lb">{it.label}</span>}
                {it.badge ? <span className={`v2-nav-badge${rail ? " dot" : ""}`} aria-label={`${it.badge} waiting`}>{rail ? "" : it.badge}</span> : null}
              </button>
            );
          })}
        </div>))}
    </nav>
  );
}

function ThemeButton() {
  const [mode, setMode] = useState(getThemeMode());
  const i = Math.max(0, MODES.findIndex(([m]) => m === mode));
  const next = MODES[(i + 1) % MODES.length][0];
  return <IconButton Icon={MODES[i][2]} label={`${MODES[i][1]} — switch`} quiet onClick={() => { setThemeMode(next); setMode(next); }} />;
}

function UserMenu({ userEmail, roleLabel, onLogout }) {
  const [open, setOpen] = useState(false);
  const initial = (userEmail || "?")[0].toUpperCase();
  return (
    <div className="v2-user">
      <button type="button" className="v2-user-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        aria-label={`Account: ${userEmail}`}>{initial}</button>
      {open && (
        <>
          <div className="v2-menu-scrim" onClick={() => setOpen(false)} aria-hidden="true" />
          <div className="v2-menu" role="menu">
            <div className="v2-menu-who">{userEmail}{roleLabel ? <span> · {roleLabel}</span> : null}</div>
            <button type="button" role="menuitem" className="v2-menu-item" onClick={onLogout}>
              <LogOut size={18} strokeWidth={1.75} aria-hidden="true" /> Sign out
            </button>
          </div>
        </>)}
    </div>
  );
}

export default function VulaShellV2({
  brand, groups, zones, activeZoneId, onZoneChange, activeId, onSelect, title, userEmail, roleLabel,
  onLogout, headerExtra, children,
  onNavigate,       // (leafId) — open a page, including a section's sub-tab (palette, Add)
  onLaunch,         // (key) — open a Quick Launcher overlay ("assistant", "scanner")
  searchContacts,   // optional async (q) => [{ id, label, detail }] for the palette
}) {
  const bp = useBreakpoint();
  const [railOpen, setRailOpen] = useState(false);
  const [more, setMore] = useState(false);
  const [add, setAdd] = useState(false);
  const [palette, setPalette] = useState(false);
  useCommandPaletteHotkey(setPalette);
  useEffect(() => {
    if (!railOpen) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setRailOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [railOpen]);

  const activeZone = zones?.find((z) => z.id === activeZoneId) || zones?.[0];
  const baseGroups = zones ? (activeZone?.groups || []) : groups;
  const navGroups = useMemo(() => (zones ? baseGroups : houseGroups(baseGroups)), [zones, baseGroups]);
  const pages = useMemo(() => flattenNav(baseGroups), [baseGroups]);
  const has = (id) => pages.some((p) => p.id === id);
  const go = (id) => (onNavigate ? onNavigate(id) : onSelect(id));
  const inboxItem = pages.find((p) => p.id === "inbox");
  const inboxCount = baseGroups.flatMap((g) => g.items).find((it) => it.id === "inbox")?.badge;

  const addActions = ADD_ACTIONS
    .map((a) => ({ ...a, target: has(a.tab) ? a.tab : a.alsoTab && has(a.alsoTab) ? a.alsoTab : null }))
    .filter((a) => a.target);
  const runAdd = (a) => { setAdd(false); if (a.launch && onLaunch) onLaunch(a.launch); else go(a.target); };

  const paletteSources = [
    { group: "Pages", items: pages.map((p) => ({ id: p.id, label: p.label, detail: p.sectionLabel || undefined,
      Icon: iconFor(p.id) || undefined, keywords: [p.sectionLabel], onSelect: () => go(p.id) })) },
    ...(addActions.length ? [{ group: "Quick actions", items: addActions.map((a) => ({ id: `add-${a.key}`, label: a.label,
      Icon: ADD_ICONS[a.key], onSelect: () => runAdd(a) })) }] : []),
  ];
  const search = searchContacts ? async (q) => {
    const rows = await searchContacts(q);
    return rows?.length ? [{ group: "Contacts", items: rows.map((r) => ({ ...r, Icon: UserPlus, onSelect: () => go(r.page || "customers") })) }] : [];
  } : undefined;
  const ask = onLaunch ? () => onLaunch("assistant") : undefined;

  const zoneSwitch = zones && (
    <div className="v2-zones" role="group" aria-label="Workspace">
      {zones.map((z) => (
        <button key={z.id} type="button" className={`v2-zone${z.id === activeZone?.id ? " on" : ""}`} aria-pressed={z.id === activeZone?.id}
          onClick={() => onZoneChange?.(z.id)}>{z.label}</button>))}
    </div>
  );
  const select = (id) => { onSelect(id); setRailOpen(false); setMore(false); };
  const powered = <div className="v2-powered">Powered by <b>Vula</b></div>;

  const sidebar = (rail, onClose) => (
    <aside className={`v2-side${rail ? " rail" : ""}`}>
      <div className="v2-side-head">
        {rail
          ? <IconButton Icon={PanelLeftOpen} label="Open the menu" quiet onClick={() => setRailOpen(true)} />
          : <BrandChip brand={brand} />}
        {onClose && <IconButton Icon={X} label="Close the menu" quiet onClick={onClose} />}
      </div>
      {!rail && zoneSwitch}
      <NavList groups={navGroups} activeId={activeId} onSelect={select} rail={rail} />
      {!rail && powered}
    </aside>
  );

  const mobile = bp === "mobile";
  return (
    <div className={`v2-shell v2-shell--${bp}${zones ? ` v2-shell--zone-${activeZone?.id}` : ""}`}>
      {bp === "desktop" && sidebar(false)}
      {bp === "tablet" && sidebar(true)}
      {bp === "tablet" && railOpen && (
        <>
          <div className="vui-scrim" onClick={() => setRailOpen(false)} aria-hidden="true" />
          <div className="v2-drawer" role="dialog" aria-modal="true" aria-label="Menu">
            {sidebar(false, () => setRailOpen(false))}
          </div>
        </>)}

      <div className="v2-main">
        <header className="v2-top">
          {mobile && <BrandChip brand={brand} compact />}
          <h1 className="v2-title">{title}</h1>
          <div className="v2-top-right">
            {headerExtra}
            {!mobile && (
              <button type="button" className="v2-search" onClick={() => setPalette(true)}>
                <Search size={18} strokeWidth={1.75} aria-hidden="true" /><span>Search</span>
                <span className="vui-kbd" aria-hidden="true">Ctrl K</span>
              </button>)}
            {inboxItem && <IconButton Icon={Inbox} label="Inbox" count={Number(String(inboxCount || "").replace("+", "")) || 0}
              quiet onClick={() => select("inbox")} />}
            {!mobile && DARK_MODE_READY && <ThemeButton />}
            {!mobile && <UserMenu userEmail={userEmail} roleLabel={roleLabel} onLogout={onLogout} />}
          </div>
        </header>
        <main className="v2-content">
          {children}
          {mobile && <div className="vui-toolbar-pad" />}
        </main>
      </div>

      {mobile && (
        <FloatingToolbar items={[
          { id: "search", label: "Search", Icon: Search, onClick: () => setPalette(true) },
          ...(addActions.length ? [{ id: "add", label: "Add", Icon: Plus, onClick: () => setAdd(true), primary: true }] : []),
          ...(inboxItem ? [{ id: "inbox", label: "Inbox", Icon: Inbox, onClick: () => select("inbox"),
            count: Number(String(inboxCount || "").replace("+", "")) || 0, current: activeId === "inbox" }] : []),
          { id: "more", label: "More", Icon: Menu, onClick: () => setMore(true) },
        ]} />)}

      <Sheet open={more} title="Menu" onClose={() => setMore(false)}>
        {zoneSwitch}
        {navGroups.map((g, gi) => (
          <div key={gi} className="v2-sheet-group">
            {g.label && <div className="v2-nav-label">{g.label}</div>}
            {g.items.map((it) => (
              <ListRow key={it.id} Icon={iconFor(it.id) || undefined} title={it.label}
                trailing={it.badge ? <span className="v2-nav-badge">{it.badge}</span> : undefined}
                aria-current={activeId === it.id ? "page" : undefined} onClick={() => select(it.id)} />))}
          </div>))}
        <div className="v2-sheet-group">
          <div className="v2-nav-label">You</div>
          <ListRow Icon={LogOut} title="Sign out" detail={`${userEmail || ""}${roleLabel ? ` · ${roleLabel}` : ""}`} onClick={onLogout} />
          {DARK_MODE_READY && <div style={{ padding: "8px 12px" }}><ThemeButton /></div>}
        </div>
        {powered}
      </Sheet>

      <Sheet open={add} title="Add" onClose={() => setAdd(false)}>
        {addActions.map((a) => <ListRow key={a.key} Icon={ADD_ICONS[a.key]} title={a.label} detail={a.detail} onClick={() => runAdd(a)} />)}
      </Sheet>

      <CommandPalette open={palette} onClose={() => setPalette(false)} sources={paletteSources} search={search}
        onAsk={ask} placeholder={ask ? "Search or ask Vula…" : "Search"} />
    </div>
  );
}

