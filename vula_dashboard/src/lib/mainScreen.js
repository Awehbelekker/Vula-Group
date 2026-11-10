/**
 * mainScreen.js — the screen a business works in most, on the phone bar (10 Nov 2026).
 *
 * The phone bar had Search, Add, Today, Inbox and More, so Invoices was three taps away
 * (More › Money › Invoices). DIGG's owner couldn't find it on her phone at all. Every business
 * that invoices must be able to do it on the move (Ian, 10 Nov), so Invoices comes first, shops
 * included; a shop without invoicing gets Orders. Only a screen this login can already see is
 * offered.
 *
 * pages: the login's flattened, already-filtered nav ([{ id, label, ... }]).
 */
export const MAIN_SCREENS = ["invoices", "orders"];

export function mainScreen(pages) {
  for (const id of MAIN_SCREENS) {
    const page = (pages || []).find((p) => p.id === id);
    if (page) return page;
  }
  return null;
}
