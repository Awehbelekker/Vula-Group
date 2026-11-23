# Vula WhatsApp commerce audit (23 Nov 2026)

Written to the brief "Vula WhatsApp Commerce Audit Prompt". Read only. Repositories read:
`Vula-Group` (backend `vula_mind`, dashboard `vula_dashboard`) and `vula-storefront` (one commit,
`feae147`, 12 Aug 2026). The comparison site named in the brief (Shop Easy / shopeazy.co) could
not be opened from this session (its network blocks the host) and no web listing for it was found.
The comparison therefore uses **Shopstar** (South Africa, sells on WhatsApp, from R220/month) and
Shopify's standard features as the baseline. Re-run Part 2 against ShopEazy from a session on the
`vula-group` environment, which allows that site.

## 1. Summary

**WhatsApp commerce works end to end today: browse, cart, checkout, pay, confirmation, delivery
updates, ratings.**
- **The real shop is not the Next.js storefront.** It is the WhatsApp conversation plus a fast
  `/menu/{tenant}` web page that `vula_mind` serves itself.
- **That page has:**
  - a basket that hands over to WhatsApp with a 6-character code;
  - a shop funnel;
  - broadcast attribution;
  - a custom `shop.` domain.

**What's missing is mostly polish and trust, not plumbing:**
- **Prices and options:**
  - sale prices aren't shown on the WhatsApp list;
  - sizes and colours aren't carried by tap-to-cart or the WhatsApp catalogue.
- **Checkout:**
  - no discount step in the button checkout;
  - only morning and afternoon slots, no couriers;
  - cancelled and refunded orders aren't messaged.
- **Web checkout** has Yoco as its only gateway, although five more are built.

**The `vula-storefront` repo is a page renderer only:**
- no product, cart or checkout pages;
- its product links go to a `/shop` that doesn't exist;
- no PWA;
- it has drifted from the dashboard's page builder.

**Fastest route to a pilot sale:** use the `/menu` shop and WhatsApp checkout that already work,
and fix the price, option and confirmation gaps. Making `vula-storefront` the shop is a later
build.

## 2. What exists today

| # | Item | Status | Where / note |
|---|---|---|---|
| 1 | Product model (products, variants, prices, stock, images, categories), per tenant, RLS | **Built** | `commerce_products`, `commerce_product_variants` (SKU, barcode, price, stock per size), choices (267), bundles, gallery, sale price + end date; RLS on every table. Gaps: no photo per variant, VAT per product, units beyond kg/pack, tags. |
| 2 | WhatsApp catalogue sync, product / multi-product messages | **Partial** | `commerce/meta_catalog.py` pushes products (colour ranges grouped). No sizes or choices sent; a sale stays after it ends; product links go to `/menu/{t}/products/{slug}`, which doesn't exist (tenants without a `store_url`). Connecting a catalogue is Master-only; a business can re-send from Products (PR #369). |
| 3 | Carousel / interactive product messages | **Partial** | List messages with photos, range cards, "Shop now" CTA, picture promotions (`whatsapp.py`, `broadcast`). No Meta carousel template. |
| 4 | Deep links to a product or pre-filled cart | **Partial** | `/menu/{t}` with `#r-`/`#c-` anchors; web basket → `wa.me/?text=…basket K7Q2MX` claimed into the WhatsApp cart (`web_basket.py`, `_maybe_claim_web_basket`). No link to a single product page; no signed customer identity in links (identity = the WhatsApp sender, which is sound). |
| 5 | Mobile web storefront / PWA | **Partial** | `/menu` page (`api/menu_page.py`): fast, lazy images, CDN resizing. No manifest, no service worker, no offline. `vula-storefront`: Puck pages only, plain `<img>`, no PWA. |
| 6 | Cart and checkout, guest by WhatsApp number | **Built** | WhatsApp: `button_checkout.py` (deliver/collect → address → slot → card/EFT/cash). Web: `POST /{t}/checkout` guest-by-phone (unused by `vula-storefront`). |
| 7 | Payment links and SA gateways; env vars failing loudly | **Partial** | Yoco, PayFast, Peach, Ozow, Paystack, iKhokha built (`vula/payments`); web checkout uses **Yoco only**; no-`store_url` redirect falls back to `offthehook.co.za`; `failureUrl` `/payment/failed` has no route. Missing keys: `/ready` reports them as info, startup logs an ERROR per business with no payment method, the app still boots. |
| 8 | Order lifecycle, ledger, invoices, receipts | **Built** | Orders, statuses, receipt page with signed token (`order_slip.py`), invoice on order, one-book ledger postings (`ledger_events`). |
| 9 | WhatsApp notifications | **Partial** | Packing, dispatched, delivered (with rating), payment received, basket reminders (`bring_back.py`). No message on cancel/refund; collection orders told "on its way, expected morning". |
| 10 | Shipping, couriers, click-and-collect | **Partial** | Delivery radius/fees, collect, run sheet. Slots fixed to morning/afternoon; express surcharge never applied; **no couriers** (no Courier Guy/Pargo/Bob Go/PUDO). |
| 11 | Discounts | **Partial** | Codes (percent, fixed, free delivery, limits, first order). Not in the button checkout; a wrong code dropped silently; confirmation omits the discount line. |
| 12 | Admin from WhatsApp and dashboard | **Built** (uneven) | Dashboard Products/Orders/Stock/Discounts/Delivery; WhatsApp `create_product` etc. are thinner (4 fields, category "other", no sizes/sale/bundles). See `plans/full-audit-2026-11-23.md` §5. |
| 13 | Storefront templates | **Partial** | Dashboard Puck builder with niche templates (food, retail, trades, services, health, real estate) and brand theming; `vula-storefront` renders the same JSON from a hand-synced, drifted copy (no Booking, FAQ, Pricing, Property blocks). |
| 14 | Custom domains / subdomains | **Built** (backend) / **Missing** (storefront) | `shop_domain.py` adds `shop.`/`order.` domains on Railway with DNS checks; `vula-storefront` is `{tenant}.vula.site` only, custom domains "Phase 2". |
| 15 | Analytics | **Built** (WhatsApp) / **Missing** (storefront) | Shop funnel open → basket → claimed → ordered → paid + searches (`shop_stats.py`); broadcast click attribution (`links.py`). Sales reports now in Sell (PR #369). Nothing in `vula-storefront`. |
| 16 | Human handoff and confirm gate on orders/refunds | **Built** | Handoff with reminder + give-back buttons (migration 295); confirm-before-send on money and customer messages; Yoco refund on cancelled → refunded. Customer "cancel/change order" tells them the team was asked whether or not anyone was. |
| 17 | Tests | **Built** (backend) / **Missing** (storefront) | `test_web_shop_basket`, `test_button_checkout`, `test_order_slip`, `test_shop_funnel`, `test_shop_domain_setup`, `test_storefront_checkout` (~9,000 backend tests). `vula-storefront`: none. |

## 3. Comparison (Shopstar / Shopify baseline)

| Feature | Shopstar / Shopify | Vula today | Gap | Effort |
|---|---|---|---|---|
| Storefront templates & themes | Several themes, drag-and-drop | Puck builder + niche templates, brand theming | Shop pages (product, cart, checkout) not in templates; storefront copy drifted | M |
| Product management | Variants with images, bulk edit, CSV | Variants, choices, bundles, gallery, CSV | Photo per variant, grid generator, bulk edit, VAT per product; category bug on create | M |
| Checkout | Web checkout, saved details | WhatsApp checkout + web checkout API | Discount step in WhatsApp; web checkout page | S–M |
| Payments | Yoco, PayFast, Ozow, card | 6 gateways built | Web checkout uses Yoco only; failure page missing | S |
| Shipping | Courier integrations, rates, tracking | Own delivery, collect | Couriers (Bob Go / Courier Guy / Pargo), tracking links, flexible slots | L |
| Discounts | Codes, automatic discounts | Codes | Automatic/BOGO, in WhatsApp checkout | S–M |
| Abandoned cart | Email reminders | WhatsApp basket reminder | Screen of what was sent/recovered | S |
| Customer accounts | Logins, order history | Identity = WhatsApp number; receipts | Order history page by WhatsApp link | S–M |
| Analytics | Sales, conversion, top products | Funnel, attribution, sales reports | Storefront analytics | S |
| Apps / integrations | App store | Shopify, WooCommerce, Xero, Google, Microsoft | — (Vula imports from them) | — |
| Custom domains | Yes | `shop.` domains on Railway | Storefront custom domains | M |
| Mobile experience | Responsive, apps | `/menu` page fast on mobile | PWA (install, offline) | S |
| WhatsApp | Link-out / chat button | **Native**: catalogue, cart, checkout, payment, updates, ratings, AI assistant in 11 languages, handoff | — | — |

**Where WhatsApp-first is a real advantage:**
- The customer never leaves the chat they already use.
- No login.
- The assistant answers product questions and takes the order in their language.
- Updates, ratings and support all come back into the same chat.
- Broadcast clicks are attributed to sales.
- The business runs it all from WhatsApp too.

Shopstar's WhatsApp is a channel link; Vula's is the shop.

## 4. Template websites plan

Keep one page system, the dashboard Puck builder plus `vula_pages`. Make the renderer shared,
not hand-copied: one package used by the dashboard preview and `vula-storefront`, so the drift
can't recur. Three starting layouts, each a preset of existing blocks:

1. **Hero store (one product or a small range).**
   - Hero, product feature, testimonials, FAQ, WhatsApp CTA.
   - For Awake SA-style focused ranges.
2. **Catalogue store.**
   - Category tiles, product grid (live), featured, delivery info, WhatsApp CTA.
   - Reuses the `retail` / `home_food` templates.
3. **Services / booking.**
   - Hero, services, pricing table, booking widget, reviews, contact.
   - Reuses `services` / `trades` / `health`.

**How a business picks and brands a layout:**
- It picks a layout in Storefront.
- Brand kit colours and logo apply automatically (already built).
- One "Shop" button points at the working `/menu` shop until web product, cart and checkout
  pages exist.

**Then add to `vula-storefront`:**
- `/shop`, `/shop/{slug}`, `/cart` and `/checkout`, on the existing `/{t}/cart*` and
  `/{t}/checkout` API.
- A manifest and service worker.
- `next/image`.
- Custom domains in `middleware.ts`.

## 5. Go-live blockers (as recorded in `golive-and-takeoff-audit.md`; no 11 Sept document exists)

| Blocker | Status | Evidence |
|---|---|---|
| Production log mislabelling | **Fixed** | JSON logs with `level`; below ERROR → stdout, ERROR+ → stderr (`log_streams.py`), tests `test_logging_stream`, `test_json_logs` |
| Missing env vars silently disabling payment links and email | **Partly fixed** | `/ready` lists PUBLIC_BASE_URL, Resend, PayFast, Yoco as info, never failing; startup logs ERROR per business with no payment method; app still boots |
| No staging environment | **Open** | `docs/staging.md` "Vula has no staging environment"; only local tools (`smoke.py`, `staging_flows.py`) and CI's fresh-database replay |
| Broken rate scraper | **Partly fixed** | Weekly loop runs; matching rewritten with units, bands, "installed"; scraped rows mostly supply-only and rejected; web price lookup `takeoff/market.py` |
| Human handoff ghost risk | **Fixed** (caveats) | Reminder 30 min / give back 2 h per business, buttons (migration 295). Caveats: the "reminded" set is in memory (a restart can repeat one); the apology can't go outside WhatsApp's 24 h window |
| No end-to-end reply deadline | **Fixed** | `reply_deadline_s=75`, `reply_hard_cap_s=180`, holding message (`whatsapp._run_with_holding_message`) |
| English/Afrikaans-only escalation detection | **Fixed** | isiZulu, isiXhosa, Sesotho, Sepedi, Setswana phrase lists + an AI check for other and mixed-language messages (`escalation.py`, `core/lang.py`) |

Tenant auth (`ENFORCE_TENANT_AUTH`) is on by default (`config.py:455`) and `/ready` flags it if off.

## 6. Build list to a real sale for one pilot (Awake SA)

Ordered by what unblocks a real sale first.

1. **Payments that can't silently fail.**
   - Check Awake SA has a working gateway and the PUBLIC_BASE_URL is right.
   - `/ready` fails, not just informs, when the business has no way to take payment.
   - Add the `/payment/failed` page.
   - Remove the `offthehook.co.za` fallback.
2. **Prices customers see = prices they pay.**
   - Sale price on the WhatsApp list and in "added to cart".
   - The catalogue drops a sale when it ends.
3. **Sizes and colours.**
   - Carried by tap-to-cart and catalogue orders (ask which one).
   - Sent to the WhatsApp catalogue as variants.
   - A photo per colour.
4. **Checkout trust.**
   - A discount step with a clear "code not valid".
   - The discount line on the confirmation.
   - "Out of stock" said plainly.
   - Collection orders told "ready for collection", not "on its way".
5. **Order updates for cancelled and refunded orders,** and "the team will follow up" only when
   someone was actually told.
6. **Product links that open.**
   - `/menu/{t}/products/{slug}`, or point catalogue links at the menu anchor.
   - The storefront's `/shop` links go to the working shop.
7. **Opt-out read-back** (STOP must stick), and "catch"/"sell" no longer treated as supplier words.
8. **Couriers (Bob Go or Courier Guy) and tracking links.** Needed once Awake SA ships beyond its
   own delivery area.
9. **Later:** web shop pages in `vula-storefront` (shared renderer), PWA, storefront analytics,
   custom domains.
