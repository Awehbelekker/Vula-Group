# Vula Commerce — Meta WhatsApp App Launch Packet

Everything needed to take the Vula WhatsApp integration live so tenants can
self-connect their own numbers. Copy-paste the text blocks straight into Meta.

App ID: **1318233147079069**

---

## Which portal does what

| Step | Portal |
|---|---|
| Business Verification | **business.facebook.com** → Business Settings → Security Centre |
| App Review, permissions, Embedded Signup config, webhooks | **developers.facebook.com** → your app |
| WhatsApp number management | developers.facebook.com → app → WhatsApp |

So yes — App Review + Embedded Signup config + webhooks are all on the
**developers.facebook.com** portal. Only Business Verification lives in
Business Manager (business.facebook.com), which is linked to the same app.

---

## Step 1 — Business Verification (business.facebook.com)
Business Settings → **Security Centre** → Start Verification.
- Legal business name + registration number (CIPC reg)
- Business address, phone, website (vula-ai.com)
- Upload: CIPC registration doc + proof of address (utility bill / bank letter)
- Timeline: ~2–5 business days.

---

## Step 2 — App configuration (developers.facebook.com → app 1318233147079069)

### 2a. WhatsApp product
Add the **WhatsApp** product if not present.

### 2b. Webhook (so Meta delivers inbound messages)
WhatsApp → Configuration → Webhook:
- **Callback URL:** `https://vula-group-production.up.railway.app/v1/whatsapp/webhook`
- **Verify token:** the value of `WHATSAPP_VERIFY_TOKEN` (already set on Railway)
- **Subscribe to fields:** `messages`, plus three that only fire on a number shared with the
  WhatsApp Business app ("coexistence", see 2e): `smb_message_echoes` (the owner replied from
  their phone, so Vula goes quiet in that chat), `history` (the chats they chose to share) and
  `smb_app_state_sync` (their contacts' names). Without these three, a shared number still
  works, but Vula would talk over the owner.
- Also subscribe to the account-health fields: `account_update` (account banned, restricted or
  disabled), `message_template_status_update` (a template approved, rejected, paused or disabled),
  `phone_number_quality_update` (quality rating / daily limit changes) and
  `template_category_update`. Vula logs each one and sends an operator alert
  (docs/operator-alerts.md) for a rejected/paused/disabled template, a quality downgrade
  (RED/YELLOW) or a banned/restricted/disabled account (`vula/api/whatsapp_account_events.py`).
- **Graph API version:** every Graph call uses `GRAPH_API_VERSION` on Railway (default `v25.0`,
  set in `vula_mind/config.py`), and the dashboard's FB SDK uses `VITE_FB_GRAPH_VERSION` on Vercel
  (default `v25.0`). Keep the two the same, and move both before Meta retires the version.

### 2c. Embedded Signup configuration (v4)
Meta retired Embedded Signup v2 and v3 on 15 Oct 2026. The version comes from the
configuration, so an old Configuration ID has to be replaced with a new one.

Facebook Login for Business → **Configurations** → Create configuration.
- Name: "Vula Onboarding v4"
- Login variation: **WhatsApp Embedded Signup**
- Assets: WhatsApp accounts; permissions `whatsapp_business_management`, `whatsapp_business_messaging`
- Tick **WhatsApp Business app onboarding** (coexistence) so owners can bring the number they already use
- Copy the **Configuration ID** → set as `VITE_FB_CONFIG_ID` on Vercel → redeploy the dashboard.

### 2d. App settings
- App Domains: `vula-ai.com`, `vuladashboard.vercel.app`
- Privacy Policy URL: `https://vuladashboard.vercel.app/#/privacy`
- Data Deletion URL: `https://vuladashboard.vercel.app/#/data-deletion`
- Category: Business

### 2e. Coexistence: a business keeps its WhatsApp Business app number
In Settings → WhatsApp number, the owner picks one of two buttons:
- **Use my WhatsApp Business number**: Meta's window asks them to scan a code in the
  WhatsApp Business app (Settings → Linked devices / Business platform) and choose whether to
  share chats and contacts. They keep using the app on their phone.
  Vula does **not** call `/register` on this number. Right after connecting it asks Meta for
  the contacts and history (`POST /{phone_number_id}/smb_app_data`; Meta only sends them if
  asked within 24 hours). History is imported into each chat's thread, the last 50 messages
  per chat, text only. When the owner replies from the phone, Vula goes quiet in that chat.
  It picks the chat up again after 2 hours of silence, the same as an Inbox handoff.
- **Connect a new number**: the usual Embedded Signup. The number must not be on WhatsApp
  yet. Meta sends a code by SMS or call. Vula registers it for the Cloud API (`/register`) only
  when Railway has `WHATSAPP_REGISTER_ON_CONNECT=true` **and** `WHATSAPP_REGISTRATION_PIN` (the
  number's two-step PIN) set; otherwise the number has to be registered by hand before it can send.

### 2f. Several numbers per business, each with a purpose
Settings → WhatsApp number lists every number with what it's for:
- **Customers: shop and orders**
- **Customers: bookings**: bookings first
- **Assistant: answers from your documents**
- **Team only**: the team is answered; customers are pointed to the main number

**Add a number** asks what the new one is for, then connects it either of the two ways above. One number is the **main number**:
- Vula's own messages (order updates, reminders, login codes) go from it.
- A reply always goes from the number the person wrote to.

Numbers count toward the WhatsApp account they sit on:
- Vula's account: 20 once verified.
- A business's own account: 2 until its business is verified.

Needs migration `225_whatsapp_numbers.sql`, applied only after the code is deployed (it drops the one-number-per-business constraint the old code relies on).

Needs migration `223_whatsapp_coexistence.sql` applied (Supabase SQL editor). Until it is,
connecting still works, but the dashboard can't show "Shared with" on the number.

---

## Step 3 — App Review submission (developers.facebook.com → App Review → Permissions and Features)

Request **Advanced Access** for both:
- `whatsapp_business_management`
- `whatsapp_business_messaging`

### App description (paste into "App details")
> Vula Commerce is a multi-tenant business platform for South African retailers
> (food, seafood, retail). It lets each merchant connect their own WhatsApp
> Business number so their customers can browse products, place orders, pay, and
> receive order updates over WhatsApp, and so the merchant can manage orders,
> invoices, stock and broadcasts from a web dashboard. Vula acts as a technology
> provider: each merchant authorises Vula via Meta Embedded Signup to send and
> manage messages on their own WhatsApp Business Account.

### Justification — `whatsapp_business_messaging` (paste)
> Used to send and receive WhatsApp messages on behalf of each merchant who has
> connected their own WhatsApp Business Account via Embedded Signup. Customer
> messages are answered by the merchant's AI ordering assistant (product search,
> cart, checkout, order tracking) and the merchant sends order confirmations and
> opt-in promotional broadcasts using approved message templates.

### Justification — `whatsapp_business_management` (paste)
> Used to read the merchant's WhatsApp Business Account details (phone numbers,
> display name) discovered during Embedded Signup, and to subscribe Vula's
> webhook to the merchant's WABA so inbound customer messages are delivered to
> the merchant's assistant. No data is shared between merchants; each WABA is
> isolated by tenant.

### Demo video script (Meta requires a screencast showing the permissions in use)
Record this flow (≤ 3 min, screen recording):
1. Log into the Vula dashboard as a merchant owner.
2. Go to **Settings → WhatsApp number** and click **Use my WhatsApp Business number** (or **Connect a new number**).
3. Show the Meta Embedded Signup popup: log in, select the WhatsApp Business
   Account, select/verify the phone number, finish.
4. Back in Vula, show the status flips to **Connected** with the number + webhook registered.
5. From another phone, send a WhatsApp message to that number ("hi").
6. Show the AI assistant replying with the product menu, adding an item, and a checkout link.
7. Show the merchant sending an order confirmation / broadcast from the dashboard.
Narrate which permission each step uses (management = connect/discover/webhook;
messaging = send/receive).

### Data handling answers
- **What data:** WhatsApp business account id, phone number id, display name,
  access token (stored encrypted at rest in Supabase, per tenant), message
  content for processing replies.
- **Why stored:** to route and answer the merchant's customer messages and send
  order updates.
- **Deletion:** merchants can disconnect from Settings → WhatsApp. Vula deletes the stored
  access token and marks the number disconnected (the account row is kept, without the token,
  so the history of which number belonged to which business stays). When no connected number is
  left on that WhatsApp Business Account, Vula also removes its app's webhook subscription
  (`DELETE /{waba_id}/subscribed_apps`, best effort). The token itself is not revoked at Meta; the
  merchant can remove Vula's access in Meta Business Settings. Data-deletion endpoint at
  `/#/data-deletion`.

---

## Step 4 — After approval (Vula dev)
1. Make sure the **Configuration ID** you created yourself in the portal (Step 2c — Meta doesn't
   send one) is set as `VITE_FB_CONFIG_ID` on Vercel → redeploy dashboard.
2. Switch the app to **Live** mode (toggle, developers.facebook.com).
3. Test: a real tenant self-connects from Settings → WhatsApp.

---

## Launch NOW (bridge while review is pending)
The app is in **Development mode** until approved, so external Embedded Signup
won't work yet. Two things already work today:
1. **Off the Hook is live** on its own number via a manually-provisioned token.
2. **Shared-number bridge:** `WHATSAPP_TOKEN` + `WHATSAPP_PHONE_ID` are set on
   Railway, so any tenant *without* their own connection automatically falls back
   to Vula's number (`_get_tenant_wa_creds` in `vula/api/whatsapp.py`). Good for
   demos and early tenants; migrate each to their own number via Embedded Signup
   once the app is approved.

Test users can also complete Embedded Signup before approval: add their FB user
under App Roles → Roles → Testers.
