# Google Calendar for Vula — Google Cloud setup

Each team member can connect their own Google Calendar ("connect my Google calendar" on WhatsApp,
or Team › People › My Google calendar). The code is in `vula_mind/vula/google/member_credentials.py`,
`vula_mind/vula/google_calendar.py` and `vula_mind/vula/api/google.py`. Reading calendars is a
*sensitive* Google scope (`calendar.events.readonly`, read-only), so Google must verify Vula's app
before anyone beyond its 100 Test users can connect. Until then it works for Test users, whose
connection lasts 7 days.

We reuse the existing Google project and OAuth client (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`).

## Steps (about 1 hour, then Google's review)

### 0. Before you start: a domain you own
Google only verifies apps whose home page, privacy policy and redirect URL sit on a domain you own
and have verified. `*.up.railway.app` is Railway's domain, not yours, and Google is likely to refuse
it.
- Use Vula's own domain (e.g. `vula-ai.com`).
- In Railway › the backend service › Settings › Networking › **Custom domain**, add
  `api.vula-ai.com` and create the CNAME it shows at your DNS provider.
- In Railway variables, set `PUBLIC_BASE_URL=https://api.vula-ai.com`, then run **`railway up`**
  again (an env change alone redeploys stale code).
- The app needs two public pages: a **home page** (e.g. `https://vula-ai.com`) and a **privacy
  policy** (e.g. `https://vula-ai.com/privacy`). The privacy policy must say Vula reads calendar
  events (titles, times, locations) only to remind that person and show meetings in their Vula
  diary, never sells them or uses them for ads, and deletes them when they disconnect.

### 1. Open the existing project
- Go to console.cloud.google.com. Sign in with the account that owns Vula's Google project.
- At the top, pick the project that holds the current Vula OAuth client. Its Client ID matches
  `GOOGLE_CLIENT_ID` in Railway.

### 2. Turn on the Google Calendar API
- Go to **APIs & Services › Library**, search for **Google Calendar API**, and click **Enable**.

### 3. Prove you own the domain
- Go to search.google.com/search-console › **Add property** › **Domain** › `vula-ai.com`.
- Add the TXT record it gives you at your DNS provider and click **Verify**.
- Use the same Google account as the Cloud project, or add it as an owner.

### 4. Branding (Google Auth Platform › **Branding**)
- **App name:** Vula
- **User support email:** your support address
- **App logo:** a 120×120 PNG (optional, but it speeds up review)
- **App home page:** `https://vula-ai.com`
- **Privacy policy:** `https://vula-ai.com/privacy`
- **Terms of service:** `https://vula-ai.com/terms` (if you have one)
- **Authorized domains:** `vula-ai.com`
- **Developer contact email:** your email
- Click **Save**.

### 5. Audience (Google Auth Platform › **Audience**)
- **User type:** External.
- While Google reviews, leave it in **Testing** and add yourself and up to 99 team members as
  **Test users**, by the Google email they'll connect.
  - In Testing, a connection lasts 7 days and then has to be connected again. That's fine for
    trying it out.
- After approval, click **Publish app**, so it goes "In production".

### 6. Data access: add the calendar scope (Google Auth Platform › **Data Access**)
- Click **Add or remove scopes**. Tick `.../auth/calendar.events.readonly` ("View events on all
  your calendars"), then **Update** and **Save**.
- Keep `drive.file` as it is.

### 7. Client redirect URL (Google Auth Platform › **Clients** › the Vula web client)
- Under **Authorized redirect URIs**, add `https://api.vula-ai.com/v1/google/oauth/callback`.
- Keep the old railway one until the switch is done, then remove it.
- Click **Save**. The Client ID and secret don't change.

### 8. Record the demo video (needed for verification)
Upload a 2–4 minute screen recording to YouTube as **Unlisted**, in English, showing:
1. The Vula dashboard › Team › People › **My Google calendar** › **Connect** (or WhatsApp
   "connect my Google calendar" opening the link).
2. Google's consent screen, with the **address bar visible**, showing the app name *Vula*, the
   Client ID in the URL, and the "View events on all your calendars" permission.
3. Granting access, then the meeting appearing in Vula ("what meetings do I have?") and a reminder
   with the address and map link.
4. Disconnecting.

I'll build the feature first (Part B) so you can record this against the real thing in Testing mode.

### 9. Submit for verification (Google Auth Platform › **Verification Center**)
- Click **Prepare for verification** / **Submit**.
- **Scope justification** (paste and adjust):
  > Vula is a WhatsApp business assistant for small businesses. A team member can choose to
  > connect their own Google Calendar so Vula can remind them before meetings (with the address
  > and a map link), show their meetings in their Vula diary, and prepare a short brief about who
  > they're meeting. We only read events (calendar.events.readonly); we never create, change or
  > delete events, never read other Google data, never sell data or use it for advertising.
  > Events are kept only for the next 7 days and removed when the person disconnects.
- Add the YouTube link and confirm the domain and privacy policy.
- **Google replies by email**, usually within 3–10 working days. Often they ask a follow-up
  question; answer it in the same thread. When approved, **Publish app** (step 5).

### 10. Things that commonly get an app rejected
- The privacy policy doesn't mention Google calendar data, or isn't on the verified domain.
- The video doesn't show the consent screen with the URL / Client ID.
- The app name on the consent screen differs from the one on the home page.
- Asking for more than needed. We only ask read-only events, which is fine.

---
