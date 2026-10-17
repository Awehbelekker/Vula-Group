/**
 * useBreakpoint — which layout the screen is in (2026-10-17, UX redesign): "mobile" under
 * 768px, "tablet" 768–1199px, "desktop" from 1200px. The redesigned shell and the shared
 * components (DataTable's stacked rows, Sheet's bottom-sheet-or-side-panel) switch on this;
 * plain styling should still prefer CSS media queries at the same widths.
 */
import { useEffect, useState } from "react";

export const BREAKPOINTS = { tablet: 768, desktop: 1200 };

function current() {
  if (typeof window === "undefined" || !window.matchMedia) return "desktop";
  if (window.matchMedia(`(min-width: ${BREAKPOINTS.desktop}px)`).matches) return "desktop";
  if (window.matchMedia(`(min-width: ${BREAKPOINTS.tablet}px)`).matches) return "tablet";
  return "mobile";
}

export function useBreakpoint() {
  const [bp, setBp] = useState(current);
  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const queries = [BREAKPOINTS.tablet, BREAKPOINTS.desktop].map((w) => window.matchMedia(`(min-width: ${w}px)`));
    const onChange = () => setBp(current());
    queries.forEach((q) => q.addEventListener("change", onChange));
    return () => queries.forEach((q) => q.removeEventListener("change", onChange));
  }, []);
  return bp;
}
