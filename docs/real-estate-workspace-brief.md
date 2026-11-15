# Real-estate workspace — design brief

Studied from the Lintel demo (codeandchill.store, paid template) on 2026-11-10 for **design
direction only**. None of its code, images or copy is reused; everything below is rebuilt in the
Vula dashboard on real tenant data.

## What Lintel is
An agent/agency **back-office dashboard**, not a public property site. Pages: Overview, Rentals,
Sales, Deals (pipeline board), Leads (inbox), Contacts, and a per-property workspace. A "live chat"
rail runs down the right of every page.

## Look and feel (the parts worth matching)
- Pale grey page (#eef0f4-ish) with a soft multi-colour mesh glow behind white, slightly
  translucent cards. Large card radius (~24px), tiles ~18px, every control a full pill.
- One clean geometric sans throughout (Lintel uses Plus Jakarta Sans), 500 weight body, 700 headings;
  big confident numbers (KPIs at ~22px bold, hero figures ~36px).
- Top bar: logo left, round icon buttons right (search, notifications, dark mode, avatar). Below it,
  a full-width pill tab bar; the active tab is a solid dark pill.
- Page header = title + a row of KPI chips (round icon + number + small label) + one primary dark
  pill button ("New listing", "Record offer").
- A small status palette used consistently: blue = for sale / viewing, orange = offer / under offer,
  green = let / won / positive, red = risk / negative, near-black = under contract.
- Photo-led cards: image on top with a status pill overlaid, price bold, beds/baths/size as small
  chips, "x days left" footers, % vs asking coloured green/red.
- Subtle depth: inner white rim + long soft shadow; no hard borders.

## Page-by-page, mapped to Vula

| Lintel | What it shows | Vula equivalent (real data) |
|---|---|---|
| Overview | KPIs, commission chart vs target, featured listing carousel, pipeline by stage, today's agenda, portfolio map, rental occupancy, top agents, activity feed | listings + deals + bookings (viewings) + team; activity from the audit/activity feed |
| Sales | KPIs, asking-vs-estimate "market pulse", grid / list / map views, status filter chips | `vula_properties` (sale) |
| Rentals | occupancy, rent roll chart, lease calendar, units per building | `vula_properties` (rent) + leases (new) |
| Deals | kanban: Viewing → Offer → Negotiation → Under contract → Closed; card = property photo, buyer, offer, % vs asking, days left | deals/offers (new table) |
| Leads | inbox with score, source, budget, area, status chips (New/Contacted/Qualified/Viewing booked/Converted/Lost) | `vula_property_enquiries` + WhatsApp inbox; Vula scores the lead |
| Contacts | buyers / sellers / tenants / landlords / investors, budget, last contact, owner agent | existing CRM contacts + a role tag |
| Property page | stage stepper (Listing → Marketing → Viewings → Closing), price gauge vs market, offers, documents, media, overview facts, description, features | listing detail + offers + documents module |
| Live chat rail | team chat with photos and voice notes | **the real WhatsApp inbox** — customers and team, already in Vula |

## Where Vula does better than the template
- Everything is live: listings, enquiries and viewings come from the database, not demo JSON.
- Leads arrive from the website **and WhatsApp**, are scored and replied to by Vula, and route to
  the listing's agent automatically.
- Agents can run the workspace from WhatsApp ("mark Casa Alva under offer at R2.1m").
- Per-tenant branding: the blue accent becomes the agency's own `accent_color`.
- South African details: rand pricing, levies/rates, PPRA FFC numbers, POPIA-safe lead handling.

## Build order
1. Shared look: card, KPI chip, pill tabs, status pills (as a real-estate theme in the dashboard).
2. Listings (Sales/Rentals) page with grid/list/map + add/edit listing form — unblocks everything.
3. Leads inbox on `vula_property_enquiries` with statuses.
4. Deals board (new `vula_property_deals` table: property, contact, stage, offer cents, close date).
5. Overview page assembling the above.
6. Property workspace page (stepper, offers, documents, media).

## Status (2026-11-10) — what's built on this branch

Everything is behind module switches; a business without them sees no change.

| Piece | Module | Where |
|---|---|---|
| Listings, agents, enquiries (DB + API) | `listings` | migration 257, `vula/listings/`, `vula/api/listings.py` |
| Website blocks: property search grid + listing dialog, agents, enquiry form; "Homepage — Real estate" template | `listings` (editor only) | `vula_dashboard/src/puck/config.jsx`, `pages/usePages.js` |
| Customer WhatsApp: search, details + photo, viewing / call-back requests | `listings` | `core/skills/listing_tools.py` |
| Agent WhatsApp: add / edit listings (preview → confirm → read-back), photos by caption, enquiries | `listings` | `core/skills/listing_tools.py`, `vula/api/whatsapp.py::_maybe_listing_photo` |
| Deals pipeline for **any** business type (stages per business type) | `pipeline` | migration 258, `vula/pipeline/`, `vula/api/pipeline.py`, `core/skills/pipeline_tools.py` |
| Dashboard: Sell › Listings (grid, edit, enquiries inbox, agents), People › Pipeline (board, drag & drop) | `listings`, `pipeline` | `components/listings/`, `components/pipeline/` |

New optional features plug in the same way: one tools module registered in
`commerce_admin._ADDONS`, one module key, one dashboard tab whose id is the module key.

### Go-live checklist
1. Apply migrations 257 and 258 in the Supabase SQL editor (staging first).
2. Merge the branch; deploy the backend (`railway up`) and the dashboard (Vercel).
3. Switch on `listings` + `pipeline` for one agency (Master › Tenants) — or onboard it as
   "Real estate", which includes both.
4. Website leads: submit the `enquiry_received` template (docs/whatsapp-enquiry-template.md);
   once Meta approves it, set `WHATSAPP_ENQUIRY_TEMPLATE` on Railway and `railway up`. Until
   then the agent is alerted but the enquirer isn't messaged.

### Added 12 Nov (migration 259)
- **Listings as WhatsApp photo cards.** A client's search shows a swipeable carousel (View details →
  photo, facts, Book a viewing / Ask a question). Taps are handled by exact ids, no model.
- **Agents share chosen listings.** "Send Thandi the Durbanville and Bellville houses" → preview →
  confirm → carousel. Recorded, so a repeat send is flagged. Only within WhatsApp's 24-hour window
  (outside it, a Meta-approved carousel template would be needed — marketing category).
- **Clients' wish lists + matching.** A client's own search (or an agent's "Thandi wants…") is kept;
  when a listing goes on the market or drops in price, the agent is told who matches and picks who
  gets it — nothing is sent automatically. Dashboard: Listings › Clients, and "N clients are looking
  for something like this" in the listing form.
- **Portal enquiry emails.** Property24 / Private Property emails in the connected mailbox become
  enquiries (source "portal") and alert the agent. Reads only clearly labelled fields; layouts were
  not verified against real portal emails — forward one sample of each to check. HTML-only emails
  are filed with a "check your inbox" note.

### Next, when needed
- Listings import from the agency's existing CRM/portal feed (avoid double capture).
- ~~A map view of listings~~ — done: Listings › Map, a pin picker in the listing form, and an
  OpenStreetMap embed in the website's listing popup (only when the address is shown).
