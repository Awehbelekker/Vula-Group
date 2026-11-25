# What a business can actually reach: products, hidden features, menus (23 Nov 2026)

Ian: "make sure all capabilities are actually available to tenants and clear and easy to find —
is it a UI/UX problem, or are some functions not built in depth?"

**Answer: mostly UI/UX.** The server can do far more than the dashboard shows. There are four
kinds of gap:

1. **Built, no screen at all.** About 25 routes, listed below.
2. **Built, but switched off per business, and only Vula's master login can switch it on.**
   - Modules are set by business type at sign-up. No owner can see or change them.
   - Two finished screens, **Flows** and **Tap to Pay**, can never appear. Their module names
     aren't in the module list, so the master panel can't switch them on either.
3. **On screen, but shallow or broken.** Adding products is the clearest case (below).
4. **Not built.** A short list: VAT per product, add-ons with a price, availability schedules,
   tags, units other than kg/pack, loyalty, vouchers, referrals, returns, price lists.

## Adding products

The database already holds what a product needs:
- several photos, a sale price with an end date, draft/hidden status, cost and SEO;
- sizes, each with its own SKU, barcode, price, stock and reorder point;
- same-price choices and bundles.

The screen doesn't show most of it:

| Problem | Where | Kind |
|---|---|---|
| Add takes only name, price, sold by, category and description; photo, stock and sizes come after, in a long edit sheet ✔ 24 Nov: photos, stock, codes, cost and sizes/colours on the Add form; versions made from typed options; a photo per colour | `VulaMerchantAdmin.jsx` ProductEditPanel | UI |
| **Create changes any custom category to "extras"** | `api/commerce.py` create product | bug |
| **A failed add or save says nothing**: the form clears even when the server refused it | `createProduct` / `patch` never check `r.ok` | bug |
| The edit sheet saves four different ways: "Save details", on leaving a box, at once (photos, options), "Save choices". SEO boxes sit below "Save details" and only that button saves them | ProductEditPanel | UX |
| SKU, barcode and cost can't be set on the product (barcode only by the stock scanner, cost only from a scanned invoice) ✔ 24 Nov | PATCH whitelist `api/commerce.py` | UI + 1-line backend |
| Fish-shop fields (catch source, fisherman, cooking tips) shown to every business | ProductEditPanel | UX |
| "Pricing: by weight" duplicates "Sold by kg" and nothing reads it | migration 085 fields | remove |
| Categories can be added, not renamed, deleted or reordered (delete exists on the server) | Products › Categories | UI |
| CSV import wants prices in cents and offers no template | Products › Import CSV | UX |
| WhatsApp `create_product` defaults the category to "other" (no such category) and fails on a duplicate name | `core/skills/commerce_admin.py` | bug |
| Shopify/WooCommerce imports save sizes without their option names, so the shop chat can sell every size at the lowest price | `vula/integrations/shopify.py`, `woocommerce.py` | bug |
| A sale shows its normal price on the WhatsApp list and the menu page; the WhatsApp catalogue keeps a sale after it ends | `commerce_assistant` list, `meta_catalog._item` | bug |
| Sizes aren't sent to the WhatsApp catalogue or shown on the menu page | `meta_catalog.py`, menu page | not built |
| Editing products doesn't update the WhatsApp catalogue (now: a button, 23 Nov) | Products | done (manual) |
| Selling a bundle doesn't take its parts off stock | orders | not built |
| Home's low-stock list uses "< 5", ignoring each product's own reorder point | `server.py`, `service.py` | bug |
| VAT per product (zero-rated items), add-ons with a price, availability by day/hour, tags, litre/metre/each | — | not built |

## Built on the server, no screen

| Area | What it does | Other way in today |
|---|---|---|
| Sell | Customer ratings and the average | **Done 23 Nov: Sell › Reviews** |
| Sell | WhatsApp shop funnel and what customers searched for | none (Home's `shop` card is never drawn) |
| Sell | Delete a category; undo a historical import | none |
| Money | Dismiss bank lines in bulk or by filter | none |
| Money | Look up unknown payees on the web, then ask the owner on WhatsApp | none |
| Money | Reconcile a statement from pasted text | none |
| Money | Send an invoice to an approver (e.g. the architect) before the client | none |
| Money | Edit a recurring bill; delete a saved invoice client | none |
| Money | General ledger and financial statements on screen (only CSV/PDF downloads today) | downloads |
| Money | Revoke a Tap to Pay receipt | none |
| Operate | Lock the signed estimate as the cost baseline; load a programme into daily tasks; add site staff; preview the morning messages | WhatsApp, automatic on filing |
| Estimating | Past AI drafts and their branded PDF | none |
| Assistant | Create/delete inbox quick replies (the inbox only reads them) | none |
| Assistant | Cost per conversation and service figures | none |
| Settings | Choose which cards Home shows | none |
| Settings | ClickUp → knowledge base sync | none |

## Menus and names

- **Owners can't see or switch on modules.** Only the master can. Add a Settings › Features page:
  what's on, what's available on their plan, and a request button.
- **Flows and Tap to Pay can never appear.** Add `flows` and `taptopay` to the module list (or
  `MERCHANT_MODMAP`).
- **Three "Costings":** Costing (manufacturing, under Sell), Costing by trade (Estimating) and Job
  costing (inside Finances). Rename the first "Recipes & batches" and move it to Operate.
- **Money overlaps:** Finances, Accounts, Books review and Bank & matching overlap; the ledger and
  trial balance appear in both Finances and Accounts.
- **Marketing names:**
  - "Campaigns" is really a copy writer, while the real campaigns live inside Broadcast. Rename it
    "Write a message" and link it from Broadcast.
  - "Scheduling" is system jobs, not marketing. Move it to Assistant › Automations.
- **Two "Activity" pages:** Assistant › Activity and the top-level All activity.
- **Recurring things are split three ways:** subscriptions (Sell), recurring invoices (Invoices),
  recurring bills (Budget).
- **Stock and Suppliers** are listed under Sell but shown in Operate.

## Order

1. **Done 23 Nov:** Sell › Reviews with the Google review link, Sell › Sales reports, and "Send to
   WhatsApp catalogue" on Products.
2. **Products, the bugs:**
   - keep the category on create;
   - show the server's error;
   - SKU, barcode and cost on the product;
   - category rename/delete;
   - WhatsApp `create_product` category and duplicate name;
   - Shopify/Woo option names;
   - sale price on the WhatsApp list and menu, and sale end in the catalogue;
   - low stock from each product's own reorder point.
3. **Products, the screen:**
   - one Add sheet with photo, stock and sizes;
   - one Save;
   - fish fields only for food businesses;
   - CSV in rand with a template;
   - drop the unused by-weight fields.
4. **Reach:**
   - Settings › Features (see and request modules);
   - Flows and Tap to Pay switchable;
   - the shop funnel card on Home;
   - quick replies in the inbox;
   - the approver step on invoices;
   - bulk-dismiss bank lines.
5. **Names and places:** as listed above, one PR, ids unchanged so links and team access keep
   working.
6. **Real builds:** VAT per product, priced add-ons, availability schedules, returns, price lists,
   then loyalty and vouchers.
