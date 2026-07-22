/**
 * useSectionTabs — the one "parent nav item -> secondary tab strip" primitive used everywhere a
 * section needs depth (Master's Tenants/Onboard/Health/Usage/Users/Audit; merchant Money/
 * Marketing/Operate/Estimating). Generalizes what was previously hand-rolled once, inline, in
 * VulaMasterPanel.jsx.
 *
 * Uncontrolled by default (owns its own active-tab state) — pass `active`/`onChange` to make it
 * externally controlled instead, the same convention VulaMerchantAdmin already uses for its own
 * top-level `activeTab`/`onTabChange`. That's what lets a parent (e.g. App.jsx, for the "return to
 * the same Master sub-tab" fix) lift this state up when it needs to survive across a bigger
 * navigation change.
 */
import { useEffect, useState } from "react";

/**
 * @param {Array<{id:string, label:string, icon?:string, visible?:boolean}>} tabs
 * @param {{defaultTabId?:string, active?:string, onChange?:(id:string)=>void}} [opts]
 */
export function useSectionTabs(tabs, { defaultTabId, active: externalActive, onChange: externalOnChange } = {}) {
  const visibleTabs = (tabs || []).filter((t) => t.visible !== false);
  const fallbackId =
    (defaultTabId && visibleTabs.some((t) => t.id === defaultTabId) ? defaultTabId : visibleTabs[0]?.id) || null;

  const [internalActive, setInternalActive] = useState(fallbackId);
  const controlled = externalActive !== undefined;
  const active = controlled ? externalActive : internalActive;
  const setActive = controlled ? externalOnChange : setInternalActive;

  // Graceful collapse: if the active tab drops out of visibility (module gating changed under us
  // — a new tenant selected, staff access changed), fall back to a still-visible tab instead of
  // rendering nothing.
  useEffect(() => {
    if (active && !visibleTabs.some((t) => t.id === active)) setActive(fallbackId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabs]);

  return { tabs: visibleTabs, active, setActive, isEmpty: visibleTabs.length === 0 };
}
