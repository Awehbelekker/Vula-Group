# Full audit: can a business actually use everything Vula does? (23 Nov 2026)

Ian: "do a full audit of Vula and make sure we are not missing anything else". Read only. Each
section was read end to end, and the worst claims were re-checked in the code before going in
here (marked ✔). Sell and Marketing are in `capabilities-2026-11-23.md`; the WhatsApp side is §5
below; the shop, checkout and storefront are in `../vula-commerce-audit.md`.

**Verdict:**
- **The engine is deep.** The dashboard, by contrast, often fails silently, hides what the server
  can do, or still carries one client's wording.
- **Four kinds of problem, in the order to fix them:**
  1. **Access holes.** A restricted login can change things only an owner should.
  2. **Things that lose or corrupt work.** A save fails and the screen says it worked.
  3. **One client's leftovers.** Fish, DIGG and a real person's name, shown to every business.
  4. **Built but unreachable or shallow.** No screen, master-only, or split across confusing places.

---

## 1. Access: fix first

| # | Finding | Evidence |
|---|---|---|
| A1 | **Any team login (a sales rep too) can change the invoice bank details, VAT registration and invoice settings** ✔. The server's section rules cover `invoices…` but not `invoice-settings`. Invoice-fraud risk: change the account number, customers pay someone else. | `vula/api/server.py` `_SECTION_RULES`; `commerce.py` `/admin/invoice-settings` |
| A2 | The same applies to opening hours, delivery fees, voice, shop domain, the Yoco secret key, connecting/disconnecting WhatsApp or a mailbox — membership only. Hiding the screens is browser-only. | `server.py:2086-2092`, `tenant_auth.py:68-79`, `yoco_connect.py:88` |
| A3 | **An empty access list means full access** ✔. "Add member" sends `access: []`, so a new staff member is a full user; un-ticking the last chip makes someone full; creating a login can overwrite a restricted member's list with `[]`. | `tenant_auth.py:183-188`, `team/useTeam.js:143`, `users.py:186` |
| A4 | "My" lists that return everyone's records when the person has no number: My Work › Expenses, rep Today (bookings, meeting notes), rep Contacts and Reminders. The server trusts the filter the browser sends. | `useExpenses.js:45`, `commerce.py:2429, 2501-2513, 4835` |
| A5 | Rep contacts are keyed on (tenant, phone): one rep saving a number overwrites another rep's contact. | `commerce.py:2462` |
| A6 | Every member can read the whole team list (WhatsApp numbers, birthdays). | `team.py:99-114` |
| A7 | Public `/v1/tenant/{id}/status` shows plan, paid and trial to anyone. | `onboarding.py:653-673` |
| A8 | Sign-up has no reserved names (`registry`, `by-domain`, `ai-spend` collide with routes). | `signup.py:92-100`, `tenants.py:340-465` |
| A9 | The old app login compares an unsalted SHA-256 of a temporary password. | `onboarding.py:635` |

**Fix:**
- Settings, connections and invoice settings become owner/manager only on the server, with a
  test per route.
- Access is "nothing" unless listed. Full access comes only from the role.
- "My" filters come from the signed-in person on the server, never from the browser.

## 2. Wrong or lost work (money, customers, content)

| # | Finding | Evidence |
|---|---|---|
| B1 | **Saving invoice branding marks a business VAT-registered when it has no settings yet** ✔ (`vat_registered ?? true`). An unregistered business would then charge 15% VAT. That's illegal, and it puts wrong totals on invoices. | `VulaInvoices.jsx:1020` |
| B2 | 15% is hard-coded in the recurring-invoice create (`vat_rate: 15`), in credit-note previews (`|| 15`) and in the invoice form; totals are worked out in the browser and can differ from what the server saves. | `VulaInvoices.jsx:500, 597-612, 788, 969` |
| B3 | **Renaming a Storefront page wipes its content and SEO** ✔ (sends only title and status; the server overwrites the rest with `{}`). | `pages/usePages.js:355-361`, `commerce.py:352-358` |
| B4 | "Make live" deletes the preview page even when copying it failed. | `usePages.js:407-411` |
| B5 | Replying to a customer in the Inbox clears the box before sending and never checks it went: the message is lost and the customer never gets it. Escalation answers the same. | `inbox/useInbox.js:65, 150` |
| B6 | Creating an invoice or quote closes the form whether or not it saved: the typed lines are lost. Invoice settings, recurring invoices, catalog items the same. | `VulaInvoices.jsx:641-658` ✔, `871, 969, 1135` |
| B7 | **Field Ops sign-off always fails** ✔ (sends the task id to the walkthrough route → "not found"), and "Chase on WhatsApp" always fails (empty body; the server requires `task_id`). Neither tells the user. | `fieldops/useFieldOps.js:153, 168`, `field_ops.py:74, 312` |
| B8 | **Adding a product drops its category** unless it's one of Off the Hook's five ✔; a failed add says nothing ✔. | `commerce.py:1102-1103`, `VulaMerchantAdmin.jsx:921-937` |
| B9 | Bank statement upload: a refused upload polls for up to 10 minutes, then says "Statement reconciled ✓". | `bank/useBank.js:164-181` |
| B10 | Expenses: the server answers failures with HTTP 200 `{error}`, the screen never reads it: "Updated." / "Allocated + learned." on failure. | `commerce.py:4874, 4884`, `useExpenses.js` |
| B11 | Payment gateway connect (keys typed in), branding, delivery settings, opening hours, automations, team add: shown as saved when refused. | `usePayments.js:45`, `useSettings.js:237, 308`, `useAutomations.js:91`, `useTeam.js:138` |
| B12 | Brand kit: if loading fails the form opens with defaults; Save then overwrites the real brand. | `useSettings.js:68` |
| B13 | Imported invoices/orders: a refusal shows "✓ undefined invoice(s) registered" and clears the reviewed list. | `import/useImport.js:49, 64` |
| B14 | Follow-ups "Dismiss" actually snoozes forever (snoozed items are never fetched again). | `FollowupsV2.jsx:182`, `useFollowups.js:207` |
| B15 | Finances crashes on an error answer; project budgets, contracts and claims are stored as float rand, not cents. | `FinancesV2.jsx:59, 191`, `api/projects.py:89-124` |

**Root cause, across the whole dashboard:**
- **The shared `authFetch` treats any error other than 401/403 as success** ✔ (`lib/authFetch.js:14-22`).
- **162 saves don't check whether the server accepted them**, across 52 files: 90 with no check at
  all, 33 judging only by an `error` key while FastAPI says `detail`, and 39 with only a network catch.
- **34 forms clear or close regardless of the result.**
- **175 page loads show "No … yet" or spin forever when the load failed**, so a business sees "No
  orders" when the server is down.

**Fix:**
- **One helper** (`lib/api.js`): it throws on any non-2xx with the server's `detail`.
- **Server failures become real errors.** Routes that answer 200 `{error}` (expenses, cards,
  accounts, payments, QS) get proper status codes.
- **Sweep file by file,** worst first (the table in §5), with a Playwright test per screen that
  makes the server refuse and checks the error shows and nothing is cleared.

## 3. One client's leftovers, shown to every business

- **Fish everywhere:**
  - Broadcast's default template is "🐟 Weekly fish specials", and its example is yellowtail and snoek.
  - Campaigns says "20% off crayfish".
  - The Products list stars "Catch of the day" on every row, and the product editor has Catch source,
    Fisherman ("Skipper Jan") and the categories Fresh Fish and Frozen Seafood.
  - The storefront's default blocks say "Caught daily" and "Today's catch".
  - The page-builder prompt says "fresh fish shop".
- **DIGG and people's names:**
  - "DIGG paid the R3,700 plumbing change" (Projects).
  - "DIGG turns 5" (Notices).
  - "e.g. DIGG 2026" (QS Rates).
  - "the Dick meeting was about self-levelling, not HBC" (rep call sheet).
  - **"Judy Downing, Director" as the example signature on every business's invoice settings** ✔.
    That's a real person from one client, shown to every other business.
  - "e.g. Off the Hook" at sign-up.
  - "gerflor.co.za" and "seedleme.myshopify.com" as placeholders.
- **One region and one bank:**
  - Bank uploads are labelled "Capitec" whatever the bank.
  - The map starts in Cape Town.
  - Quick Cost uses City of Cape Town fees, a Western Cape factor and "HVAC / ExtraAir".
- **Field Ops WhatsApp templates** are named `digg_task_assigned` and similar, for every business.
- **Vula's own legal name differs between pages:** "Aweh Be Lekker (Pty) Ltd" on the dashboard
  and "Vula Group (Pty) Ltd" on Privacy.

**Fix:**
- Fish wording only when the business type is food.
- Neutral examples for everyone else.
- The real name and the DIGG/Dick/HBC examples removed now.
- Region defaults from the business's own address.
- Field Ops templates renamed (re-submitted to Meta under neutral names).

## 4. Built but not reachable, or shallow

**Money**
- **The owner can't see a balance sheet, cash flow, the statements PDF or aged creditors.** The
  accountant's login can.
- **No screen for:** the tax estimate and provisional tax (`commerce/tax.py`), pro-forma documents,
  an invoice's payment history, declining or expiring a quote, overdue-reminder settings, the
  chart of accounts, undoing a bank match, a bank-lines CSV, editing a recurring bill.
- **Expenses can't be rejected or deleted,** and VAT and "paid with" can't be set when adding one.
- **"Budget" has no budgets.** It's a second expenses screen with shop categories and totals
  worked out in the browser; real project budgets are under Finances.
- **The Scanner can't edit what it read before booking it,** and takes no PDFs.
- **The invoice list shows only the first 50.**

**People, Inbox, Assistant**
- **Home and Inbox:**
  - "Waiting longest" and activity items open the Inbox list, not the chat.
  - Meetings on Today can't be clicked.
  - "Chats waiting on you" counts escalations.
  - An escalation has no link to its chat.
  - Answered escalations can't be looked back at.
- **Never on screen:** customer opt-outs and consent, the client-memory note, standing rules
  ("remember that…"), and the meetings diary and check-ins (WhatsApp only).
- **Customer data export or erase can only be asked for by the customer, on WhatsApp.** The owner
  can't do it for them (POPIA requests often come by email or phone).
- **Automations:**
  - Can't be edited.
  - Have no history.
  - Treat a refused rule as saved.
- **Customers and Pipeline:**
  - The segment chips are 3 fixed ones and ignore the saved segments from Broadcast.
  - Pipeline stages can't be edited, and "Who's on it" is free text.
- **My Work:**
  - "CRM sync" only searches Dynamics 365.
  - Reminders can only be made on WhatsApp.
  - Rep contacts can't be edited.

**Operate, Estimating, Storefront**
- **Projects:** team members and codes can't be removed, and several saves are unchecked.
- **Field Ops:**
  - No list of walkthroughs.
  - Approvals message a project team that no screen can set, so they reach nobody.
- **Estimating:**
  - Quick Cost, QS Pro and the AI draft only copy to the clipboard; nothing is saved.
  - A Takeoff job is lost on reload, and "send RFQs" isn't built.
- **Storefront:**
  - No general contact or enquiry form; "Get a quote" jumps to static text.
  - No analytics.
  - The domain is set in Settings while the page says "added later".
- **Four separate people lists:** Labour, contractors, the professional team and Team. Projects
  are picked three different ways.

**Settings, Team, sign-up**
- **Plan and billing:**
  - No plan or billing page: sign-up is Starter, nothing upgrades it, and the limit message says
    "upgrade to Growth" with no way to do so.
  - Cancelling is Master only.
  - AI top-ups are requests that Vula invoices by hand.
- **Owners can't change their business name or type,** and can't see or request modules.
- **POPIA for the business itself:**
  - It can't export all its data or delete its account. The privacy page says to email.
  - Sign-up has no consent box and no links to the privacy policy or terms.
- **WhatsApp self-connect** only works when the Facebook app ids are set; otherwise "ask Vula".
- **Logins:**
  - Invites are a temporary password the owner passes on by hand.
  - Staff without Settings access have no screen for their own two-step sign-in or password,
    although the two-step page says "everyone turns it on for themselves".
- **Payments are split.** Yoco is in Settings; PayFast, Ozow, Peach, iKhokha and Paystack are in
  Money › Payments, which services, trades and rep businesses don't get.

**Confusing names and places**
- **Duplicates and lookalikes:**
  - Three "Costing" screens.
  - Two Yoco set-ups.
  - Three "Activity" pages.
  - "QS Rates" (Operate) and "QS Pro" (Estimating) with the same icon.
  - Budget vs project budgets.
- **Settings in odd places:**
  - VAT and bank details are behind "Branding" in Invoices.
  - Handoff timing is under Marketing.
  - Owner briefs are under Team › Notices.
- **Screens that point to the wrong place:**
  - Finances says "Money › Books" (the tab is "Accounts").
  - Accounts says "turn on VAT in Settings" (it's in Invoices › Branding).
  - Invoices says "connect Yoco in Branding" (it's in Payments).
- **Operate:** every business with stock or suppliers gets a 🏗️ "Operate" section, a food shop
  included.

**Dead code still shipped**
- `FiledLibrary` (VulaDocuments).
- `BrandKitSettings` (VulaSettings).
- `OrdersTabV1` and `DiscountCodesV1` after unreachable returns.
- 49 one-line V1 wrappers.
- The old inline-style invoice forms are still the live create, credit-note, recurring and
  settings screens.

## 5. WhatsApp

**Customers**
- **Opt-out isn't checked before saying "Done"** ✔.
  - If saving STOP fails, the failure is logged quietly and the customer is still told "✅ Done", so
    they keep getting marketing (POPIA).
  - A failed delete of the contact record isn't counted as a failure either.
  - `commerce.record_opt_out`, `whatsapp.py:6662, 6709`.
- **"catch", "sell" and "supply" count as supplier words for every shop** ✔. "What's the catch
  today?" gets "Thanks for reaching out! We're always looking for quality suppliers", and the lead
  is written to a table no migration creates, nobody reads and nobody is told about.
  `whatsapp.py:9518-9543`.
- **Sale prices:** the WhatsApp product list and the "added to cart" reply show the normal price
  while the cart charges the sale price. The WhatsApp catalogue keeps a sale after it ends.
- **Sizes and colours:** tap-to-cart and WhatsApp catalogue orders add the product with no size,
  colour or choice. The catalogue sends no sizes at all.
- **Bundles** don't take their contents off stock.
- **Checkout:**
  - Morning and afternoon slots only.
  - The express surcharge is never added.
  - Collection orders are saved as "morning", so the customer is told "on its way! Expected:
    morning".
  - No discount-code step in the button checkout, and a wrong code is dropped silently.
  - The order confirmation leaves the discount out, so its sum doesn't add up.
  - Out of stock at checkout says "Something went wrong… try again".
- **Order updates:** only packing, dispatched and delivered are sent. A cancelled or refunded order
  gets no message.
- **Ratings on WhatsApp** are kept without the product, so they never show as product stars.
- **Help that doesn't happen:**
  - Cancel or change order, "ask the team" and unmatched catalogue items tell the customer someone
    will follow up, whether or not anyone was told.
  - Booking and subscription cancel and pause say "Done" without reading the change back.
- **Fixed texts are English only:** order updates, checkout buttons, STOP/START, delete/export.
- **Booking times** in "which one?" are raw UTC.

**Knowledge lines (DIGG-style)**
- **DONE / APPROVE / REJECT are taken from any number** with no role check. A customer typing
  "done" gets the contractor reply.
- **A customer's uploaded document is filed as if the business uploaded it.**
- **Owners don't reliably reach their own assistant** on a knowledge line. Only reps go straight
  there; owners need a costing or send-a-document phrasing.

**The business assistant (staff)**
- **WhatsApp only:**
  - finance settings, tax regime, payment reminders, email thread summary;
  - standing rules (remember/list/forget);
  - past-chat search, team message toggles, fill in a form, timesheets.
- **Weaker than the dashboard:**
  - `create_product` takes 4 fields, puts the product in category "other" and doesn't read it back.
  - `update_product` changes only price, name and "daily catch".
  - There are no tools for sizes, sale price, bundles or archiving.
  - Discount edits change only active and limit.
  - Low stock is "5 or fewer".
- **Reports success without reading back:**
  - cancel_booking says cancelled even for a wrong id;
  - quote status, suppliers, discounts, create_product, create_booking.
  - Only 17 tools use the read-back gate.
- **Tools only offered when the message matches their keywords.** "Add hake at R50 and broadcast
  it" only gets the broadcast tools.
- **One client's examples in tool descriptions:** Off the Hook ids (OTH-…), DIGG suppliers and
  projects (Belladonna, Pudlo, HPC), `friday_catch_reminder`, food-shop expense categories.
- **Fish in replies to every business:** "1kg hake", "Browse the catch", "photo Hake Fillets",
  seafood category labels, and a seafood-only `ProductCategory`.

---

## Fix order

1. **Access (§1).** One PR, server-side, with a test per route. Before more clients sign up.
2. **VAT default and invoice money (B1, B2), plus WhatsApp trust.** Budget figures move to cents;
   on WhatsApp:
   - opt-out read back before "Done";
   - "catch" and "sell" no longer treated as supplier words;
   - DONE/APPROVE/REJECT only from the right people;
   - "someone will follow up" only when someone was told.
3. **The save/load helper and the sweep (§2).** Worst first:
   - Inbox reply.
   - Invoice create and settings.
   - Storefront rename/make-live.
   - Field Ops sign-off and chase.
   - Products.
   - Bank upload.
   - Expenses.
   - Payments connect.
   - Settings.
   - Team.
4. **Leftovers (§3).** One PR; remove the real names now.
5. **Products** (see `capabilities-2026-11-23.md`).
6. **POPIA and self-service:**
   - business data export and account deletion;
   - consent at sign-up;
   - a plan and billing page;
   - email invites;
   - "my sign-in" (password, two-step) for every login.
7. **Reach:** a Settings › Features page, the owner's financial statements, opt-outs and
   client notes on the customer, escalation links, editable automations and pipeline stages.
8. **Names and places,** one PR (ids unchanged).
9. **Dead code out,** and the old invoice forms rebuilt on the V2 kit.
