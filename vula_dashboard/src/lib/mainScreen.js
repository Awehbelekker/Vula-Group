/**
 * mainScreen.js — the screen a business works in most, on the phone bar (10 Nov 2026).
 *
 * The phone bar had Search, Add, Today, Inbox and More, so a business's everyday screen was three
 * taps away (More › Money › Invoices). DIGG's owner couldn't find Invoices on her phone at all,
 * and invoicing is most of what DIGG does in Vula. A shop works in Orders; a business that
 * doesn't sell products works in Invoices. Only a screen this login can already see is offered.
 *
 * pages: the login's flattened, already-filtered nav ([{ id, label, ... }]).
 */
export const MAIN_SCREENS = ["orders", "invoices"];

export function mainScreen(pages) {
  for (const id of MAIN_SCREENS) {
    const page = (pages || []).find((p) => p.id === id);
    if (page) return page;
  }
  return null;
}
