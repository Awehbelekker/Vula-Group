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
- **Subscribe to field:** `messages`

### 2c. Embedded Signup configuration
WhatsApp → **Embedded Signup** → Create configuration.
- Name: "Vula Commerce Onboarding"
- Grant: `whatsapp_business_management`, `whatsapp_business_messaging`
- Copy the **Configuration ID** → send to Ian's dev → set as `VITE_FB_CONFIG_ID` on Vercel.

### 2d. App settings
- App Domains: `vula-ai.com`, `vuladashboard.vercel.app`
- Privacy Policy URL: `https://vuladashboard.vercel.app/#/privacy`
- Data Deletion URL: `https://vuladashboard.vercel.app/#/data-deletion`
- Category: Business

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
2. Go to **Settings → WhatsApp** and click **Connect WhatsApp**.
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
- **Deletion:** merchants can disconnect (token revoked + record cleared) from
  Settings → WhatsApp; data-deletion endpoint at `/#/data-deletion`.

---

## Step 4 — After approval (Vula dev)
1. Receive the **Configuration ID** → set `VITE_FB_CONFIG_ID` on Vercel → redeploy dashboard.
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
