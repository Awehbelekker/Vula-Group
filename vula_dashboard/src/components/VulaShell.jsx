/**
 * VulaShell.jsx — the app shell (UI overhaul Phase 2): left sidebar with grouped navigation,
 * tenant/master brand header, top bar, content area. Replaces the horizontal tab strips.
 *
 * Pure presentation: gating is done by the caller (App.jsx filters groups via navConfig +
 * module/access predicates) so this stays reusable for both the merchant and master shells.
 * Styles live in index.css (.vshell-*) so media queries + tokens (var(--accent) etc.) apply.
 */
import { useState } from "react";

export default function VulaShell({
  brand,          // { logoUrl?, logoEmoji?, name, sub }
  groups,         // [{label, items:[{id, icon, label, badge?}]}] — pre-filtered
  zones,          // optional: [{id, label, groups}] — a segmented switch above the nav, each
                   // with its own `groups` shape; when present, `groups`/above is ignored and the
                   // active zone's groups render instead (Master's Platform Ops vs Vula's Business)
  activeZoneId,
  onZoneChange,
  activeId,
  onSelect,
  title,          // top-bar heading (defaults to active item's label)
  userEmail,
  roleLabel,      // e.g. "master" — shown next to the email
  onLogout,
  headerExtra,    // e.g. the master tenant switcher <select>
  children,
}) {
  const [open, setOpen] = useState(false); // mobile drawer state
  const activeZone = zones?.find((z) => z.id === activeZoneId) || zones?.[0];
  const renderedGroups = zones ? (activeZone?.groups || []) : groups;

  return (
    <div className="vshell">
      <button className="vshell-burger" aria-label="Menu" onClick={() => setOpen(o => !o)}>☰</button>
      {open && <div className="vshell-scrim" onClick={() => setOpen(false)} />}

      <aside className={"vshell-side" + (open ? " open" : "")}>
        <div className="vshell-brand">
          {brand?.logoUrl
            ? <img src={brand.logoUrl} alt={brand.name} className="vshell-logoimg" />
            : <div className="vshell-logo">{brand?.logoEmoji || "◆"}</div>}
          <div className="vshell-brandtext">
            <b>{brand?.name}</b>
            {brand?.sub && <span>{brand.sub}</span>}
          </div>
        </div>
        {zones && (
          <div className="vshell-zone-switch">
            {zones.map((z) => (
              <button key={z.id} className={"vshell-zone-btn" + (z.id === activeZone?.id ? " on" : "")}
                onClick={() => onZoneChange?.(z.id)}>
                {z.label}
              </button>
            ))}
          </div>
        )}
        <nav className="vshell-nav">
          {renderedGroups.map((g, gi) => (
            <div key={gi}>
              {g.label && <div className="vshell-grp">{g.label}</div>}
              {g.items.map(it => (
                <button
                  key={it.id}
                  className={"vshell-it" + (activeId === it.id ? " on" : "")}
                  onClick={() => { onSelect(it.id); setOpen(false); }}
                  title={it.label}
                >
                  <span className="vshell-ic">{it.icon}</span>
                  <span className="vshell-lb">{it.label}</span>
                  {it.badge ? <span className="vshell-badge">{it.badge}</span> : null}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="vshell-powered">Powered by <b>Vula</b></div>
      </aside>

      <div className="vshell-main">
        <div className="vshell-top">
          <h1>{title}</h1>
          <div className="vshell-topright">
            {headerExtra}
            <span className="vshell-user">{userEmail}{roleLabel ? ` · ${roleLabel}` : ""}</span>
            <button className="vshell-signout" onClick={onLogout}>Sign out</button>
          </div>
        </div>
        <div className="vshell-content">{children}</div>
      </div>
    </div>
  );
}
